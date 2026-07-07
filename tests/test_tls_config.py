"""Tests for sketchlog.tls_config."""
from __future__ import annotations

import json
import os
import ssl
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

from sketchlog.tls_config import (
    CertInfo,
    TLSCheckResult,
    TLSConfig,
    _parse_cert,
    build_ssl_context,
    check_tls_config,
    main,
)

# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_self_signed(tmpdir: str) -> tuple[str, str]:
    """Generate a real self-signed cert+key using Python ssl / subprocess."""
    import subprocess
    cert = os.path.join(tmpdir, "cert.pem")
    key = os.path.join(tmpdir, "key.pem")
    subprocess.run(
        [
            "openssl", "req", "-x509", "-newkey", "rsa:2048",
            "-keyout", key, "-out", cert,
            "-days", "365", "-nodes",
            "-subj", "/CN=test.sketchlog.local",
        ],
        capture_output=True, check=True,
    )
    return cert, key


def _has_openssl() -> bool:
    import shutil
    return shutil.which("openssl") is not None


# ── TLSConfig validation ───────────────────────────────────────────────────────

class TestTLSConfigValidation(unittest.TestCase):

    def test_missing_cert_file(self) -> None:
        with self.assertRaises(ValueError) as cm:
            TLSConfig(cert_file="/nonexistent/cert.pem", key_file="/nonexistent/key.pem")
        self.assertIn("cert_file", str(cm.exception))

    def test_missing_key_file(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".pem", delete=False) as f:
            cert = f.name
        try:
            with self.assertRaises(ValueError) as cm:
                TLSConfig(cert_file=cert, key_file="/nonexistent/key.pem")
            self.assertIn("key_file", str(cm.exception))
        finally:
            os.unlink(cert)

    def test_mtls_requires_ca(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".pem", delete=False) as f:
            cert = f.name
        with tempfile.NamedTemporaryFile(suffix=".pem", delete=False) as f:
            key = f.name
        try:
            with self.assertRaises(ValueError) as cm:
                TLSConfig(cert_file=cert, key_file=key, mtls=True, ca_file=None)
            self.assertIn("ca_file", str(cm.exception))
        finally:
            os.unlink(cert)
            os.unlink(key)

    def test_invalid_tls_version(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".pem", delete=False) as f:
            cert = f.name
        with tempfile.NamedTemporaryFile(suffix=".pem", delete=False) as f:
            key = f.name
        try:
            with self.assertRaises(ValueError) as cm:
                TLSConfig(cert_file=cert, key_file=key, min_tls_version="TLSv1.0")
            self.assertIn("min_tls_version", str(cm.exception))
        finally:
            os.unlink(cert)
            os.unlink(key)

    def test_invalid_expiry_warn_days(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".pem", delete=False) as f:
            cert = f.name
        with tempfile.NamedTemporaryFile(suffix=".pem", delete=False) as f:
            key = f.name
        try:
            with self.assertRaises(ValueError) as cm:
                TLSConfig(cert_file=cert, key_file=key, expiry_warn_days=0)
            self.assertIn("expiry_warn_days", str(cm.exception))
        finally:
            os.unlink(cert)
            os.unlink(key)

    def test_bool_coercion_mtls(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".pem", delete=False) as f:
            cert = f.name
        with tempfile.NamedTemporaryFile(suffix=".pem", delete=False) as f:
            key = f.name
        with tempfile.NamedTemporaryFile(suffix=".pem", delete=False) as f:
            ca = f.name
        try:
            # bool True is fine for mtls
            cfg = TLSConfig(cert_file=cert, key_file=key, mtls=True, ca_file=ca)
            self.assertTrue(cfg.mtls)
        finally:
            os.unlink(cert)
            os.unlink(key)
            os.unlink(ca)

    def test_valid_tls13(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".pem", delete=False) as f:
            cert = f.name
        with tempfile.NamedTemporaryFile(suffix=".pem", delete=False) as f:
            key = f.name
        try:
            cfg = TLSConfig(cert_file=cert, key_file=key, min_tls_version="TLSv1.3")
            self.assertEqual(cfg.min_tls_version, "TLSv1.3")
        finally:
            os.unlink(cert)
            os.unlink(key)


# ── CertInfo ──────────────────────────────────────────────────────────────────

class TestCertInfo(unittest.TestCase):

    def test_to_dict_schema(self) -> None:
        ci = CertInfo(
            path="/tmp/cert.pem", subject="CN=test", issuer="CN=ca",
            not_before="Jan  1 00:00:00 2025 UTC",
            not_after="Jan  1 00:00:00 2026 UTC",
            days_until_expiry=100, expired=False, expiry_warning=False,
            sans=["DNS:test.local"],
        )
        d = ci.to_dict()
        self.assertIn("path", d)
        self.assertIn("days_until_expiry", d)
        self.assertIn("expired", d)
        self.assertIn("expiry_warning", d)
        self.assertIn("sans", d)
        json.dumps(d)  # must be JSON-serializable

    def test_expired_flag(self) -> None:
        ci = CertInfo(
            path="/tmp/cert.pem", subject="CN=test", issuer="CN=ca",
            not_before="Jan  1 00:00:00 2020 UTC",
            not_after="Jan  1 00:00:00 2021 UTC",
            days_until_expiry=-100, expired=True, expiry_warning=False,
            sans=[],
        )
        self.assertTrue(ci.expired)
        self.assertFalse(ci.expiry_warning)


# ── TLSCheckResult ────────────────────────────────────────────────────────────

class TestTLSCheckResult(unittest.TestCase):

    def test_to_dict_schema(self) -> None:
        result = TLSCheckResult(
            status="pass", mode="tls",
            checks=[{"name": "cert_expiry", "status": "pass", "message": "ok"}],
            cert_info=None, ca_info=None,
            message="TLS configuration is valid",
        )
        d = result.to_dict()
        self.assertEqual(d["status"], "pass")
        self.assertEqual(d["mode"], "tls")
        self.assertIsNone(d["cert_info"])
        json.dumps(d)

    def test_fail_status(self) -> None:
        result = TLSCheckResult(
            status="fail", mode="mtls",
            checks=[{"name": "cert_expiry", "status": "fail", "message": "expired"}],
            cert_info=None, ca_info=None,
            message="TLS configuration has errors",
        )
        self.assertEqual(result.status, "fail")
        self.assertEqual(result.mode, "mtls")


# ── check_tls_config (mocked cert parsing) ────────────────────────────────────

class TestCheckTLSConfig(unittest.TestCase):

    def _good_cert_info(self, path: str = "/tmp/cert.pem") -> CertInfo:
        return CertInfo(
            path=path, subject="CN=test", issuer="CN=ca",
            not_before="Jan  1 00:00:00 2025 UTC",
            not_after="Jan  1 00:00:00 2030 UTC",
            days_until_expiry=1000, expired=False, expiry_warning=False,
            sans=[],
        )

    def test_pass_with_valid_config(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cert = os.path.join(td, "cert.pem")
            key = os.path.join(td, "key.pem")
            ca = os.path.join(td, "ca.pem")
            for p in (cert, key, ca):
                Path(p).write_text("placeholder")

            cfg = TLSConfig.__new__(TLSConfig)
            object.__setattr__(cfg, "cert_file", cert)
            object.__setattr__(cfg, "key_file", key)
            object.__setattr__(cfg, "ca_file", ca)
            object.__setattr__(cfg, "mtls", False)
            object.__setattr__(cfg, "min_tls_version", "TLSv1.2")
            object.__setattr__(cfg, "ciphers", None)
            object.__setattr__(cfg, "check_hostname", False)
            object.__setattr__(cfg, "expiry_warn_days", 30)

            with (
                patch("sketchlog.tls_config._parse_cert", return_value=self._good_cert_info(cert)),
                patch("sketchlog.tls_config.build_ssl_context", return_value=MagicMock()),
                patch("ssl.SSLContext") as mock_ctx,
            ):
                mock_ctx.return_value.__enter__ = MagicMock()
                mock_ctx.return_value.load_cert_chain = MagicMock()
                result = check_tls_config(cfg)

            self.assertIn(result.status, ("pass", "warn"))
            self.assertEqual(result.mode, "tls")
            json.dumps(result.to_dict())

    def test_warn_for_tls12(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cert = os.path.join(td, "cert.pem")
            key = os.path.join(td, "key.pem")
            for p in (cert, key):
                Path(p).write_text("placeholder")

            cfg = TLSConfig.__new__(TLSConfig)
            object.__setattr__(cfg, "cert_file", cert)
            object.__setattr__(cfg, "key_file", key)
            object.__setattr__(cfg, "ca_file", None)
            object.__setattr__(cfg, "mtls", False)
            object.__setattr__(cfg, "min_tls_version", "TLSv1.2")
            object.__setattr__(cfg, "ciphers", None)
            object.__setattr__(cfg, "check_hostname", False)
            object.__setattr__(cfg, "expiry_warn_days", 30)

            with (
                patch("sketchlog.tls_config._parse_cert", return_value=self._good_cert_info(cert)),
                patch("sketchlog.tls_config.build_ssl_context", return_value=MagicMock()),
                patch("ssl.SSLContext") as mock_ctx,
            ):
                mock_ctx.return_value.load_cert_chain = MagicMock()
                result = check_tls_config(cfg)

            tls_check = next(c for c in result.checks if c["name"] == "tls_version")
            self.assertEqual(tls_check["status"], "warn")

    def test_fail_on_expired_cert(self) -> None:
        expired_info = CertInfo(
            path="/tmp/cert.pem", subject="CN=test", issuer="CN=ca",
            not_before="Jan  1 00:00:00 2020 UTC",
            not_after="Jan  1 00:00:00 2021 UTC",
            days_until_expiry=-100, expired=True, expiry_warning=False,
            sans=[],
        )
        with tempfile.TemporaryDirectory() as td:
            cert = os.path.join(td, "cert.pem")
            key = os.path.join(td, "key.pem")
            for p in (cert, key):
                Path(p).write_text("placeholder")

            cfg = TLSConfig.__new__(TLSConfig)
            object.__setattr__(cfg, "cert_file", cert)
            object.__setattr__(cfg, "key_file", key)
            object.__setattr__(cfg, "ca_file", None)
            object.__setattr__(cfg, "mtls", False)
            object.__setattr__(cfg, "min_tls_version", "TLSv1.2")
            object.__setattr__(cfg, "ciphers", None)
            object.__setattr__(cfg, "check_hostname", False)
            object.__setattr__(cfg, "expiry_warn_days", 30)

            with (
                patch("sketchlog.tls_config._parse_cert", return_value=expired_info),
                patch("sketchlog.tls_config.build_ssl_context", return_value=MagicMock()),
                patch("ssl.SSLContext") as mock_ctx,
            ):
                mock_ctx.return_value.load_cert_chain = MagicMock()
                result = check_tls_config(cfg)

            self.assertEqual(result.status, "fail")
            expiry_check = next(c for c in result.checks if c["name"] == "cert_expiry")
            self.assertEqual(expiry_check["status"], "fail")

    def test_expiry_warning(self) -> None:
        warn_info = CertInfo(
            path="/tmp/cert.pem", subject="CN=test", issuer="CN=ca",
            not_before="Jan  1 00:00:00 2025 UTC",
            not_after="Jan  1 00:00:00 2026 UTC",
            days_until_expiry=10, expired=False, expiry_warning=True,
            sans=[],
        )
        with tempfile.TemporaryDirectory() as td:
            cert = os.path.join(td, "cert.pem")
            key = os.path.join(td, "key.pem")
            for p in (cert, key):
                Path(p).write_text("placeholder")

            cfg = TLSConfig.__new__(TLSConfig)
            object.__setattr__(cfg, "cert_file", cert)
            object.__setattr__(cfg, "key_file", key)
            object.__setattr__(cfg, "ca_file", None)
            object.__setattr__(cfg, "mtls", False)
            object.__setattr__(cfg, "min_tls_version", "TLSv1.3")
            object.__setattr__(cfg, "ciphers", None)
            object.__setattr__(cfg, "check_hostname", False)
            object.__setattr__(cfg, "expiry_warn_days", 30)

            with (
                patch("sketchlog.tls_config._parse_cert", return_value=warn_info),
                patch("sketchlog.tls_config.build_ssl_context", return_value=MagicMock()),
                patch("ssl.SSLContext") as mock_ctx,
            ):
                mock_ctx.return_value.load_cert_chain = MagicMock()
                result = check_tls_config(cfg)

            self.assertIn(result.status, ("warn", "fail"))


# ── CLI ───────────────────────────────────────────────────────────────────────

class TestCLI(unittest.TestCase):

    def test_missing_cert_exits_2(self) -> None:
        rc = main(["--cert", "/nonexistent/cert.pem", "--key", "/nonexistent/key.pem"])
        self.assertEqual(rc, 2)

    def test_mtls_missing_ca_exits_2(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".pem", delete=False) as f:
            cert = f.name
        with tempfile.NamedTemporaryFile(suffix=".pem", delete=False) as f:
            key = f.name
        try:
            rc = main(["--cert", cert, "--key", key, "--mtls"])
            self.assertEqual(rc, 2)
        finally:
            os.unlink(cert)
            os.unlink(key)

    def test_json_output(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cert = os.path.join(td, "cert.pem")
            key = os.path.join(td, "key.pem")
            Path(cert).write_text("placeholder")
            Path(key).write_text("placeholder")

            good_info = CertInfo(
                path=cert, subject="CN=test", issuer="CN=ca",
                not_before="Jan  1 00:00:00 2025 UTC",
                not_after="Jan  1 00:00:00 2030 UTC",
                days_until_expiry=1000, expired=False, expiry_warning=False,
                sans=[],
            )

            import io
            from contextlib import redirect_stdout
            buf = io.StringIO()
            with (
                patch("sketchlog.tls_config._parse_cert", return_value=good_info),
                patch("sketchlog.tls_config.build_ssl_context", return_value=MagicMock()),
                patch("ssl.SSLContext") as mock_ctx,
                redirect_stdout(buf),
            ):
                mock_ctx.return_value.load_cert_chain = MagicMock()
                rc = main(["--cert", cert, "--key", key, "--format", "json"])

            out = buf.getvalue()
            d = json.loads(out)
            self.assertIn("status", d)
            self.assertIn("mode", d)
            self.assertIn("checks", d)

    def test_help_exits_0(self) -> None:
        with self.assertRaises(SystemExit) as cm:
            main(["--help"])
        self.assertEqual(cm.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
