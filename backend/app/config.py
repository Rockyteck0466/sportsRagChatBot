from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    nba_allowed_hosts: str = "www.nba.com,nba.com"
    nba_seed_url: str = "https://www.nba.com/"
    scraperapi_key: str = ""
    use_scraperapi: bool = False
    scraperapi_render: bool = False
    crawler_user_agent: str = "SportsInformativeRAGCollegeDemo/1.0 (contact: student@example.edu)"
    crawl_max_pages: int = 150
    crawl_delay_seconds: float = 1.5
    crawl_timeout_seconds: float = 15
    crawl_max_bytes: int = 3_000_000
    chunk_words: int = 280
    chunk_overlap_words: int = 45
    semantic_top_k: int = 12
    keyword_top_k: int = 12
    fusion_top_k: int = 12
    retrieval_top_k: int = 3
    min_retrieval_score: float = 0.25
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    reranker_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    enable_reranker: bool = False
    vector_collection_prefix: str = "nba_markdown"
    llm_provider: str = "openai"
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-5.6-terra"
    openai_max_output_tokens: int = 500
    openai_timeout_seconds: float = 90
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:3b"
    ollama_num_predict: int = 180
    ollama_num_ctx: int = 4096
    data_dir: Path = PROJECT_ROOT / "data"

    @property
    def allowed_hosts(self) -> frozenset[str]:
        return frozenset(host.strip().lower() for host in self.nba_allowed_hosts.split(","))

    @property
    def markdown_dir(self) -> Path:
        return self.data_dir / "markdown"

    @property
    def chroma_dir(self) -> Path:
        return self.data_dir / "chroma"


settings = Settings()
