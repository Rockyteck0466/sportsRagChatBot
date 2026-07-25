import asyncio
import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Any

import httpx

from .config import Settings
from .database import Database
from .rag import compact_evidence
from .vector_store import VectorStore

QUESTION_KINDS = {"direct", "paraphrase", "combined"}
QUESTION_BATCH_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "chunk_id": {"type": "string"},
                    "questions": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "text": {"type": "string"},
                                "kind": {
                                    "type": "string",
                                    "enum": sorted(QUESTION_KINDS),
                                },
                            },
                            "required": ["text", "kind"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["chunk_id", "questions"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["items"],
    "additionalProperties": False,
}


class ExpectedQuestionBuilder:
    """Generate retrieval aliases that can never be used as answer evidence."""

    def __init__(self, config: Settings):
        self.config = config

    @staticmethod
    def _output_text(payload: dict[str, Any]) -> str:
        text = ""
        for output in payload.get("output", []):
            for content in output.get("content", []):
                if content.get("type") == "output_text":
                    text += content.get("text", "")
        if not text.strip():
            raise ValueError("OpenAI returned no expected-question output.")
        return text.strip()

    async def _request_batch(
        self,
        client: httpx.AsyncClient,
        batch: list[dict[str, Any]],
        semaphore: asyncio.Semaphore,
    ) -> tuple[list[dict[str, Any]], str | None]:
        context = "\n\n".join(
            f"[CHUNK {chunk['chunk_id']}]\n"
            f"Title: {chunk['title']}\n"
            f"Section: {chunk['section']}\n"
            f"NBA.com passage: {compact_evidence(chunk['text'], max_chars=2_400)}"
            for chunk in batch
        )
        system_prompt = f"""Create retrieval-only expected questions for indexed NBA.com
passages. Never answer a question. Use only facts and entities visible in each supplied
passage. For every chunk, create up to {self.config.questions_per_chunk} natural questions:
one direct/basic form, one differently worded paraphrase, and one combined or more complex
form when the passage supports it. Every question must be fully answerable from that same
passage. Do not add outside facts, invented names, or invented quantities. Return each
input chunk_id exactly. The questions are search aliases, never evidence."""
        user_prompt = f"""Indexed passages:

{context}

Return expected questions only. Do not include answers or explanations."""
        async with semaphore:
            for attempt in range(3):
                try:
                    response = await client.post(
                        f"{self.config.openai_base_url.rstrip('/')}/responses",
                        headers={
                            "Authorization": f"Bearer {self.config.openai_api_key}",
                            "Content-Type": "application/json",
                        },
                        json={
                            "model": (
                                self.config.openai_question_model
                                or self.config.openai_model
                            ),
                            "instructions": system_prompt,
                            "input": user_prompt,
                            "max_output_tokens": max(900, len(batch) * 180),
                            "store": False,
                            "reasoning": {
                                "effort": self.config.openai_reasoning_effort,
                            },
                            "text": {
                                "verbosity": "low",
                                "format": {
                                    "type": "json_schema",
                                    "name": "nba_expected_questions",
                                    "strict": True,
                                    "schema": QUESTION_BATCH_SCHEMA,
                                },
                            },
                        },
                    )
                    response.raise_for_status()
                    generated = json.loads(self._output_text(response.json()))
                    return generated.get("items", []), None
                except (
                    httpx.HTTPError,
                    json.JSONDecodeError,
                    TypeError,
                    ValueError,
                ) as exc:
                    if attempt == 2:
                        chunk_ids = ", ".join(chunk["chunk_id"] for chunk in batch)
                        return [], f"{chunk_ids}: {type(exc).__name__}"
                    await asyncio.sleep(2**attempt)
        return [], "Unexpected expected-question generation failure."

    @staticmethod
    def _fallback_questions(chunk: dict[str, Any]) -> list[dict[str, str]]:
        title = re.sub(r"\s+", " ", chunk["title"]).strip()
        section = re.sub(r"\s+", " ", chunk["section"]).strip()
        return [
            {
                "text": f"What does {title} say about {section}?",
                "kind": "direct",
            },
            {
                "text": f"Which details are provided in {section} for {title}?",
                "kind": "paraphrase",
            },
        ]

    def _records(
        self,
        chunks: list[dict[str, Any]],
        generated_items: list[dict[str, Any]],
    ) -> list[dict[str, str]]:
        chunks_by_id = {chunk["chunk_id"]: chunk for chunk in chunks}
        questions_by_chunk: dict[str, list[dict[str, str]]] = {
            chunk_id: [] for chunk_id in chunks_by_id
        }
        for item in generated_items:
            chunk_id = str(item.get("chunk_id", ""))
            if chunk_id not in chunks_by_id:
                continue
            for raw_question in item.get("questions", []):
                kind = str(raw_question.get("kind", "")).lower()
                text = " ".join(str(raw_question.get("text", "")).split())[:300]
                if kind not in QUESTION_KINDS or len(text) < 8:
                    continue
                if not text.endswith("?"):
                    text += "?"
                normalized = re.sub(r"\s+", " ", text).strip().lower()
                if normalized in {
                    re.sub(r"\s+", " ", entry["text"]).strip().lower()
                    for entry in questions_by_chunk[chunk_id]
                }:
                    continue
                questions_by_chunk[chunk_id].append({"text": text, "kind": kind})
                if (
                    len(questions_by_chunk[chunk_id])
                    >= self.config.questions_per_chunk
                ):
                    break

        records: list[dict[str, str]] = []
        for chunk_id, chunk in chunks_by_id.items():
            questions = questions_by_chunk[chunk_id]
            if not questions:
                questions = self._fallback_questions(chunk)
            for question in questions[: self.config.questions_per_chunk]:
                digest = hashlib.sha256(
                    f"{chunk_id}:{question['text'].lower()}".encode()
                ).hexdigest()[:18]
                records.append({
                    "question_id": f"nba:q:{digest}",
                    "chunk_id": chunk_id,
                    "question": question["text"],
                    "kind": question["kind"],
                })
        return records

    async def generate(
        self,
        chunks: list[dict[str, Any]],
    ) -> tuple[list[dict[str, str]], list[str]]:
        if not self.config.openai_api_key:
            raise RuntimeError(
                "OPENAI_API_KEY is required to build the expected-question index."
            )
        batch_size = max(1, min(self.config.question_generation_batch_size, 10))
        batches = [
            chunks[start : start + batch_size]
            for start in range(0, len(chunks), batch_size)
        ]
        semaphore = asyncio.Semaphore(
            max(1, min(self.config.question_generation_concurrency, 8))
        )
        timeout = httpx.Timeout(self.config.openai_timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout) as client:
            results = await asyncio.gather(
                *(
                    self._request_batch(client, batch, semaphore)
                    for batch in batches
                )
            )
        generated_items = [
            item
            for items, _error in results
            for item in items
        ]
        errors = [error for _items, error in results if error]
        return self._records(chunks, generated_items), errors


async def build_expected_question_index(
    database: Database,
    vector_store: VectorStore,
    config: Settings,
) -> dict[str, Any]:
    chunks = database.all_chunks()
    if not chunks:
        raise ValueError("No source chunks exist for expected-question generation.")
    records, errors = await ExpectedQuestionBuilder(config).generate(chunks)
    if not records:
        raise ValueError("No expected questions were generated.")
    database.replace_chunk_questions(records)
    collection = vector_store.build_question_index(records)
    return {
        "collection": collection,
        "questions": len(records),
        "source_chunks": len({record["chunk_id"] for record in records}),
        "errors": errors,
        "generated_at": datetime.now(UTC).isoformat(),
        "model": config.openai_question_model or config.openai_model,
    }
