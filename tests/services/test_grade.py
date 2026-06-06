import pytest
from app.services.grade import derive_grade_from_filename


@pytest.mark.parametrize("name,expected", [
    ("7-sinf_Algebra_2022_(elekton_darslikbot).pdf", "7"),
    ("8-sinf_Ingliz_tili_darslik_2022.pdf", "8"),
    ("9 sinf fizika.pdf", "9"),
    ("5-klass_russkiy.pdf", "5"),
    ("7-класс_история.pdf", "7"),
    ("11-SINF_GEOMETRIYA.PDF", "11"),
    ("algebra_final.pdf", None),
    ("12-sinf_too_high.pdf", None),
    ("0-sinf.pdf", None),
    ("", None),
    (None, None),
])
def test_derive_grade_from_filename(name, expected):
    assert derive_grade_from_filename(name) == expected
