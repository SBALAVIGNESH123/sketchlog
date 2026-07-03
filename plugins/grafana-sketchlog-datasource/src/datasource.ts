import {
  DataFrame,
  DataQueryRequest,
  DataQueryResponse,
  DataSourceApi,
  DataSourceInstanceSettings,
  FieldType,
  MutableDataFrame,
  TestDataSourceResponse,
} from '@grafana/data';
import { getBackendSrv } from '@grafana/runtime';
import { SketchLogDataSourceOptions, SketchLogQuery } from './types';

interface SketchLogMetricResponse {
  stream_id: string;
  p50: number;
  p90: number;
  p99: number;
  p99_9: number;
  unique_count: number;
  total_events: number;
  memory_footprint_bytes: number;
}

interface SketchLogEventResponse {
  stream_id: string;
  event_name: string;
  count: number;
}

interface SketchLogSLOResponse {
  stream_id: string;
  baseline_stream_id: string;
  target_ms: number;
  violations: number;
  total: number;
  error_rate: number;
  burn_rate: number;
  healthy: boolean;
}

interface SketchLogSQLResponse {
  query: string;
  results: Array<{ stream: string; metric: string; value: number }>;
  execution_time_ms: number;
}

export class SketchLogDataSource extends DataSourceApi<SketchLogQuery, SketchLogDataSourceOptions> {
  private readonly endpoint: string;
  private readonly defaultNamespace: string;

  constructor(instanceSettings: DataSourceInstanceSettings<SketchLogDataSourceOptions>) {
    super(instanceSettings);
    this.endpoint = (instanceSettings.jsonData.endpoint || instanceSettings.url || '').replace(/\/$/, '');
    this.defaultNamespace = instanceSettings.jsonData.defaultNamespace || 'default';
  }

  async query(options: DataQueryRequest<SketchLogQuery>): Promise<DataQueryResponse> {
    const visibleTargets = options.targets.filter((target) => !target.hide);
    const settled = await Promise.allSettled(visibleTargets.map((target) => this.runQuery(target)));
    const data = settled.map((result, index) => {
      if (result.status === 'fulfilled') {
        return result.value;
      }
      const target = visibleTargets[index];
      const message = result.reason instanceof Error ? result.reason.message : String(result.reason);
      return this.errorFrame(target?.refId || `error_${index + 1}`, message);
    });
    return { data };
  }

  async testDatasource(): Promise<TestDataSourceResponse> {
    try {
      const result = await this.request<{ status: string }>('/health');
      if (result.status !== 'ok') {
        return { status: 'error', message: 'SketchLog health check did not return ok' };
      }
      return { status: 'success', message: 'SketchLog data source is connected' };
    } catch (error) {
      return {
        status: 'error',
        message: error instanceof Error ? error.message : 'Unable to connect to SketchLog',
      };
    }
  }

  private async runQuery(target: SketchLogQuery): Promise<DataFrame> {
    const functionName = target.functionName || 'p99';
    if (functionName === 'sql') {
      return this.querySQL(target);
    }

    const stream = this.requireStream(target);
    const namespace = target.namespace || this.defaultNamespace;

    if (functionName === 'event_count') {
      return this.queryEventCount(target, namespace, stream);
    }
    if (functionName === 'slo_burn_rate') {
      return this.querySLOBurnRate(target, namespace, stream);
    }
    if (functionName === 'p95') {
      return this.queryPercentileViaSQL('p95', namespace, stream, target.refId || 'p95');
    }
    return this.queryMetrics(functionName, namespace, stream, target.refId || functionName);
  }

  private async queryMetrics(
    functionName: 'p50' | 'p99' | 'unique_count',
    namespace: string,
    stream: string,
    refId: string
  ): Promise<DataFrame> {
    const response = await this.request<SketchLogMetricResponse>(
      `/v1/namespaces/${encodeURIComponent(namespace)}/streams/${encodeURIComponent(stream)}/metrics`
    );
    return this.singleValueFrame(refId, `${functionName}(${namespace}/${stream})`, response[functionName]);
  }

  private async queryPercentileViaSQL(
    functionName: 'p95',
    namespace: string,
    stream: string,
    refId: string
  ): Promise<DataFrame> {
    const qualifiedStream = `${namespace}/${stream}`.replace(/"/g, '""');
    const response = await this.request<SketchLogSQLResponse>('/v1/query', {
      method: 'POST',
      body: { query: `SELECT ${functionName}(latency) FROM "${qualifiedStream}"` },
    });
    const first = response.results[0];
    if (!first) {
      throw new Error(`SketchLog returned no result for ${functionName}(${qualifiedStream})`);
    }
    return this.singleValueFrame(refId, `${functionName}(${namespace}/${stream})`, first.value);
  }

  private async queryEventCount(target: SketchLogQuery, namespace: string, stream: string): Promise<DataFrame> {
    if (!target.eventName) {
      throw new Error('event_count queries require an event name');
    }
    const response = await this.request<SketchLogEventResponse>(
      `/v1/namespaces/${encodeURIComponent(namespace)}/streams/${encodeURIComponent(stream)}/events?name=${encodeURIComponent(target.eventName)}`
    );
    return this.singleValueFrame(target.refId || 'event_count', `event_count(${stream}, ${target.eventName})`, response.count);
  }

  private async querySLOBurnRate(target: SketchLogQuery, namespace: string, stream: string): Promise<DataFrame> {
    if (!target.baselineStream) {
      throw new Error('slo_burn_rate queries require a baseline stream');
    }
    const response = await this.request<SketchLogSLOResponse>(
      `/v1/namespaces/${encodeURIComponent(namespace)}/streams/${encodeURIComponent(stream)}/slo/evaluate`,
      {
        method: 'POST',
        body: {
          baseline_stream_id: target.baselineStream,
          target_percentile: target.targetPercentile ?? 0.995,
          budget_percent: target.budgetPercent ?? 0.005,
        },
      }
    );
    return this.singleValueFrame(target.refId || 'slo_burn_rate', `slo_burn_rate(${stream})`, response.burn_rate);
  }

  private async querySQL(target: SketchLogQuery): Promise<DataFrame> {
    if (!target.sql) {
      throw new Error('SQL queries require a query string');
    }
    const response = await this.request<SketchLogSQLResponse>('/v1/query', {
      method: 'POST',
      body: { query: target.sql },
    });
    const frame = new MutableDataFrame({
      refId: target.refId || 'sql',
      fields: [
        { name: 'stream', type: FieldType.string, values: [] },
        { name: 'metric', type: FieldType.string, values: [] },
        { name: 'value', type: FieldType.number, values: [] },
      ],
    });
    for (const row of response.results) {
      frame.add({ stream: row.stream, metric: row.metric, value: row.value });
    }
    return frame;
  }

  private singleValueFrame(refId: string, name: string, value: number): DataFrame {
    return new MutableDataFrame({
      refId,
      fields: [
        { name: 'time', type: FieldType.time, values: [Date.now()] },
        { name, type: FieldType.number, values: [value] },
      ],
    });
  }

  private errorFrame(refId: string, message: string): DataFrame {
    return new MutableDataFrame({
      refId,
      fields: [
        { name: 'time', type: FieldType.time, values: [Date.now()] },
        { name: 'error', type: FieldType.string, values: [message] },
      ],
    });
  }

  private requireStream(target: SketchLogQuery): string {
    if (!target.stream) {
      throw new Error('SketchLog query requires a stream name');
    }
    return target.stream;
  }

  private async request<T>(path: string, options?: { method?: string; body?: unknown }): Promise<T> {
    if (!this.endpoint) {
      throw new Error('SketchLog endpoint is not configured');
    }
    const headers: Record<string, string> = {};
    if (options?.body !== undefined) {
      headers['Content-Type'] = 'application/json';
    }
    const response = await getBackendSrv()
      .fetch<T>({
        url: `${this.endpoint}${path}`,
        method: options?.method || 'GET',
        headers,
        data: options?.body,
      })
      .toPromise();
    if (!response?.data) {
      throw new Error('SketchLog returned an empty response');
    }
    return response.data;
  }
}
