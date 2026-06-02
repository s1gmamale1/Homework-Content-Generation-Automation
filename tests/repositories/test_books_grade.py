import inspect
from app.repositories import books as books_repo


def test_books_create_accepts_grade():
    params = inspect.signature(books_repo.create).parameters
    assert "grade" in params
    assert params["grade"].default is None
