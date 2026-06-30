# Linux eBPF collector

The collector is a Linux/BCC TCP timing collector. It attaches kprobes to
`tcp_sendmsg`, `tcp_cleanup_rbuf`, and `tcp_close`, periodically drains bounded
kernel buckets, and sends validated sketches to the server merge endpoint.
It does not claim HTTP/gRPC semantics, language uprobes, process attribution,
or container discovery.

Requirements:

- Linux with BCC-compatible kernel headers and BCC Python bindings;
- root or the kernel capabilities needed to load and attach BPF programs;
- network access to a SketchLog server.

```bash
sudo sketchlog-collector \
  --server https://sketchlog.example \
  --namespace production \
  --stream-id tcp-timing \
  --auth-token "$SKETCHLOG_AUTH_TOKEN" \
  --flush-interval 5
```

`GET /health` and `GET /ready` are served on loopback port 9091 by default.
Readiness becomes degraded after an export failure and reports buffered event
count. Failed exports are merged back into the local buffer. SIGINT/SIGTERM
stops probes and the health server cleanly.

The CLI exits non-zero on non-Linux systems, missing BCC, missing privileges,
or invalid intervals. Kernel-level validation requires a privileged Linux CI
runner and is separate from the mocked unit tests.
