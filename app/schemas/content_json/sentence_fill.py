from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import Field, model_validator

from .common import (
    StrictModel, StrippedStr, all_unique_normalized, first_duplicate_id, norm,
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
        bank = {norm(w) for w in self.word_bank}
        if not all(norm(a) in bank for a in self.answers):
            raise ValueError("word_bank must contain every answer")
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
