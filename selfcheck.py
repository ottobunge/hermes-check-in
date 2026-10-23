"""Self-check for the check-in plugin (no gateway needed).

    python3 ~/.hermes/plugins/check-in/selfcheck.py
"""
import importlib.util
import pathlib
import tempfile

PATH = pathlib.Path(__file__).with_name("__init__.py")
spec = importlib.util.spec_from_file_location("check_in_under_test", PATH)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
mod.STATE = pathlib.Path(tempfile.mkdtemp()) / "check-ins.json"

timers, injected = [], []


class FakeTimer:
    def __init__(self, seconds):
        self.seconds = seconds
        self.cancelled = False

    def cancel(self):
        self.cancelled = True


def fake_start(key, seconds):
    timer = FakeTimer(seconds)
    timers.append((key, seconds, timer))
    return timer


class FakeCtx:
    def inject_message(self, content, role="user", session_key=None):
        injected.append((content, role, session_key))
        return True


mod._start_timer = fake_start
mod._CTX = FakeCtx()

# interval parsing
assert mod._parse_interval("10m") == 600
assert mod._parse_interval("30s") == 30
assert mod._parse_interval("2h") == 7200
assert mod._parse_interval("90") == 5400  # bare number = minutes
assert mod._parse_interval("5s") is None  # below the 10s floor
assert mod._parse_interval("soon") is None

# arm -> timer started with the interval
mod._arm("k1", 600, "report progress")
assert timers[-1][0] == "k1" and timers[-1][1] == 600, timers[-1]
assert not timers[-1][2].cancelled

# idling activity resets (cancels the old timer, starts a fresh one)
old = timers[-1][2]
mod._reset("k1")
assert old.cancelled and timers[-1][1] == 600

# fire injects into the same session, as a user turn, then re-arms
mod._fire("k1")
assert injected == [("report progress", "user", "k1")], injected
assert timers[-1][0] == "k1"

# close stops it for good
assert mod._cancel("k1") is True
assert mod._cancel("k1") is False
before = len(injected)
mod._fire("k1")
assert len(injected) == before, "fired after close"

# persisted across reloads
mod._arm("k2", 60, "second")
saved = mod.STATE.read_text()
assert "second" in saved and "k2" in saved, saved
mod._cancel("k2")

print("check-in selfcheck: OK")
