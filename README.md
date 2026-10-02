# ERP Matcher

Links distributor ERP items to items in the Unilog master catalog. For every ERP row the service returns a
Unilog item ID or `NO_MATCH`, a confidence between 0 and 1, a workflow band
(`AUTO_ACCEPT` / `REVIEW` / `NO_MATCH`) and the evidence behind the decision.

Built with **FastAPI** and **Elasticsearch 8.15**, plus a small local embedding model (`bge-small-en-v1.5`)
for semantic suggestions.

---

## How it works

1. **Clean**: part numbers are taken from the MPN field and from the start of the description. Placeholders
   (`TBD`, `000000000000055`) and leading sizes (`16OZ`, `13 PC`) are ignored, and joined numbers
   (`636/L-131`) are split.
2. **Retrieve**: Elasticsearch looks up each part number in several normalized forms (separators removed,
   leading zeros removed, alternate PNs, valid UPC/GTIN), plus the manufacturer and description. The top 20
   candidates per row go on to scoring.
3. **Score**: each candidate gains or loses log-odds points for how its part number matched, how distinctive
   the key is, contradicting fields, manufacturer agreement and description similarity.
4. **Learn aliases**: confident rows teach the mapping from ERP manufacturer to catalog manufacturer (e.g.
   SOUTHWIRE → Halco/TOPAZ), and all rows are re-scored with it.
5. **Decide**: a softmax over the candidates plus an explicit `NO_MATCH` option. Only candidates linked by an
   identifier can be auto-accepted. If the answer is `NO_MATCH`, a semantic (vector) search suggests lookalikes
   for a reviewer.

| Band | Rule |
|---|---|
| `AUTO_ACCEPT` | Best identifier-linked candidate with confidence ≥ 0.90 |
| `NO_MATCH` | Confident that nothing in the catalog fits (≥ 0.80) |
| `REVIEW` | Everything else: a person decides, using the top candidates and their evidence |

**On the 50-row sample file:** 50/50 correct, 42 auto-accepted, 0 wrong answers outside `REVIEW`. The rules
were tuned on that same file, so treat these as training-set numbers.

---

## Project structure

```
erp-matcher/
├── app/
│   ├── main.py              # FastAPI app, registers the routers
│   ├── config.py            # settings (overridable with environment variables)
│   ├── api/                 # HTTP layer: health, indexing, matching
│   ├── schemas/             # request/response models (Pydantic)
│   ├── services/
│   │   ├── index_service.py # catalog file → Elasticsearch documents → new index + alias swap
│   │   ├── match_service.py # retrieve → score → decide → semantic fallback
│   │   └── aliases.py       # ERP → catalog manufacturer alias learning
│   ├── core/                # normalization, ES client and mapping, embeddings
│   └── jobs/store.py        # in-memory job status for indexing
├── tests/
└── requirements.txt
```

---

## Setup

Tested on macOS (Apple Silicon) with Python 3.12 and Elasticsearch 8.15.5. Docker is **not** required.

### 1. Prerequisites

- **Python 3.12**
  ```bash
  brew install python@3.12
  ```
- About 1.5 GB of free disk space (Elasticsearch, Python packages and the embedding model).

### 2. Install and start Elasticsearch 8.15 (no Docker)

Download and unpack it. Elasticsearch ships with its own Java, so nothing else is needed.

```bash
cd ~
# Apple Silicon Mac. Intel Mac: darwin-x86_64. Linux: linux-x86_64.
ES=elasticsearch-8.15.5-darwin-aarch64.tar.gz
curl -O https://artifacts.elastic.co/downloads/elasticsearch/$ES
curl -s https://artifacts.elastic.co/downloads/elasticsearch/$ES.sha512 | shasum -a 512 -c -
tar -xzf $ES && rm $ES
```

Configure it for local development: a single node, reachable only from this machine, with security off.

```bash
cat >> ~/elasticsearch-8.15.5/config/elasticsearch.yml <<'EOF'

# ---- local development settings (erp-matcher) ----
cluster.name: erp-matcher-dev
node.name: local-1
discovery.type: single-node
network.host: 127.0.0.1
http.port: 9200
xpack.security.enabled: false
xpack.security.enrollment.enabled: false
xpack.ml.enabled: false
EOF

printf -- "-Xms1g\n-Xmx1g\n" > ~/elasticsearch-8.15.5/config/jvm.options.d/heap.options
```

> Security is disabled for local use only. Turn it back on (`xpack.security.enabled: true`) before running
> anywhere shared.

Start, check and stop it:

```bash
~/elasticsearch-8.15.5/bin/elasticsearch -d -p ~/elasticsearch-8.15.5/es.pid   # start in the background
curl "localhost:9200/_cluster/health?pretty"                                    # expect "status" : "green"
kill $(cat ~/elasticsearch-8.15.5/es.pid)                                       # stop
```

Elasticsearch does not start automatically after a reboot; run the start command again.

### 3. Python environment

```bash
cd erp-matcher
/opt/homebrew/bin/python3.12 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
```

Check it can reach Elasticsearch:

```bash
.venv/bin/python -c "from app.core.es_client import es; print(es.ping())"   # expect True
```

### 4. Embedding model (downloaded automatically)

The first indexing run downloads `BAAI/bge-small-en-v1.5` (about 64 MB) into `~/.cache/fastembed`.

> **Behind a corporate HTTPS proxy** (Netskope, Zscaler and similar): the download can fail with
> `CERTIFICATE_VERIFY_FAILED`. The app already calls `truststore.inject_into_ssl()`, which makes Python trust
> the macOS keychain, where the proxy's certificate is installed. Nothing else is needed on a managed Mac.

### 5. Run the API

```bash
.venv/bin/uvicorn app.main:app --reload
```

- API: http://localhost:8000/api/v1
- Swagger UI: http://localhost:8000/docs
- Health: http://localhost:8000/api/v1/health → `{"status": "ok", "elasticsearch": true}`

---

## Usage

The data files are **not** in this repository. Use your own catalog export and ERP file.

### 1. Index the catalog

```bash
curl -s -X POST localhost:8000/api/v1/index/jobs -F "file=@/path/to/Unilog_Master_Catalog.xlsx"
# → {"job_id": "idx_3fae5f38", "status": "queued", ...}

curl -s localhost:8000/api/v1/index/jobs/idx_3fae5f38
# → "status": "completed", "result": {"rows_read": 428, "indexed": 428, ...}
```

Each run builds a new timestamped index, switches the `unilog_items` alias to it, and keeps the newest two
indexes. Re-run it whenever the catalog changes.

### 2. Match ERP rows

**JSON** (1 to 5,000 rows):

```bash
curl -s -X POST localhost:8000/api/v1/match -H 'Content-Type: application/json' -d '{
  "rows": [
    {"input_row_id": "IN-009", "manufacturer_name": "MARKWORT SPORTING GOODS",
     "manufacturer_part_number": "5221", "item_description": "521001 PENN TENNIS BALL CAN OF 3"}
  ],
  "options": {"top_k": 3, "include_evidence": true}
}'
```

**Excel or CSV file** (1 to 5,000 rows), returned as JSON or as an Excel download:

```bash
curl -s -X POST localhost:8000/api/v1/match/file -F "file=@/path/to/ERP_Items.xlsx"
curl -s -X POST localhost:8000/api/v1/match/file -F "file=@/path/to/ERP_Items.xlsx" \
     -F "output_format=xlsx" -o match_results.xlsx
```

Column headers are matched ignoring case, spaces and punctuation: `INPUT_ROW_ID`, `Manufacturer Name`,
`Manufacturer Part Number`, `ITEM DESCRIPTION`, `UPC`. Other columns are kept in the Excel output.

Send a distributor's rows **together** in one request: manufacturer aliases are learned within a request.

### Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/health` | Service up, Elasticsearch reachable |
| POST | `/api/v1/index/jobs` | Upload a catalog and index it in the background |
| GET | `/api/v1/index/jobs/{job_id}` | Indexing job status and result |
| POST | `/api/v1/match` | Match rows sent as JSON |
| POST | `/api/v1/match/file` | Match an uploaded `.xlsx` / `.csv` file |

---

## Configuration

Every setting in `app/config.py` can be overridden with an environment variable of the same name:

| Variable | Default | Effect |
|---|---|---|
| `ES_URL` | `http://localhost:9200` | Elasticsearch address |
| `ES_INDEX_ALIAS` | `unilog_items` | Alias that matching reads and indexing switches |
| `AUTO_ACCEPT` | `0.90` | Minimum match confidence for `AUTO_ACCEPT` |
| `NO_MATCH_SURE` | `0.80` | Minimum `NO_MATCH` confidence for the `NO_MATCH` band |
| `SEMANTIC_MIN` | `0.75` | Minimum cosine similarity for semantic suggestions |
| `KEEP_INDEXES` | `2` | Indexes kept after each re-index |
| `EMBEDDING_MODEL` | `BAAI/bge-small-en-v1.5` | Embedding model (changing it requires re-indexing) |

Example:

```bash
ES_URL=http://my-es:9200 AUTO_ACCEPT=0.95 .venv/bin/uvicorn app.main:app
```

---

## Known issues and limitations

- **Matching before indexing returns everything as `NO_MATCH`.** If the alias doesn't exist yet, every row
  comes back `NO_MATCH` with confidence 1.0 instead of an error. Index first, and check the job status.
- **Hand-set weights.** The scoring weights were tuned on a 50-row sample. Measure accuracy on a new labelled
  file before trusting the bands.
- **Aliases are per request.** Learned manufacturer aliases aren't stored between requests.
- **Indexing jobs live in memory.** Job status is lost when the server restarts.
- **No authentication.** Intended for local use.
- **No automated tests yet.** `tests/` is a placeholder.
