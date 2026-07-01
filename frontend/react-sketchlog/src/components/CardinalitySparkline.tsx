import React, { useEffect, useRef, useState } from 'react';
import * as d3 from 'd3';
import { useSketchLog } from './SketchLogContext';
import { counterToNumber } from '../counter';
import { useResponsiveWidth } from './useResponsiveWidth';

export interface CardinalitySparklineProps {
  width?: number;
  height?: number;
  color?: string;
  historySize?: number;
  className?: string;
}

export const CardinalitySparkline: React.FC<CardinalitySparklineProps> = ({
  width = 300,
  height = 100,
  color = '#10b981', // Emerald
  historySize = 60,
  className = ''
}) => {
  const { state } = useSketchLog();
  const svgRef = useRef<SVGSVGElement>(null);
  const renderWidth = useResponsiveWidth(svgRef, width);
  
  const [history, setHistory] = useState<{ time: number; value: number }[]>([]);

  useEffect(() => {
    if (!state) return;

    const val = counterToNumber(state.metrics?.unique_count ?? 0);
    const now = Date.now();

    const update = window.setTimeout(() => {
      setHistory(prev => {
        const next = [...prev, { time: now, value: val }];
        return next.slice(-historySize);
      });
    }, 0);
    return () => window.clearTimeout(update);
  }, [state, historySize]);

  useEffect(() => {
    if (!svgRef.current || history.length < 2) return;

    const svg = d3.select(svgRef.current);
    svg.selectAll("*").remove();

    const margin = { top: 5, right: 5, bottom: 5, left: 5 };
    const innerWidth = Math.max(1, renderWidth - margin.left - margin.right);
    const innerHeight = height - margin.top - margin.bottom;

    const x = d3.scaleTime()
      .domain(d3.extent(history, d => d.time) as [number, number])
      .range([0, innerWidth]);

    const y = d3.scaleLinear()
      .domain([0, d3.max(history, d => d.value) || 10])
      .range([innerHeight, 0]);

    const g = svg.append("g")
      .attr("transform", `translate(${margin.left},${margin.top})`);

    const line = d3.line<{ time: number, value: number }>()
      .x(d => x(d.time))
      .y(d => y(d.value))
      .curve(d3.curveMonotoneX);

    const area = d3.area<{ time: number, value: number }>()
      .x(d => x(d.time))
      .y0(innerHeight)
      .y1(d => y(d.value))
      .curve(d3.curveMonotoneX);

    const gradientId = `spark-gradient-${Math.random().toString(36).substring(2)}`;
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
      .attr("stop-opacity", 0.3);

    gradient.append("stop")
      .attr("offset", "100%")
      .attr("stop-color", color)
      .attr("stop-opacity", 0.0);

    g.append("path")
      .datum(history)
      .attr("fill", `url(#${gradientId})`)
      .attr("d", area)
      .style("transition", "d 0.3s linear");

    g.append("path")
      .datum(history)
      .attr("fill", "none")
      .attr("stroke", color)
      .attr("stroke-width", 2)
      .attr("stroke-linecap", "round")
      .attr("d", line)
      .style("transition", "d 0.3s linear");
      
    // Add current value text
    const lastVal = history[history.length - 1].value;
    svg.append("text")
      .attr("x", margin.left)
      .attr("y", margin.top + 15)
      .text(d3.format("~s")(lastVal))
      .attr("fill", "white")
      .attr("font-size", "14px")
      .attr("font-weight", "600");

  }, [history, renderWidth, height, color]);

  return (
    <div className={`relative rounded-xl overflow-hidden backdrop-blur-md bg-white/5 border border-white/10 p-2 shadow-xl flex flex-col ${className}`}>
      <h3 className="text-white/70 font-medium mb-1 text-xs px-2 pt-1">Estimated Cardinality</h3>
      <svg
        ref={svgRef}
        width="100%"
        height={height}
        viewBox={`0 0 ${renderWidth} ${height}`}
        className="w-full text-white/80"
        style={{ minHeight: height }}
      />
    </div>
  );
};
