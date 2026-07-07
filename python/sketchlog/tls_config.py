"""sketchlog.tls_config — mTLS/TLS configuration, validation, and SSL context builder.

Extends the existing --tls-cert / --tls-key server flags with:
- mTLS (mutual TLS) client certificate verification
- Certificate chain validation and expiry checks
- Secure cipher-suite selection (TLS 1.2+)
- Production-ready SSL context factory
- sketchlog-tls-check CLI for pre-flight certificate checks
"""
from __future__ import annotations

import argparse
import datetime
import json
import math
import os
import socket
import ssl
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

__all__ = [
    "TLSConfig",
    "CertInfo",
    "TLSCheckResult",
    "build_ssl_context",
    "check_tls_config",
    "main",
]

# Minimum TLS version — TLS 1.2 minimum, prefer TLS 1.3
_MIN_TLS_VERSION = ssl.TLSVersion.TLSv1_2

# Secure cipher suites (TLS 1.2 fallback; TLS 1.3 suites are always preferred by Python ssl)
_SECURE_CIPHERS = (
    "TLS_AES_256_GCM_SHA384:"
    "TLS_CHACHA20_POLY1305_SHA256:"
    "TLS_AES_128_GCM_SHA256:"
    "ECDHE+AESGCM:"
    "ECDHE+CHACHA20:"
    "DHE+AESGCM:"
    "!aNULL:!eNULL:!EXPORT:!DES:!RC4:!MD5:!PSK:!SRP"
)

# Warn when cert expires within this many days
_EXPIRY_WARN_DAYS = 30


# ── Dataclasses ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class TLSConfig:
    """Validated TLS/mTLS configuration for SketchLog server."""

    cert_file: str
    key_file: str
    ca_file: Optional[str] = None          # Required for mTLS
    mtls: bool = False                      # Require client certs
    min_tls_version: str = "TLSv1.2"       # "TLSv1.2" or "TLSv1.3"
    ciphers: Optional[str] = None           # None = use secure defaults
    check_hostname: bool = False            # For client-side verification
    expiry_warn_days: int = _EXPIRY_WARN_DAYS

    def __post_init__(self) -> None:
        errors: List[str] = []

        if isinstance(self.mtls, bool) is False:
            errors.append("mtls must be a bool")
        if isinstance(self.check_hostname, bool) is False:
            errors.append("check_hostname must be a bool")

        if not self.cert_file or not isinstance(self.cert_file, str):
            errors.append("cert_file must be a non-empty string")
        elif not os.path.isfile(self.cert_file):
            errors.append(f"cert_file not found: {self.cert_file!r}")

        if not self.key_file or not isinstance(self.key_file, str):
            errors.append("key_file must be a non-empty string")
        elif not os.path.isfile(self.key_file):
            errors.append(f"key_file not found: {self.key_file!r}")

        if self.mtls:
            if not self.ca_file:
                errors.append("ca_file is required when mtls=True")
            elif not os.path.isfile(self.ca_file):
                errors.append(f"ca_file not found: {self.ca_file!r}")

        if self.min_tls_version not in ("TLSv1.2", "TLSv1.3"):
            errors.append("min_tls_version must be 'TLSv1.2' or 'TLSv1.3'")

        if not isinstance(self.expiry_warn_days, int) or isinstance(self.expiry_warn_days, bool):
            errors.append("expiry_warn_days must be an int")
        elif self.expiry_warn_days < 1:
            errors.append("expiry_warn_days must be >= 1")

        if errors:
            raise ValueError("TLSConfig validation errors: " + "; ".join(errors))


@dataclass(frozen=True)
class CertInfo:
    """Parsed certificate information."""
    path: str
    subject: str
    issuer: str
    not_before: str
    not_after: str
    days_until_expiry: int
    expired: bool
    expiry_warning: bool
    sans: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "subject": self.subject,
            "issuer": self.issuer,
            "not_before": self.not_before,
            "not_after": self.not_after,
            "days_until_expiry": self.days_until_expiry,
            "expired": self.expired,
            "expiry_warning": self.expiry_warning,
            "sans": self.sans,
        }


@dataclass(frozen=True)
class TLSCheckResult:
    """Result of a TLS pre-flight check."""
    status: str          # "pass", "warn", "fail"
    mode: str            # "tls" or "mtls"
    checks: List[Dict[str, Any]]
    cert_info: Optional[CertInfo]
    ca_info: Optional[CertInfo]
    message: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "mode": self.mode,
            "checks": self.checks,
            "cert_info": self.cert_info.to_dict() if self.cert_info else None,
            "ca_info": self.ca_info.to_dict() if self.ca_info else None,
            "message": self.message,
        }


# ── Certificate helpers ───────────────────────────────────────────────────────

def _parse_cert(path: str, warn_days: int = _EXPIRY_WARN_DAYS) -> CertInfo:
    """Parse a PEM certificate file and return CertInfo."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        raw = ssl._ssl._test_decode_cert(path)  # type: ignore[attr-defined]
    except Exception as exc:
        raise ValueError(f"Cannot parse certificate {path!r}: {exc}") from exc

    def _dn(pairs: Any) -> str:
        if not pairs:
            return ""
        parts = []
        for pair in pairs:
            for k, v in pair:
                parts.append(f"{k}={v}")
        return ", ".join(parts)

    subject = _dn(raw.get("subject", ()))
    issuer = _dn(raw.get("issuer", ()))
    not_before = raw.get("notBefore", "")
    not_after = raw.get("notAfter", "")
    sans: List[str] = [v for t, v in raw.get("subjectAltName", ())]

    expiry_dt: Optional[datetime.datetime] = None
    days_until_expiry = 0
    expired = False
    expiry_warning = False
    try:
        expiry_dt = datetime.datetime.strptime(not_after, "%b %d %H:%M:%S %Y %Z").replace(
            tzinfo=datetime.timezone.utc
        )
        now = datetime.datetime.now(datetime.timezone.utc)
        delta = expiry_dt - now
        days_until_expiry = delta.days
        expired = days_until_expiry < 0
        expiry_warning = 0 <= days_until_expiry < warn_days
    except Exception:
        pass

    return CertInfo(
        path=path,
        subject=subject,
        issuer=issuer,
        not_before=not_before,
        not_after=not_after,
        days_until_expiry=days_until_expiry,
        expired=expired,
        expiry_warning=expiry_warning,
        sans=sans,
    )


# ── SSL context factory ───────────────────────────────────────────────────────

def build_ssl_context(config: TLSConfig) -> ssl.SSLContext:
    """Build a production-ready SSLContext from TLSConfig.

    Usage with uvicorn::

        ctx = build_ssl_context(tls_config)
        uvicorn.run(app, ssl=ctx)
    """
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)

    # Minimum version
    if config.min_tls_version == "TLSv1.3":
        ctx.minimum_version = ssl.TLSVersion.TLSv1_3
    else:
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2

    # Cipher suites
    try:
        ctx.set_ciphers(config.ciphers or _SECURE_CIPHERS)
    except ssl.SSLError:
        ctx.set_ciphers(_SECURE_CIPHERS)

    # Server certificate + key
    ctx.load_cert_chain(certfile=config.cert_file, keyfile=config.key_file)

    # mTLS — require client certificates
    if config.mtls:
        ctx.verify_mode = ssl.CERT_REQUIRED
        if config.ca_file:
            ctx.load_verify_locations(cafile=config.ca_file)
    else:
        ctx.verify_mode = ssl.CERT_NONE

    # Security options
    # Disable obsolete protocol versions via getattr for cross-version compat
    for _opt in ("OP_NO_SSLv2", "OP_NO_SSLv3", "OP_NO_TLSv1", "OP_NO_TLSv1_1"):
        _val = getattr(ssl, _opt, None)
        if _val is not None:
            ctx.options |= _val
    ctx.options |= ssl.OP_SINGLE_DH_USE
    ctx.options |= ssl.OP_SINGLE_ECDH_USE

    return ctx


# ── Pre-flight check ──────────────────────────────────────────────────────────

def check_tls_config(config: TLSConfig) -> TLSCheckResult:
    """Run a pre-flight TLS/mTLS configuration check."""
    checks: List[Dict[str, Any]] = []
    overall = "pass"
    cert_info: Optional[CertInfo] = None
    ca_info: Optional[CertInfo] = None

    # 1. Parse server cert
    try:
        cert_info = _parse_cert(config.cert_file, config.expiry_warn_days)
        if cert_info.expired:
            checks.append({"name": "cert_expiry", "status": "fail",
                           "message": f"Server certificate EXPIRED {abs(cert_info.days_until_expiry)} days ago"})
            overall = "fail"
        elif cert_info.expiry_warning:
            checks.append({"name": "cert_expiry", "status": "warn",
                           "message": f"Server certificate expires in {cert_info.days_until_expiry} days"})
            if overall == "pass":
                overall = "warn"
        else:
            checks.append({"name": "cert_expiry", "status": "pass",
                           "message": f"Server certificate valid for {cert_info.days_until_expiry} days"})
    except ValueError as exc:
        checks.append({"name": "cert_parse", "status": "fail", "message": str(exc)})
        overall = "fail"

    # 2. Verify key matches cert (load_cert_chain raises on mismatch)
    try:
        _ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        _ctx.load_cert_chain(certfile=config.cert_file, keyfile=config.key_file)
        checks.append({"name": "key_cert_match", "status": "pass",
                       "message": "Private key matches certificate"})
    except ssl.SSLError as exc:
        checks.append({"name": "key_cert_match", "status": "fail",
                       "message": f"Private key/certificate mismatch: {exc}"})
        overall = "fail"

    # 3. TLS version
    version_label = config.min_tls_version
    if config.min_tls_version == "TLSv1.3":
        checks.append({"name": "tls_version", "status": "pass",
                       "message": "Minimum TLS version: TLS 1.3 (strongest)"})
    else:
        checks.append({"name": "tls_version", "status": "warn",
                       "message": "Minimum TLS version: TLS 1.2 — consider upgrading to TLS 1.3"})
        if overall == "pass":
            overall = "warn"

    # 4. mTLS CA cert
    if config.mtls and config.ca_file:
        try:
            ca_info = _parse_cert(config.ca_file, config.expiry_warn_days)
            if ca_info.expired:
                checks.append({"name": "ca_expiry", "status": "fail",
                               "message": f"CA certificate EXPIRED {abs(ca_info.days_until_expiry)} days ago"})
                overall = "fail"
            elif ca_info.expiry_warning:
                checks.append({"name": "ca_expiry", "status": "warn",
                               "message": f"CA certificate expires in {ca_info.days_until_expiry} days"})
                if overall == "pass":
                    overall = "warn"
            else:
                checks.append({"name": "ca_expiry", "status": "pass",
                               "message": f"CA certificate valid for {ca_info.days_until_expiry} days"})
        except ValueError as exc:
            checks.append({"name": "ca_parse", "status": "fail", "message": str(exc)})
            overall = "fail"

    # 5. SSL context build test
    try:
        build_ssl_context(config)
        checks.append({"name": "ssl_context", "status": "pass",
                       "message": "SSL context builds successfully"})
    except Exception as exc:
        checks.append({"name": "ssl_context", "status": "fail",
                       "message": f"SSL context build failed: {exc}"})
        overall = "fail"

    mode = "mtls" if config.mtls else "tls"
    messages = {"pass": "TLS configuration is valid", "warn": "TLS configuration has warnings",
                "fail": "TLS configuration has errors — do not deploy"}

    return TLSCheckResult(
        status=overall,
        mode=mode,
        checks=checks,
        cert_info=cert_info,
        ca_info=ca_info,
        message=messages[overall],
    )


# ── CLI ───────────────────────────────────────────────────────────────────────

def _render_text(result: TLSCheckResult) -> str:
    icon = {"pass": "PASS", "warn": "WARN", "fail": "FAIL"}[result.status]
    lines = [
        "SketchLog TLS/mTLS Pre-flight Check",
        f"Mode   : {result.mode.upper()}",
        f"Result : {icon} — {result.message}",
        "",
    ]
    for c in result.checks:
        s = c["status"].upper().ljust(4)
        lines.append(f"  {s}  {c['name']:<20}  {c['message']}")
    if result.cert_info:
        ci = result.cert_info
        lines += ["", f"  Server cert : {ci.subject}",
                  f"  Expires     : {ci.not_after} ({ci.days_until_expiry} days)"]
    if result.ca_info:
        ca = result.ca_info
        lines += [f"  CA cert     : {ca.subject}",
                  f"  Expires     : {ca.not_after} ({ca.days_until_expiry} days)"]
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="sketchlog-tls-check",
        description="Pre-flight TLS/mTLS configuration check for SketchLog",
    )
    parser.add_argument("--cert", required=True, help="Path to server certificate (PEM)")
    parser.add_argument("--key", required=True, help="Path to private key (PEM)")
    parser.add_argument("--tls-ca", default=None, help="Path to CA certificate for mTLS (PEM)")
    parser.add_argument("--mtls", action="store_true", default=False,
                        help="Enable mTLS (require client certificates)")
    parser.add_argument("--min-tls", default="TLSv1.2", choices=["TLSv1.2", "TLSv1.3"],
                        help="Minimum TLS version (default: TLSv1.2)")
    parser.add_argument("--expiry-warn-days", type=int, default=_EXPIRY_WARN_DAYS,
                        help=f"Warn when cert expires within N days (default: {_EXPIRY_WARN_DAYS})")
    parser.add_argument("--format", choices=["text", "json"], default="text",
                        help="Output format (default: text)")
    args = parser.parse_args(argv)

    try:
        config = TLSConfig(
            cert_file=args.cert,
            key_file=args.key,
            ca_file=args.tls_ca,
            mtls=args.mtls,
            min_tls_version=args.min_tls,
            expiry_warn_days=args.expiry_warn_days,
        )
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    result = check_tls_config(config)

    if args.format == "json":
        print(json.dumps(result.to_dict(), indent=2))
    else:
        print(_render_text(result))

    return 0 if result.status == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
