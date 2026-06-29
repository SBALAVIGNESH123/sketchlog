import subprocess
import time
import sys
import argparse
import json
import urllib.request
import os
import shutil
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

class ConformanceSuite:
    def __init__(self, command: str, cwd: str, port: int = 8999):
        self.command = command
        self.cwd = cwd
        self.port = port
        self.server_process = None
        self.retry_server = None
        self.retry_thread = None

    def start_server(self):
        print(f"Starting sketchlog server on port {self.port}...")
        self.server_process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "sketchlog.server:app", "--port", str(self.port)],
            stdout=sys.stdout,
            stderr=sys.stderr,
            env={
                **os.environ,
                "PYTHONPATH": "python",
                "SKETCHLOG_AUTH_TOKEN": "conformance-secret",
            }
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
        if self.retry_server:
            self.retry_server.shutdown()
            self.retry_server.server_close()
        if self.retry_thread:
            self.retry_thread.join(timeout=5)

    def start_retry_fixture(self):
        class RetryHandler(BaseHTTPRequestHandler):
            attempts = 0

            def do_GET(self):
                type(self).attempts += 1
                status = 503 if type(self).attempts <= 2 else 200
                payload = b'{"status":"ok"}'
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *_args):
                return

        self.retry_server = ThreadingHTTPServer(
            ("127.0.0.1", self.port + 1), RetryHandler)
        self.retry_thread = threading.Thread(
            target=self.retry_server.serve_forever, daemon=True)
        self.retry_thread.start()

    def run_test(self, name: str, args: list[str], port=None):
        print(f"Running test: {name}...")
        import shlex
        endpoint_port = port if port is not None else self.port
        cmd_args = shlex.split(self.command, posix=os.name != "nt")
        executable = shutil.which(cmd_args[0])
        if executable is None:
            raise RuntimeError(
                f"Conformance command was not found: {cmd_args[0]}")
        cmd_args[0] = executable
        cmd_args += [
            f"--endpoint=http://127.0.0.1:{endpoint_port}",
            "--token=conformance-secret",
        ] + args
        result = subprocess.run(
            cmd_args, cwd=self.cwd, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            print(f"Test '{name}' FAILED!")
            print(f"STDOUT: {result.stdout}")
            print(f"STDERR: {result.stderr}")
            sys.exit(1)
        print(f"Test '{name}' passed.")

    def run_all(self):
        try:
            self.start_server()
            self.start_retry_fixture()

            # Test 1: Basic Ingestion
            self.run_test("ingest_basic", ["test-ingest"])
            self.run_test("auth_missing", ["test-auth-missing"])
            self.run_test("auth_invalid", ["test-auth-invalid"])

            # Verify ingestion via metrics
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/metrics", timeout=5) as res:
                metrics = res.read().decode('utf-8')
                if "sketchlog_events_ingested_total" not in metrics:
                    print("FAILED: Metrics did not record ingested events.")
                    sys.exit(1)

            # Test 2: the retry fixture returns two 503 responses, then a 200.
            self.run_test("retries", ["test-retries"], self.port + 1)
            # No listener exists on this port. Real transport failures must
            # exercise the idempotent retry path rather than fail immediately.
            self.run_test(
                "transport_retries", ["test-transport-retries"], self.port + 2)

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
