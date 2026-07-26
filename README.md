# Document Context Resolution

TechForce AIML intern assignment. Makes compliance PDF sections more self-contained by resolving cross-references and tagging defined terms.

## Pipeline

```text
PDF -> Markdown (+ breadcrumbs) -> section chunks -> LLM resolve -> enriched JSON
```

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Set an API key before running the resolver:

```bash
export OPENAI_API_KEY=...
```

## Run

```bash
python src/parser.py
python src/chunker.py
python src/resolver.py

# optional
python src/resolver.py --limit 3
python scripts/print_enriched_totals.py
```

## Layout

| Path | What |
|------|------|
| `input_docs/` | Sample PDFs (Apple, GDPR, RBA) |
| `src/parser.py` | PDF -> structured Markdown with breadcrumbs |
| `src/chunker.py` | Markdown -> section chunks JSON |
| `src/resolver.py` | LLM resolves cross-refs + tags definitions |
| `scripts/print_enriched_totals.py` | Summarize enriched JSON totals |
| `output/markdown/` | Parsed Markdown |
| `output/chunks/` | Chunk JSON |
| `output/enriched/` | Enriched JSON |
| `notes/` | Design notes, annotations, evaluation |
| `requirements.txt` | Python deps (`pymupdf`) |

## Notes

- Resolver uses OpenAI (`OPENAI_API_KEY`). Anthropic is also supported if `ANTHROPIC_API_KEY` is set.
- Keep API keys out of git (`.env` is gitignored).