import time
import random
import requests
import threading

def run_synthetic_load():
    print("Starting synthetic data load to stream 'demo-stream'...")
    while True:
        # Simulate bimodal latency distribution (e.g. fast cache hits and slow DB queries)
        latencies = []
        for _ in range(50):
            if random.random() < 0.8:
                # Fast path (cache hit) ~10-20ms
                latencies.append(random.gauss(15, 3))
            else:
                # Slow path (DB miss) ~80-120ms
                latencies.append(random.gauss(100, 20))

        payload = {
            "latencies": [max(0.1, l) for l in latencies],
            "uniques": [f"user_{random.randint(1, 1000)}" for _ in range(10)],
            "events": {"http_request": 50, "cache_miss": 10}
        }

        try:
            requests.post("http://localhost:8000/v1/streams/demo-stream/events", json=payload)
        except Exception as e:
            pass # ignore if server is not up

        time.sleep(0.5)

if __name__ == "__main__":
    run_synthetic_load()
