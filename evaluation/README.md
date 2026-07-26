# Evaluation assets

This directory contains golden validation material for measuring SIA. It is
not part of the RAG knowledge base.

## Hard boundary

- Do not ingest these files into SQLite FTS, Chroma, source chunks, or the
  prepared-question index.
- Do not include expected answers in query planning, retrieval, prompts, or
  answer-generation context.
- An evaluation runner may submit each golden **question** to `/api/chat`.
- Compare the completed response with the expected answer only after SIA has
  returned its answer.
- Grade correctness, completeness, refusal behavior, groundedness, latency,
  and citation quality independently.
- Citations must still resolve to original indexed NBA.com evidence.
- Dynamic cases must be evaluated against the selected snapshot date and
  season rather than treated as permanently fixed answers.

## Golden bank

`golden/NBA_RAG_Validation_Merged_Unique_With_Refusals.md`

- Version: 1.0
- Prepared: 2026-07-25
- Validation cases: 548
- SHA-256:
  `DC34F8262437F492AB9F00741509D3969A6E6C50861D1F2F9C49FA8D51399B82`

Keeping evaluation artifacts outside `data/markdown/` ensures that the current
ingestion, reindexing, and retrieval paths cannot mistake expected answers for
source evidence.
