import { fetch, Agent } from 'undici';

export class SketchLogError extends Error {
  constructor(public status: number, message: string) {
    super(message);
    this.name = 'SketchLogError';
  }
}

export interface ClientOptions {
  endpoint: string;
  maxRetries?: number;
  timeoutMs?: number;
}

export interface EventBatch {
  latencies?: number[];
  uniques?: string[];
  events?: Record<string, number>;
}

export class SketchLogClient {
  private endpoint: string;
  private maxRetries: number;
  private timeoutMs: number;
  private agent: Agent;

  constructor(options: ClientOptions) {
    this.endpoint = options.endpoint.replace(/\/$/, '');
    this.maxRetries = options.maxRetries ?? 3;
    this.timeoutMs = options.timeoutMs ?? 5000;
    // Connection pooling
    this.agent = new Agent({
      keepAliveTimeout: 10000,
      keepAliveMaxTimeout: 10000,
    });
  }

  private async request(method: string, path: string, body?: any, signal?: AbortSignal): Promise<any> {
    const url = `${this.endpoint}${path}`;
    const isIdempotent = method === 'GET' || method === 'PUT' || method === 'DELETE';
    let attempt = 0;

    while (true) {
      attempt++;
      try {
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), this.timeoutMs);
        
        // Combine provided signal with our timeout signal so neither is ignored
        const activeSignal = signal ? AbortSignal.any([signal, controller.signal]) : controller.signal;

        const res = await fetch(url, {
          method,
          headers: body ? { 'Content-Type': 'application/json' } : undefined,
          body: body ? JSON.stringify(body) : undefined,
          dispatcher: this.agent,
          signal: activeSignal as any,
        });
        
        clearTimeout(timeoutId);

        if (res.status >= 200 && res.status < 300) {
          if (res.status === 204) return null;
          return await res.json();
        }

        // Retry on 5xx or 429
        if ((res.status >= 500 || res.status === 429) && isIdempotent && attempt <= this.maxRetries) {
          await res.arrayBuffer(); // Consume to free socket
          await this.delay(attempt);
          continue;
        }

        throw new SketchLogError(res.status, await res.text());

      } catch (err: any) {
        if (err.name === 'AbortError') {
          throw new SketchLogError(408, 'Request Timeout');
        }
        if (isIdempotent && attempt <= this.maxRetries && (err.code === 'ECONNRESET' || err.code === 'UND_ERR_SOCKET')) {
          await this.delay(attempt);
          continue;
        }
        throw err;
      }
    }
  }

  private delay(attempt: number): Promise<void> {
    const base = 100 * Math.pow(2, attempt - 1);
    const jitter = Math.random() * 50;
    return new Promise(resolve => setTimeout(resolve, base + jitter));
  }

  async health(): Promise<any> {
    return this.request('GET', '/health');
  }

  async ingestEvents(streamId: string, batch: EventBatch, signal?: AbortSignal): Promise<void> {
    await this.request('POST', `/v1/streams/${streamId}/events`, batch, signal);
  }
}
