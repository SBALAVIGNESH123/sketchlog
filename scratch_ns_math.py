import time

class StreamLog:
    def __init__(self, **kwargs):
        self.events = 0
    def add_latency(self, val):
        self.events += 1

class MockDriftSketch:
    def __init__(self, window_seconds, base_time_ns=1000_000_000_000):
        self._window_seconds = window_seconds
        self._window_ns = int(self._window_seconds * 1_000_000_000)
        if self._window_ns == 0:
            raise ValueError(f"Window {self._window_seconds}s is too small (sub-nanosecond resolution).")

        self._window_start = {"name": base_time_ns}
        self._current = {"name": StreamLog()}
        self._previous = {"name": StreamLog()}

    def add(self, now_ns):
        elapsed_ns = now_ns - self._window_start["name"]

        if elapsed_ns >= self._window_ns:
            windows_elapsed = elapsed_ns // self._window_ns
            if windows_elapsed >= 2:
                self._previous["name"] = StreamLog()  # empty
                print(f"At {now_ns}, rotated EMPTY (gap of {windows_elapsed} windows)")
            else:
                self._previous["name"] = self._current["name"]
                print(f"At {now_ns}, rotated FROZEN (gap of 1 window)")

            self._current["name"] = StreamLog()
            self._window_start["name"] += windows_elapsed * self._window_ns
        else:
            print(f"At {now_ns}, NO rotation")


print("--- Standard boundaries ---")
ds = MockDriftSketch(0.1, base_time_ns=1000_000_000_000)
ds.add(1000_100_000_000)
ds.add(1000_200_000_000)
ds.add(1000_300_000_000)

print("--- Pre-boundary ---")
ds = MockDriftSketch(0.1, base_time_ns=1000_000_000_000)
ds.add(1000_199_999_500)

print("--- Huge monotonic clock boundary ---")
base = 10_000_000_000_000_000
ds = MockDriftSketch(0.1, base_time_ns=base)
now = base + 100_000_000
print(f"Base: {base}, Now: {now}")
ds.add(now)

print("--- No time advance tiny window ---")
try:
    MockDriftSketch(5e-10)
    print("Failed: Should have rejected sub-nanosecond window")
except ValueError as e:
    print(f"Correctly rejected: {e}")
