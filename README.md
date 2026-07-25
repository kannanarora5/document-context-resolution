# document-context-resolution

POC pipeline for **Document Context Resolution** on compliance PDFs:

`PDF → structured Markdown (+ breadcrumbs) → section chunks → resolve cross-refs/defs → enriched JSON`

## Docs

| Doc | File in `input_docs/` |
|-----|------------------------|
| RBA Code of Conduct v8.0 | `rba_code_of_conduct.pdf` |
| Apple Supplier Code + Responsibility Standards | `apple_suppliercode_responsibility.pdf` |
| GDPR | `GDPR.pdf` |

## Day 2 — parser

Single entrypoint: `src/parser.py` (generic, structure-aware).

```bash
# all PDFs in input_docs/
python3 src/parser.py

# one file, optional breadcrumb label
python3 src/parser.py input_docs/rba_code_of_conduct.pdf
python3 src/parser.py input_docs/GDPR.pdf
```

Markdown lands in `output/markdown/`.

### Numbering / GDPR

`Article N` / `Section N` / `Chapter N` only count as headings when they are the **entire line** (`…\s*$`). Mid-sentence wraps like `Article 51;` or `Article 8(1).` stay paragraphs so breadcrumbs stay on real articles.

GDPR **recitals** `(1)`…`(173)` are indexed as their own units (`^\(\d+\)`), including bare `(N)` lines and missing-space forms like `(15)In order…`. OJ footnote cites (`(1) OJ C …`) stay out of the recital index. Article 4 definitions reuse the same parenthetical unit pattern and **nest under `Article 4`** in the breadcrumb (`Article 4 › (N)`), not as siblings of Article 4.

### Apple Standards pages

Mini table-of-contents blocks (`1. Company Statement` … `12. Documentation` with no body between items) are stripped before breadcrumb attach. Numbered section titles that recur across Standards are **not** treated as repeated running-header noise. Heading text collapses tabs/whitespace; large cover titles wrap with a size-aware y-gap.

### Known limitations (accepted)

- **Tables:** preserved only where PyMuPDF extracts cleanly; no dedicated table model.
- **Apple dual-list TOC chrome:** sidebar TOC lines still rely on the repeated-line noise filter; dual-list Standards pages with ≥2 gutter rows are column-split.

## Day 3 — chunk + resolve

Two generic steps after Markdown:

1. **`src/chunker.py`** — split on headings (not fixed tokens). Keeps breadcrumbs + `context_before`. Oversized sections split on paragraph boundaries (`--max-chars`, default 6000).
2. **`src/resolver.py`** — build a glossary registry + section index from chunks, then attach definitions/cross-refs onto each chunk → enriched JSON for a downstream extraction LLM.

```bash
# chunk all Markdown in output/markdown/
python3 src/chunker.py

# resolve all chunk JSON in output/chunks/
python3 src/resolver.py

# optional: LLM paraphrase / disambiguation (needs OPENAI_API_KEY)
# also: pip install certifi  (helps macOS SSL trust for api.openai.com)
python3 src/resolver.py --llm
```

If `--llm` fails with `CERTIFICATE_VERIFY_FAILED` on macOS (python.org install), run:
`/Applications/Python 3.10/Install Certificates.command`
(adjust the version folder to match your Python), then retry. Do not disable SSL verification.
Outputs:
- `output/chunks/*.json` — section chunks
- `output/enriched/*.json` — same chunks plus `attached_definitions`, `resolved_references`, and compact `glossary` / `section_index`

Resolution policy (matches the design note):
- **Deterministic first** — `Article N` → section index; acronyms/aliased terms → glossary on use; `the <Name> Standard` → TOC/section lookup
- **LLM only when asked** (`--llm`) — shorten long excerpts or pick among ambiguous named-section candidates
- **External refs** (e.g. TFEU Article 263) stay `unresolved` rather than invented

### What Day 3 demonstrates on the three docs

| Doc | Main signal |
|-----|-------------|
| RBA | Breadcrumbs carry hierarchy; `Participant` attaches from the preamble into later sections |
| Apple | `FCW` / `TPEA` attach far from their glossary defs; named Standards resolve when detectable |
| GDPR | `Article 5` chunk gets an `Article 89(1)` excerpt; Article 4 terms (`processing`, `controller`, …) attach where used; `Article 4 › (N)` breadcrumbs keep defs under Article 4 |

### Accepted Day 3 limits

- Apple Standards section titles were often fragmented in Day 2 parsing; named-standard lookup may land on the nearest Code/TOC match (e.g. Code "Wages and Benefits") rather than the full Standards chapter body.
- Apple Wages page (~p.42) can still zigzag in two-column + sidebar-table layouts (known parser limit).
- Glossary harvest is pattern-based (not a full legal NER). Noise terms can appear; attachments are still filterable in eval.
- Without `--llm` / API key, long resolved excerpts are truncated rather than paraphrased.

## Tests

```bash
python3 tests/test_parser_numbering.py
python3 tests/test_chunker.py
python3 tests/test_resolver.py
```
