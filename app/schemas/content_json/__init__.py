from .common import all_unique_normalized, norm
from .rlc import RlcConfig
from .sentence_fill import SentenceFillConfig
from .teacher_deck import TeacherDeck

SCHEMAS: dict[str, type] = {
    "practice-rlc": RlcConfig,
    "practice-sentence": SentenceFillConfig,
    "teacher-deck": TeacherDeck,
}

__all__ = [
    "SCHEMAS", "RlcConfig", "SentenceFillConfig", "TeacherDeck", "norm", "all_unique_normalized",
]
