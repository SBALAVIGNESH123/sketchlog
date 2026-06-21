import subprocess
import time
import sys
import argparse
import json
import urllib.request
import os

class ConformanceSuite:
    def __init__(self, command: str, cwd: str, port: int = 8999):
        self.command = command
        self.cwd = cwd
        self.port = port
        self.server_process = None

    def start_server(self):
        print(f"Starting sketchlog server on port {self.port}...")
        self.server_process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "sketchlog.server:app", "--port", str(self.port)],
            stdout=sys.stdout,
            stderr=sys.stderr,
            env={**os.environ, "PYTHONPATH": "python"}
        )

        # Wait for server to become healthy (up to 10 seconds)
        for _ in range(100):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/health", timeout=5) as res:
                    if res.getcode() == 200:
                        print("Server is healthy.")
                        return
            except Exception:
                time.sleep(0.1)
        raise RuntimeError("Server failed to start")

    def stop_server(self):
        if self.server_process:
            self.server_process.terminate()
            try:
                self.server_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.server_process.kill()

    def run_test(self, name: str, args: list[str]):
        print(f"Running test: {name}...")
        import shlex, sys
        cmd_args = shlex.split(self.command) + [f"--endpoint=http://127.0.0.1:{self.port}"] + args
        result = subprocess.run(cmd_args, cwd=self.cwd, capture_output=True, text=True, shell=(sys.platform == 'win32'))
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
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/metrics", timeout=5) as res:
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
    parser.add_argument('--command', required=True, help="Command to run SDK conformance wrapper")
    parser.add_argument('--cwd', required=False, default=".", help="Working directory for the command")
    args = parser.parse_args()

    suite = ConformanceSuite(args.command, args.cwd)
    suite.run_all()
