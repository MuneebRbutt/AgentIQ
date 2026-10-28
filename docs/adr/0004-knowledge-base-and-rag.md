# ADR-0004: Knowledge base, retrieval and grounded answers

- **Status:** Accepted (proposed default; the team may revise by superseding this ADR)
- **Date:** 2026-10-18
- **Requirements:** FR-05-01…06, FR-09-03, NFR03-02, NFR04-01; TC-05; analysis Q-1 (LLM provider, pending), Q-2 (embeddings); builds on ADR-0001 §4 and ADR-0003 §6

## Context

M4 delivers the Knowledge agent: operators upload their own documents (policies, FAQs,
menus) and the agent answers customers' questions from them, or says it doesn't know
(FR-05-05). It must work before the orchestrator exists (M5), stay tenant-isolated, cost
nothing in tests, and keep working when the language model is unavailable.

## Decisions

### 1. ChromaDB runs embedded, not as a compose service

ADR-0001 planned a Chroma container for M4. Instead the API uses Chroma's embedded
`PersistentClient`, storing files under `backend/data/chroma` (`CHROMA_PATH`; git-ignored).
For one API process at FYP scale this is simpler (nothing else to start or health-check),
and tests use an in-memory client. Chroma is still behind `ChromaVectorStore`
(`app/knowledge/store.py`), so moving to a Chroma server for several API instances (M12)
changes one constructor. The `reindex-knowledge` command must not run while the API is
writing the same folder.

### 2. Tenant isolation: one collection per tenant

Each tenant gets its own collection, `t_<tenant hex>_<embedder name>`, and every query is
also filtered by `profile_id`. A bug in a filter can therefore never return another
business's passages (CLAUDE.md rule 1). The tenant ID always comes from the login or
`AgentContext`, never from the model.

### 3. Postgres keeps the documents; Chroma is an index

`knowledge_documents` stores each file's name, type, size, SHA-256, chunk count, uploader
and **extracted text**. Chroma holds only passages, their vectors and metadata
(`document_id`, `profile_id`, `filename`, `chunk_index`). Because the text is in Postgres
(the source of truth, rule 6), `python -m app.cli reindex-knowledge` rebuilds the index after
the embedder or chunking changes or the Chroma folder is lost. The original files are not
kept (they may be large; the text is what the agent uses).

### 4. Ingestion

| Step | Choice |
|---|---|
| Formats | `.pdf` (pypdf), `.docx` (python-docx, paragraphs and tables), `.txt`, `.md`. Anything else, a corrupt file or a file with no text is refused with 422 `unsupported_document`. Scanned PDFs (images only) have no text and are refused; OCR is out of scope. |
| Size | At most `MAX_DOCUMENT_BYTES` (5 MB), else 413. |
| Chunking | Paragraph- and sentence-aware passages of up to 350 characters with an 80-character overlap of whole sentences (`app/knowledge/chunking.py`). Short passages matched short spoken questions noticeably better with MiniLM than whole pages did; tune with the retrieval-accuracy evaluation in M10. |
| Same file name | Replaces the earlier document (FR-05-06). New passages are added first under a new document ID, then the database row is swapped, then the old passages are deleted, so a failure at any step leaves the previous version fully searchable. |
| Work off the event loop | Parsing, embedding and Chroma calls run in worker threads (CLAUDE.md §4). |

### 5. Embeddings (Q-2)

Default `EMBEDDING_PROVIDER=minilm`: all-MiniLM-L6-v2 through the ONNX runtime that ships
with Chroma. It runs locally, is free, needs no `torch`, and downloads once (~80 MB) on first
use. `hashing`, a deterministic bag-of-words embedder, exists only for tests and offline
demos. The embedder's name is part of the collection name, so switching never mixes vectors;
run `reindex-knowledge` after switching.

### 6. Retrieval and the "insufficient information" rule (FR-05-03/05)

The question is embedded, the top `KNOWLEDGE_TOP_K` (4) passages are retrieved by cosine
similarity, and those scoring below `KNOWLEDGE_MIN_SCORE` (0.30) are dropped. With none left
the agent answers `no_answer`. The threshold is provisional: on the example hotel policies,
MiniLM scores real questions at about 0.38–0.56 and unrelated ones (pool, shuttle) at about
0.15–0.27, but some unrelated questions ("valet parking", 0.33) still pass. A score threshold
alone can't separate them, hence the second check in §7.

### 7. Grounded answers, with and without a model (Q-1, NFR04-01)

- **With a model** (`LLM_API_KEY` set; any OpenAI-compatible chat API via `LLM_BASE_URL`
  and `LLM_MODEL`): one call at temperature 0 with the system prompt in
  `app/agents/knowledge/prompts/answer.md`. The model may use only the numbered passages
  and must reply `INSUFFICIENT_INFORMATION` when they don't answer the question; the agent
  turns that into `no_answer`. Passages are labelled as data, so instructions inside a
  document are not followed.
- **Without a model, or when the call fails or times out** (`LLM_TIMEOUT_SECONDS`): the
  agent quotes up to two sentences from the retrieved passages that share the most words
  with the question. Words of a heading count for the sentences under it. If no sentence
  shares a word with the question, the answer is `no_answer` rather than a quote about
  something else. The result says which `mode` produced it (`llm` or `extractive`).

### 8. Agent contract addition (amends ADR-0003 §6)

`AgentStatus` gains **`no_answer`**: the business's knowledge doesn't cover the question,
and the orchestrator must say so rather than guess. The Knowledge agent takes
`KnowledgeSubtask(question)` and returns `completed` (with the answer in `message` and the
passages used in `data.sources`) or `no_answer`.

### 9. API

All routes are under `/api/v1/profiles/{profile_id}` and need a login:

- `POST documents` (multipart `file`): 201 for a new document, 200 with `replaced: true`
  when it replaced one.
- `GET documents`, `GET documents/{id}` (includes the extracted text, so operators can
  check what was read), `DELETE documents/{id}`.
- `POST knowledge/ask` with `{"question": ...}`: runs the Knowledge agent and returns its
  `AgentResult`, so operators can test their documents before the test console (M6).

Deleting a profile also deletes its passages from the index.

## Consequences

- TC-05 passes with the offline embedder in CI and with MiniLM where the model is cached
  (`tests/unit/test_minilm.py`, skipped when it can't load).
- Profile `faqs` and `policies` are not indexed in Chroma. (Amended by ADR-0005: the
  Knowledge agent also searches passages built from the profile, cached in memory.)
- New runtime dependencies: `chromadb` (Apache-2.0; also provides ONNX MiniLM), `pypdf`
  (BSD), `python-docx` (MIT), `httpx` (BSD, already present through FastAPI).
- An answer costs one LLM call of roughly 1–2k input tokens when a model is configured,
  and nothing otherwise.
