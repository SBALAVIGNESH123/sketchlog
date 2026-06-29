const features = [
  {
    title: 'O(1) Memory Constraints',
    description: 'SketchLog bounds each default stream with capped sketch dimensions and a 1,024-bucket-per-sign DDSketch store.',
    icon: '📦'
  },
  {
    title: 'Mathematical Accuracy',
    description: 'DDSketch provides a configured relative mapping-error bound; HLL and Count-Min expose their approximation semantics.',
    icon: '📐'
  },
  {
    title: 'C++ Core Speed',
    description: 'Python wheels include a Pybind11 C++ fast path, with a deterministic Python backend for portability and mesh interoperability.',
    icon: '⚡'
  },
  {
    title: 'Type-Safe SDKs',
    description: 'First-party TypeScript and Go SDKs with built-in connection pooling, exponential backoff, jitter, and typed error handling.',
    icon: '🛡️'
  },
  {
    title: 'DriftSketch Auto-Pilot',
    description: 'Compare bounded distribution summaries and configure explicit sensitivity thresholds for drift and anomaly signals.',
    icon: '🧠'
  },
  {
    title: 'Edge & WASM Ready',
    description: 'Use the published WebAssembly package in browsers and compatible edge JavaScript runtimes.',
    icon: '🌐'
  }
];

export function FeaturesGrid() {
  return (
    <section id="features" className="container section">
      <div className="features-header text-center animate-fade-in-up">
        <h2>Engineering at the Edge</h2>
        <p className="subtitle">Built for platform engineers who need bounded resources, observable failure modes, and reproducible performance evidence.</p>
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
