# Evaluation Notes

Ran the pipeline on all three documents and went looking for where it actually holds up versus where it just looks like it does. The goal here isn't to sell the POC, it's to figure out whether the enrichment step helps someone answer a compliance question better than naive chunking would, and where it doesn't.

**Documents evaluated**

| Document | Enriched file | Chunks | LLM chunks | Model |
|---|---|---|---:|---|
| RBA Code of Conduct | `output/enriched/rba_code_of_conduct.json` | 51 | 50 | `gpt-4o-mini` |
| GDPR | `output/enriched/GDPR.json` | 312 | 307 | `gpt-4o-mini` |
| Apple Supplier Code + Standards | `output/enriched/apple_suppliercode_responsibility.json` | 397 | 347 | `gpt-4o-mini` |

Totals below are pulled straight from each file's `totals` block via `scripts/print_enriched_totals.py` (same method for all three).

| Doc | Defs | Refs | Resolved | Unresolved | Ambiguous | Est. cost |
|---|---:|---:|---:|---:|---:|---:|
| RBA | 53 | 7 | 6 | 1 | 0 | ~$0.05 |
| GDPR | 536 | 600 | 562 | 38 | 0 | ~$0.93 |
| Apple | 866 | 232 | 168 | 64 | 0 | ~$1.95 |

## How I checked this

I ran the pipeline end to end on all three PDFs, then looked at the aggregate totals in each enriched JSON, spot-checked the compliance questions I actually care about (recruitment fees, UNGPs, GDPR Art. 6, FCW/TPEA), went back through the hard spots I'd already flagged in my notes, and compared the enriched output against the same document chunked naively with no resolution step.

What I didn't do, and why it matters: I don't have gold labels for all 232 Apple references or the 600 in GDPR, so a resolve rate isn't an accuracy rate, a wrong resolve still counts as "resolved" in the aggregate numbers. I also didn't do inter-annotator agreement (whether a target is "correct" is sometimes genuinely a judgment call, e.g. Code vs. Standards siblings covering the same topic), didn't run any retrieval metrics like recall@k since the brief is asking about Q&A usefulness rather than a full RAG benchmark, and didn't get a second person to blind-check my calls, so the audit below is author-biased by construction.

Bottom line on method: treat resolve rates as a coverage signal, not a correctness signal. The accuracy claims that follow come from a targeted audit log, not exhaustive labeling.

## What "good" looks like here

The brief is really asking whether this helps someone answer a compliance question better than naive chunking would. I think that splits into three different bars: can the demo show a clear before/after with honest failures, would an analyst actually trust the attachments without double-checking the source PDF, and is this safe to auto-answer from without a human in the loop. Evaluation should clear the first bar, show real progress on the second, and be upfront that it hasn't hit the third. Calling this production-ready would be the wrong call.

## Is each chunk actually self-contained?

Mostly yes, for the same reason the breadcrumbs work, chunking on heading boundaries means a chunk's content and its section label match, so someone reading `GDPR › Article 4 › (1)` immediately knows what they're looking at even without surrounding pages. Where it falls short: heading-only chunks with no body text of their own (a heading like "Article 4" that's immediately followed by numbered sub-definitions) carry structure but not much standalone content, they're not wrong, just thin. And the multi-part splits on oversized sections (long GDPR clauses, Apple's longer Standards) mean part 2 or 3 of a section can read a little disconnected from its own heading if you land on it without the earlier parts, since the heading title itself only appears once, in part 1. Neither of these breaks anything downstream, but they're the honest edge cases for "does every chunk stand alone."

## Cross-reference resolution

**What I'm testing:** does a mention of another section resolve to the actual correct target, or correctly stay unresolved when it's genuinely external?

Coverage numbers:

| Doc | Refs | Resolved | Unresolved | Ambiguous | Resolved rate |
|---|---:|---:|---:|---:|---:|
| RBA | 7 | 6 | 1 | 0 | 85.7% |
| GDPR | 600 | 562 | 38 | 0 | 93.7% |
| Apple | 232 | 168 | 64 | 0 | 72.4% |

The GDPR rate looks strong mostly because article numbers are unambiguous - "Article 6" and "Article 89" don't collide the way section titles can. That's an easy win for the catalog lookup, not evidence the hard cases are solved. Apple's lower rate is actually more informative than it looks: a lot of what's staying unresolved is genuinely external, OECD Guidelines, the ILO Declaration, Apple's own Privacy Policy, and correctly refusing to resolve those is good behavior, not a gap.

One thing worth calling out on its own: `ambiguous_count` is 0 across the board, and I don't think that's a clean bill of health. The schema gives the model an "ambiguous" option, and the hardest cases below (deictic references like "this Standard," and twin Code/Standards sections covering the same topic) are exactly where I'd want the model to say "not sure" instead of guessing. It never does. That suggests the prompt isn't really giving it permission or incentive to land there, it's biased toward looking decided rather than staying honestly unresolved when it's actually unsure.

### Targeted accuracy check on Apple

Apple's the highest-risk document for this failure mode, so I went through 14 individual reference judgments by hand.

| # | Class | Chunk / mention | Outcome |
|---|---|---|---|
| 1 | Named unique | `…-0004` UNGPs → References › UNGPs | Correct |
| 2 | Named unique | Code/Standards cross-link (spot check) | Correct |
| 3 | Named unique | FCW / TPEA named Standard targets in fee-related chunks | Correct |
| 4 | Named unique | Glossary-backed heading (Base Wage / FCW / TPEA family) | Correct |
| 5 | External | OECD Guidelines | Correct — unresolved |
| 6 | External | ILO Declaration | Correct — unresolved |
| 7 | External | Apple Privacy Policy | Correct — unresolved |
| 8 | Repeated heading | "1. Regulatory Permits" family, post-fix | OK - no wrong first-match, empty + note |
| 9 | Deictic | TPEA `…-0109` "Section 1 of this Standard" -> landed on Code `…-0005` | Wrong - should be TPEA `…-0099` |
| 10 | Twin name | "Wages, Benefits, and Contracts Standard" -> landed on Code `1.7` | Wrong - should be Standards `…-0166` |
| 11 | Soft/thematic | Materials `…-0030`, "Relevant Materials" -> landed on title chunk `…-0001` | Wrong |
| 12 | Soft/thematic | Materials `…-0030`, "Salient Issues" -> same title chunk | Wrong |
| 13 | Self/circular | `…-0007` "References section below" -> resolved to itself | Wrong |
| 14 | External marked local | `…-0010` ILO Convention articles -> local Child Labor chunk | Wrong |

8 of 14 correct. But the more useful way to read this: every case in the "easy" bucket, named unique targets and clearly external mentions, 7 of 7, came back right. Every case in the "hard" bucket, deixis, twin names, soft/thematic references, self-resolution, 6 of 6, came back wrong. That's not really a 57% accuracy score, it's a finding about where the failures cluster: the system is fine on the easy stuff and reliably wrong on anything that needs local context or judgment about what "this" refers to.

The failures split into two severities worth treating differently. Rows 9–12 are the serious ones, a wrong in-document target that reads as confident, which could genuinely mislead a downstream extraction LLM into pulling the wrong clause. Rows 13–14 are more of a nuisance than a danger, a chunk resolving to itself, or an external instrument getting treated as if it were answered locally, annoying, pollutes the output with false confidence, but less likely to cause a wrong extraction than 9–12.

**Where this lands:** conditional pass for a POC. Clean, uniquely-named references work. Anything relying on "this Standard" or distinguishing a Code section from its Standards twin doesn't, yet. I wouldn't describe cross-reference resolution as accurate without that qualifier attached.

## Definition attachment

Formal glossary terms and acronyms (FCW, TPEA, Base Wage) attach well and are usually grounded in something real nearby. Local formal definitions, recruitment fees defined in the Forced Labor standard, Applicable Laws in the Code, are useful too. Where it falls apart is common nouns that happen to look defined: "Supplier," "Apple," "Workers" get tagged almost everywhere they appear. 866 definition attachments on Apple alone and 536 on GDPR are both signs of over-recall, not thoroughness. And the definitions themselves are a mixed bag, some are faithful quotes from the source, some are the model's own paraphrase that sounds right but isn't something I'd want to hand to an extraction step without checking.

For a compliance use case I think precision matters a lot more than recall here, a downstream model that gets fed hundreds of noisy attachments per document either learns to ignore them or, worse, trusts a paraphrase that drifted from the actual defined term. Pass on the high-value terms, fail on precision overall.

## Breadcrumbs and structure

This is the part of the system I'd stand behind without much hedging. Breadcrumbs show up on essentially every chunk, the Code-vs-Standards paths stay distinguishable from each other, and the multi-part splits on oversized sections stay readable with their `part`/`context_before` fields. The one piece of real debt: the section catalog mostly only indexes part 1 of a multi-part section, so later parts of something like the Glossary are harder for the resolver to target directly. Structurally this is the clearest, most reliable win over naive chunking, and it's also what makes the failures above diagnosable in the first place, you can actually see when a resolve jumped to the wrong breadcrumb family instead of just getting a bad answer with no way to tell why.

## Does this actually help answer compliance questions?

This is the real test, so I ran it against concrete questions rather than abstract metrics:

| Question | Naive chunking | Enriched | Better | Confidence |
|---|---|---|---|---|
| What do the UNGPs require, as cited by Apple? | Mention only, no substance | Resolved excerpt from References | Enriched | High |
| Can workers be charged recruitment fees, and what fees? | Usually only one of Code / Forced Labor / FCW | Related definitions + Standards links cover more ground | Enriched | Medium–high |
| GDPR: Art. 9 points to Art. 6(1) — what does 6(1) say? | Incomplete | Cross-ref resolved with excerpt | Enriched | High |
| Apple wages rounding / tardiness math | Table text is already broken | Still broken - attachments can't fix a bad parse | Neither | High |
| What are "Relevant Materials" under Materials Due Diligence? | Incomplete but not misleading | Wrong title-chunk attach | Naive is actually safer here | High |

Enrichment clearly helps when the parse is clean and the reference has a unique name. It can actively hurt when the resolve is wrong but confident, and that asymmetry is the thing I keep coming back to: a silent wrong attach does more damage than an honest gap, because the gap at least signals "go check the source" while the wrong attach doesn't. Overall: passes for the demo scenarios, but I wouldn't claim it's unconditionally better than naive chunking yet.

## Before / after examples

**UNGPs (`apple_…-0004`) — a real win.** Naive chunking leaves you with "due diligence processes set out in the UNGPs..." and nothing else. The enriched version resolves this to the References section entry for the UNGPs, with the actual policy commitment, due diligence, and remediation language attached. One chunk answers a question that used to require digging through the PDF.

**GDPR Article 6(1), referenced from a later article.** Naive chunking gives you a bare pointer. The enriched version pulls the actual article text. This is the long-range legal cross-reference the assignment specifically cares about, and it works.

**Materials Due Diligence (`…-0030`), a real failure.** "Relevant Materials" and "Salient Issues" both resolve to the document's title chunk instead of anything meaningful. Here, unresolved would have been the better outcome, at least it wouldn't mislead anyone.

**"Section 1 of this Standard" (`…-0109`), a real failure.** Meant to point at TPEA Standard §1 (`…-0099`); instead resolves to Code Labor (`…-0005`). Root cause: nothing in the system currently prefers "whatever Standard this chunk already belongs to" when resolving a purely local, deictic reference, it's picking from the full flat catalog with no locality bias.

## Failure taxonomy

| ID | Failure | Where it lives | Fix direction | Priority |
|---|---|---|---|---|
| F1 | Wrong twin (Code vs. Standard, same topic) | Resolver prompt / catalog ranking | Prefer same breadcrumb family, weight the "Standard" token in the heading | High |
| F2 | Deictic "this Standard / this section" | No locality prior at all | Constrain candidates to the current Standards parent | High |
| F3 | Soft/thematic term resolving to a title chunk | Weak match accepted with no sanity check | Reject title/root chunks as valid targets; require real content overlap | High |
| F4 | External instrument marked resolved locally | Model too eager to resolve | Force unresolved for known external patterns (ILO Convention, OECD, etc.) | Medium |
| F5 | Self-referential resolve | No rule enforcing target ≠ source | Post-filter that drops any self-resolve | Medium |
| F6 | Definition over-tagging | Prompt rewards recall over precision | Denylist common nouns, require an actual definitional cue, prefer glossary-sourced terms | Medium |
| F7 | Table/column scrambling | Parser layout inference, not the resolver | Table-aware extraction — separate workstream, out of resolver's scope | High for wages questions specifically |
| F8 | Catalog only indexes part 1 of multi-part sections | Chunker/catalog construction | Index every part, or merge into one catalog entry with all parts referenced | Low–medium |
| F9 | Full catalog sent on every LLM call | Architecture | Retrieve top-k catalog entries instead of the whole thing; pre-resolve numbered refs deterministically first | High at scale |

## Scorecard

| Criterion | Grade | Why |
|---|---|---|
| Cross-ref accuracy | B- | Perfect on the easy slice of the audit (7/7), zero for zero on the hard slice (0/6); wrong attaches are high-severity when they happen |
| Definition correctness | C+ | Strong on the terms that actually matter; way too much noise overall |
| Breadcrumbs / structure | A- | The most reliable part of the system, small gap on multi-part catalog indexing |
| Q&A usefulness vs. naive | B | Clear wins, occasional cases where enrichment actively misleads |
| Honesty of this evaluation | A | Failures are named with actual chunk IDs, sample sizes, and root causes |
| Production readiness | D | Cost, confident wrong resolves, and parser gaps all still open |

**Verdict: pass for a POC.** The system demonstrates the right decomposition of the problem, structure, terminology, and resolution as separate concerns, and shows real, measurable wins. But the accuracy claims need to stay scoped to what was actually checked. If there's one finding to lead with, it's this: a wrong resolve does more damage than no resolve at all, and right now the system's metrics reward resolving something over admitting it isn't sure, the `ambiguous_count: 0` across all three documents is the clearest evidence of that.

## What I'd fix next

1. Hard post-filters: never let a reference resolve to itself, never let it resolve to the document's own title/root chunk, and prefer unresolved over a low-confidence guess.
2. Give resolution a locality bias, for "this Standard" or "this section," rank candidates that share the current breadcrumb prefix before anything else.
3. Add the deterministic pre-pass the original design called for, direct lookup for `Article N`, `Section X`, exact Standard titles, and only fall back to the LLM when that lookup genuinely misses.
4. Make the `ambiguous` status actually usable, reward abstaining on deixis and twin-name cases instead of treating a confident wrong guess as no worse than an honest "not sure."
5. Tighten definition attachment: glossary-sourced terms first, denylist the common nouns, require an actual definitional cue in the source text.
6. Treat the table/column parsing problem as its own workstream so it doesn't unfairly drag down the resolver's grade.
7. Build a small stratified gold set, 30 to 50 Apple references, hand-labeled, to replace this convenience sample with a real accuracy number.