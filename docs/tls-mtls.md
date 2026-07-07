# TLS / mTLS Production Hardening

SketchLog ships with **TLS 1.2+ out of the box** (`--tls-cert` / `--tls-key`).
This guide covers mutual TLS (mTLS), cipher hardening, certificate management,
and the `sketchlog-tls-check` pre-flight CLI.

---

## Quick start — TLS

```bash
# Generate a self-signed cert (development only)
openssl req -x509 -newkey rsa:4096 -keyout key.pem -out cert.pem \
  -days 365 -nodes -subj "/CN=sketchlog.example.com"

# Start server with TLS
sketchlog-server --tls-cert cert.pem --tls-key key.pem --port 7700
```

## Quick start — mTLS

```bash
# 1. Create CA
openssl req -x509 -newkey rsa:4096 -keyout ca-key.pem -out ca-cert.pem \
  -days 3650 -nodes -subj "/CN=SketchLog CA"

# 2. Create server cert signed by CA
openssl req -newkey rsa:4096 -keyout server-key.pem -out server-req.pem \
  -nodes -subj "/CN=sketchlog-server"
openssl x509 -req -in server-req.pem -CA ca-cert.pem -CAkey ca-key.pem \
  -CAcreateserial -out server-cert.pem -days 365

# 3. Create client cert signed by CA
openssl req -newkey rsa:4096 -keyout client-key.pem -out client-req.pem \
  -nodes -subj "/CN=sketchlog-agent"
openssl x509 -req -in client-req.pem -CA ca-cert.pem -CAkey ca-key.pem \
  -CAcreateserial -out client-cert.pem -days 365

# 4. Start server with mTLS
sketchlog-server \
  --tls-cert server-cert.pem \
  --tls-key  server-key.pem \
  --tls-ca   ca-cert.pem \
  --mtls \
  --port 7700
```

---

## Pre-flight check

Run before every deployment to catch expired certs, key mismatches, and weak TLS versions:

```bash
sketchlog-tls-check --cert cert.pem --key key.pem
sketchlog-tls-check --cert cert.pem --key key.pem --ca ca.pem --mtls
sketchlog-tls-check --cert cert.pem --key key.pem --format json
```

### Exit codes

| Code | Meaning |
|---|---|
| `0` | All checks passed |
| `1` | Warning or failure (expiry warning, TLS 1.2 advisory) |
| `2` | Bad config / missing files |

---

## Python API

```python
from sketchlog.tls_config import TLSConfig, build_ssl_context, check_tls_config

# Validate and build SSL context
config = TLSConfig(
    cert_file="cert.pem",
    key_file="key.pem",
    ca_file="ca.pem",     # required for mTLS
    mtls=True,
    min_tls_version="TLSv1.3",
    expiry_warn_days=30,
)

# Run pre-flight checks
result = check_tls_config(config)
print(result.status)   # "pass" / "warn" / "fail"

# Build SSL context for uvicorn
ssl_ctx = build_ssl_context(config)
import uvicorn
uvicorn.run(app, ssl=ssl_ctx, host="0.0.0.0", port=7700)
```

---

## Nginx reverse proxy (recommended for production)

```nginx
server {
    listen 443 ssl;
    server_name sketchlog.example.com;

    ssl_certificate     /etc/sketchlog/cert.pem;
    ssl_certificate_key /etc/sketchlog/key.pem;

    # mTLS — require client certificates
    ssl_client_certificate /etc/sketchlog/ca.pem;
    ssl_verify_client      on;

    # Cipher hardening
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers ECDHE+AESGCM:ECDHE+CHACHA20:DHE+AESGCM:!aNULL:!eNULL;
    ssl_prefer_server_ciphers on;
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 10m;

    # HSTS
    add_header Strict-Transport-Security "max-age=63072000; includeSubDomains" always;

    location / {
        proxy_pass http://127.0.0.1:7700;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header X-SSL-Client-Cert $ssl_client_escaped_cert;
    }
}
```

---

## Certificate rotation

```bash
# 1. Generate new cert
openssl req -x509 -newkey rsa:4096 -keyout new-key.pem -out new-cert.pem \
  -days 365 -nodes -subj "/CN=sketchlog.example.com"

# 2. Pre-flight check
sketchlog-tls-check --cert new-cert.pem --key new-key.pem

# 3. Rolling restart (zero downtime with Kubernetes or Docker)
cp new-cert.pem cert.pem && cp new-key.pem key.pem
kill -HUP $(pidof sketchlog-server)  # or kubectl rollout restart
```

---

## Caveats

1. **Self-signed certs** are only for development — use a CA-signed cert in production.
2. **mTLS** requires every client to present a valid certificate — configure all SDK clients.
3. **TLS 1.2** is the minimum; TLS 1.3 is strongly preferred and the sketchlog-tls-check will warn on TLS 1.2.
4. **Certificate expiry** — set up automated rotation (Let's Encrypt + certbot, or cert-manager on Kubernetes).
5. **nginx/Caddy in front** — for high-traffic deployments, terminate TLS at the reverse proxy for better performance.
6. **Key permissions** — private keys must be `chmod 600` and owned by the server process user.
