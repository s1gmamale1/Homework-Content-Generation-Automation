import inspect
from app.services import agent


def test_spawn_uses_kill_tree_on_cancel():
    src = inspect.getsource(agent._spawn)
    assert "kill_tree(proc.pid)" in src, "_spawn must kill the whole tree on cancel"
    assert "proc.kill()" not in src, "replace proc.kill() with kill_tree(proc.pid)"
