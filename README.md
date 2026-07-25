# SIA - Sports Interactive Agent

SIA is a source-locked NBA.com RAG demonstration project. A bounded ingestion job
retrieves approved NBA.com pages through ScraperAPI in Markdown format, stores the
snapshot locally, chunks it, and indexes it in ChromaDB and SQLite FTS.

Chat does not scrape the web. It searches the stored corpus first and sends only
retrieved evidence to the configured answer model.

## Architecture

```text
NBA.com URLs
-> robots.txt and URL safety checks
-> ScraperAPI Markdown ingestion
-> local Markdown snapshot
-> BGE embeddings in ChromaDB
-> keyword index in SQLite FTS

Question
-> deterministic query expansion
-> semantic search + keyword search
-> rank fusion + lexical evidence filtering
-> adjacent-chunk evidence expansion
-> OpenAI Responses API (or optional Ollama)
-> structured-output and citation validation
-> cited answer or safe refusal
```

## Source-locking rules

- Only HTTPS `nba.com` and `www.nba.com` targets are accepted.
- Chat never calls ScraperAPI or performs live scraping.
- Retrieval must find sufficiently relevant stored evidence before an LLM is called.
- The answer model receives only the question and retrieved NBA.com evidence.
- Invalid or invented citations produce a refusal.
- `.env`, scraped Markdown, ChromaDB, SQLite, caches, and local metadata are ignored by Git.

Review the [NBA.com Terms of Use](https://www.nba.com/termsofuse) and obtain any
permission required for your intended use.

## Requirements

- Python 3.11-3.13
- Node.js 22.13 or newer
- A ScraperAPI key for ingestion
- An OpenAI API key for the default answer provider

Ollama with `qwen2.5:3b` can be used instead of OpenAI, but CPU-only generation is
considerably slower.

## Configure

Copy `.env.example` to `.env`. Never commit `.env`.

```env
SCRAPERAPI_KEY=your-scraperapi-key
USE_SCRAPERAPI=true
CRAWL_MAX_PAGES=150

LLM_PROVIDER=openai
OPENAI_API_KEY=your-openai-api-key
OPENAI_MODEL=gpt-5.6-terra

NEXT_PUBLIC_API_URL=http://localhost:8000
```

Every tester must use their own API keys. OpenAI API usage is billed separately
from a ChatGPT subscription.

## Install

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r backend\requirements.txt
npm install
```

## Build or refresh the stored snapshot

Run ingestion only initially or when intentionally refreshing the corpus:

```powershell
python scripts\ingest.py
```

The first run downloads the embedding model. The snapshot is local and is not
included in this repository.

## Run

Start the backend:

```powershell
cd backend
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

In another terminal, start the frontend from the project root:

```powershell
npm run dev
```

Open `http://localhost:3000`.

## Optional Ollama provider

```env
LLM_PROVIDER=ollama
OLLAMA_MODEL=qwen2.5:3b
```

```powershell
ollama pull qwen2.5:3b
```

## Tests

```powershell
cd backend
pytest
```

```powershell
npm test
```

## Current limitations

- Answers are limited to pages in the latest successful local ingestion.
- NBA.com structure and ScraperAPI Markdown output can change.
- Semantic similarity alone is not proof, so the evidence and citation gates may refuse.
- Testers must build their own local snapshot because scraped data is not published.
