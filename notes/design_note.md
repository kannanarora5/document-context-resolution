# Design Note

## Problem

Context loss across these three documents isn't one problem, it's three. GDPR loses meaning when a clause points to something numbered dozens of articles away. The Apple document loses meaning when an acronym defined early on shows up unexplained, sections later, in a completely different part of the file. RBA loses meaning when a numbered sub-item gets separated from the lettered section it belongs to. One chunking trick won't fix all three, so this needs structure tracking and terminology tracking working as two separate mechanisms.

## Chunking approach

Chunks split at heading and section boundaries rather than at a fixed token count. Each chunk carries a breadcrumb showing where it sits (e.g. RBA > D. Ethics > 6) Protection of Identity).

Semantic or embedding based chunking was considered and rejected. The problem in these documents isn't unrelated topics getting mixed together, it's connected content getting pulled apart. Section based chunking solves the actual problem here and is simpler to build and verify.

Breadcrumbs alone don't solve terminology drift. RBA's "Participant," defined on page one and still in use on page eleven, makes that clear. So parsing builds two indexes up front:

A **glossary registry**: every defined term or acronym, tied to where it was first defined. A chunk only gets a definition attached if that term actually appears in its text, not the entire registry pasted into every chunk. This keeps things cheap and keeps definitions relevant to what's actually being read.

A **section index**: every numbered article or named section, mapped to its own text, so that a reference to "Article 89" or "the Wages, Benefits, and Contracts Standard" can be looked up directly rather than searched for at resolution time.

## Cross References

With both indexes in place, most references resolve by direct lookup. Find "Article 89(1)" in the text, pull the matching entry from the section index, done. Find "FCW" in a chunk, attach its glossary line, done. This is the higher signal path for a proof of concept, since a matched excerpt is something an evaluator can check directly, rather than trusting that a model summarized correctly.

The LLM only gets involved for the genuinely ambiguous cases: deciding whether a matched excerpt is short enough to inline as is or needs paraphrasing, or judging whether two differently worded references are actually pointing at the same section. That's a much narrower and more honest job for a model than being handed every reference by default.

## Build order

Working through RBA first makes sense, since it has almost no cross referencing and is the cleanest test of whether breadcrumbs and section structure hold up. Apple comes next, since it introduces the glossary registry, acronym tracking, and the repeated table of contents noise on every page, which needs to be stripped during parsing rather than fixed later. GDPR comes last, since its long range article references are the hardest test of the section index once the simpler pieces are already working.

## What this deliberately doesn't solve

References to something outside the document entirely, like a named policy that's mentioned but never included, get flagged rather than resolved, since fetching external documents is out of scope. If the parser fails to keep a footnote attached to its anchor point, nothing downstream can fix that. Structural fidelity has to start at parsing.

Tables will be preserved where the parsing library extracts them cleanly. If a library fights the extraction, that's a known and accepted gap rather than something to force a fix for.

## Pipeline

```
PDF -> parser (extract structure, build glossary registry and section index, strip repeated noise)
    -> markdown converter (attach breadcrumbs)
    -> chunker (split on section boundaries, attach definitions on use)
    -> context resolver (deterministic lookup first, LLM for ambiguous cases)
    -> enriched JSON (ready for the extraction LLM)
```