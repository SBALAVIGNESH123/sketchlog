import { act, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { SketchLogState } from '../types';
import { SketchLogProvider } from './SketchLogProvider';
import { useSketchLog } from './SketchLogContext';

const socket = vi.hoisted(() => ({
  value: {
    lastJsonMessage: null as SketchLogState | { error: string } | null,
    readyState: 0,
  },
}));

vi.mock('react-use-websocket', () => ({
  default: () => socket.value,
}));

const state: SketchLogState = {
  version: 1,
  total: 4,
  deterministic: false,
  latency: {
    alpha: 0.01,
    zero_count: 0,
    count: 4,
    min: 1,
    max: 4,
    positive: {},
    negative: {},
  },
  events: { width: 1, depth: 1, table: [[0]], total: 0 },
  uniques: { precision: 4, registers: Array(16).fill(0) as number[] },
  metrics: {
    p50: 2,
    p95: 4,
    p99: 4,
    p99_9: 4,
    unique_count: '1',
    total_events: '4',
    memory_footprint_bytes: '1024',
  },
};

function Probe() {
  const context = useSketchLog();
  return (
    <div>
      <span data-testid="connected">{String(context.isConnected)}</span>
      <span data-testid="total">{context.state?.total ?? 'none'}</span>
      <span data-testid="error">{context.error?.message ?? 'none'}</span>
    </div>
  );
}

describe('SketchLogProvider', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    socket.value = { lastJsonMessage: null, readyState: 0 };
  });

  it('applies state and error transitions from the WebSocket', async () => {
    const view = render(
      <SketchLogProvider url="ws://example.test/stream">
        <Probe />
      </SketchLogProvider>,
    );
    expect(screen.getByTestId('connected').textContent).toBe('false');

    socket.value = { lastJsonMessage: state, readyState: 1 };
    view.rerender(
      <SketchLogProvider url="ws://example.test/stream">
        <Probe />
      </SketchLogProvider>,
    );
    await act(async () => vi.runAllTimers());
    expect(screen.getByTestId('connected').textContent).toBe('true');
    expect(screen.getByTestId('total').textContent).toBe('4');

    socket.value = {
      lastJsonMessage: { error: 'Stream not found' },
      readyState: 1,
    };
    view.rerender(
      <SketchLogProvider url="ws://example.test/stream">
        <Probe />
      </SketchLogProvider>,
    );
    await act(async () => vi.runAllTimers());
    expect(screen.getByTestId('total').textContent).toBe('none');
    expect(screen.getByTestId('error').textContent).toBe('Stream not found');
  });
});
