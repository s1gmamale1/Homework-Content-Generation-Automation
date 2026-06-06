"""SSE streams must not `yield` while a DB session is still checked out.

Yielding inside an `async with SessionLocal()` block holds a pooled connection
open across the suspension; if the client disconnects mid-yield the async
generator can be garbage-collected without a clean `aclose()`, orphaning the
connection — SQLAlchemy then reaps it with a "non-checked-in connection"
warning. The fix: snapshot needed data inside the session block, release the
session, THEN yield. This test encodes that invariant via the AST so a
regression is caught structurally (the endpoints need a live DB to run).
"""
import ast
import inspect

from app.api.v1 import books as books_mod
from app.api.v1 import jobs as jobs_mod


def _call_name(func_node: ast.AST) -> str | None:
    if isinstance(func_node, ast.Name):
        return func_node.id
    if isinstance(func_node, ast.Attribute):
        return func_node.attr
    return None


def _yields_while_holding_session(func) -> bool:
    """True if any `yield`/`yield from` sits inside an `async with SessionLocal()`."""
    tree = ast.parse(inspect.getsource(func))
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncWith):
            holds_session = any(
                isinstance(item.context_expr, ast.Call)
                and _call_name(item.context_expr.func) == "SessionLocal"
                for item in node.items
            )
            if holds_session:
                for inner in ast.walk(node):
                    if isinstance(inner, (ast.Yield, ast.YieldFrom)):
                        return True
    return False


def test_stream_job_does_not_yield_holding_session():
    assert _yields_while_holding_session(jobs_mod.stream_job) is False


def test_stream_toc_does_not_yield_holding_session():
    assert _yields_while_holding_session(books_mod.stream_toc) is False
