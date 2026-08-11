# Teacher Material Deck — fidelity contract (judge-only, via `contract_override`)

This is NOT an authoring prompt. It is the CONTRACT the judge uses to grade an
already-generated teacher deck. It is never reachable through the normal phase-name
prompt lookup (`get_prompt`) — it is passed explicitly as `contract_override` because
the deck itself is JSON, not the judge's usual markdown contract.

## What you are grading

You will be shown a **serialized PLAIN-TEXT view of the deck** (its stages, quiz, answer
key, pair work, conclusion, and rubric flattened to readable text), NOT the raw JSON. Grade
that plain-text serialization against the LESSON CONTEXT for factual fidelity. Do not comment
on JSON formatting, key names, or schema shape at all — that is validated elsewhere, before
you ever see this output. **Never require or expect JSON output from this review; you are
reading and grading plain text, and nothing in this contract should be read as demanding a
JSON response from the generator.**

## The one thing that matters: contradiction vs absence

- Raise a `major` failure ONLY when a claim in the deck **CONTRADICTS** the LESSON CONTEXT —
  a changed date, a changed number, a changed name, a swapped fact. Quote the deck's claim
  and the contradicting context.
- A fact that is simply **absent** from the LESSON CONTEXT but not contradicted by it (a
  reasonable elaboration, a standard curriculum fact, connective framing) is at most `minor`
  — never `major`, never a reason to regenerate.

## Teaching/structure numbers are NOT defects

The deck's own **teaching and structural numbers are not facts about the world** and must
never be flagged, at any severity, even if they don't appear verbatim in the LESSON CONTEXT:
stage **timings** (3/3/9/9/8/9/4 minutes), the **option counts** on quiz questions (4 options
each), rubric **points** (5 kviz + 3 amaliy + 2 faollik = 10), the number of pair-work tasks,
or any other number the author chose to shape the lesson plan. Only flag a number when it
claims something ABOUT THE LESSON'S SUBJECT MATTER (a historical date, a measured quantity, a
count of real things) and that claim contradicts the LESSON CONTEXT.

## Also not defects

- Invented but clearly-fictional practice content (a made-up example scenario for the pair
  work) is expected authoring, not a fidelity violation.
- The stage-3 video observation task naming specific names/dates from the lesson is REQUIRED
  by the authoring contract — do not flag it as "inventing" facts when those names/dates are
  genuinely present in the LESSON CONTEXT; only flag it if it names something that is NOT in
  the LESSON CONTEXT and could be mistaken for a source fact.

## Severity

- `major`: a claim in the deck about the world (a date, a number, a name, a definition, a
  rule, a causal claim) that directly contradicts the LESSON CONTEXT.
- `minor`: everything else worth noting — an absent-but-uncontradicted fact, a stylistic
  issue, a claim you cannot verify either way.

Be conservative. When in doubt between `major` and `minor`, or between flagging and not
flagging a teaching/structure number, do not flag it as `major`.
