import { act, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ReactNode } from 'react';
import type { SketchLogState } from '../types';
import { CardinalitySparkline } from './CardinalitySparkline';
import { CDFCurve } from './CDFCurve';
import { QuantileHeatmap } from './QuantileHeatmap';
import { SketchLogContext } from './SketchLogContext';

function makeState(
  uniqueCount: number,
  firstBucketCount: number,
): SketchLogState {
  return {
    version: 1,
    total: firstBucketCount + 2,
    deterministic: false,
    latency: {
      alpha: 0.01,
      zero_count: 0,
      count: firstBucketCount + 2,
      min: 1,
      max: 4,
      positive: { 1: firstBucketCount, 2: 2 },
      negative: {},
    },
    events: { width: 1, depth: 1, table: [[0]], total: 0 },
    uniques: { precision: 4, registers: Array(16).fill(0) as number[] },
    metrics: {
      p50: 2,
      p95: 4,
      p99: 4,
      p99_9: 4,
      unique_count: String(uniqueCount),
      total_events: String(firstBucketCount + 2),
      memory_footprint_bytes: '1024',
    },
  };
}

function Harness({
  state,
  children,
}: {
  state: SketchLogState;
  children: ReactNode;
}) {
  return (
    <SketchLogContext.Provider
      value={{ state, isConnected: true, error: null }}
    >
      {children}
    </SketchLogContext.Provider>
  );
}

describe('dashboard visualizations', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('renders a connected CDF from the current distribution', () => {
    const view = render(
      <Harness state={makeState(10, 2)}>
        <CDFCurve width={400} height={240} />
      </Harness>,
    );

    expect(screen.getByText('Latency CDF')).toBeTruthy();
    expect(screen.getByText('LIVE')).toBeTruthy();
    expect(view.container.querySelectorAll('path').length).toBeGreaterThan(0);
  });

  it('recomputes chart coordinates from the rendered container width', () => {
    let resizeCallback: ResizeObserverCallback | null = null;
    class MockResizeObserver {
      constructor(callback: ResizeObserverCallback) {
        resizeCallback = callback;
      }
      observe() {
        return;
      }
      disconnect() {
        return;
      }
      unobserve() {
        return;
      }
    }
    vi.stubGlobal('ResizeObserver', MockResizeObserver);

    const view = render(
      <Harness state={makeState(10, 2)}>
        <CDFCurve width={400} height={240} />
      </Harness>,
    );
    const svg = view.container.querySelector('svg');

    act(() => {
      const callback = resizeCallback as ResizeObserverCallback;
      callback(
        [{ contentRect: { width: 280 } } as ResizeObserverEntry],
        {} as ResizeObserver,
      );
    });
    expect(svg?.getAttribute('viewBox')).toBe('0 0 280 240');
  });

  it('renders cardinality history after two live updates', async () => {
    const view = render(
      <Harness state={makeState(10, 2)}>
        <CardinalitySparkline width={300} height={120} />
      </Harness>,
    );
    await act(async () => vi.runAllTimers());

    view.rerender(
      <Harness state={makeState(25, 3)}>
        <CardinalitySparkline width={300} height={120} />
      </Harness>,
    );
    await act(async () => vi.runAllTimers());

    expect(screen.getByText('Estimated Cardinality')).toBeTruthy();
    expect(view.container.querySelectorAll('path').length).toBeGreaterThan(0);
  });

  it('renders a quantile heatmap from distribution deltas', async () => {
    const view = render(
      <Harness state={makeState(10, 2)}>
        <QuantileHeatmap width={300} height={240} />
      </Harness>,
    );
    await act(async () => vi.runAllTimers());

    view.rerender(
      <Harness state={makeState(20, 5)}>
        <QuantileHeatmap width={300} height={240} />
      </Harness>,
    );
    await act(async () => vi.runAllTimers());

    expect(screen.getByText('Quantile Heatmap (Live)')).toBeTruthy();
    expect(view.container.querySelectorAll('rect').length).toBeGreaterThan(0);
  });
});
