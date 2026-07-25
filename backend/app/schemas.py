from pydantic import BaseModel, Field, HttpUrl


class IngestRequest(BaseModel):
    seed_url: HttpUrl
    max_pages: int = Field(default=150, ge=1, le=150)


class IngestResponse(BaseModel):
    pages_indexed: int
    chunks_created: int
    skipped: int


class ChatRequest(BaseModel):
    question: str = Field(min_length=2, max_length=800)


class Citation(BaseModel):
    citation_id: str
    title: str
    url: str
    section: str
    chunk_id: str
    retrieved_at: str


class ChatResponse(BaseModel):
    answer: str
    citations: list[Citation]
    confidence: str
    refused: bool
