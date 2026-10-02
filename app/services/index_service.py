from datetime import datetime

import pandas as pd
from elasticsearch import helpers
from app.config import settings
from app.core.es_client import es
from app.core.es_mapping import create_index
from app.core.normalize import clean, compact, gtin14, no_zeros
from app.core.embeddings import embed

TEXT_COLUMNS = ["SHORT_DESC", "LONG_DESC", "INVOICE_DESC", "ITEM_NAME", "RETAIL_DESC", "CATEGORY_NAME"]

def read_catalog(path: str) -> pd.DataFrame:
    """Read the catalog .xlsx/.csv as strings, with empty cells as ''."""
    df = pd.read_csv(path, dtype=str) if path.lower().endswith(".csv") else pd.read_excel(path, dtype = str)
    return df.fillna("")

def to_document(row: dict) -> dict:
    """One catalog row -> one Elasticsearch document with normalized part-number keys."""
    mpn = clean(row["MANUFACTURER_PART_NUMBER"])
    alts = [a for col in ('ALTERNATE_PART_NUMBER_1', 'ALTERNATE_PART_NUMBER_2')
            for a in clean(row.get(col)).split("|") if len(compact(a)) >= 3]
    gtins = {g for col in ('UPC', 'EAN_UCC_13') if (g := gtin14(clean(row.get(col))))}
    return {
        "item_id": row["PART_NUMBER"].strip(),
        "mfr_code": row["MANUFACTURER_CODE"],
        "mfr_name": clean(row["MANUFACTURER_NAME"]),
        "brand": " ".join(clean(row.get(c)) for c in ("BRAND_NAME", "SUB_BRAND")).strip(),
        "mpn": mpn,
        "mpn_compact": mpn,
        "mpn_nozeros": no_zeros(mpn),
        "mpn_ngram": compact(mpn),
        "alt_pns": alts,
        "gtins": sorted(gtins),
        "short_desc": clean(row["SHORT_DESC"]),
        "description": " ".join(clean(row.get(c)) for c in TEXT_COLUMNS)
    }

def swap_alias(new_index: str) -> None:
    """Point the alias at new_index, detaching old indexes in the same atomic call."""
    alias = settings.es_index_alias
    actions = [{"add": {"index": new_index, "alias": alias}}]
    if es.indices.exists_alias(name=alias):
        for old in es.indices.get_alias(name = alias):
            actions.insert(0, {"remove": {"index": old, "alias": alias}})
    es.indices.update_aliases(actions=actions)

def build_index(path: str) -> dict:
    """Index a catalog file into a new timestamped index, then switch the alias to it."""
    df = read_catalog(path)
    index = f"{settings.es_index_alias}_{datetime.now().strftime('%Y%m%d%H%M%S')}"
    create_index(index)
    docs = [to_document(r) for r in df.to_dict("records")]
    for doc, vector in zip(docs, embed([f"{d['brand']} {d['short_desc']}" for d in docs])):
        doc["description_vector"] = vector
    actions = ({"_index": index, "_id": doc["item_id"], "_source": doc} for doc in docs)

    indexed, errors = helpers.bulk(es, actions, raise_on_error=True)
    es.indices.refresh(index=index)
    swap_alias(index)
    deleted = delete_old_indexes()
    return {"index": index, "rows_read": len(df), "indexed": indexed, "errors": errors[:10], "deleted": deleted}

def delete_old_indexes() -> None:
    """Keep the newest `keep_indexes` indexes; never delete the one the alias points to."""
    alias = settings.es_index_alias
    live = set(es.indices.get_alias(name=alias))
    names = sorted(es.indices.get(index=f"{alias}_*"), reverse=True)
    old = [n for n in names[settings.keep_indexes:] if n not in live]
    for name in old:
        es.indices.delete(index=name)
    return old