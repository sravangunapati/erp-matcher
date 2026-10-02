from elasticsearch import Elasticsearch

from app.config import settings

es = Elasticsearch(settings.es_url)
