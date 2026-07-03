import { DataQuery, DataSourceJsonData } from '@grafana/data';

export type SketchLogQueryFunction = 'p50' | 'p95' | 'p99' | 'unique_count' | 'event_count' | 'slo_burn_rate' | 'sql';

export interface SketchLogQuery extends DataQuery {
  functionName?: SketchLogQueryFunction;
  stream?: string;
  namespace?: string;
  eventName?: string;
  baselineStream?: string;
  sql?: string;
  targetPercentile?: number;
  budgetPercent?: number;
}

export interface SketchLogDataSourceOptions extends DataSourceJsonData {
  endpoint?: string;
  defaultNamespace?: string;
}
