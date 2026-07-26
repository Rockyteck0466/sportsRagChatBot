# SIA - Sports Interactive Agent

SIA is a source-locked NBA.com RAG demonstration project. A bounded ingestion job
retrieves approved NBA.com pages through ScraperAPI in Markdown format, stores the
snapshot locally, chunks it, and indexes it in ChromaDB and SQLite FTS.

Chat does not scrape the web. It searches the stored corpus first, maps any
expected-question match back to real source chunks, and sends the original
question, the matched retrieval alias, and those NBA.com passages to the answer
model. The prompt explicitly labels the alias as navigation metadata, not
evidence.

## Architecture

```text
NBA.com URLs
-> robots.txt and URL safety checks
-> ScraperAPI Markdown ingestion
-> local Markdown snapshot
-> BGE embeddings in ChromaDB
-> keyword index in SQLite FTS
-> one-time OpenAI expected-question generation
-> retrieval-only question vectors + question FTS mapped to source chunks

Question
-> universal OpenAI intent rewrite for every question
-> faithful atomic decomposition, 2-4 synonyms/variants, operation and ambiguity detection
-> server-side validation that preserves the original numbers, dates, negation and scope
-> source-backed team/entity validation and generic per-team fan-out (up to 30)
-> every atomic variant searches four lanes:
   source semantic + source keyword + prepared semantic + prepared keyword
-> prepared matches mapped back to their original NBA.com chunks
-> batched embeddings/SQLite searches, rank fusion and cross-source deduplication
-> exact complete-section expansion when the operation requires complete evidence
-> per-task coverage ledger: found, complete, partial or missing
-> bounded, round-robin evidence context so large fan-outs cannot overflow the model
-> short-lived in-memory plan cache for repeated questions
-> evidence-only OpenAI Responses API answer
-> structured-output and citation validation
-> cited complete/partial answer or safe refusal
```

## Source-locking rules

- Only HTTPS `nba.com` and `www.nba.com` targets are accepted.
- Chat never calls ScraperAPI or performs live scraping.
- Retrieval must find sufficiently relevant stored evidence before an LLM is called.
- The answer model receives the original question, matched retrieval aliases
  labeled as non-evidence, and retrieved NBA.com passages.
- The exact user question remains authoritative; generated rewrites may route
  retrieval but cannot silently change quantities, dates, exclusions or scope.
- Generated expected questions are retrieval aliases, never factual evidence.
- Every expected-question match is resolved back to its original NBA.com chunk.
- Every simple or composite request uses the same atomic retrieval and coverage path.
- Missing one atomic fact does not discard other supported parts.
- Rankings, maxima and minima require verified complete-population coverage;
  a complete page section or nearest-neighbor snippets cannot prove a league-wide
  aggregate.
- Team names suggested by the planner must resolve to team pages already in the corpus.
- Invalid or invented citations produce a refusal.
- `.env`, scraped Markdown, ChromaDB, SQLite, caches, and local metadata are ignored by Git.

Review the [NBA.com Terms of Use](https://www.nba.com/termsofuse) and obtain any
permission required for your intended use.

## Requirements

- Python 3.11-3.13
- Node.js 22.13 or newer
- A ScraperAPI key for ingestion
- An OpenAI API key for query planning and answer generation

## Configure

Copy `.env.example` to `.env`. Never commit `.env`.

```env
SCRAPERAPI_KEY=your-scraperapi-key
USE_SCRAPERAPI=true
CRAWL_MAX_PAGES=150

OPENAI_API_KEY=your-openai-api-key
OPENAI_MODEL=gpt-5.6-luna
OPENAI_QUERY_MODEL=gpt-5.6-luna
OPENAI_CONTEXT_MAX_CHARS=60000
OPENAI_CONTEXT_CHUNKS_PER_TASK=3
OPENAI_QUERY_TIMEOUT_SECONDS=20
QUERY_PLAN_CACHE_SIZE=128

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
included in this repository. Full ingestion also generates the expected-question
index once. Normal chat never regenerates it.

To apply cleaner or chunking changes to the existing local snapshot without
using ScraperAPI again:

```powershell
python scripts\reindex.py
```

Reindexing source chunks intentionally clears stale expected-question mappings.
Rebuild them once after a reindex or incremental source update:

```powershell
python scripts\build_question_index.py
```

To inspect page, chunk, estimated-token, and disk-size totals:

```powershell
python scripts\corpus_stats.py
```

## Run

Start the backend:

```powershell
cd backend
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

The backend preloads the cached embedding model before it reports ready. The
first startup can therefore take several seconds, but the first submitted
question no longer pays that model-loading delay.

In another terminal, start the frontend from the project root:

```powershell
npm run dev
```

Open `http://localhost:3000`.

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
- Prepared questions and AI rewrites improve wording recall but cannot supply
  facts absent from the snapshot.
- Exact league-wide analytics require a complete standings table or complete
  historical game records in the indexed snapshot.
- Materially ambiguous questions may receive one concise clarification question
  instead of an invented interpretation.
- NBA.com structure and ScraperAPI Markdown output can change.
- Semantic similarity alone is not proof, so the evidence and citation gates may refuse.
- Testers must build their own local snapshot because scraped data is not published.
