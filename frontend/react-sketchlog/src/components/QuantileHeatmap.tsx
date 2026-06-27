import React, { useEffect, useRef, useState, useMemo } from 'react';
import * as d3 from 'd3';
import { useSketchLog } from './SketchLogProvider';

function getBucketValue(alpha: number, index: number): number {
  const gamma = (1 + alpha) / (1 - alpha);
  return (2.0 / (1.0 + gamma)) * Math.pow(gamma, index);
}

export interface QuantileHeatmapProps {
  width?: number;
  height?: number;
  colorScheme?: readonly string[] | string;
  historySize?: number;
  className?: string;
}

export const QuantileHeatmap: React.FC<QuantileHeatmapProps> = ({
  width = 600,
  height = 300,
  colorScheme = d3.interpolateInferno,
  historySize = 30,
  className = ''
}) => {
  const { state, isConnected } = useSketchLog();
  const svgRef = useRef<SVGSVGElement>(null);
  
  // Keep rolling history of bucket distributions
  const [history, setHistory] = useState<{ time: number; total: number; bins: Map<number, number> }[]>([]);

  useEffect(() => {
    if (!state || !state.latency) return;

    const lat = state.latency;
    const now = Date.now();
    
    // Create a new snapshot of buckets
    const bins = new Map<number, number>();
    
    const posIndices = Object.keys(lat.positive).map(Number);
    for (const idx of posIndices) {
      bins.set(idx, lat.positive[idx]);
    }

    setHistory(prev => {
      const next = [...prev, { time: now, total: lat.count, bins }];
      if (next.length > historySize) {
        return next.slice(next.length - historySize);
      }
      return next;
    });
  }, [state, historySize]);

  useEffect(() => {
    if (!svgRef.current || history.length === 0) return;

    const svg = d3.select(svgRef.current);
    svg.selectAll("*").remove();

    const margin = { top: 20, right: 10, bottom: 30, left: 50 };
    const innerWidth = width - margin.left - margin.right;
    const innerHeight = height - margin.top - margin.bottom;

    // Collect all unique bucket indices across history to determine Y axis
    let minIdx = Infinity;
    let maxIdx = -Infinity;
    let maxCount = 0;

    // Calculate diffs between snapshots (since SketchLog is cumulative)
    const diffs = [];
    for (let i = 1; i < history.length; i++) {
      const prev = history[i - 1];
      const curr = history[i];
      const diffBins = new Map<number, number>();
      
      for (const [idx, count] of curr.bins.entries()) {
        const prevCount = prev.bins.get(idx) || 0;
        const diff = Math.max(0, count - prevCount);
        if (diff > 0) {
          diffBins.set(idx, diff);
          minIdx = Math.min(minIdx, idx);
          maxIdx = Math.max(maxIdx, idx);
          maxCount = Math.max(maxCount, diff);
        }
      }
      diffs.push({ time: curr.time, bins: diffBins });
    }

    if (diffs.length === 0 || maxIdx === -Infinity) return;

    const alpha = state?.latency?.alpha || 0.01;

    // X Axis: Time
    const x = d3.scaleTime()
      .domain([history[0].time, history[history.length - 1].time])
      .range([0, innerWidth]);

    // Y Axis: Buckets (logarithmic-like since buckets are log-spaced)
    const y = d3.scaleLinear()
      .domain([minIdx - 1, maxIdx + 1])
      .range([innerHeight, 0]);

    // Color scale
    const color = typeof colorScheme === 'string' 
      ? d3.scaleSequential(d3.interpolate(d3.color('rgba(255,255,255,0)') as any, colorScheme)).domain([0, maxCount])
      : d3.scaleSequential(colorScheme as any).domain([0, maxCount]);

    const g = svg.append("g")
      .attr("transform", `translate(${margin.left},${margin.top})`);

    // Draw Heatmap Cells
    const cellWidth = innerWidth / Math.max(1, (historySize - 1));
    const cellHeight = Math.abs(y(minIdx) - y(minIdx + 1));

    diffs.forEach((d, i) => {
      for (const [idx, count] of d.bins.entries()) {
        g.append("rect")
          .attr("x", x(d.time) - cellWidth)
          .attr("y", y(idx) - cellHeight / 2)
          .attr("width", cellWidth + 1)
          .attr("height", cellHeight + 1)
          .attr("fill", color(count) as string)
          .attr("rx", 2)
          .attr("ry", 2)
          .attr("opacity", 0.9);
      }
    });

    // X Axis
    g.append("g")
      .attr("transform", `translate(0,${innerHeight})`)
      .call(d3.axisBottom(x).ticks(5))
      .attr("color", "rgba(255, 255, 255, 0.5)");

    // Y Axis (custom tick formatting to show bucket values)
    const yAxis = d3.axisLeft(y)
      .tickValues(d3.range(minIdx, maxIdx + 1, Math.max(1, Math.floor((maxIdx - minIdx) / 5))))
      .tickFormat(idx => d3.format(".1f")(getBucketValue(alpha, idx as number)));

    g.append("g")
      .call(yAxis)
      .attr("color", "rgba(255, 255, 255, 0.5)");

  }, [history, width, height, colorScheme, state]);

  return (
    <div className={`relative rounded-xl overflow-hidden backdrop-blur-md bg-white/5 border border-white/10 p-4 shadow-2xl ${className}`}>
      <h3 className="text-white/90 font-medium mb-2 text-sm">Quantile Heatmap (Live)</h3>
      <div className="absolute top-4 right-4 flex items-center gap-2">
        <span className="relative flex h-2 w-2">
          {isConnected && <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-blue-400 opacity-75"></span>}
          <span className={`relative inline-flex rounded-full h-2 w-2 ${isConnected ? 'bg-blue-500' : 'bg-red-500'}`}></span>
        </span>
      </div>
      <svg ref={svgRef} width={width} height={height} className="w-full h-full text-white/80" style={{ minHeight: height }} />
    </div>
  );
};
