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


def test_normal_exit_while_parent_alive_is_clean():
    # nightly finishes on its own while the app still holds stdin open: must exit 0, no fatal error
    code = "from common.supervised import exit_with_parent; exit_with_parent(); import time; time.sleep(0.3)"
    p = subprocess.Popen([sys.executable, "-c", code], stdin=subprocess.PIPE, stderr=subprocess.PIPE,
                         env={**os.environ, "MF_SUPERVISED": "1"}, cwd=os.getcwd())
    err = p.stderr.read()  # EOF once the child exits; stdin stays open like the app's pipe
    assert p.wait(timeout=10) == 0, err.decode()
    assert b"Fatal Python error" not in err
