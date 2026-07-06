def test_extract_prompt_hash_is_v3():
    import inspect
    from app.services import pipeline
    src = inspect.getsource(pipeline)
    assert '"builtin:extract:v3"' in src
    assert '"builtin:extract:v2"' not in src
