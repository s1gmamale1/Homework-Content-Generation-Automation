from .common import all_unique_normalized, norm
from .rlc import RlcConfig
from .sentence_fill import SentenceFillConfig

SCHEMAS: dict[str, type] = {
    "practice-rlc": RlcConfig,
    "practice-sentence": SentenceFillConfig,
}

__all__ = [
    "SCHEMAS", "RlcConfig", "SentenceFillConfig", "norm", "all_unique_normalized",
]
