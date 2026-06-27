#include <uapi/linux/ptrace.h>
#include <net/sock.h>
#include <bcc/proto.h>

#define MAX_BUCKETS 2048

// Userspace provides the boundaries for the DDSketch buckets (in nanoseconds)
BPF_ARRAY(bucket_boundaries, u64, MAX_BUCKETS);

// Kernel accumulates counts directly into these buckets.
// We use a PERCPU array to avoid lock contention on high throughput.
BPF_PERCPU_ARRAY(bucket_counts, u64, MAX_BUCKETS);

// Map to track socket timestamps for latency calculation
BPF_HASH(sock_starts, struct sock *, u64);

// Map to track the number of active buckets configured by userspace
BPF_ARRAY(config_map, u32, 1);

static inline void record_latency(u64 latency_ns) {
    u32 zero = 0;
    u32 *num_buckets = config_map.lookup(&zero);
    if (!num_buckets || *num_buckets == 0 || *num_buckets > MAX_BUCKETS) {
        return;
    }
    u32 total = *num_buckets;
    
    // Binary search for the correct bucket index.
    // eBPF requires bounded loops. 11 iterations covers up to 2048 buckets.
    u32 low = 0;
    u32 high = total - 1;
    u32 mid = 0;
    
    #pragma unroll
    for (int i = 0; i < 11; i++) {
        if (low > high) {
            break;
        }
        mid = low + ((high - low) >> 1);
        u64 *bound = bucket_boundaries.lookup(&mid);
        if (!bound) break;
        
        if (latency_ns < *bound) {
            if (mid == 0) break;
            high = mid - 1;
        } else {
            low = mid + 1;
        }
    }
    
    // Increment the appropriate bucket in the PERCPU array
    u64 *count = bucket_counts.lookup(&mid);
    if (count) {
        (*count)++;
    }
}

// Hook tcp_sendmsg to mark the start of a request
int trace_tcp_sendmsg(struct pt_regs *ctx, struct sock *sk) {
    u64 ts = bpf_ktime_get_ns();
    sock_starts.update(&sk, &ts);
    return 0;
}

// Hook tcp_cleanup_rbuf to mark the receipt of a response
int trace_tcp_cleanup_rbuf(struct pt_regs *ctx, struct sock *sk, int copied) {
    if (copied <= 0) return 0;
    
    u64 *ts = sock_starts.lookup(&sk);
    if (!ts) {
        return 0; // Not tracking this socket or missing sendmsg
    }
    
    u64 now = bpf_ktime_get_ns();
    if (now > *ts) {
        u64 latency_ns = now - *ts;
        record_latency(latency_ns);
    }
    
    // Remove the timestamp to avoid double-counting until the next send
    sock_starts.delete(&sk);
    return 0;
}

// Hook tcp_close to clean up any stale timestamps
int trace_tcp_close(struct pt_regs *ctx, struct sock *sk) {
    sock_starts.delete(&sk);
    return 0;
}
