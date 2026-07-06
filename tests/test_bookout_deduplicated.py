"""BookOut.deduplicated defaults False and is set True only on the dedup path."""
from uuid import uuid4

from app.schemas import BookOut


def test_deduplicated_defaults_false():
    out = BookOut(id=uuid4(), subject="biology",
                  original_filename="b.pdf", status="uploading")
    assert out.deduplicated is False
