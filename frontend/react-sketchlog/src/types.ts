export interface SketchLogLatencyState {
  alpha: number;
  zero_count: number;
  count: number;
  min: number | null;
  max: number | null;
  positive: Record<string, number>;
  negative: Record<string, number>;
}

export interface SketchLogEventsState {
  width: number;
  depth: number;
  table: number[][];
  total: number;
}

export interface SketchLogUniquesState {
  precision: number;
  registers: number[];
}

export interface SketchLogState {
  version: number;
  total: number;
  deterministic: boolean;
  latency: SketchLogLatencyState;
  events: SketchLogEventsState;
  uniques: SketchLogUniquesState;
}

export interface SketchLogContextType {
  state: SketchLogState | null;
  isConnected: boolean;
  error: Error | null;
}
