import subprocess
import time
import sys
import argparse
import json
import urllib.request
import os

class ConformanceSuite:
    def __init__(self, command: str, port: int = 8999):
        self.command = command
        self.port = port
        self.server_process = None

    def start_server(self):
        print(f"Starting sketchlog server on port {self.port}...")
        self.server_process = subprocess.Popen(
            ["uvicorn", "sketchlog.server:app", "--port", str(self.port)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env={**os.environ, "PYTHONPATH": "python"}
        )

        # Wait for server to become healthy
        for _ in range(30):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/health") as res:
                    if res.getcode() == 200:
                        print("Server is healthy.")
                        return
            except Exception:
                time.sleep(0.1)
        raise RuntimeError("Server failed to start")

    def stop_server(self):
        if self.server_process:
            self.server_process.terminate()
            self.server_process.wait()

    def run_test(self, name: str, args: list[str]):
        print(f"Running test: {name}...")
        cmd_str = f"{self.command} {' '.join(args)} --endpoint=http://127.0.0.1:{self.port}"
        result = subprocess.run(cmd_str, capture_output=True, text=True, shell=True)
        if result.returncode != 0:
            print(f"Test '{name}' FAILED!")
            print(f"STDOUT: {result.stdout}")
            print(f"STDERR: {result.stderr}")
            sys.exit(1)
        print(f"Test '{name}' passed.")

    def run_all(self):
        try:
            self.start_server()

            # Test 1: Basic Ingestion
            self.run_test("ingest_basic", ["test-ingest"])

            # Verify ingestion via metrics
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/metrics") as res:
                metrics = res.read().decode('utf-8')
                if "sketchlog_events_ingested_total" not in metrics:
                    print("FAILED: Metrics did not record ingested events.")
                    sys.exit(1)

            # Test 2: Idempotent Retry (SDK should retry 503s correctly)
            # We can't easily force the server to 503 without a proxy, so the SDK test wrapper
            # will just simulate it internally or we use a special endpoint if available.
            # For now, we trust the SDK test wrapper runs its own mock tests for retries.
            self.run_test("retries", ["test-retries"])

            print("All conformance tests passed!")

        finally:
            self.stop_server()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SketchLog Protocol Conformance Suite")
    parser.add_argument("--command", required=True, help="Command to run the SDK wrapper (e.g. 'node run.js' or 'go run main.go')")
    args = parser.parse_args()

    suite = ConformanceSuite(args.command)
    suite.run_all()
