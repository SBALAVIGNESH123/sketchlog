import React from 'react';

export function CodePreview() {
  return (
    <div className="container section delay-300 animate-fade-in-up">
      <div className="glass-panel code-terminal">
        <div className="terminal-header">
          <div className="dot close"></div>
          <div className="dot minimize"></div>
          <div className="dot maximize"></div>
          <div className="terminal-title">quickstart.py</div>
        </div>
        <div className="code-block">
<pre><code><span className="token-keyword">from</span> sketchlog <span className="token-keyword">import</span> StreamLog

<span className="token-comment"># Initialize high-performance sketching engine</span>
log = StreamLog()

<span className="token-comment"># Ingest millions of events instantly (C++ fast-path)</span>
log.<span className="token-function">add_latencies</span>(<span className="token-string">"api_latency"</span>, [<span className="token-number">42.5</span>, <span className="token-number">12.1</span>, <span className="token-number">55.0</span>, ...<span className="token-number">100_000_000</span> more])

<span className="token-comment"># Query exact percentiles with mathematical guarantees</span>
p99 = log.<span className="token-function">p99</span>(<span className="token-string">"api_latency"</span>)
<span className="token-function">print</span>(<span className="token-string">{"f\"p99 Latency: {p99}ms\""}</span>)

<span className="token-comment"># Memory footprint? Consistently &lt; 100 KB.</span></code></pre>
        </div>
      </div>

      <style>{`
        .code-terminal {
          max-width: 800px;
          margin: 0 auto;
          overflow: hidden;
        }
        .terminal-header {
          display: flex;
          align-items: center;
          padding: 1rem;
          background: rgba(0, 0, 0, 0.5);
          border-bottom: 1px solid rgba(255, 255, 255, 0.05);
        }
        .dot {
          width: 12px;
          height: 12px;
          border-radius: 50%;
          margin-right: 8px;
        }
        .close { background: #ef4444; }
        .minimize { background: #eab308; }
        .maximize { background: #22c55e; }
        .terminal-title {
          margin-left: auto;
          margin-right: auto;
          font-family: var(--font-mono);
          font-size: 0.85rem;
          color: var(--color-text-muted);
        }
        .code-block {
          border: none;
          border-radius: 0;
          background: transparent;
        }
      `}</style>
    </div>
  );
}
