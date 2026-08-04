from .common import StrippedStr, all_unique_normalized, first_duplicate_id, norm
from .rlc import RlcConfig
from .sentence_fill import SentenceFillConfig

SCHEMAS: dict[str, type] = {
    "practice-rlc": RlcConfig,
    "practice-sentence": SentenceFillConfig,
}

__all__ = [
    "SCHEMAS", "RlcConfig", "SentenceFillConfig", "norm", "all_unique_normalized",
    "StrippedStr", "first_duplicate_id",
]
