from app.db import _connection_server_settings, _engine_options, _pool_config


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


def test_head_connection_is_identifiable_and_times_out_idle_transactions() -> None:
    got = _connection_server_settings(
        worker_concurrency=0, hostname="head-mini", pid=101
    )
    assert got == {
        "application_name": "hcga-head:head-mini:101",
        "idle_in_transaction_session_timeout": "300000",
    }


def test_worker_connection_is_process_identifiable_and_name_is_bounded() -> None:
    got = _connection_server_settings(
        worker_concurrency=2, hostname="x" * 100, pid=202
    )
    assert got["application_name"].startswith("hcga-worker:")
    assert got["application_name"].endswith(":202")
    assert len(got["application_name"].encode("utf-8")) <= 63
    assert got["idle_in_transaction_session_timeout"] == "300000"


def test_engine_options_preserve_worker_pool_and_apply_asyncpg_server_settings() -> None:
    got = _engine_options(worker_concurrency=2, hostname="host-40", pid=303)
    assert got["pool_size"] == 2
    assert got["max_overflow"] == 2
    assert got["connect_args"] == {
        "server_settings": {
            "application_name": "hcga-worker:host-40:303",
            "idle_in_transaction_session_timeout": "300000",
        }
    }
