# Document Context Resolution

Proof-of-concept for the TechForce AI/ML Intern Assignment: reduce context loss
before a downstream compliance extraction LLM by producing a structured,
context-resolved intermediate file.

## Pipeline (per assignment)

```
PDF → parser (structure + breadcrumbs)
    → Markdown
    → section chunker (breadcrumb + surrounding context)
    → LLM context resolver (cross-refs + defined terms only)
    → enriched JSON (for the extraction LLM)
```

## Setup

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=...   # preferred (assignment suggests Claude)
# or
export OPENAI_API_KEY=...
```

## Run

```bash
# Day 2 — PDF → structured Markdown
python src/parser.py

# Day 3 — section chunks
python src/chunker.py

# Day 3 — LLM resolves cross-refs / tags definitions on each chunk
python src/resolver.py

# Smoke test (first N chunks only)
python src/resolver.py output/chunks/rba_code_of_conduct.json --limit 5
```

Outputs:

- `output/markdown/*.md`
- `output/chunks/*.json`
- `output/enriched/*.json`

## What the resolver does

For each chunk it sends **breadcrumb + `context_before` + chunk text**, plus a
compact **section catalog** of the same document (so long-range refs like
GDPR Article 89 can be resolved). The LLM’s only job is:

- resolve cross-references (with short excerpts)
- tag defined terms / acronyms used far from their definitions

It does **not** extract risks, controls, or obligations.
