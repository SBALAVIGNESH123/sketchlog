export function HeroSection() {
  return (
    <section className="hero-section section animate-fade-in-up">
      <div className="container">
        <div className="hero-content">
          <div className="badge">v1.2.0</div>
          <h1>
            Bounded-memory metrics with <span className="text-gradient-primary">explicit guarantees</span>.
          </h1>
          <p className="subtitle delay-100 animate-fade-in-up">
            SketchLog estimates percentiles, cardinality, and frequency with explicit error semantics, a compiled Python fast path, and portable SDKs.
          </p>
          <div className="hero-actions delay-200 animate-fade-in-up">
            <a href="https://github.com/SBALAVIGNESH123/sketchlog" target="_blank" rel="noopener noreferrer" className="btn btn-primary">
              View on GitHub
            </a>
            <a href="#features" className="btn btn-secondary">
              Explore Features
            </a>
          </div>
        </div>
      </div>

      <style>{`
        .hero-section {
          text-align: center;
          padding-top: 10rem;
          padding-bottom: 6rem;
          position: relative;
        }
        .badge {
          display: inline-block;
          padding: 0.25rem 0.75rem;
          border-radius: 9999px;
          background: rgba(6, 182, 212, 0.1);
          color: var(--color-primary);
          border: 1px solid rgba(6, 182, 212, 0.2);
          font-size: 0.875rem;
          font-weight: 600;
          margin-bottom: 1.5rem;
        }
        .hero-content h1 {
          margin-bottom: 1.5rem;
          max-width: 900px;
          margin-left: auto;
          margin-right: auto;
        }
        .hero-actions {
          display: flex;
          gap: 1rem;
          justify-content: center;
          margin-top: 2rem;
        }
      `}</style>
    </section>
  );
}
