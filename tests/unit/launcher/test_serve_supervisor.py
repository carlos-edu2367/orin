import os
import sys
import time

from agentos.launcher.serve import ServeSupervisor


def test_the_first_child_to_exit_stops_the_others_and_sets_the_exit_code(tmp_path):
    commands = {
        "backend": [sys.executable, "-c", "import time; time.sleep(60)"],
        "worker": [sys.executable, "-c", "import sys; sys.exit(3)"],
    }
    # The full environment: on Windows the interpreter cannot start without SYSTEMROOT.
    supervisor = ServeSupervisor(commands, dict(os.environ), cwd=tmp_path, poll_interval=0.05)
    started = time.monotonic()
    assert supervisor.run() == 3
    assert time.monotonic() - started < 30
    assert all(child.poll() is not None for child in supervisor.children.values())


def test_stop_ends_every_child(tmp_path):
    commands = {"backend": [sys.executable, "-c", "import time; time.sleep(60)"]}
    supervisor = ServeSupervisor(commands, dict(os.environ), cwd=tmp_path, poll_interval=0.05)
    supervisor.start()
    supervisor.stop()
    assert all(child.poll() is not None for child in supervisor.children.values())
