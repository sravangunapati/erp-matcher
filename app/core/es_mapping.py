from app.core.es_client import es
from app.config import settings

SETTINGS = {
    "number_of_replicas": 0,
    "analysis": {
        "char_filter": {
            "strip_non_alnum": { "type": "pattern_replace", "pattern": "[^A-Za-z0-9]", "replacement": ""}
        },
       "normalizer": {
            "pn_compact": {"type": "custom", "char_filter": ["strip_non_alnum"], "filter": ["uppercase"]}
        },
        "tokenizer": {
            "pn_trigram": {"type": "ngram", "min_gram": 3, "max_gram": 3}
        },
        "analyzer": {
            "pn_ngram": {"type": "custom", "tokenizer": "pn_trigram", "filter": ["uppercase"]}
        },
    }
}

MAPPINGS = {
    "properties": {
        "item_id":     {"type": "keyword"},
        "mfr_code":    {"type": "keyword"},
        "mfr_name":    {"type": "text"},
        "brand":       {"type": "text"},
        "mpn":         {"type": "keyword"},
        "mpn_compact": {"type": "keyword", "normalizer": "pn_compact"},
        "mpn_nozeros": {"type": "keyword"},
        "mpn_ngram":   {"type": "text", "analyzer": "pn_ngram"},
        "alt_pns":     {"type": "keyword", "normalizer": "pn_compact"},
        "gtins":       {"type": "keyword"},
        "short_desc":  {"type": "keyword", "index": False},
        "description": {"type": "text"},
        "description_vector": {"type": "dense_vector", "dims": settings.embedding_dims, "similarity": "cosine"},

    }
}

def create_index(name: str) -> None:
    """Create an empty index with the analyzers and field mappings above."""
    es.indices.create(index=name, settings=SETTINGS, mappings=MAPPINGS)