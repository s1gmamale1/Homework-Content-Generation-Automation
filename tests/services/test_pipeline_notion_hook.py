import ast
from pathlib import Path


def test_pipeline_calls_archive_job_after_done():
    src = Path("app/services/pipeline.py").read_text(encoding="utf-8")
    assert "notion_archive" in src, "pipeline must import/call notion_archive"
    assert "archive_job" in src
    tree = ast.parse(src)
    found_guarded = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Try):
            call_names = {
                getattr(n.func, "attr", "")
                for n in ast.walk(node)
                if isinstance(n, ast.Call) and hasattr(n, "func")
            }
            if "archive_job" in call_names:
                found_guarded = True
    assert found_guarded, "archive_job must be inside a try/except"
