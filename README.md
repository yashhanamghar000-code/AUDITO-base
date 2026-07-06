# AUDITO AI

Multi-tenant financial/legal document RAG platform: FastAPI + LangGraph +
Qdrant (vector) + BM25 (sparse) + Cross-Encoder reranking + JWT auth +
Postgres (users).

## Folder structure

```
AUDITO-AI/
├── backend/
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py          # FastAPI app: /api/upload, /api/chat, /api/auth/*
│   │   ├── config.py        # LLM, reranker, tuning constants, env vars
│   │   ├── db.py            # SQLAlchemy engine/session (Postgres)
│   │   ├── models.py        # User ORM model
│   │   ├── schemas.py       # Pydantic request/response models
│   │   ├── auth.py          # password hashing, JWT issue/verify
│   │   ├── routes_auth.py   # /api/auth/register, /login, /me, /logout
│   │   ├── parser.py        # MultiUserParser: PDF/DOCX/TXT/image -> chunks
│   │   ├── vectorstore.py   # Qdrant client wrapper (multi-tenant filters)
│   │   ├── retriever.py     # MultiUserRetriever: hybrid search + rerank
│   │   └── workflow.py      # LangGraph: decompose -> retrieve -> generate
│   ├── legacy_prototypes/
│   │   └── code-mm.py       # original single-user CLI prototype (reference only)
│   ├── data/                # uploaded PDFs (gitkeeped, empty)
│   ├── chroma_db/           # legacy Chroma persistence dir (unused by prod path)
│   ├── bm25_cache/          # per-user/session BM25 pickle cache
│   ├── chat_history/        # per-user/session chat history JSON
│   ├── debug_logs/          # chunk audit logs written by parser.py
│   ├── requirements.txt
│   ├── .env.example
│   └── Dockerfile
├── frontend/
│   └── README.md            # API contract for wiring up your existing UI
├── docker-compose.yml        # backend + postgres + qdrant + redis
├── .gitignore
└── README.md
```

## What this actually is

Everything in `backend/app/` is your pasted code, reassembled into a real
importable Python package with the bugs from copy-pasting fixed:

- **`retriever.py`** — was named `retieriver.py` in your paste (typo);
  renamed so `from app.retriever import ...` in `workflow.py` and `main.py`
  actually resolves.
- **`requirements.txt`** — deduplicated (you had `passlib`, `python-jose`,
  `sqlalchemy`, `psycopg2-binary` listed twice) and added
  `langchain-text-splitters`, which `parser.py` imports but which was
  missing from your requirements list.
- **`legacy_prototypes/code-mm.py`** — your original single-user, CLI-driven,
  Chroma-based prototype. It's not imported by the app; kept only as a
  reference for where the entity-routing + Chroma logic came from.

I did **not** invent new business logic — the auth flow, upload flow, chat
flow, hybrid search, and reranking are exactly what you pasted, just made
into a coherent runnable structure. The one small addition is a
`DELETE /api/session/{session_id}` endpoint in `main.py`, since you already
had a `MultiUserRetriever.clear_session()` method defined but nothing calling
it — useful for a "clear chat" button in the UI.

## Running it

```bash
cd AUDITO-AI
cp backend/.env.example backend/.env   # fill in MY_API_KEY, JWT_SECRET_KEY, etc.
docker compose up --build
```

Backend comes up on `http://localhost:8000`, Postgres on `5432`, Qdrant on
`6333`, Redis on `6379` (reserved for your planned worker queue — not wired
into the request path yet).

## Your TODO list mapped onto this structure

| Your TODO item | Where it lives now |
|---|---|
| Upload PDF | `app/main.py` → `/api/upload` |
| Parsing (OCR, pdfplumber, parallel) | `app/parser.py` (currently sequential per file; parallelize page loop if needed) |
| Chunking | `app/parser.py::_chunk_documents` |
| Embedding | `app/retriever.py` (`HuggingFaceEmbeddings`, BGE small) |
| Elastic search | not present yet — currently BM25 (sparse) + Qdrant (dense); swap/add Elastic in `retriever.py` if you want it alongside BM25 |
| Retrieval | `app/retriever.py::hybrid_search`, `retrieve_and_rerank` |
| Qdrant/Chroma | Qdrant is the live store (`app/vectorstore.py`); Chroma only exists in the legacy prototype |
| Follow-up questions / system prompt improvement | `app/workflow.py::generate_node` system prompt |
| Speed of parsing | `app/parser.py` — OCR fallback only triggers on low-quality text; page loop is currently sequential, candidate for `concurrent.futures` |
| LangGraph/LangChain | `app/workflow.py` |
| UI, integration | `frontend/` — drop your existing UI in, wire to the contract in `frontend/README.md` |
| Authentication, FastAPI gateway, JWT | `app/auth.py`, `app/routes_auth.py` |
| User service, chat history, Postgres | `app/models.py`, `app/db.py`, history JSON files in `chat_history/` |
| Worker, Redis queue | `docker-compose.yml` has a `redis` service ready; no worker code yet — next step is a Celery/RQ task for `/api/upload` so large PDFs don't block the request |
| Multiple user testing | multi-tenancy is enforced via `user_id` + `session_id` on every Qdrant filter and BM25 cache file — test by hitting `/api/upload` and `/api/chat` as two different logged-in users with the same `session_id` string and confirming no cross-contamination |

## Next integration step

Point me at your frontend's upload/chat components (or the repo) and I'll
wire the actual `fetch`/`axios` calls to these exact endpoints.
