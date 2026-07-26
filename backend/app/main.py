import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from .chunker import chunk_pages
from .config import settings
from .database import Database
from .question_index import build_expected_question_index
from .rag import RagService
from .retrieval import HybridRetriever
from .schemas import ChatRequest, ChatResponse, IngestRequest, IngestResponse
from .scraper import NbaScraper
from .security import UnsafeUrl
from .vector_store import VectorStore, VectorStoreUnavailable

database = Database(settings.data_dir / "courtside.sqlite")
scraper = NbaScraper(settings)
vector_store = VectorStore(settings)
retriever = HybridRetriever(vector_store, database, settings)
rag = RagService(retriever, settings)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    try:
        await asyncio.to_thread(vector_store.warm)
    except VectorStoreUnavailable:
        # Health/status endpoints must still explain an unbuilt local index.
        pass
    yield


app = FastAPI(
    title="SIA RAG API",
    version="1.0.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:5173",
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "source_policy": "nba.com snapshot only"}


@app.get("/api/status")
def status() -> dict:
    markdown_files = len(list(settings.markdown_dir.glob("*.md"))) if settings.markdown_dir.exists() else 0
    return {
        **database.status(),
        **vector_store.status(),
        "markdown_files": markdown_files,
        "source_host": "www.nba.com",
        "chat_scrapes_live": False,
        "answer_provider": "OpenAI API",
        "answer_model": settings.openai_model,
        "query_model": settings.openai_query_model or settings.openai_model,
        "query_planner_enabled": settings.enable_query_planner,
    }


@app.post("/api/ingest", response_model=IngestResponse)
async def ingest(payload: IngestRequest) -> IngestResponse:
    started_at = datetime.now(UTC).isoformat()
    try:
        pages, errors = await scraper.crawl(str(payload.seed_url), min(payload.max_pages, settings.crawl_max_pages))
    except UnsafeUrl as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not pages:
        raise HTTPException(status_code=422, detail="No permitted readable NBA pages were found.")
    chunks = chunk_pages(pages, settings)
    if not chunks:
        raise HTTPException(status_code=422, detail="NBA pages were retrieved but produced no usable text chunks.")
    try:
        vector_store.build(chunks)
    except VectorStoreUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    database.replace_corpus(pages, chunks)
    expected_questions = 0
    try:
        question_result = await build_expected_question_index(
            database,
            vector_store,
            settings,
        )
        expected_questions = question_result["questions"]
    except Exception as exc:
        errors.append(f"Expected-question index: {type(exc).__name__}")
    database.record_run(str(payload.seed_url), started_at, len(pages), len(chunks), errors)
    return IngestResponse(
        pages_indexed=len(pages),
        chunks_created=len(chunks),
        skipped=len(errors),
        expected_questions=expected_questions,
    )


@app.post("/api/chat", response_model=ChatResponse)
async def chat(payload: ChatRequest) -> ChatResponse:
    return await rag.answer(payload.question.strip())
