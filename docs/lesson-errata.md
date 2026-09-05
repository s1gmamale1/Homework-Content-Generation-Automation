# Reviewed lesson errata

## Plan amendment (2026-09-05)

Task 3 includes the verified Russian grade-7 technology textbook defect below,
in addition to the history correction. The controller approved canonical reviewed
extracts keyed by the exact `(subject, TOC entry UUID)` pair. These two summaries
are standardized from the observed original extracts: arbitrary generated
paraphrases cannot reintroduce the errors through a missed sentence regex.
All other lessons retain their input byte for byte. These are source-language
primers; downstream Uzbek/Russian/English output selection remains independent.

## History: ancient trade routes

- Subject: `history`; TOC entry: `768820b7-54ea-45d2-bbb4-d95275ef95e6`.
- Preserve the school answer Sian; remove the claim that it stands on the Yellow
  River. [Smithsonian Xi'an geography](https://festival.si.edu/2002/the-silk-road/xian-geography-and-history/smithsonian)
  places it in the Wei valley.
- Retain the school route name La’l yo‘li and its precious-stone trade, but omit
  the la’l/lojuvard synonym claim. The Uzbek definitions distinguish
  [la’l](https://izoh.uz/word/la%CA%BCl) and [lojuvard](https://izoh.uz/word/lojuvard).
- Preserve the textbook's two Royal Road directions explicitly, without adding
  an expansion chronology. [Encyclopaedia Iranica on Achaemenid commerce](https://www.iranicaonline.org/articles/commerce-ii/)
  distinguishes the royal road and eastern connections.
- Attribute the 12,000 km / seventeen-century figures to the textbook and retain
  the second-century BCE school date; do not add uninterrupted operation.
  [UNESCO's Chang’an–Tianshan corridor](https://whc.unesco.org/en/list/1442)
  describes a changing network, rather than one invariant road.
- Retain definitions, comparison activities, route endpoints, Doro I, and the
  explanation of the Silk Road name. Do not manufacture ancient testimony.

## Technology: crop rotation

- Subject: `texnologiya`; TOC entry: `d93f33a7-8120-4895-bc51-d2055c8ef7d4`.
- Russian grade 7, book `436a0895-621d-4b22-8562-448aa4556ac2`, page 158.
- The textbook itself says alfalfa cultivation increases soil sand content.
  Remove only that unsupported claim; retain 2–3 years, organic residues,
  restored soil structure, school definitions and other lesson facts. Do not
  guess which Russian word the publisher intended.
- University of Minnesota Extension describes alfalfa supplying organic matter
  in [Forage legumes](https://extension.umn.edu/agriculture/crop-production/forages/forage-legumes),
  improved structure and tilth in [Adding alfalfa benefits corn-soybean rotations](https://extension.umn.edu/agriculture/crop-production/corn/adding-alfalfa-benefits-corn-soybean-rotations),
  and distinguishes mineral texture from structure/organic matter in
  [Reducing tillage intensity](https://extension.umn.edu/natural-resources/conservation/agricultural-soil-and-water/reducing-tillage-intensity).

## Audit and maintenance contract

Original extracts are retained as test fixtures. Canonical text is maintained in
`app/services/lesson_errata.py`. The citations above are maintainer evidence,
not student-facing technical metadata. Fresh accepted extracts and cross-job
cache returns are corrected before persistence. A changed resumed extract and
all existing downstream outputs are reset together through fenced repository
operations; the corrected primer is persisted in that same transaction. The
normal scheduler then regenerates downstream phases. An unchanged resume keeps
its completed phases. No global extract hash bump is needed: all three entry
paths apply this idempotent correction before use.

This registry is deliberately finite, not a general fact checker. A revised
source or expanded lesson requires refreshing and reviewing its canonical text
and fixtures; new information in stochastic summaries for these two identities
is not silently incorporated. Existing frozen/published artifacts are not
rewritten. Semantic model quality is outside these deterministic tests.
