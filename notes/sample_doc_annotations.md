# Sample Document Annotations

Three documents were reviewed for this analysis: GDPR (Regulation (EU) 2016/679), 
Apple's Supplier Code of Conduct and Supplier Responsibility Standards v5, and 
the RBA Code of Conduct v8.0. These were selected for their structural diversity 
like legal cross-referencing, a bundled corporate code and standards document, and 
an enumerated industry standard, to avoid designing a pipeline that only works 
on one document style.

For each document, a naive fixed-size chunking approach (~400 tokens, no 
structure awareness) was mentally simulated to identify where context would 
be lost.

## 1. GDPR - Regulation (EU) 2016/679

This document is dense with cross-references, which was the primary focus of 
review.

Article 5(1)(b) states that further processing for archiving or research 
purposes "shall, in accordance with Article 89(1), not be considered to be 
incompatible with the initial purposes." Article 89 appears approximately 84 
articles later. A fixed-size chunk containing Article 5 would not contain 
Article 89, meaning the condition governing this exception would be invisible 
to the chunk.

Article 4 defines core terms such as "controller," "processor," "data subject"
once, and these terms are used continuously from Article 5 onward without 
re-explanation. Any chunk beginning at Article 6 assumes the reader already 
has Article 4 in scope, which a naive chunker cannot guarantee.

A compound case appears in Article 6(4)(c), which references both Article 9 
(special categories of data) and Article 10 (criminal convictions) within a 
single sentence, two simultaneous forward references, which represents a 
more demanding case than the single-reference examples above.

**Assessment:** GDPR's primary risk is cross-reference resolution rather than 
structural parsing. The numbering hierarchy (Article > paragraph > point) is 
clean and should parse reliably; the challenge lies in resolving what each 
reference points to.

## 2. Apple Supplier Code of Conduct and Supplier Responsibility Standards (v5)

This PDF contains two linked documents, the Code (pages 3–13) and the 
Standards (pages 14 onward), with the Standards explicitly stated to 
supplement the Code. This introduces a cross-reference type distinct from 
GDPR: references to a named document section rather than a numbered article.

For example, the Foreign Contract Worker protections section (page 29) 
states that contracts must include terms "in addition to the requirements 
specified in the Wages, Benefits, and Contracts Standard", a name-based 
reference rather than a numeric one. Reference-detection logic will need to 
account for named-section references in addition to numbered ones.

Acronym usage presents a further risk. "TPEA" (Third Party Employment Agency) 
and "FCW" (Foreign Contract Worker) are introduced in full early in the 
document and used as bare acronyms in unrelated sections more than 20 pages 
later. This is a longer definition-to-usage gap than either GDPR or RBA 
exhibit, and acronyms are arguably more likely than quoted phrases to be 
misresolved silently by an automated system.

A further observation, separate from chunking: every page of this document 
repeats the full table of contents (approximately 29 section headings) as a 
running header preceding the page's actual content. Left unaddressed, this 
would cause every extracted chunk to be preceded by irrelevant repeated text. 
Whether this is best handled via a text-cleaning pass on raw extraction 
output or at the PDF-parsing library level is still to be determined.

**Assessment:** this document's dominant risk is parser-level noise handling, 
in addition to the extreme-distance acronym definitions described above which is a 
different failure class from GDPR's cross-reference problem.

## 3. RBA Code of Conduct v8.0

This document contains minimal internal cross-referencing, which makes it a 
useful contrast case: its primary risks are structural rather than referential.

The document uses two levels of structure, a lettered section (A. Labor, 
B. Health and Safety, C. Environment, D. Ethics, E. Management Systems) 
containing numbered sub-items (e.g., "1) Prohibition of Forced Labor"). A 
flat chunker has no mechanism for associating a sub-item such as "6) 
Protection of Identity and Non-Retaliation" with its parent section (D. 
Ethics) unless that association is explicitly preserved, which is the 
function the breadcrumb is intended to serve.

The term "Participant" is defined once in the preamble (page 1) and used, 
capitalized, through page 11 (Section E), an 11-page definition-to-usage 
gap, the longest plain-term distance observed across the three documents.

A footnote on page 8 defines "whistleblower," referenced from body text on 
the same page. This presents lower risk than the Apple document's acronym 
case, as it is same-page, but remains a useful test of whether footnote text 
is correctly ordered relative to its anchor point during extraction.

**Assessment:** this document is the strongest test of breadcrumb and 
structural fidelity specifically, since it offers minimal cross-referencing 
to otherwise mask a structural parsing failure.

## Summary

The three documents cover distinct failure classes: GDPR stresses long-range 
cross-reference resolution, the Apple document stresses both extreme-distance 
acronym definitions and raw parsing noise, and the RBA document isolates 
structural/breadcrumb fidelity in the near-absence of cross-referencing. 
Handling all three classes adequately would represent reasonable coverage 
for a proof-of-concept scope. Apple’s repeated header noise has to be stripped during parsing, if it isn’t, it will pollute every stage downstream.