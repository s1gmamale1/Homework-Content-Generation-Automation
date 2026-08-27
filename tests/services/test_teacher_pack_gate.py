"""Unit tests for the deterministic teacher-pack coverage gate.

Fixtures are condensed from real grade-9 canary packets (2026-08-27 loop):
the same shapes the parsers must survive in production.
"""

from app.services import teacher_pack_gate as gate

MC = """# Memory Check

O'tish bali: 0.60

### multiple_choice — card 1
Which system warms rooms?
A) heating
B) lighting
C) air conditioning
D) washing machine

**To'g'ri javob:** A
Noto'g'ri (B): Lighting illuminates.
Noto'g'ri (C): AC cools.
Noto'g'ri (D): Washes clothes.

### fill_blank — card 5
We use 'will' when we are _____.

**Kutilayotgan javob:** certain

### choose_correct_explanation — card 6
Why do we use may and might?
A) obligations
B) guarantees
C) tentative possibilities
D) past reports

**To'g'ri javob:** C
"""

CBP = """Case type: Storytelling

## 2. Checkpoint 1 — Identify
Which choice fits?

A) will become
B) should not become
C) might become
D) may become

**To'g'ri javob:** A

## 4. Checkpoint 2 — Decide
Which question form is standard?

A) May our homes have
B) Might our homes have
C) Will our homes have
D) Are our homes having

**To'g'ri javob:** C
"""

RLC = """# Real-Life Challenge

### Step 1 — Decision (kind: decision)

- A) Use will for proven systems, might for prototypes. (Correct)
- B) Use may for proven systems.
- C) Use might not for everything.
- D) Prohibit modals.

### Step 2 — Governing concept (kind: concept_select)

- Modals of prediction: will for certainty, might for possibility (Correct)
- Past simple narrative tense for archives
- Imperative commands for manuals
"""

SENT = """# Sentence

## Choices
- will (To'g'ri)
- might
- must

## Why prompt
Explain.
"""

ED = """## The blocks
1. Engineers are certain,
2. so heating
3. might reduce waste,

## The correct version
Block 3 is broken. The correct word is **will**.
"""

PRIORS = {
    "memory-check": MC,
    "case-based-preview": CBP,
    "practice-rlc": RLC,
    "practice-sentence": SENT,
    "practice-error-detection": ED,
}

DECK_COMPLETE = """## 12. Mistake: Will Without Proof

x wrong / Right: fixed

<!-- QA-WHERE: Memory Check Card 1: B, C, D; Case Preview Checkpoint 1: B, C, D; Real-Life Challenge Step 1: B, C, D -->

## 13. Mistake: May in Questions

<!-- QA-WHERE: Case Preview Checkpoint 2: A, B, D; Memory Check Card 6: A, B, D; Sentence Practice: might, must; Error Detection: Block 3 -->

## 14. Mistake: Extra Facts

<!-- QA-WHERE: Real-Life Challenge Step 2: Past simple narrative tense for archives, Imperative commands for manuals -->
"""

DECK_GAPPY = """## 12. Mistake: Will Without Proof

<!-- QA-WHERE: Memory Check Card 1: B, C, A; Case Preview Checkpoint 1: B, C -->

## 13. Mistake: May in Questions

<!-- QA-WHERE: Case Preview Checkpoint 2: A, B, D; Sentence Practice: might, will -->
"""

DECK_NO_COMMENTS = """## 12. Mistake: Something

no comments at all
"""


def test_complete_deck_passes():
    r = gate.check(DECK_COMPLETE, PRIORS)
    assert r.passed, (r.missing, r.bogus, r.notes)
    assert r.missing == [] and r.bogus == []
    # 3 MC card1 wrongs + 3 card6 wrongs + 3 CP1 + 3 CP2 + 3 RLC step1
    # + 2 chips + 2 sentence + 1 ED block
    assert r.declared_count == 20
    assert r.cited_count == 20


def test_gaps_and_bogus_detected():
    r = gate.check(DECK_GAPPY, PRIORS)
    assert not r.passed
    joined = " | ".join(r.missing)
    # card 6 never cited; CP1 option D missing; RLC step 1 + chips + ED missing
    assert "Memory Check card 6" in joined
    assert "Case Preview checkpoint 1: option D" in joined
    assert "Real-Life Challenge step 1" in joined
    assert "Error Detection: broken block 3" in joined
    # bogus: MC card1 cited A (the key); sentence cited 'will' (the key)
    bj = " | ".join(r.bogus)
    assert "Memory Check card 1: option A" in bj
    assert "Sentence Practice: choice 'will'" in bj
    # feedback carries both sections
    assert "MISSING" in r.feedback and "BOGUS" in r.feedback


def test_no_comments_fails_loudly():
    r = gate.check(DECK_NO_COMMENTS, PRIORS)
    assert not r.passed
    assert "no QA-WHERE comments" in r.missing[0]


def test_unparseable_priors_fail_open():
    r = gate.check(DECK_COMPLETE, {"memory-check": "garbage", "practice-rlc": ""})
    assert r.passed  # nothing declared → nothing missing


def test_never_raises_on_garbage_deck():
    r = gate.check("\x00\x01 not markdown <!-- QA-WHERE: ???: -->", PRIORS)
    assert isinstance(r.passed, bool)
