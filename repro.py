import math
from sketchlog import WindowedStreamLog
from sketchlog.drift import DriftSketch

for cls in (WindowedStreamLog, DriftSketch):
    for value in [float("nan"), float("inf"), "nan", "inf"]:
        try:
            obj = cls(window=value)
            print(cls.__name__, repr(value), "accepted")
        except Exception as e:
            print(cls.__name__, repr(value), type(e).__name__, str(e))

try:
    DriftSketch(window="   ")
except Exception as e:
    print("DriftSketch", repr("   "), type(e).__name__, str(e))
