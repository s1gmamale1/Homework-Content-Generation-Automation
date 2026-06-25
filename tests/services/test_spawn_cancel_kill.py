import inspect
from app.services import agent


def test_spawn_uses_kill_tree_on_cancel():
    # The subprocess spawn (and its cancel-kill handling) lives in _spawn_once;
    # _spawn is now the thin retry-on-rate-limit wrapper around it.
    src = inspect.getsource(agent._spawn_once)
    assert "kill_tree(proc.pid)" in src, "_spawn_once must kill the whole tree on cancel"
    assert "proc.kill()" not in src, "replace proc.kill() with kill_tree(proc.pid)"
