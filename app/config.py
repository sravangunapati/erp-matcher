from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """App settings; each one can be overridden by an environment variable of the same name."""
    es_url: str = "http://localhost:9200"
    es_index_alias: str = "unilog_items"
    auto_accept: float = 0.90
    no_match_sure: float = 0.80
    keep_indexes: int = 2
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_dims: int = 384
    embedding_cache: str = "~/.cache/fastembed"
    semantic_min: float = 0.75



settings = Settings()
