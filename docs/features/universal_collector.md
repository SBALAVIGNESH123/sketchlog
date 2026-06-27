# Universal Collector

The **Universal Collector** is a zero-instrumentation sidecar or daemon that automatically intercepts and sketches network and system metrics using **eBPF** (Extended Berkeley Packet Filter). 

This means you can capture accurate latencies, throughput, and error rates *without modifying a single line of application code*.

## eBPF Integration

The collector loads our highly optimized eBPF program (`ebpf/bpf_code.c`) into the kernel. It attaches to:
1. `kprobes` for socket send/recv (to measure network latencies).
2. `uprobes` for standard library HTTP/gRPC calls (Go, Node.js, Python).

As packets flow, the eBPF program populates an in-kernel `DDSketch` implementation. Periodically, the SketchLog agent reads these kernel maps in O(1) time and pushes the compressed sketch to the central mesh.

## Getting Started

### 1. Prerequisites

- A Linux kernel version 5.4 or higher (for `bpf_spin_lock` and map-in-map support).
- Root privileges (to load the eBPF program).

### 2. Running the Collector

You can run the collector as a standalone daemon or a DaemonSet in Kubernetes:

```bash
sudo sketchlog-collector --namespace k8s-cluster-1
```

By default, the collector will auto-discover running containers and tag streams with the container name (e.g., `k8s-cluster-1/payment-pod`).

## Advanced Configuration

You can filter which processes or ports the collector monitors using a YAML config file:

```yaml
collector:
  namespace: "production"
  bpf:
    attach_ports:
      - 80
      - 443
      - 8080
    ignore_namespaces:
      - "kube-system"
  export:
    mesh_address: "http://sketchlog-mesh.internal:8000"
    flush_interval_ms: 1000
```
