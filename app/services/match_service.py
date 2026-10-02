import math
import re

from rapidfuzz import fuzz

from app.config import settings
from app.core.embeddings import embed
from app.core.es_client import es
from app.core.normalize import clean, compact, gtin14, is_placeholder, no_zeros
from app.schemas.matching import Candidate, ErpRow, MatchResponse, MatchResult, MatchSummary
from app.services.aliases import AliasModel, learn_aliases

BATCH = 500
UNITS = r'"|\'|IN|FT|OZ|GAL|QT|LBS?|TON|PCS?|PK|MM|CM|W|V|A|HP|PSI'
SIZE = re.compile(rf'\d+(?:[./]\d+)?(?:{UNITS})', re.IGNORECASE)


def split_joined(pn: str) -> list[str]:
    """'636/L-131' -> ['636/L-131', '636', 'L-131']"""
    pieces = [p.strip(" -") for p in re.split(r"[/,;]", pn) if p.strip(" -")]
    return list(dict.fromkeys([pn] + pieces))

def part_numbers(row: ErpRow) -> list[str]:
    """Part-number guesses from the MPN field and the first description word."""
    found = []
    mpn = clean(row.manufacturer_part_number)
    if mpn and not is_placeholder(mpn):
        found += split_joined(mpn)
    words = clean(row.item_description).split()
    if words and any(c.isdigit() for c in words[0]) and not is_size(words):
        found += split_joined(words[0])
    return [p for p in dict.fromkeys(found) if len(compact(p)) >= 2]

def is_size(words: list[str]) -> bool:
    """'16OZ FUNNEL', '3/4" UNION', '13 PC SET' start with a size, not a part number."""
    first, second = words[0], words[1] if len(words) > 1 else ""
    if SIZE.fullmatch(first) or re.match(r'\d+(?:/\d+)?["\']', first):
        return True
    return bool(re.fullmatch(r"\d+(?:/\d+)?", first) and re.fullmatch(UNITS, second, re.IGNORECASE))

def build_query(row: ErpRow, size: int = 20) -> dict:
    """Elasticsearch query that collects candidates for one row (part numbers, UPC, manufacturer, words)."""
    pns = part_numbers(row)
    keys = [compact(p) for p in pns]
    should = []
    if pns:
        should += [
            {"terms": {"mpn_compact": keys, "boost": 10}},
            {"terms": {"mpn_nozeros": [no_zeros(p) for p in pns], "boost": 8}},
            {"terms": {"alt_pns": keys, "boost": 6}},
            {"match": {"mpn_ngram": {"query": " ".join(keys), "boost": 2}}}
        ]
    gtin = gtin14(clean(row.upc))
    if gtin:
        should.append({"term": {"gtins": {"value": gtin, "boost": 8}}})
    if row.manufacturer_name:
        should.append({"multi_match": {"query": clean(row.manufacturer_name), "fields": ["mfr_name", "brand"], "boost": 3}})
    if row.item_description:
        should.append({"match": {"description": {"query": clean(row.item_description), "boost": 1}}})
    query = {"bool": {"should": should}} if should else {"match_none": {}}
    return {"size": size, "query": query}

def retrieve(rows: list[ErpRow], size: int = 20):
    """Candidates per row, using one _msearch request per batch of rows."""
    results = []
    for start in range(0, len(rows), BATCH):
        searches = []
        for row in rows[start:start + BATCH]:
            searches += [{"index": settings.es_index_alias}, build_query(row, size)]
        for response in es.msearch(searches=searches)["responses"]:
            hits = response.get("hits", {}).get("hits", [])
            results.append([{"es_score": h["_score"], **h["_source"]} for h in hits])
    return results


# ----------------------------------------------------------------------------- scoring
BIAS = -4.5
ID_WEIGHT = {"exact": 6.0, "compact": 5.5, "gtin": 5.0, "zeros": 4.5, "alt": 4.0, "prefix": 3.0}
TEXT_ONLY = -3.5
W_KEY_LEN = 0.25
W_DISAGREE = -1.5
W_MFR = 2.0
W_PREFIX_IS_BRAND = 2.0
W_DESC, DESC_CENTER = 5.0, 0.25
W_ALIAS = 2.0
W_MFR_CONFLICT = -2.5
STOPWORDS = {"OF", "THE", "AND", "W", "WITH", "FOR", "ON", "IN", "X", "A", "TO", "BY", "PER", "EA", "OR"}


def segment_key(pn: str) -> str:
    """Leading zeros dropped per segment: '214-01453-02I' -> '21414532I'"""
    return "".join(s.lstrip("0") or "0" for s in re.split(r"[^A-Z0-9]+", clean(pn)) if s)

def brand_prefix(prefix: str, cand: dict) -> bool:
    """Distributor line codes are brand abbreviations: 'RCA', 'TOP' for TOPAZ."""
    names = f"{cand['brand']} {cand['mfr_name']}".split()
    return len(prefix) >= 2 and prefix.isalpha() and any(n.startswith(prefix) for n in names)

def stripped_prefix(pn: str, cand: dict) -> str | None:
    """'RCAVH625R' vs 'VH625R' -> 'RCA'; None if the ERP key isn't a short prefix + the catalog key."""
    for erp in {compact(pn), segment_key(pn)}:
        for cat in {compact(cand["mpn"]), segment_key(cand["mpn"])}:
            prefix = erp[: len(erp) - len(cat)]
            if cat and erp.endswith(cat) and 1 <= len(prefix) <= 4 and (len(cat) >= 4 or brand_prefix(prefix, cand)):
                return prefix
    return None
def id_link(pns: list[str], gtin: str | None, cand: dict) -> str | None:
    """Strongest identifier link between the row and a candidate, or None."""
    kinds = []
    for pn in pns:
        if clean(pn) == cand["mpn"]:
            kinds.append("exact")
        elif compact(pn) == compact(cand["mpn"]):
            kinds.append("compact")
        elif no_zeros(pn) and no_zeros(pn) == cand["mpn_nozeros"]:
            kinds.append("zeros")
        elif compact(pn) in {compact(a) for a in cand["alt_pns"]}:
            kinds.append("alt")
        elif stripped_prefix(pn, cand):
            kinds.append("prefix")
    if gtin and gtin in cand["gtins"]:
        kinds.append("gtin")
    return max(kinds, key=ID_WEIGHT.get) if kinds else None

def mpn_field(row: ErpRow) -> list[str]:
    """Part numbers from the MPN field only (placeholders skipped)."""
    mpn = clean(row.manufacturer_part_number)
    return split_joined(mpn) if mpn and not is_placeholder(mpn) else []

def desc_words(text: str) -> set[str]:
    """Meaningful description words: no numbers, codes, stopwords or 1-letter words."""
    return {w for w in re.split(r"[^A-Z0-9]+", clean(text))
            if len(w) >= 2 and w not in STOPWORDS and not any(c.isdigit() for c in w)}

def word_overlap(erp: set[str], cand: set[str]) -> float:
    """Average best match if each ERP word against the candidate's words."""
    if not erp:
        return 0.0
    return sum(max((token_sim(w, c) for c in cand), default = 0.0) for w in erp) / len(erp)

def token_sim(erp: str, candidate: str) -> float:
    """Abbreviation-aware: FERT ~ FERTILIZER, CNNCTR ~ CONNECTOR."""
    if erp == candidate or erp + "S" == candidate or candidate + "S" == erp:
        return 1.0
    if len(erp) >= 3 and candidate.startswith(erp):
        return 0.9
    if len(erp) >= 3 and len(candidate) > len(erp) and erp[0] == candidate[0] and is_subsequence(erp, candidate):
        return 0.75
    return 0.0

def is_subsequence(short: str, long: str) -> bool:
    """True if the letters of short appear in long in order: 'QCK' in 'QUICK'."""
    it = iter(long)
    return all(c in it for c in short)

def score(row: ErpRow, cand: dict, aliases: AliasModel | None = None) -> tuple[float, str | None, list[str]]:
    """Log-odds that the candidate is the row's item, its identifier link, and the reasons."""
    gtin = gtin14(clean(row.upc))
    link = id_link(part_numbers(row), gtin, cand)
    s = BIAS + (ID_WEIGHT[link] if link else TEXT_ONLY)
    why = [f"part number: {link}" if link else "no identifier link"]

    if link and link != "gtin":
        spec = max(-1.0, min(1.0, W_KEY_LEN * (len(compact(cand["mpn"])) - 6)))
        s += spec
        why.append(f"{len(compact(cand['mpn']))}-char key {spec:+.2f}")
    if link == "prefix":
        prefix = next(p for pn in part_numbers(row) if (p := stripped_prefix(pn, cand)))
        if brand_prefix(prefix, cand):
            s += W_PREFIX_IS_BRAND
            why.append(f"prefix '{prefix}' is the brand {W_PREFIX_IS_BRAND:+.1f}")
    field = mpn_field(row)
    if link and field and not id_link(field, gtin, cand):
        s += W_DISAGREE
        why.append(f"MPN field disagrees {W_DISAGREE:+.1f}")

    by_name = 0.0
    if row.manufacturer_name:
        names = f"{cand['mfr_name']} {cand['brand']}"
        sim = max(fuzz.token_set_ratio(part, names) for part in clean(row.manufacturer_name).split("/")) / 100
        by_name = W_MFR * sim if sim >= 0.85 else 0.0
    shares = aliases.shares(row) if aliases else {}
    share = shares.get(cand["mfr_code"], 0.0)
    if by_name and by_name >= W_ALIAS * share:
        s += by_name
        why.append(f"manufacturer agrees {by_name:+.1f}")
    elif share:
        s += W_ALIAS * share
        why.append(f"learned alias -> {cand['mfr_name']} ({share:.0%}) {W_ALIAS * share:+.1f}")
    elif shares:
        penalty = W_MFR_CONFLICT * max(shares.values())
        s += penalty
        why.append(f"this ERP manufacturer maps to another manufacturer {penalty:+.1f}")

    d = word_overlap(desc_words(row.item_description), desc_words(cand["description"]))
    s += W_DESC * (d - DESC_CENTER)
    why.append(f"description {d:.2f} {W_DESC * (d - DESC_CENTER):+.1f}")
    return s, link, why

# ----------------------------------------------------------------------------- decision
NO_IDENTIFIER_CAP = 0.5   # nothing to look up: "not found" proves little

def decide(row: ErpRow, cands: list[dict], top_k: int, aliases: AliasModel | None = None) -> MatchResult:
    """Softmax over the candidates plus NO_MATCH (score 0)."""
    scored = sorted(((*score(row, c, aliases), c) for c in cands), key=lambda x: x[0], reverse=True)
    z = 1.0 + sum(math.exp(s) for s, *_ in scored)
    p_null = 1.0 / z
    linked = [x for x in scored if x[1]]
    top = [Candidate(item_id=c["item_id"], mpn=c["mpn"], manufacturer=c["mfr_name"], description=c["short_desc"],
                     score=round(math.exp(s) / z, 3), evidence=why) for s, _, why, c in scored[:top_k]]

    if linked and math.exp(linked[0][0]) / z > p_null:
        s, _, _, c = linked[0]
        conf = math.exp(s) / z
        band = "AUTO_ACCEPT" if conf >= settings.auto_accept else "REVIEW"
        return MatchResult(input_row_id=row.input_row_id, predicted_item_id=c["item_id"],
                           confidence=round(conf, 3), band=band, candidates=top)

    has_identifier = bool(part_numbers(row) or gtin14(clean(row.upc)))
    conf = p_null if has_identifier else min(p_null, NO_IDENTIFIER_CAP)
    band = "NO_MATCH" if conf >= settings.no_match_sure else "REVIEW"
    return MatchResult(input_row_id=row.input_row_id, predicted_item_id="NO_MATCH", confidence=round(conf, 3), band=band, candidates=top)

def match_rows(rows: list[ErpRow], top_k: int = 3) -> MatchResponse:
    """Match rows end to end: retrieve, score, learn aliases, re-score, semantic fallback, summary."""
    candidates = retrieve(rows)
    first_pass = [decide(row, cands, top_k) for row, cands in zip(rows, candidates)]
    aliases = learn_aliases(rows, candidates, first_pass)
    results = [decide(row, cands, top_k, aliases) for row, cands in zip(rows, candidates)]
    semantic_fallback(rows, results, top_k)
    bands = [r.band for r in results]
    summary = MatchSummary(rows=len(results), auto_accept=bands.count("AUTO_ACCEPT"),
                           review=bands.count("REVIEW"), no_match=bands.count("NO_MATCH"))
    return MatchResponse(summary=summary, results=results)


# ----------------------------------------------------------------------------- semantic fallback
def semantic_text(row: ErpRow) -> str:
    """The description without its leading part number: vectors compare meaning, not codes."""
    words = clean(row.item_description).split()
    if words and any(c.isdigit() for c in words[0]) and not is_size(words):
        words = words[1:]
    return " ".join(words)


def semantic_fallback(rows: list[ErpRow], results: list[MatchResult], top_k: int) -> None:
    """NO_MATCH rows: look for items with a similar meaning; if any are close enough, send them to review."""
    todo = [(row, res) for row, res in zip(rows, results) if res.predicted_item_id == "NO_MATCH" and semantic_text(row)]
    if not todo:
        return
    vectors = embed([semantic_text(row) for row, _ in todo])
    searches = []
    for vector in vectors:
        searches += [{"index": settings.es_index_alias},
                     {"size": top_k, "_source": {"excludes": ["description_vector"]},
                      "knn": {"field": "description_vector", "query_vector": vector, "k": top_k, "num_candidates": 100}}]
    for (row, res), response in zip(todo, es.msearch(searches=searches)["responses"]):
        hits = response.get("hits", {}).get("hits", [])
        similar = [(2 * h["_score"] - 1, h["_source"]) for h in hits]  # ES cosine score is (1 + cos) / 2
        if not similar or similar[0][0] < settings.semantic_min:
            continue  # nothing close: stays NO_MATCH
        res.band = "REVIEW"
        res.candidates = [Candidate(item_id=c["item_id"], mpn=c["mpn"], manufacturer=c["mfr_name"],
                                    description=c["short_desc"], score=round(sim, 3), source="semantic",
                                    evidence=[f"similar meaning {sim:.2f}", "no part number match"])
                          for sim, c in similar]
