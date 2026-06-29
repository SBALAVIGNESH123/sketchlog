import './index.css';
import { HeroSection } from './components/HeroSection';
import { CodePreview } from './components/CodePreview';
import { FeaturesGrid } from './components/FeaturesGrid';

function App() {
  return (
    <>
      <div className="bg-glow"></div>

      <header className="navbar glass-panel">
        <div className="container nav-content">
          <div className="logo">
            <span className="logo-icon">📊</span>
            <span className="logo-text">SketchLog</span>
          </div>
          <nav>
            <a href="https://github.com/SBALAVIGNESH123/sketchlog" target="_blank" rel="noopener noreferrer" className="nav-link">GitHub</a>
            <a href="#features" className="nav-link">Features</a>
          </nav>
        </div>
      </header>

      <main>
        <HeroSection />
        <CodePreview />
        <FeaturesGrid />
      </main>

      <footer className="footer section">
        <div className="container text-center">
          <p className="subtitle">© {new Date().getFullYear()} SketchLog. Open-source observability engine.</p>
        </div>
      </footer>

      <style>{`
        .navbar {
          position: fixed;
          top: 0;
          left: 0;
          width: 100%;
          z-index: 100;
          border-radius: 0;
          border-top: none;
          border-left: none;
          border-right: none;
        }
        .nav-content {
          display: flex;
          justify-content: space-between;
          align-items: center;
          height: 70px;
        }
        .logo {
          display: flex;
          align-items: center;
          gap: 0.5rem;
          font-weight: 700;
          font-size: 1.25rem;
          letter-spacing: -0.02em;
        }
        .logo-icon { font-size: 1.5rem; }
        nav { display: flex; gap: 1.5rem; }
        .nav-link {
          color: var(--color-text-muted);
          text-decoration: none;
          font-weight: 500;
          transition: color var(--transition-fast);
        }
        .nav-link:hover { color: var(--color-text); }
        .text-center { text-align: center; }
        .footer { padding-top: 4rem; padding-bottom: 4rem; border-top: 1px solid rgba(255, 255, 255, 0.05); }
      `}</style>
    </>
  );
}

export default App;
