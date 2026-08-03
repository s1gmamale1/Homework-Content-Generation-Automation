from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .common import StrictModel, all_unique_normalized, norm


class SentenceItem(StrictModel):
    id: str = Field(min_length=1)
    # Pass 1 is word_bank ONLY: mobile's normalizeConfigItems() drops `mode` and
    # the component has no TextInput, so free_recall is uncompletable.
    mode: Literal["word_bank"]
    passage: str = Field(min_length=1)
    answers: list[str]
    word_bank: list[str]

    @model_validator(mode="after")
    def _shape(self):
        blanks = self.passage.count("___")
        if not (1 <= blanks <= 6):
            raise ValueError("passage needs 1-6 '___' blanks")
        if len(self.answers) != blanks:
            raise ValueError(f"answers length must equal blank count ({blanks})")
        if any(not a.strip() for a in self.answers):
            raise ValueError("answers must be non-empty")
        if any(not w.strip() for w in self.word_bank):
            raise ValueError("word_bank entries must be non-empty")
        if not all_unique_normalized(self.answers):
            raise ValueError("answers must be normalized-unique (mobile consumes each chip once)")
        if not all_unique_normalized(self.word_bank):
            raise ValueError("word_bank entries must be normalized-unique")
        bank = {norm(w) for w in self.word_bank}
        if not all(norm(a) in bank for a in self.answers):
            raise ValueError("word_bank must contain every answer")
        return self


class SentenceFillConfig(StrictModel):
    SCHEMA_VERSION: str = "sentence_fill_config@1"

    items: list[SentenceItem] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_ids(self):
        if not all_unique_normalized([i.id for i in self.items]):
            raise ValueError("item ids must be unique")
        return self
