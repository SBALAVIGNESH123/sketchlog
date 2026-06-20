# SketchLog Troubleshooting Guide

This guide covers common diagnostic commands and support bundle generation for the SketchLog server.

## Generating a Support Bundle

When engaging with the support or platform teams, please provide a safe diagnostics bundle. **Do not** provide raw memory dumps without consulting security, as stream IDs or unique item names may contain PII depending on the integration.

To generate a bundle:

```bash
#!/bin/bash
# collect_bundle.sh
mkdir -p /tmp/sketchlog_support
date > /tmp/sketchlog_support/timestamp.txt

# 1. Collect Metrics
curl -s http://localhost:8000/metrics > /tmp/sketchlog_support/metrics.txt

# 2. Collect Readiness/Health Status
curl -s -v http://localhost:8000/ready > /tmp/sketchlog_support/ready.txt 2>&1
curl -s -v http://localhost:8000/health > /tmp/sketchlog_support/health.txt 2>&1

# 3. Collect Recent Logs (Redacted)
# We assume SketchLog is running under systemd or similar container orchestrator.
# The structlog output is JSON, so we filter it with jq to remove any sensitive fields if necessary.
journalctl -u sketchlog --no-pager -n 5000 | jq -c 'del(.sensitive_field)' > /tmp/sketchlog_support/logs.json

tar -czf /tmp/sketchlog_support.tar.gz -C /tmp sketchlog_support
echo "Bundle created at /tmp/sketchlog_support.tar.gz"
```

## Common Diagnostic Commands

### Checking Active Configuration
Environment variables dictate the limits of the server. Ensure they match your expectations:

```bash
cat /proc/$(pidof sketchlog)/environ | tr '\0' '\n' | grep SKETCHLOG_
```

### Checking Open File Descriptors
If the server is failing to accept new connections, it may have exhausted its file descriptor limit:

```bash
lsof -p $(pidof sketchlog) | wc -l
```

### Live Profiling
If CPU usage is unusually high, you can attach `py-spy` to generate a flamegraph without stopping the process:

```bash
sudo py-spy record -o /tmp/profile.svg --pid $(pidof sketchlog)
```
