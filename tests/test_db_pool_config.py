from app.db import _pool_config


def test_head_process_keeps_large_database_pool() -> None:
    assert _pool_config(worker_concurrency=0) == {
        "pool_size": 20,
        "max_overflow": 30,
    }


def test_standalone_worker_database_pool_is_bounded() -> None:
    assert _pool_config(worker_concurrency=1) == {
        "pool_size": 2,
        "max_overflow": 2,
    }


def test_worker_pool_does_not_scale_with_pipeline_concurrency() -> None:
    assert _pool_config(worker_concurrency=8) == {
        "pool_size": 2,
        "max_overflow": 2,
    }
