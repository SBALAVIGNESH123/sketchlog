import React, { useMemo, useRef, useEffect } from 'react';
import * as d3 from 'd3';
import { useSketchLog } from './SketchLogContext';
import { counterToNumber } from '../counter';


function getBucketValue(alpha: number, index: number): number {
  const gamma = (1 + alpha) / (1 - alpha);
  return (2.0 / (1.0 + gamma)) * Math.pow(gamma, index);
}

export interface CDFCurveProps {
  width?: number;
  height?: number;
  color?: string;
  className?: string;
}

export const CDFCurve: React.FC<CDFCurveProps> = ({ 
  width = 600, 
  height = 300, 
  color = '#8b5cf6', // Violet
  className = '' 
}) => {
  const { state, isConnected } = useSketchLog();
  const svgRef = useRef<SVGSVGElement>(null);

  // Compute CDF points
  const points = useMemo(() => {
    if (!state || !state.latency) return [];
    
    const lat = state.latency;
    let accumulated = 0;
    const pts: { x: number, y: number }[] = [];
    const totalCount = counterToNumber(lat.count);

    if (totalCount === 0) return [];

    // Negative buckets (not typical for latency, but handled for completeness)
    const negIndices = Object.keys(lat.negative).map(Number).sort((a, b) => b - a); // reverse sort
    for (const idx of negIndices) {
      accumulated += counterToNumber(lat.negative[idx]);
      pts.push({ x: -getBucketValue(lat.alpha, idx), y: accumulated / totalCount });
    }

    if (counterToNumber(lat.zero_count) > 0) {
      accumulated += counterToNumber(lat.zero_count);
      pts.push({ x: 0, y: accumulated / totalCount });
    }

    const posIndices = Object.keys(lat.positive).map(Number).sort((a, b) => a - b);
    for (const idx of posIndices) {
      accumulated += counterToNumber(lat.positive[idx]);
      pts.push({ x: getBucketValue(lat.alpha, idx), y: accumulated / totalCount });
    }
    
    // Ensure it ends at 1.0 (sometimes float math is off, but count matches)
    if (pts.length > 0) {
      pts[pts.length - 1].y = 1.0;
    }

    return pts;
  }, [state]);

  useEffect(() => {
    if (!svgRef.current || points.length === 0) return;

    const svg = d3.select(svgRef.current);
    svg.selectAll("*").remove();

    const margin = { top: 20, right: 30, bottom: 40, left: 50 };
    const innerWidth = width - margin.left - margin.right;
    const innerHeight = height - margin.top - margin.bottom;

    const x = d3.scaleLog()
      .domain([Math.max(0.1, points[0].x > 0 ? points[0].x : 0.1), Math.max(0.1, d3.max(points, d => d.x) || 100)])
      .range([0, innerWidth])
      .clamp(true);

    const y = d3.scaleLinear()
      .domain([0, 1])
      .range([innerHeight, 0]);

    const g = svg.append("g")
      .attr("transform", `translate(${margin.left},${margin.top})`);

    // Grid lines
    g.append("g")
      .attr("class", "grid")
      .attr("transform", `translate(0,${innerHeight})`)
      .call(d3.axisBottom(x).ticks(5).tickSize(-innerHeight).tickFormat(() => ""))
      .attr("stroke-opacity", 0.1)
      .attr("stroke", "currentColor");

    g.append("g")
      .attr("class", "grid")
      .call(d3.axisLeft(y).ticks(5).tickSize(-innerWidth).tickFormat(() => ""))
      .attr("stroke-opacity", 0.1)
      .attr("stroke", "currentColor");

    // X Axis
    g.append("g")
      .attr("transform", `translate(0,${innerHeight})`)
      .call(d3.axisBottom(x).ticks(5, "~s"))
      .attr("color", "rgba(255, 255, 255, 0.5)");

    // Y Axis
    g.append("g")
      .call(d3.axisLeft(y).tickFormat(d3.format(".0%")))
      .attr("color", "rgba(255, 255, 255, 0.5)");

    // Line generator
    const line = d3.line<{ x: number, y: number }>()
      .x(d => x(Math.max(0.1, d.x)))
      .y(d => y(d.y))
      .curve(d3.curveStepAfter);

    // Area generator
    const area = d3.area<{ x: number, y: number }>()
      .x(d => x(Math.max(0.1, d.x)))
      .y0(innerHeight)
      .y1(d => y(d.y))
      .curve(d3.curveStepAfter);

    // Gradient
    const gradientId = `cdf-gradient-${Math.random().toString(36).substring(2)}`;
    const defs = svg.append("defs");
    const gradient = defs.append("linearGradient")
      .attr("id", gradientId)
      .attr("x1", "0%")
      .attr("y1", "0%")
      .attr("x2", "0%")
      .attr("y2", "100%");

    gradient.append("stop")
      .attr("offset", "0%")
      .attr("stop-color", color)
      .attr("stop-opacity", 0.4);

    gradient.append("stop")
      .attr("offset", "100%")
      .attr("stop-color", color)
      .attr("stop-opacity", 0.0);

    // Draw Area
    g.append("path")
      .datum(points)
      .attr("fill", `url(#${gradientId})`)
      .attr("d", area)
      .style("transition", "d 0.5s ease");

    // Draw Line
    g.append("path")
      .datum(points)
      .attr("fill", "none")
      .attr("stroke", color)
      .attr("stroke-width", 2)
      .attr("stroke-linejoin", "round")
      .attr("stroke-linecap", "round")
      .attr("d", line)
      .style("transition", "d 0.5s ease");

  }, [points, width, height, color]);

  return (
    <div className={`relative rounded-xl overflow-hidden backdrop-blur-md bg-white/5 border border-white/10 p-4 shadow-2xl ${className}`}>
      <h3 className="text-white/90 font-medium mb-2 text-sm">Latency CDF</h3>
      <div className="absolute top-4 right-4 flex items-center gap-2">
        <span className="relative flex h-2 w-2">
          {isConnected && <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-green-400 opacity-75"></span>}
          <span className={`relative inline-flex rounded-full h-2 w-2 ${isConnected ? 'bg-green-500' : 'bg-red-500'}`}></span>
        </span>
        <span className="text-xs text-white/50">{isConnected ? 'LIVE' : 'OFFLINE'}</span>
      </div>
      <svg ref={svgRef} width={width} height={height} className="w-full h-full text-white/80" style={{ minHeight: height }} />
    </div>
  );
};
