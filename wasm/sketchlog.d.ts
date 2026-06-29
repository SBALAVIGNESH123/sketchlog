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
  countGreaterThan(threshold: number): bigint;
  readonly latencyCount: bigint;
  addEvent(name: string, count?: number | bigint): void;
  eventCount(name: string): bigint;
  addUnique(item: string | number | bigint): void;
  readonly uniqueCount: bigint;
  readonly totalEvents: bigint;
  readonly memoryBytes: number;
  readonly memoryKb: number;
  reset(): void;
  merge(other: StreamLog): void;
  toDict(): Record<string, unknown>;
  serialize(): { state: Record<string, unknown> };
  stats(): Record<string, unknown>;
  destroy(): void;
}
