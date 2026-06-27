# Sketch Mesh (P2P Federation)

The **Sketch Mesh** is a distributed, peer-to-peer federation layer that allows multiple SketchLog servers to seamlessly combine their sketches in real-time. Because Sketches are mathematically mergeable (a union of two HyperLogLogs is exactly equivalent to a single HyperLogLog of all items, while DDSketch merges are bounded-approximate), SketchLog naturally scales horizontally.

## How It Works

Instead of relying on a centralized database or heavy message brokers like Kafka, SketchLog instances form a mesh using a **Gossip Protocol**.

1. **Anti-Entropy Digest**: Nodes periodically exchange small version vectors to determine which node has the freshest data for a given `(namespace, stream_id)`.
2. **Delta Sync**: If a node detects a peer has newer data, it requests the compressed sketch payload.
3. **In-Memory Merge**: The remote sketch is merged locally into the thread-safe `StreamLog` in microseconds.

This guarantees eventual consistency across all nodes without blocking ingestion paths.

## Configuration

To enable clustered mode, you just need to start the servers with the `SKETCHLOG_PEERS` and `SKETCHLOG_ADVERTISED_ADDRESS` environment variables.

### Node 1 (192.168.1.100)
```bash
export SKETCHLOG_ADVERTISED_ADDRESS="http://192.168.1.100:8000"
export SKETCHLOG_PEERS="http://192.168.1.101:8000"
uvicorn sketchlog.server:app --host 0.0.0.0 --port 8000
```

### Node 2 (192.168.1.101)
```bash
export SKETCHLOG_ADVERTISED_ADDRESS="http://192.168.1.101:8000"
export SKETCHLOG_PEERS="http://192.168.1.100:8000"
uvicorn sketchlog.server:app --host 0.0.0.0 --port 8000
```

Any API request (like `/v1/namespaces/default/streams/app/metrics`) made to *either* node will automatically return the globally merged statistics.
