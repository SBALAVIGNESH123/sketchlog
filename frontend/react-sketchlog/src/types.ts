export type SketchLogCounter = number | string;

export interface SketchLogLatencyState {
  alpha: number;
  zero_count: SketchLogCounter;
  count: SketchLogCounter;
  min: number | null;
  max: number | null;
  positive: Record<string, SketchLogCounter>;
  negative: Record<string, SketchLogCounter>;
}

export interface SketchLogEventsState {
  width: number;
  depth: number;
  table: SketchLogCounter[][];
  total: SketchLogCounter;
}

export interface SketchLogUniquesState {
  precision: number;
  registers: number[];
}

export interface SketchLogState {
  version: number;
  total: SketchLogCounter;
  deterministic: boolean;
  latency: SketchLogLatencyState;
  events: SketchLogEventsState;
  uniques: SketchLogUniquesState;
  metrics?: {
    p50: number;
    p95: number;
    p99: number;
    p99_9: number;
    unique_count: SketchLogCounter;
    total_events: SketchLogCounter;
    memory_footprint_bytes: SketchLogCounter;
  };
}

export interface SketchLogContextType {
  state: SketchLogState | null;
  isConnected: boolean;
  error: Error | null;
}
