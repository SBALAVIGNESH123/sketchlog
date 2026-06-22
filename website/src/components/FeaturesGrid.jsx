import React from 'react';

const features = [
  {
    title: 'O(1) Memory Constraints',
    description: 'Never worry about cardinality explosions again. SketchLog bounds memory to exactly 93KB per dimension, whether you have 10 events or 10 billion.',
    icon: '📦'
  },
  {
    title: 'Mathematical Accuracy',
    description: 'Guaranteed relative error bounds using the exact same DDSketch algorithm trusted by Datadog. Know your p99 with mathematical certainty.',
    icon: '📐'
  },
  {
    title: 'C++ Core Speed',
    description: 'Built on a ruthless C++ extension wrapped in Pybind11. Ingest streams at millions of events per second with virtually zero overhead.',
    icon: '⚡'
  },
  {
    title: 'Type-Safe SDKs',
    description: 'First-party TypeScript and Go SDKs with built-in connection pooling, exponential backoff, jitter, and typed error handling.',
    icon: '🛡️'
  },
  {
    title: 'DriftSketch Auto-Pilot',
    description: 'Detect distribution shifts and anomalies automatically using advanced streaming statistics and CDF diffing. No manual thresholds.',
    icon: '🧠'
  },
  {
    title: 'Edge & WASM Ready',
    description: 'Run identical sketching logic natively in the browser, Cloudflare Workers, or Deno using our upcoming WebAssembly core.',
    icon: '🌐'
  }
];

export function FeaturesGrid() {
  return (
    <section id="features" className="container section">
      <div className="features-header text-center animate-fade-in-up">
        <h2>Engineering at the Edge</h2>
        <p className="subtitle">Built for platform engineers who need absolute reliability and predictable resource utilization under massive scale.</p>
      </div>
      
      <div className="grid">
        {features.map((feature, i) => (
          <div key={i} className={`glass-panel feature-card delay-${(i % 3 + 1) * 100} animate-fade-in-up`}>
            <div className="feature-icon">{feature.icon}</div>
            <h3>{feature.title}</h3>
            <p>{feature.description}</p>
          </div>
        ))}
      </div>

      <style>{`
        .text-center { text-align: center; }
        .features-header { margin-bottom: 4rem; }
        
        .grid {
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
          gap: 2rem;
        }
        
        .feature-card {
          padding: 2rem;
          display: flex;
          flex-direction: column;
          align-items: flex-start;
        }
        
        .feature-icon {
          font-size: 2.5rem;
          margin-bottom: 1.5rem;
          background: rgba(255, 255, 255, 0.05);
          width: 60px;
          height: 60px;
          display: flex;
          align-items: center;
          justify-content: center;
          border-radius: 12px;
          border: 1px solid rgba(255, 255, 255, 0.1);
        }
        
        .feature-card h3 { margin-bottom: 0.75rem; }
        .feature-card p { color: var(--color-text-muted); font-size: 0.95rem; }
      `}</style>
    </section>
  );
}
