import os
import subprocess
import sys
import time

CODE = "from common.supervised import exit_with_parent; exit_with_parent(); import time; time.sleep(30)"


def _start(env_extra):
    env = {**os.environ, **env_extra}
    return subprocess.Popen([sys.executable, "-c", CODE], stdin=subprocess.PIPE, env=env, cwd=os.getcwd())


def test_exits_when_stdin_closes():
    p = _start({"MF_SUPERVISED": "1"})
    time.sleep(0.5)
    p.stdin.close()  # what the app's death looks like to the child
    assert p.wait(timeout=5) == 0


def test_noop_without_env():
    p = _start({"MF_SUPERVISED": "0"})
    time.sleep(0.5)
    p.stdin.close()
    time.sleep(0.5)
    assert p.poll() is None
    p.kill()
