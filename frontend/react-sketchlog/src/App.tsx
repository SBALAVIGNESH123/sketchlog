import { useState } from 'react';
import { SketchLogProvider, CDFCurve, QuantileHeatmap, CardinalitySparkline } from './index';
import './index.css';

function App() {
  const [streamUrl, setStreamUrl] = useState('ws://localhost:8000/v1/streams/demo-stream/ws');

  return (
    <div className="min-h-screen bg-slate-950 text-slate-50 p-8 font-sans selection:bg-indigo-500/30">
      <div className="max-w-6xl mx-auto space-y-8">
        
        <header className="flex flex-col md:flex-row md:items-end justify-between gap-4 pb-6 border-b border-white/10">
          <div>
            <h1 className="text-4xl font-bold bg-gradient-to-r from-indigo-400 to-cyan-400 bg-clip-text text-transparent mb-2">
              SketchLog Live Dashboard
            </h1>
            <p className="text-slate-400 text-lg">Real-time distribution analytics via WebSocket.</p>
          </div>
          
          <div className="flex items-center gap-3 bg-white/5 p-1.5 rounded-lg border border-white/10">
            <input 
              type="text" 
              value={streamUrl}
              onChange={(e) => setStreamUrl(e.target.value)}
              className="bg-transparent border-none outline-none text-sm px-3 py-1.5 w-72 text-slate-300 font-mono"
            />
            <div className="w-2 h-2 rounded-full bg-green-500 animate-pulse mr-2" />
          </div>
        </header>

        <SketchLogProvider url={streamUrl}>
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
            
            {/* Main CDF Curve spanning 2 columns */}
            <div className="lg:col-span-2 space-y-6">
              <CDFCurve 
                width={800} 
                height={400} 
                color="#818cf8"
                className="w-full h-[400px]" 
              />
              
              <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                <CardinalitySparkline 
                  width={400} 
                  height={150} 
                  color="#34d399"
                  className="w-full h-[150px]"
                />
                
                <div className="backdrop-blur-md bg-white/5 border border-white/10 p-6 shadow-xl rounded-xl flex flex-col justify-center items-center text-center">
                  <h3 className="text-slate-400 text-sm font-medium uppercase tracking-wider mb-2">Constant Memory</h3>
                  <div className="text-5xl font-light text-white flex items-baseline gap-2">
                    93 <span className="text-xl text-slate-500">KB</span>
                  </div>
                  <p className="text-xs text-slate-500 mt-4">Fixed footprint regardless of event volume</p>
                </div>
              </div>
            </div>

            {/* Heatmap column */}
            <div className="lg:col-span-1 h-full">
              <QuantileHeatmap 
                width={400} 
                height={574} 
                className="w-full h-full"
              />
            </div>
            
          </div>
        </SketchLogProvider>
        
      </div>
    </div>
  );
}

export default App;
