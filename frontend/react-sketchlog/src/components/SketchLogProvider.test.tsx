import { act, cleanup, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { SketchLogState } from '../types';
import { SketchLogProvider } from './SketchLogProvider';
import { useSketchLog } from './SketchLogContext';

class MockWebSocket {
  static instances: MockWebSocket[] = [];
  readonly url: string;
  onopen: (() => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: (() => void) | null = null;

  constructor(url: string | URL) {
    this.url = String(url);
    MockWebSocket.instances.push(this);
  }

  close() {
    return;
  }
}

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
    MockWebSocket.instances = [];
    vi.stubGlobal('WebSocket', MockWebSocket);
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it('applies state and error transitions from the WebSocket', () => {
    render(
      <SketchLogProvider url="ws://example.test/stream">
        <Probe />
      </SketchLogProvider>,
    );
    const socket = MockWebSocket.instances[0];
    expect(socket.url).toBe('ws://example.test/stream');
    expect(screen.getByTestId('connected').textContent).toBe('false');

    act(() => {
      socket.onopen?.();
      socket.onmessage?.(new MessageEvent('message', { data: JSON.stringify(state) }));
    });
    expect(screen.getByTestId('connected').textContent).toBe('true');
    expect(screen.getByTestId('total').textContent).toBe('4');

    act(() => {
      socket.onmessage?.(new MessageEvent('message', {
        data: JSON.stringify({ error: 'Stream not found' }),
      }));
    });
    expect(screen.getByTestId('total').textContent).toBe('none');
    expect(screen.getByTestId('error').textContent).toBe('Stream not found');
  });

  it('ignores callbacks from a retired socket after the URL changes', () => {
    const view = render(
      <SketchLogProvider url="ws://example.test/first">
        <Probe />
      </SketchLogProvider>,
    );
    const retiredSocket = MockWebSocket.instances[0];

    view.rerender(
      <SketchLogProvider url="ws://example.test/second">
        <Probe />
      </SketchLogProvider>,
    );
    const activeSocket = MockWebSocket.instances[1];

    act(() => {
      retiredSocket.onopen?.();
      retiredSocket.onmessage?.(new MessageEvent('message', { data: JSON.stringify(state) }));
    });
    expect(screen.getByTestId('connected').textContent).toBe('false');
    expect(screen.getByTestId('total').textContent).toBe('none');

    act(() => {
      activeSocket.onopen?.();
      activeSocket.onmessage?.(new MessageEvent('message', { data: JSON.stringify(state) }));
    });
    expect(screen.getByTestId('connected').textContent).toBe('true');
    expect(screen.getByTestId('total').textContent).toBe('4');
  });
});
