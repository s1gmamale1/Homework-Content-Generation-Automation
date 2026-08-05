from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import Field, model_validator

from .common import (
    StrictModel, StrippedStr, all_unique_normalized, first_duplicate_id,
)


class SentenceItem(StrictModel):
    id: StrippedStr
    # Pass 1 is word_bank ONLY: mobile's normalizeConfigItems() drops `mode` and
    # the component has no TextInput, so free_recall is uncompletable.
    mode: Literal["word_bank"]
    passage: str = Field(min_length=1)
    # StrippedStr, so a blank/whitespace chip can never reach the word bank —
    # it would render as an untappable empty chip.
    answers: list[StrippedStr]
    word_bank: list[StrippedStr]

    @model_validator(mode="after")
    def _shape(self):
        blanks = self.passage.count("___")
        if not (1 <= blanks <= 6):
            raise ValueError("passage needs 1-6 '___' blanks")
        if len(self.answers) != blanks:
            raise ValueError(f"answers length must equal blank count ({blanks})")
        # Non-emptiness is enforced by StrippedStr on the field types above.
        if not all_unique_normalized(self.answers):
            raise ValueError("answers must be normalized-unique (mobile consumes each chip once)")
        if not all_unique_normalized(self.word_bank):
            raise ValueError("word_bank entries must be normalized-unique")
        # EXACT membership, deliberately not normalized: the platform validator
        # does `all(a in bank for a in answers)` on the raw strings, so a
        # case-differing answer would pass here and be REJECTED at publish. The
        # prompt already requires every answer "verbatim"; this enforces it.
        # (Uniqueness stays normalized — mobile consumes chips by normalized
        # value, so two chips differing only in case are one chip to a student.)
        missing = [a for a in self.answers if a not in self.word_bank]
        if missing:
            raise ValueError(
                f"word_bank must contain every answer VERBATIM; missing: {missing}"
            )
        # Redaction pops `answers` and ships `word_bank` as the chips, so a bank
        # with no distractor hands the student exactly the right words — a
        # one-blank item becomes a single tappable chip that cannot be failed.
        if len(self.word_bank) <= len(self.answers):
            raise ValueError(
                "word_bank needs at least one distractor beyond the answers "
                f"(got {len(self.word_bank)} chips for {len(self.answers)} blanks)"
            )
        return self


class SentenceFillConfig(StrictModel):
    # ClassVar, NOT a field — see rlc.py.
    SCHEMA_VERSION: ClassVar[str] = "sentence_fill_config@1"

    items: list[SentenceItem] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_ids(self):
        dup = first_duplicate_id([i.id for i in self.items])
        if dup is not None:
            raise ValueError(f"duplicate item id '{dup}' — item ids must be unique")
        return self
