# RAG Evaluation for WhatsApp Orders Agent

## Context

The system has two parts:
- **This backend** (`invproc`): Processes supplier invoices (PDF → structured JSON) and stores products (name, code, UOM, price) in Supabase.
- **React frontend + WhatsApp agent** (separate repo): A conversational agent taking customer orders via WhatsApp. In active development.

The question is whether adding RAG to the WhatsApp agent would improve conversation quality.

---

## What RAG Would Retrieve

For an orders agent, the corpus would be:
1. **Product catalog** — extracted by this backend, already in Supabase (name, code, price, UOM, category)
2. **Customer order history** — past orders per customer
3. **Business rules / FAQ** — minimum quantities, pricing tiers, supplier notes

---

## Pros

| # | Benefit | Impact |
|---|---------|--------|
| 1 | **Product grounding** — LLM retrieves exact names/codes instead of hallucinating | High |
| 2 | **Price accuracy** — retrieved invoice prices prevent price hallucinations | High |
| 3 | **Order history context** — enables "repeat last order" / "what did I order from METRO?" | Medium |
| 4 | **Scales with catalog size** — no prompt-bloat as SKU count grows | Medium |
| 5 | **Supabase pgvector is already in the stack** — zero new infra, just enable the extension | High (cost) |
| 6 | **Reuses backend output** — invoice extraction already produces a clean RAG corpus | High |
| 7 | **Reduces ambiguity resolution turns** — agent finds the right product faster | Medium |

## Cons

| # | Risk | Impact |
|---|------|--------|
| 1 | **Added latency** — retrieval adds ~100–400 ms per conversation turn | Medium |
| 2 | **Retrieval quality risk** — poor chunking/embedding = worse than no RAG | Medium |
| 3 | **Embedding costs** — OpenAI `text-embedding-3-small` ~$0.02/1M tokens; catalog re-indexing on every invoice import | Low |
| 4 | **Sync complexity** — vector store must stay in sync with Supabase product table | Medium |
| 5 | **Overkill for small catalogs** — if catalog < ~200 SKUs, a system-prompt listing suffices | Depends |
| 6 | **Chunking decisions** — product records are short; wrong chunking loses context | Low-Med |

---

## Key Decision Factor

**Catalog size:**
- < ~200 SKUs → include in system prompt directly (simpler, lower latency)
- > ~200 SKUs → RAG is clearly worthwhile

Given METRO Cash & Carry invoices (large wholesale supplier), the catalog likely grows to thousands of SKUs. **RAG is the right call.**

---

## Recommended Approach (if approved)

1. **Vector store**: Enable `pgvector` in Supabase (already in stack). Create an `embeddings` table alongside the existing products table.
2. **Embedding model**: `text-embedding-3-small` (OpenAI, cheap, good quality).
3. **Corpus**: Each product row from the processed invoices becomes one vector. Embed: `"{name} {code} {category} {uom}"`.
4. **Trigger**: Re-embed on invoice import (post-extract webhook or Supabase trigger).
5. **Retrieval**: Top-5 products by cosine similarity to the WhatsApp message.
6. **Integration point**: WhatsApp agent prepends retrieved products as grounded context before calling the LLM.

### Architecture sketch
```
WhatsApp message
      │
      ▼
Embed query (text-embedding-3-small)
      │
      ▼
pgvector similarity search (Supabase)  ←── product catalog (from invproc)
      │
      ▼
Top-K products injected into LLM context
      │
      ▼
LLM generates order response (grounded)
```

---

## Verdict

**Yes — add RAG.** The stack already has everything needed (Supabase pgvector, OpenAI). The invoice backend already produces a clean product corpus. The main downside (sync complexity) is manageable with a simple Supabase trigger or post-import hook. The upside — eliminating product/price hallucinations — directly improves the agent's core job.

---

## Files in This Repo Relevant to RAG Integration

- `src/invproc/models.py` — `Product` model (RAG corpus schema)
- `src/invproc/import_service.py` — import pipeline (hook point for triggering re-embedding)
- `src/invproc/api.py` — `/extract` endpoint (could emit product embeddings post-extract)
- `src/invproc/config.py` — where embedding model config would live

No code changes required in this repo immediately. The embedding/retrieval logic lives in the React/agent side. This backend only needs a post-import webhook or Supabase trigger to keep the vector store fresh.
