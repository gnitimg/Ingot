# Ingot_En

![Version](https://img.shields.io/badge/Version-1.2-blue) ![License](https://img.shields.io/badge/License-MIT-green) ![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688?logo=fastapi&logoColor=white) ![Vue](https://img.shields.io/badge/Vue-3-4FC08D?logo=vue.js&logoColor=white) ![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white) [![Switch](https://img.shields.io/badge/Switch-CN-red)](README.md)

> Local-first RAG + OCR + GraphRAG knowledge-base application — forging scattered documents into a traceable knowledge network.

Ingot is a full-stack knowledge-base application that runs entirely on your local machine. It provides multi-knowledge-base management, office document parsing, scanned-document OCR, vector retrieval, two-stage reranking, knowledge graph construction, community summarization, four retrieval modes, streaming Q&A with evidence tracing — all data stored in local SQLite. The admin frontend is built with Vue 3 + TypeScript + Vite, and the production build is served by FastAPI on the same localhost port.

## Core Capabilities

| Capability | Description |
|---|---|
| **Multi-KB Management** | Create and delete multiple independent knowledge bases, each with its own documents, vector index, and graph |
| **Full-Format Parsing** | PDF, DOCX, PPTX, XLSX, Markdown, TXT, HTML, JSON, CSV, and common image formats |
| **Smart OCR** | Per-page text density detection for PDFs; low-density pages auto-rendered for OCR; images auto-corrected via EXIF and normalized to PNG |
| **Vector Retrieval + Reranker** | Embedding + two-stage reranking; automatic fallback to vector ranking on Reranker failure |
| **GraphRAG** | LLM entity-relation extraction → same-name entity merging → community detection → community summarization → summary vectorization |
| **Four Retrieval Modes** | Vector, Graph Local, Graph Global, Hybrid |
| **Streaming Q&A** | SSE streaming answer generation with source, relation, and community evidence |
| **Interactive Graph Visualization** | Browser-based force-directed entity-relation graph with community summaries |
| **Multi-Format Export** | 11 data types (snapshot, summary, originals, documents, chunks, vectors, entities, relationships, graph, communities, checkpoints) in Markdown / JSON / CSV / HTML / GraphML / PNG; multi-select bundled as ZIP |
| **One-Click Config** | Interactive initialization wizard writing `.env`; hot-update supported for existing configs |
| **Provider Presets** | 30+ built-in AI provider presets (SiliconFlow, OpenAI, DeepSeek, Xiaomi MiMo, Kimi, Google Gemini, DashScope, etc.) with quick selection and search |

## Quick Start

Requires Python 3.11 or higher (verified on Python 3.13).

### 1. Create Virtual Environment & Install Dependencies

**Windows PowerShell:**

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

**macOS / Linux:**

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

If your local PyPI mirror is missing packages, use the official PyPI temporarily:

```bash
python -m pip install --index-url https://pypi.org/simple -r requirements.txt
```

Image OCR requires no extra local engine. For **scanned PDFs**, you also need PyMuPDF to render pages as images; some mirrors lack this package:

```bash
python -m pip install --index-url https://pypi.org/simple -r requirements-ocr-pdf.txt
```

Without PyMuPDF, Ingot still handles text-based PDFs, images, and other supported documents; only scanned PDF pages that need rendering will show a warning.

### 2. Build the Vue Frontend

Requires Node.js 20.19+, 22.12+, or newer:

```bash
cd frontend
npm install
npm run build
cd ..
```

Build output is written directly into `app/static/`. FastAPI serves these files on the same port in production. The repo includes a pre-built production bundle, so Node.js is not required on first run — only rebuild after modifying frontend source.

### 3. Start the Application

```bash
python run.py
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000), then configure the model API URLs and Keys for this device in any knowledge base's **Settings** tab. Every device must be configured separately.

If the port is occupied, change `APP_PORT` in `.env` to another available port (e.g., `8001`).

The **Settings** tab covers provider URLs, models, API Keys, timeout/concurrency parameters, Chunking, GraphRAG, and KB security. Model settings are encrypted into an `HttpOnly`, `SameSite=Strict` cookie on the current device; they are not written to server `.env`, SQLite, or logs. The server decrypts them only in memory while handling that device's request or background task. Existing API Keys are never echoed; leaving a Key field blank retains the current device's value. Clearing cookies, changing browser, or using another device requires configuration again.

GraphRAG concurrency can also be set in the browser (range 1–1000, recommend starting with 3–5), applying to both **entity-relation extraction** and **community summarization**. It takes effect from the next build; the current round still uses the startup value. The model uses persistent connection pools and rolling worker pools: each completed request immediately saves a checkpoint and picks up the next task. Timeout chunks go to the back of the queue without blocking healthy chunks. On 429, 5xx, network errors, or invalid responses, retries respect the provider's `Retry-After`, exponential backoff, and jitter; explicit provider throttling temporarily lowers concurrency, gradually restored after consecutive successes.

You can also run directly with Uvicorn:

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

## Usage Flow

```
Create KB → View home guide → Drop documents → Wait for indexing → (Optional) Build GraphRAG → Ask questions
```

1. **Create Knowledge Base** — Click "New Knowledge Base" in the sidebar, fill in name, description, and access password; entering the KB requires the password
2. **View KB Home** — Each KB has a dedicated home page showing name, description, graph status, document/chunk/entity/relation/community stats, and common guides
3. **Import Documents** — Drag-and-drop or click the upload area; batch upload supported; SHA256 deduplication with a keep/discard confirmation dialog
4. **Wait for Indexing** — Backend automatically executes: document parsing → OCR (if needed) → text chunking → vectorization → SQLite storage
5. **Build GraphRAG** (optional) — Click "Build GraphRAG" on the documents page; real-time progress for entity-relation extraction, optional entity matching, community summarization, and community vectorization; 90% success threshold auto-generates communities and enters "partial" ready state; failed chunks retained as resumable checkpoints; below threshold or timeout pauses the build; manual stop clears partial results
6. **Start Q&A** — Select a retrieval mode on the chat page, enter your question, view streaming answer with evidence; conversation history carries the last 20 messages (~24k chars); Markdown is safely rendered. Evidence count adjustable in the Chat/Graph settings card

The admin displays each document's extraction method (native text / OCR / mixed), OCR page count, parsing warnings, and index status.

The sidebar icon, `INGOT`, and `RAG STUDIO` form a complete brand home button. Clicking returns to the global welcome page showing service status, KB count, and document count.

The admin auto-syncs backend changes: ~2.5s refresh during graph build, ~8s refresh for stats and document status at idle; immediate sync on tab/window focus. Polling pauses when the page is hidden, so manual refresh is unnecessary.

### Graph Status Flow

```
empty → stale (after document change) → building ─────────→ ready (100%)
                                       │  ↑
                          Threshold met ↓  │ Continue
                                    partial (usable, resumable)
                                       │
                         Below threshold ↓
                                    paused (resumable)
                                       │
                            stop → stale (rebuild from scratch)

Any unrecoverable error at any stage → error (retry from scratch)
```

Each chunk's extraction writes a persistent checkpoint with attempt count and last error. Total task reaching `GRAPH_BUILD_TIMEOUT` enters `paused`, preserving completed checkpoints; "Continue Build" only processes unfinished or failed chunks. Each community summary is immediately persisted; community ID is stably generated from members and relations. Service restart converts running tasks to resumable state.

Single-chunk requests use bounded HTTP retries; on failure, the chunk goes to the back of the current work queue, entering up to `GRAPH_RETRY_ROUNDS` block-level retry rounds after all healthy chunks get a chance. Reaching `GRAPH_SUCCESS_THRESHOLD` (default 90%) auto-generates communities from successful chunks and marks as `partial` — graph retrieval and hybrid Q&A become immediately usable. Below threshold enters `paused`.

Entity-relation extraction and community summarization prefer strict JSON Schema; truncation triggers smaller-batch re-extraction; plain JSON errors attempt structural repair first. Provider without structured output support degrades automatically. `GRAPH_LLM_ENTITY_MATCHING` is off by default; when enabled, only high-similarity, type-compatible entity pairs are sent to the chat model for merging confirmation.

Adding or deleting documents safely cancels the current task, clears old graph and checkpoints, and marks as "stale"; vector retrieval remains available.

## Retrieval Modes

| Mode | How It Works | Best For | Requires GraphRAG |
|---|---|---|---|
| **Vector** | Query vectorization → cosine similarity recall → Reranker reranking | Precise facts, source locating | No |
| **Graph Local** | Vector recall seeds → one-hop expansion along entity relations | Connections between people, products, technologies | Yes |
| **Graph Global** | Search over community summary vectors | Cross-document summaries, main themes, overall trends | Yes |
| **Hybrid** | Vector evidence + local graph relations merged | Default mode, balances text detail with relational context | Partial (auto-degrades to vector if graph not ready) |

> **Tip:** GraphRAG builds produce additional Chat API calls. For large KBs, set `GRAPH_MAX_CHUNKS` to control cost before building the full graph.

## Architecture

### Data Flow

```
Documents / Images
       │
       ▼
┌─────────────────────┐
│ Document Parsing     │  PDF per-page density check → native text / OCR render
│ + OCR (ocr.py)      │  Images → EXIF correction → PNG normalization → OCR model
└─────────┬───────────┘
          │ TextSection[]
          ▼
┌─────────────────────┐
│ Text Chunking        │  Sentence-boundary-aware + sliding window overlap
└─────────┬───────────┘
          │ TextChunk[]
          ▼
┌─────────────────────┐
│ Vectorization        │  Embedding API → L2 normalization
└─────────┬───────────┘
          │ float32[]
          ▼
┌─────────────────────┐
│ SQLite Storage       │  chunks table: text + vector BLOB + metadata
└─────────┬───────────┘
          │
    ┌─────┴─────┐
    ▼           ▼
 Vector      GraphRAG Build
 Retrieval       │
    │       ┌────┴────┐
    │       ▼         ▼
    │     LLM        Community
    │     Extract    Detection
    │     Entities   (Label Prop.)
    │     + Relations    │
    │       │         ▼
    │       ▼       Community
    │     Upsert    Summary
    │     entities  + Vectorize
    │     relationships   │
    │       │         │
    │       ▼         │
    │     entity_chunks│
    │       │         │
    ▼       ▼         ▼
┌──────────────────────────┐
│   Retrieval Service      │  Vector / Local Graph / Global Graph / Hybrid
└──────────┬───────────────┘
           │ Results
           ▼
┌──────────────────────────┐
│   Q&A Generation (SSE)   │  System prompt + evidence context + chat history
└──────────────────────────┘
```

### Core Pipeline

**Document Ingestion:**
1. File saved to `data/uploads/<kb_id>/` with unique storage name
2. Parser selected by file type, extracting `TextSection[]` and `OCRTarget[]`
3. `OCRService` processes OCR target pages concurrently; failures preserve native text as fallback
4. `chunk_sections()` splits by sentence boundary with overlap
5. `AIClient.embed_batched()` calls Embedding API in batches; vectors L2-normalized
6. Vectors and metadata written to SQLite chunks table

**GraphRAG Build:**
1. Read all indexed chunks from chunks table (optional `GRAPH_MAX_CHUNKS` limit)
2. Rolling worker pool calls Chat model at `GRAPH_CONCURRENCY` for entity-relation extraction; slow/failed chunks move to queue back; results write to graph data, attempt counts, and chunk checkpoints immediately
3. Same-name entities auto-merged (case-insensitive normalization); relations take max weight; optional chat-model arbitration for high-similarity alias candidates
4. Label propagation discovers communities (12 iterations)
5. Chat model generates community topic summaries at the same `GRAPH_CONCURRENCY`
6. Summarized vectors stored in communities table; failed chunks auto-retry with lower concurrency; timeout pauses and allows checkpoint resumption

### Streaming Q&A

Uses Server-Sent Events (SSE):

```
event: meta      ← Retrieval metadata (sources, relations, communities, Reranker status, warnings)
event: delta     ← Token-by-token generated answer text
event: done      ← Generation complete
event: error     ← Error message
```

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | FastAPI + Uvicorn |
| AI Client | httpx (async) + exponential backoff retries |
| Storage | SQLite (WAL mode) + NumPy cosine search |
| Document Parsing | pypdf, python-docx, python-pptx, openpyxl, BeautifulSoup4; PyMuPDF (scanned PDF optional) |
| Image Processing | Pillow (EXIF correction + PNG normalization) |
| Config Management | pydantic-settings (`.env` loading) + cryptography (Fernet encrypted cookies) |
| Frontend | Vue 3 (Composition API + `<script setup>`) |
| Build | Vite 7 + TypeScript 5.9 + vue-tsc |
| Graph Visualization | Canvas 2D + d3-force (collision avoidance, neighborhood highlighting, directional arrows, auto-fit) |
| Export | Markdown / JSON / CSV / HTML / GraphML / PNG; ZIP bundling |
| Testing | pytest + pytest-asyncio |

## Database Schema

7 tables with foreign-key cascading deletes:

```sql
knowledge_bases    ──┐
  id, name, description, graph_status, graph_error, created_at, updated_at
                     │
documents           ──┤── ON DELETE CASCADE
  id, kb_id, filename, stored_path, file_type, size_bytes, chunk_count,
  status, extraction_method, page_count, ocr_page_count, warning, error
                     │
chunks              ──┤── ON DELETE CASCADE
  id, document_id, kb_id, content, chunk_index, page_number,
  token_count, metadata_json, embedding (BLOB), embedding_dim
                     │
entities            ──┤── ON DELETE CASCADE
  id, kb_id, name, normalized_name, entity_type, description
  UNIQUE(kb_id, normalized_name)
                     │
entity_chunks       ──┤── Many-to-many
  entity_id, chunk_id, mention_count
                     │
relationships       ──┤── ON DELETE CASCADE
  id, kb_id, source_id, target_id, predicate, description,
  weight, evidence_chunk_id
  UNIQUE(kb_id, source_id, target_id, predicate)
                     │
communities         ──┘── ON DELETE CASCADE
  id, kb_id, title, summary, member_count,
  embedding (BLOB), embedding_dim
```

### Key Design

- **Vector Storage**: embeddings stored as `float32` BLOBs, loaded into NumPy matrices for exact cosine search at query time
- **Entity Merging**: `normalized_name` uses case-insensitive + whitespace normalization; upsert via `ON CONFLICT`
- **Graph State Machine**: `empty → stale → building → ready / error`; service restart converts interrupted `building` to `error`
- **Migration Compatibility**: `initialize()` auto-detects and adds missing columns — no manual migration needed

## Project Structure

```text
app/
├── main.py                 # FastAPI routes, static app hosting, and streaming SSE
├── config.py               # pydantic-settings config (.env loading)
├── database.py             # SQLite schema, migrations, and data access
├── schemas.py              # Pydantic request models
├── services/
│   ├── ai_client.py        # Embedding / Chat / OCR / Rerank API client
│   ├── parsers.py          # Multi-format document parsing (PDF per-page detection, image OCR prep)
│   ├── ocr.py              # Concurrent OCR service with native text fallback
│   ├── chunker.py          # Sentence-boundary-aware text chunking
│   ├── ingestion.py        # Upload → parse → chunk → vectorize pipeline
│   ├── graph_rag.py        # Entity-relation extraction, community detection, summary construction
│   ├── retrieval.py        # Four retrieval modes + Reranker + evidence assembly
│   └── exporter.py         # Multi-format data export (snapshot/summary/graph/communities/etc.)
└── static/                 # Vite production build output

frontend/
├── src/
│   ├── App.vue             # Vue 3 main workspace (KB / documents / Q&A / graph / export / settings)
│   ├── components/
│   │   ├── GraphCanvas.vue # Canvas 2D force-directed graph visualization
│   │   └── ProviderCombobox.vue  # Provider search & selection component
│   ├── providers.ts        # 30+ AI provider preset data
│   ├── api.ts              # HTTP client wrapper
│   ├── types.ts            # TypeScript type definitions
│   └── main.ts             # Vue app entry
├── index.html              # HTML template
├── vite.config.ts          # Build to app/static, dev proxy to :8000
├── tsconfig.json           # TypeScript config
└── package.json            # Frontend dependencies

tests/
├── test_database.py        # SQLite cascade deletes, graph data queries, column migrations
├── test_database_graph_progress.py  # Graph progress, heartbeat fields, interrupted task pause/resume migration
├── test_api.py             # FastAPI lifecycle, KB CRUD, unlock/password change, graph start/stop, config hot-update
├── test_chunker.py         # Text chunking size limits, metadata passthrough
├── test_graph_rag.py       # Markdown-fenced JSON parsing, entity normalization, community detection, timeout checkpoint recovery
├── test_ocr.py             # Image OCR preprocessing (EXIF + PNG), metadata preservation, unconfigured fallback
├── test_ai_client.py       # Reranker response parsing, invalid index filtering
├── test_retrieval.py       # Retrieval mode execution, Reranker integration, evidence assembly and mode fallback
├── test_exporter.py        # Multi-format export (Markdown/JSON/CSV/HTML/GraphML/PNG), ZIP bundling, format validation
└── test_init.py            # .env atomic update, config checks, legacy DB migration, port pre-check

init.py                     # Interactive configuration wizard
run.py                      # First-run config check and startup entry
requirements.txt            # Python dependencies
requirements-ocr-pdf.txt    # Optional scanned-PDF rendering dependency
.env.example                # Config template (committed to Git)
```

### Data Directory

```text
data/
├── ingot.db                # SQLite database
└── uploads/
    └── <knowledge-base-id>/
        └── <uuid>_<filename>   # Original file storage
```

Both `data/` and `.venv/` are git-ignored.

If a legacy data directory only contains `knowledge_forge.db`, Ingot auto-migrates it to `ingot.db` on startup; if both files exist, the newer database is not overwritten.

## Environment Variables

### `.env` vs `.env.example`

`.env` is only for server settings such as listening address, port, data directory, and the device-cookie encryption secret. Model API URLs and Keys must not be stored there. Legacy `*_API_KEY` / `*_BASE_URL` entries are ignored as device credentials by web requests. `.env` remains git-ignored.

Never put real keys into `.env`, `.env.example`, README, source code, screenshots, or commit history. Before committing, run:

```bash
git status --short
git check-ignore .env
```

The second command should output `.env`. If a key has entered Git history, deleting the file is not enough — revoke and regenerate the key immediately.

### Full Configuration Reference

Non-secret model settings below can seed the UI defaults for a new device. `*_BASE_URL` and `*_API_KEY` are deprecated and ignored by the web runtime; set their actual values in each device's browser.

**Embedding Service:**

| Variable | Default | Description |
|---|---|---|
| `EMBEDDING_BASE_URL` | — | Deprecated; configure in the current device's browser |
| `EMBEDDING_API_KEY` | — | Deprecated; configure in the current device's browser |
| `EMBEDDING_MODEL` | `BAAI/bge-m3` | Vectorization model |
| `EMBEDDING_BATCH_SIZE` | `16` | Texts per request |
| `EMBEDDING_TIMEOUT` | `90` | Request timeout (seconds) |

**Chat / Graph Model:**

| Variable | Default | Description |
|---|---|---|
| `CHAT_BASE_URL` | — | Deprecated; configure per device or reuse Embedding |
| `CHAT_API_KEY` | — | Deprecated; configure per device or reuse Embedding |
| `CHAT_MODEL` | `Qwen/Qwen3-8B` | Q&A, entity-relation extraction, and community summarization model |
| `CHAT_TIMEOUT` | `180` | Request timeout (seconds) |
| `CHAT_TEMPERATURE` | `0.2` | Generation temperature |
| `CHAT_MAX_TOKENS` | `2048` | Max output tokens |

**OCR:**

| Variable | Default | Description |
|---|---|---|
| `OCR_ENABLED` | `true` | Auto-OCR for images and scanned PDF pages |
| `OCR_BASE_URL` | — | Deprecated; configure per device or reuse Embedding |
| `OCR_API_KEY` | — | Deprecated; configure per device or reuse Embedding |
| `OCR_MODEL` | `PaddlePaddle/PaddleOCR-VL-1.5` | Vision/OCR model |
| `OCR_TIMEOUT` | `240` | Per-page OCR timeout (seconds) |
| `OCR_CONCURRENCY` | `2` | Concurrent OCR pages per document |
| `OCR_MIN_TEXT_CHARS` | `80` | PDF page below this effective char count triggers OCR |
| `OCR_MAX_PAGES` | `100` | Max OCR pages per document |
| `OCR_RENDER_DPI` | `144` | Scanned PDF render DPI |

**Reranker:**

| Variable | Default | Description |
|---|---|---|
| `RERANK_ENABLED` | `true` | Enable two-stage reranking |
| `RERANK_BASE_URL` | — | Deprecated; configure per device or reuse Embedding |
| `RERANK_API_KEY` | — | Deprecated; configure per device or reuse Embedding |
| `RERANK_MODEL` | `BAAI/bge-reranker-v2-m3` | Reranker model |
| `RERANK_CANDIDATES` | `18` | Candidates sent to reranker after vector recall |
| `RERANK_TIMEOUT` | `60` | Reranker request timeout (seconds) |

**Retrieval & Chunking:**

| Variable | Default | Description |
|---|---|---|
| `CHUNK_SIZE` | `900` | Target characters per chunk |
| `CHUNK_OVERLAP` | `160` | Overlap between adjacent chunks |
| `DEFAULT_TOP_K` | `6` | Default number of text evidence results |
| `QA_EVIDENCE_COUNT` | `6` | Evidence slices shown in Q&A and sent to the model; adjustable in browser settings |

**GraphRAG:**

| Variable | Default | Description |
|---|---|---|
| `GRAPH_CONCURRENCY` | `3` | Model request concurrency for extraction and summarization (1–1000; start with 3–5) |
| `GRAPH_MAX_CHUNKS` | `0` | Max chunks participating in graph build; `0` = unlimited |
| `GRAPH_CHUNK_TIMEOUT` | `240` | Per-chunk extraction timeout (seconds, including JSON degradation retries) |
| `GRAPH_BUILD_TIMEOUT` | `3600` | Per-round build timeout (seconds); pauses instead of discarding progress |
| `GRAPH_RETRY_ROUNDS` | `2` | Block-level retry rounds after HTTP retry exhaustion (0–5); each round auto-lowers concurrency |
| `GRAPH_RETRY_BACKOFF` | `2` | Retry backoff base seconds (0.1–60); actual wait includes exponential growth and jitter |
| `GRAPH_SUCCESS_THRESHOLD` | `90` | Auto-generate communities and enter partial-ready at this success percentage (1–100) |
| `GRAPH_LLM_ENTITY_MATCHING` | `false` | Let chat model arbitrate high-similarity entity candidates; off by default |

**Application & Storage:**

| Variable | Default | Description |
|---|---|---|
| `APP_HOST` | `127.0.0.1` | Localhost only by default; do not expose to public without authentication |
| `APP_PORT` | `8000` | Unified port for browser admin and API |
| `DATA_DIR` | `./data` | Local directory for SQLite and uploaded files |
| `MAX_UPLOAD_MB` | `50` | Per-file upload size limit |
| `DEVICE_COOKIE_SECRET` | empty | Server encryption seed for device-setting cookies; production must use a strong random value, and rotation requires every device to reconfigure |

Browser settings take effect immediately for the current device. Manual server `.env` changes require a restart; do not store model API URLs or Keys there.

## API Reference

Full OpenAPI docs available at [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs) after startup.

Password-protected knowledge bases require calling the unlock endpoint to obtain an `access_token`, then include `X-Ingot-KB-Token: <access_token>` in the request header for that KB's detail, documents, search, Q&A, and graph endpoints. Tokens are stored in server process memory and expire on restart; passwordless legacy KBs do not require this header.

### Knowledge Base Management

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/knowledge-bases` | List all KBs (with document, chunk, entity, relation, community counts) |
| `POST` | `/api/knowledge-bases` | Create KB (`{name, description, password}`), returns temp access token |
| `POST` | `/api/knowledge-bases/{id}/unlock` | Unlock with KB password, returns temp access token |
| `PUT` | `/api/knowledge-bases/{id}/password` | Change KB password (`{old_password, new_password}`), requires current access token |
| `GET` | `/api/knowledge-bases/{id}` | Get KB details |
| `DELETE` | `/api/knowledge-bases/{id}` | Delete KB with all documents, graph, and local files |

### Document Management

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/knowledge-bases/{id}/documents` | List documents (with SHA256, extraction method, OCR page count, status) |
| `POST` | `/api/knowledge-bases/{id}/documents` | Batch upload documents (multipart/form-data); server saves SHA256 |
| `DELETE` | `/api/knowledge-bases/{id}/documents/{doc_id}` | Delete document and all its chunks |

### Search & Q&A

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/knowledge-bases/{id}/search` | Execute retrieval, return sources, relations, and community evidence (no answer generation) |
| `POST` | `/api/knowledge-bases/{id}/chat` | Retrieve and stream answer via SSE (supports conversation history) |

### Graph

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/knowledge-bases/{id}/graph/rebuild` | Build from scratch when no checkpoint; auto-resumes on persisted checkpoint (202) |
| `POST` | `/api/knowledge-bases/{id}/graph/resume` | Continue paused build from checkpoint (202) |
| `POST` | `/api/knowledge-bases/{id}/graph/cancel` | Stop build and clear partial graph and checkpoints (202) |
| `GET` | `/api/knowledge-bases/{id}/graph` | Get entities, relations, and communities (`?limit=500`) |

### Data Export

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/knowledge-bases/{id}/exports/options` | List exportable data types and available formats for current KB |
| `POST` | `/api/knowledge-bases/{id}/exports` | Export selected data; returns file download (supports `bundle: true` for ZIP) |

### System

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/health` | Service status and model configuration |
| `GET` | `/api/settings` | Runtime configuration (API Keys not exposed) |
| `PUT` | `/api/settings` | Encrypt current-device model/retrieval settings into an HttpOnly cookie; API Keys are not echoed or persisted server-side |

## Testing

```bash
# Backend tests
python -m pytest -q

# Frontend type check and build
cd frontend && npm run typecheck && npm run build
```

Tests do not call real model APIs and cover:

| Test File | Coverage |
|---|---|
| `test_database.py` | SQLite cascade deletes, graph data queries, column migration, KB password hashing and document SHA256 backfill |
| `test_database_graph_progress.py` | Graph progress, heartbeat fields, interrupted task pause/resume migration |
| `test_api.py` | FastAPI lifecycle, KB CRUD, unlock/password change, graph start/stop, device-cookie isolation/encryption, and API Key non-echo |
| `test_chunker.py` | Text chunking size limits, metadata passthrough, overlap validation |
| `test_graph_rag.py` | Markdown-fenced JSON parsing, entity normalization, community detection, progress completion, per-chunk and total timeout checkpoint recovery |
| `test_ocr.py` | Image OCR preprocessing (EXIF + PNG), metadata preservation, unconfigured fallback |
| `test_ai_client.py` | Reranker response parsing, invalid index filtering |
| `test_retrieval.py` | Retrieval mode execution, Reranker integration, evidence assembly and mode fallback |
| `test_exporter.py` | Multi-format export (Markdown/JSON/CSV/HTML/GraphML/PNG), ZIP bundling, format validation |
| `test_init.py` | `.env` atomic update, config checks, legacy DB migration, port pre-check |

## Cost Control

OCR and GraphRAG produce additional model calls. These parameters control throughput and cost:

- **`OCR_MAX_PAGES`** — Limit max OCR pages per document (default 100)
- **`OCR_CONCURRENCY`** — Control OCR concurrency (default 2)
- **`QA_EVIDENCE_COUNT`** — Control evidence slices sent to model and shown in UI (default 6)
- **`GRAPH_MAX_CHUNKS`** — Limit chunks participating in graph build (default 0 = unlimited)
- **`GRAPH_CONCURRENCY`** — Control extraction and summarization concurrency (default 3, range 1–1000; too high may trigger throttling)
- **`GRAPH_CHUNK_TIMEOUT`** — Prevent single requests from blocking build slots (default 240s)
- **`GRAPH_BUILD_TIMEOUT`** — Prevent infinite single-round builds; pauses instead of discarding (default 3600s)
- **`GRAPH_RETRY_ROUNDS` / `GRAPH_RETRY_BACKOFF`** — Control failed chunk retry rounds and backoff (default 2 rounds / 2s)
- **`GRAPH_SUCCESS_THRESHOLD`** — Auto-generate communities at this success percentage (default 90%)
- **`GRAPH_LLM_ENTITY_MATCHING`** — Chat-model entity candidate arbitration (default off)

Recommendation: set `GRAPH_MAX_CHUNKS=100` for the first build, verify results, then change to `0` for the full graph.

## Current Limitations & Extension Points

**Limitations:**

- Vector retrieval uses NumPy exact cosine search over SQLite vectors within a single process — suitable for personal or team-scale knowledge bases
- Pure images embedded in DOCX/PPTX are not individually OCR'd; extendable via `OCRTarget` adapter
- Deleting a KB removes corresponding SQLite records and raw files under `data/uploads` — operation is irreversible

**Extension Points:**

- For 100k+ chunks, replace the vector layer with pgvector, Qdrant, or Milvus; move graph build to a dedicated task queue
- Public deployment requires identity authentication, HTTPS, upload content security scanning, reverse proxy rate limiting, and database backups
- Replace `AIClient` API calls to adapt other OpenAI-compatible model services

## Security Notes

- Default listening on `127.0.0.1` — localhost only
- API URLs and Keys live only in each device's encrypted HttpOnly cookie; the server does not persist them, and neither frontend JavaScript nor the settings API can read or echo existing Keys
- KB access passwords stored as PBKDF2-SHA256 salted hashes in SQLite; no plaintext. Unlock tokens stored only in server process memory and current browser session; re-unlock required after restart or page refresh
- KB password protection is a product-grade isolation safeguard, not a multi-user authentication system. For LAN/public deployment, add user management, HTTPS, reverse proxy auth, and rate limiting
- Production must use HTTPS and a stable, strong `DEVICE_COOKIE_SECRET`; rotating it invalidates every existing device cookie
- Before committing, verify with `git check-ignore .env` that secrets are not tracked
