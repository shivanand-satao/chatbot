# Crime Records AI Assistant — Implementation Plan

Updated: October 22, 2026

## 1. Goal and scope

Build an independently deployable FastAPI assistant for the existing Crime Records of India application. It will answer crime-data questions using validated PostgreSQL queries, legal-document questions using retrieval-augmented generation (RAG), and hybrid questions using both. Authenticated users will have persistent, private conversations that remain available when they return.

This document is the implementation source of truth for the next stages. A planned feature is not an implemented feature. Update the checklist and record validation results as work completes.

## 2. Existing foundation and limitations

- Dataset metadata indexing and embedding helpers already exist. A local report records 78 datasets and 434 metadata vectors; these are schema/metadata vectors, not embedded database records. Recheck live coverage before runtime integration.
- The existing embedding helper uses `intfloat/multilingual-e5-small`, normalized 384-dimensional vectors, and distinct query/passage prefixes. Runtime retrieval must match the stored model and revision.
- Six PDFs have an extraction pipeline with source text, page references, version labels, chunk states, and a ready-chunk retrieval view. Extraction does not establish complete searchable coverage.
- Document enrichment was stopped by the user. Local reports may lag actual progress. Do not restart extraction/enrichment or incur embedding/LLM charges without explicit authorization.
- Some documents have introduced-bill or index edition labels. Preserve their identity; never present them as interchangeable current enacted law.
- The frontend has a preview-only assistant with two modes. It has no live requests, backend chat sessions, or persistent chat history.
- No FastAPI application, safe query executor, persistent chat API, or production chatbot connection has been implemented by this plan document.

## 3. End-to-end request flow

1. Authenticate the request using the existing application's verified identity.
2. Load the requested conversation, enforcing ownership on the server.
3. Save the user message with an idempotent request identifier.
4. Assemble bounded conversation context and resolve follow-up references.
5. Route the standalone question: database, documents, hybrid, clarification, or unsupported.
6. Retrieve fresh evidence through the selected pipeline(s).
7. Generate an answer grounded in returned records and/or original document passages.
8. Validate the response structure, evidence references, and limitations.
9. Persist the assistant response and evidence/provenance metadata, then return it.
10. On failure, persist a bounded failure state and allow safe retry without duplicate turns.

The router and LLM do not grant permissions or execute unrestricted commands. The user-selected frontend mode is a preference; the server still validates scope and can ask for clarification or suggest the other mode.

## 4. Intent routing and follow-ups

Use validated structured output with a route, standalone question, requested entities/filters, and clarification requirements. Do not rely on a model's self-reported confidence as proof of correctness.

- Database: numerical comparisons, aggregates, record lookup, dataset discovery.
- Documents: definitions, explanations, and section/article lookup within the uploaded collection.
- Hybrid: statistical questions requiring separately sourced legal context.
- Clarification: missing metric, document, geography, time period, or unresolved reference.
- Unsupported: unavailable scope or requests for protected information.

For example, after a question about auto theft in one state and period, “What about Maharashtra?” inherits the prior topic and period, subject to clarification if ambiguous. It still requires fresh retrieval and database execution.

## 5. Database retrieval and answer pipeline

### 5.1 Candidate discovery

- Embed the resolved question with the existing query embedding helper.
- Retrieve candidate entries from `chatbot.dataset_metadata_embeddings` using matching model/revision.
- Combine semantic matching with exact table/column identifiers; evaluate lexical retrieval if needed.
- Group hits by dataset before selecting a small candidate set. Start with approximately 3–5 tables and tune against evaluation questions rather than treating this as a fixed correctness guarantee.
- Load complete verified schema metadata for the selected candidates. AI-inferred summaries assist discovery, not factual verification.

### 5.2 Answerability check

Verify current table/column existence, types, geography, year coverage, metric meaning, units, aggregation level, overlapping totals, and any documented relationships. Metadata statistics are snapshots; bounded dimension samples are not exhaustive.

Do not invent population denominators, rates, units, joins, or coverage. Ask a clarification or return an explicit limitation when required evidence is missing.

### 5.3 Query planning and execution

- Produce a validated structured query plan before SQL: approved tables/columns, parameters, grouping, aggregates, sorting, and result limits.
- Start with single-table analytical queries. Add joins only after verified relationships and aggregation tests.
- Select an SQL parser during implementation after reviewing installed dependencies; do not substitute keyword filtering for structural validation.
- Enforce a single approved read query; reject writes, write CTEs, unapproved functions, multiple statements, and unapproved schemas/tables/columns.
- Parameterize values and safely quote validated identifiers.
- Use a restricted analytical database role, read-only transactions, bounded execution time, result sizes, and connection pooling. Reject queries outside the permitted grammar and function set, including expensive or side-effecting operations.
- Never expose authentication tables, private user data, conversation storage, system catalogs, or unrestricted database tools to generated SQL.
- Keep controlled session writes separate from the read-only analytical executor.

### 5.4 Grounded response

Generate explanations from actual returned records/aggregates, verified filters, units, dataset identifiers, coverage, and warnings. Return structured table/chart data only when appropriate and bounded.

Distinguish unavailable datasets, unavailable coverage, no matching records, successful zero-valued aggregates, truncation, and execution failures. No matching rows do not automatically mean zero crimes. Avoid double-counting total and component rows.

## 6. Document RAG pipeline

- Retrieve through `chatbot.ready_document_chunks`, preserving content-hash validity, model/revision compatibility, approved document versions, and ready status.
- Combine semantic retrieval with exact document/section/article matching and lexical candidates as needed. Merge, deduplicate, rank, and bound context.
- Retrieve adjacent passages when needed for a complete explanation, maintaining page/section provenance.
- Use original extracted passages as evidence. Generated summaries/topics are retrieval aids only.
- Ask which document is intended when a section/article reference is ambiguous.
- Supply document title, version label, PDF page number, section reference, and chunk ID to the generator.
- Require every returned source identifier to map to a retrieved passage. Do not fabricate citations, source links, printed page numbers, or legal currency.
- Answer only to the supported scope. State when passages or document coverage are insufficient.
- Treat source passages as untrusted content, never as instructions to alter tools or disclose secrets.
- Retain a legal-research limitation: not legal advice, not emergency reporting, and not proof of the latest legal position.

Before enabling legal answers, produce a read-only per-document coverage audit: versions, ready/pending/review/failed chunks, and pages with usable evidence. Limited coverage must be visible to users. Restarting enrichment is a separate authorized operation.

## 7. Hybrid responses

Split the request into database and document subquestions. Run independent retrieval steps concurrently when safe. Return separate sections for what the data shows and what the documents say, followed by limitations.

One branch failing must not cause the other to fabricate evidence. Return supported partial answers with explicit missing information. Statistical patterns alone do not establish legal conclusions or causation.

## 8. Persistent chat history and returning-user context

### 8.1 User-visible behavior

- After verified login, load the user's conversation list and restore the last active available conversation, or show an empty state if none exists. Provide an explicit New chat action; do not silently create a new conversation on every login.
- Display prior user and assistant messages while the person chats, including their source references and timestamps.
- Allow selecting an older conversation, loading older messages with cursor pagination, starting a new conversation, and deleting a conversation.
- Persist completed turns on the server so refresh, logout/login, browser changes, and a later visit do not lose saved history.
- Logout clears browser conversation state and cancels requests; it does not delete server history. Account switching must never display the previous account's messages.
- Show loading, empty, unavailable, deleted-session, and retry states. Do not silently replace a saved conversation with a blank one when history retrieval fails.
- No persistent guest history in the initial release. Public users retain clearly labeled preview behavior.

### 8.2 How history influences answers

Use the selected conversation's recent turns, a bounded rolling summary for long conversations, and structured context such as topic, chosen dataset/document, year, geography, and unresolved clarification.

History resolves references and preserves research continuity. It is not authoritative evidence. Re-query database records and retrieve source passages for new factual answers; do not copy old numerical/legal claims as newly verified facts. Label saved answers as historical responses and retain when they were generated.

Resume the same conversation automatically when available. Across different conversations, show the history list but do not silently merge every old chat into the prompt. Initially, cross-conversation reuse requires an explicit user choice or reference; broader long-term personalization is a later opt-in feature.

Generated conversation summaries are derived, potentially fallible context. Record which turns they cover, preserve original messages as the source of truth, and never allow a summary to override current user instructions or tool permissions. Bound model context by token budget rather than sending the entire account history.

### 8.3 Proposed chatbot-specific storage

Use migrations in the existing separate `chatbot` schema. Final column types and identity mapping must follow the inspected authentication contract.

- `chatbot.conversations`: ID, verified owner subject/identity namespace as needed, title, created/updated timestamps, last-message ordering, and optional derived summary with covered-message boundary/version.
- `chatbot.messages`: ID, conversation ID, sequence/order, user/assistant role, content, route, timestamps, response state, idempotency/request ID, and bounded error metadata.
- `chatbot.message_sources`: assistant message ID, source type, dataset identifiers/query filters or document/chunk/version/page references, retrieval/execution timestamps, and bounded provenance snapshots where needed.

Keep raw prompts, full database result sets, model reasoning, credentials, access tokens, and unnecessary personal information out of saved evidence and logs. Store enough provenance to explain a saved answer without retaining unrestricted records. Deletion must remove owned messages, summaries, and associated source metadata according to an explicitly documented retention policy.

### 8.4 Ownership, consistency, and privacy

- Resolve owner identity from server-verified authentication, never from a client-supplied user ID. Confirm whether admin/user identifiers share a namespace before choosing owner keys.
- Check ownership for list, read, send, rename, resume, and delete operations. Admin application role alone must not grant access to other users' private conversations.
- Use database constraints and controlled transactions; use RLS as additional defense where compatible with the service's database access model. Test that runtime roles do not bypass intended isolation.
- Serialize or explicitly reject concurrent turns in the same conversation; maintain stable message ordering and idempotent retry behavior.
- Do not hold a database transaction open while waiting for LLM/network calls. Recheck conversation availability before saving a delayed answer, including deletion races.
- Document retention, deletion, and which bounded history is sent to the configured model provider before production. Do not promise data remains entirely local if a hosted LLM receives it.

## 9. Proposed API contract

Finalize endpoint schemas after authentication inspection. Initial resources:

- `POST /api/chat/conversations`: create a conversation.
- `GET /api/chat/conversations`: cursor-paginated owned conversation list.
- `GET /api/chat/conversations/{id}/messages`: cursor-paginated owned messages and sources.
- `POST /api/chat/conversations/{id}/messages`: submit a question with an idempotency key; return the saved assistant response, route, sources, structured data where applicable, and limitations.
- `DELETE /api/chat/conversations/{id}`: delete an owned conversation and associated history.
- Health/readiness endpoints that do not disclose secrets or private schema details.

Use the same response envelope for database, documents, hybrid, clarification, and unavailable states. Begin with complete JSON responses; add streaming only after persistence, cancellation, and partial-response behavior are designed and tested.

## 10. Implementation order and progress

- [x] Inspect existing indexing documentation, embedding helper, document migration, and frontend integration handoff.
- [x] Record architecture and persistent-history requirements in this document.
- [x] Phase 1: preliminary read-only coverage, authentication-source, dependency, and database privilege audit. See `READINESS.md` and the local ignored `readiness-output/report.json`. Live token verification and restricted runtime-role testing remain integration gates; document coverage is incomplete. Indexing was not restarted.
- [x] Phase 2 baseline: independently tested dataset and ready-document retrievers plus a small discovery/smoke evaluation set. See `RETRIEVAL.md`. 31 unit tests and 6 live checks passed without Groq. Broader QA/unanswerable evaluation, context expansion, and production-scale candidate retrieval remain follow-up work.
- [ ] Phase 3: safe structured query planning, SQL validation/execution, and grounded database responses.
  - Implemented offline milestone: trusted-catalog structured-plan compiler with bound values, quoted identifiers, bounded filters/aggregates/grouping/sorts, protected-table checks, and no execution path. See `QUERY_PLANS.md`. 49 offline tests passed. LLM planning, live catalog validation, restricted execution, and numerical answer generation remain pending.
- [ ] Phase 4: document context assembly, source validation, insufficient-evidence handling, and grounded legal research responses. Full coverage remains gated by authorized enrichment.
  - Implemented partial milestone: complete-passage character-budgeted context, document-selection gate, and structural citation identity validation. 42 unit tests and 8 live retrieval/context checks passed without Groq. Adjacent expansion, generation-token budgeting, claim support verification, and legal answer generation remain pending.
- [ ] Phase 5: router, follow-up resolution, clarification, and hybrid composition.
- [ ] Phase 6: conversation migrations, authenticated history/session APIs, bounded memory, idempotency, and isolation tests. Design ownership in Phase 1 even though persistence implementation follows retriever work.
- [ ] Phase 7: FastAPI integration, pooling, input limits, rate limiting, timeouts, errors, and readiness checks; verify current primary documentation when implementing dependencies/security behavior.
- [ ] Phase 8: frontend live integration and history interface after API/auth contracts are verified. Remove preview labels only after live behavior works.
- [ ] Phase 9: end-to-end evaluation, deployment configuration, production smoke tests, and documented limitations.

## 11. Verification and acceptance criteria

- Retrieval evaluation includes known-answer, unanswerable, ambiguous, multilingual, exact-section, and duplicate-table cases. Tune ranking using measured retrieval quality rather than a universal similarity threshold.
- SQL tests cover invented columns, unavailable years, unsafe functions/CTEs/statements, injection, excessive results, timeouts, null/empty results, overlapping totals, and unsupported joins.
- Document tests cover version mismatch, missing evidence, pending chunks, incomplete text, fabricated citations, and prompt injection in source passages.
- Conversation tests cover persistence after return, follow-ups, pagination, token budgets, summaries, cross-user access, admin/user namespace collisions, account switching, concurrent turns, retries, deletion races, and cancellation.
- Response tests ensure no fabricated statistics or legal citations; clarify unsupported coverage and retain provenance.
- Frontend tests cover history loading/resume/delete, mobile usability, stale responses after logout/account changes, service errors, and preview behavior when disconnected.
- Report actual tests/build checks and blockers after each implementation stage. Never claim live readiness from unit tests alone.

## 12. Folder-change and operational rules

- Primary implementation folder: `D:\cc\chatbot`.
- Change `D:\cc\backend` only when a verified authentication/integration requirement needs it; explain the change first.
- Change `D:\cc\frontend` for API connection and conversation-history UI in the later integration stage.
- Tell the user which folders changed, what changed, validation performed, and whether anything was committed, pushed, or deployed after each task.
- Do not expose `.env` values or put private service credentials in frontend variables.
- Do not run migrations, alter production privileges, restart book pipelines, make billable indexing calls, commit, push, or deploy silently. Describe required operational steps and obtain authorization when needed.

## 13. Next execution milestone

Phases 1 and the Phase 2 retrieval baseline are recorded in `READINESS.md` and `RETRIEVAL.md`. Next, design safe query planning and document context assembly with explicit readiness gates. Groq-dependent generation waits for restored credits; never restart book enrichment implicitly. Do not enable unrestricted SQL execution or pretend document coverage is complete.