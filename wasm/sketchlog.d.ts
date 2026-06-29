export interface StreamLogOptions {
  relativeAccuracy?: number;
  hllPrecision?: number;
  cmsWidth?: number;
  cmsDepth?: number;
}

export declare class StreamLog {
  static init(options?: Record<string, unknown>): Promise<void>;
  constructor(
    relativeAccuracy?: number,
    hllPrecision?: number,
    cmsWidth?: number,
    cmsDepth?: number
  );
  addLatency(value: number): void;
  addBatch(values: number[]): void;
  percentile(q: number): number;
  readonly p50: number;
  readonly p95: number;
  readonly p99: number;
  readonly p999: number;
  countGreaterThan(threshold: number): number;
  readonly latencyCount: number;
  addEvent(name: string, count?: number): void;
  eventCount(name: string): number;
  addUnique(item: string | number): void;
  readonly uniqueCount: number;
  readonly totalEvents: number;
  readonly memoryBytes: number;
  readonly memoryKb: number;
  reset(): void;
  merge(other: StreamLog): void;
  toDict(): Record<string, unknown>;
  serialize(): { state: Record<string, unknown> };
  stats(): Record<string, unknown>;
  destroy(): void;
}
