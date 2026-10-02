from collections import defaultdict

from app.core.normalize import clean
from app.schemas.matching import ErpRow, MatchResult

ANCHOR_CONFIDENCE = 0.80   # pass-1 confidence for a row to teach an alias
MIN_ALIAS_SUPPORT = 1.5    # summed confidence from *other* rows before an alias is used


class AliasModel:
    """ERP manufacturer name -> catalog manufacturer, learned from confident rows of the same file."""

    def __init__(self):
        """Start with no votes."""
        self.votes: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        self.own: dict[str, tuple[str, float]] = {}

    def add(self, row: ErpRow, mfr_code: str, weight: float) -> None:
        """Record one row's vote: its ERP manufacturer name maps to mfr_code, weighted by confidence."""
        name = clean(row.manufacturer_name)
        if name:
            self.votes[name][mfr_code] += weight
            self.own[row.input_row_id] = (mfr_code, weight)

    def shares(self, row: ErpRow) -> dict[str, float]:
        """Share of each catalog manufacturer among the *other* rows with this ERP name."""
        votes = dict(self.votes.get(clean(row.manufacturer_name), {}))
        code, weight = self.own.get(row.input_row_id, ("", 0.0))
        if code:
            votes[code] -= weight  # leave this row out: it must not confirm itself
        support = sum(votes.values())
        if support < MIN_ALIAS_SUPPORT:
            return {}
        return {c: v / (support + 1) for c, v in votes.items() if v > 1e-9}


def learn_aliases(rows: list[ErpRow], candidates: list[list[dict]], results: list[MatchResult]) -> AliasModel:
    """Build the alias votes from the confident pass-1 matches."""
    aliases = AliasModel()
    for row, cands, result in zip(rows, candidates, results):
        if result.predicted_item_id != "NO_MATCH" and result.confidence >= ANCHOR_CONFIDENCE:
            mfr_code = next(c["mfr_code"] for c in cands if c["item_id"] == result.predicted_item_id)
            aliases.add(row, mfr_code, result.confidence)
    return aliases
