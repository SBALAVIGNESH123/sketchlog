import type { ReactNode } from 'react';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import App from './App';

vi.mock('./index', () => ({
  SketchLogProvider: ({ children }: { children: ReactNode }) => children,
  useSketchLog: () => ({
    isConnected: true,
    error: null,
    state: {
      metrics: {
        p50: 91,
        p95: 125,
        p99: 327,
        p99_9: 335,
        unique_count: '410',
        total_events: '3831',
        memory_footprint_bytes: '84640',
      },
    },
  }),
  CDFCurve: () => <div data-testid="cdf-chart" />,
  QuantileHeatmap: () => <div data-testid="heatmap-chart" />,
  CardinalitySparkline: () => <div data-testid="cardinality-chart" />,
}));

const jsonResponse = (body: unknown) => Promise.resolve(
  new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  }),
);

describe('launch dashboard', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === '/api/v1/streams/demo-current/anomaly?baseline_stream_id=demo-baseline&sensitivity=0.20') {
        expect(init?.method).toBeUndefined();
        expect(init?.signal).toBeInstanceOf(AbortSignal);
        return jsonResponse({ anomaly_score: 1, sensitivity: 0.2, is_anomalous: true });
      }
      if (url === '/api/v1/query') {
        expect(init?.method).toBe('POST');
        expect(init?.headers).toEqual({ 'Content-Type': 'application/json' });
        expect(init?.signal).toBeInstanceOf(AbortSignal);
        expect(JSON.parse(String(init?.body))).toEqual({
          query: 'SELECT p50(latency), p99(latency), count_unique(users), event_count(errors, \'HTTP_500\') FROM "default/demo-current"',
        });
        return jsonResponse({
          results: [
            { metric: 'p99(latency)', value: 327 },
            { metric: 'count_unique(users)', value: 410 },
          ],
          execution_time_ms: 0.48,
        });
      }
      if (
        url === '/api/v1/namespaces/acme/streams/checkout/metrics'
        || url === '/api/v1/namespaces/globex/streams/checkout/metrics'
      ) {
        expect(init?.method).toBeUndefined();
        expect(init?.signal).toBeInstanceOf(AbortSignal);
        const isAcme = url.includes('/acme/');
        return jsonResponse({
          p50: isAcme ? 47 : 145,
          p90: 160,
          p99: isAcme ? 55 : 172,
          p99_9: 180,
          unique_count: 100,
          total_events: 320,
          memory_footprint_bytes: 80000,
        });
      }
      throw new Error(`Unexpected fetch: ${url}`);
    }));
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it('renders verified live, anomaly, SQL, and tenant evidence', async () => {
    render(<App />);

    expect(screen.getByText('SYSTEM LIVE')).toBeTruthy();
    expect(screen.getByText('3,831')).toBeTruthy();
    expect(screen.getByTestId('cdf-chart')).toBeTruthy();
    expect(screen.getByTestId('heatmap-chart')).toBeTruthy();

    await waitFor(() => expect(screen.getByText('DETECTED')).toBeTruthy());
    expect(screen.getByText('REAL QUERY')).toBeTruthy();
    expect(screen.getByText('acme / checkout')).toBeTruthy();
    expect(screen.getByText('globex / checkout')).toBeTruthy();
  });
});
