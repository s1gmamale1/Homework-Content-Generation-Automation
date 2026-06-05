import sys
import time
import subprocess

import psutil

from app.services.proc_tree import kill_tree


def test_kill_tree_kills_parent_and_child():
    code = (
        "import subprocess, sys, time; "
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
        "time.sleep(60)"
    )
    parent = subprocess.Popen([sys.executable, "-c", code])
    child_pids = []
    for _ in range(50):
        try:
            kids = psutil.Process(parent.pid).children(recursive=True)
        except psutil.NoSuchProcess:
            kids = []
        if kids:
            child_pids = [k.pid for k in kids]
            break
        time.sleep(0.1)
    assert child_pids, "child process never started"

    kill_tree(parent.pid)

    for _ in range(50):
        if not psutil.pid_exists(parent.pid) and all(not psutil.pid_exists(c) for c in child_pids):
            break
        time.sleep(0.1)
    assert not psutil.pid_exists(parent.pid), "parent survived kill_tree"
    for c in child_pids:
        assert not psutil.pid_exists(c), f"child {c} survived kill_tree"


def test_kill_tree_nonexistent_pid_is_safe():
    kill_tree(2_000_000_000)
