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
  authToken?: string;
  authTokenProvider?: () => string | Promise<string>;
}

export interface EventBatch {
  latencies?: number[];
  uniques?: string[];
  events?: Record<string, number>;
}

export class SketchLogClient {
  private static readonly MAX_RESPONSE_BYTES = 1024 * 1024;
  private endpoint: string;
  private maxRetries: number;
  private timeoutMs: number;
  private agent: Agent;
  private authToken?: string;
  private authTokenProvider?: () => string | Promise<string>;

  constructor(options: ClientOptions) {
    const endpoint = new URL(options.endpoint);
    if (!['http:', 'https:'].includes(endpoint.protocol)
        || endpoint.username || endpoint.password
        || (endpoint.pathname !== '' && endpoint.pathname !== '/')
        || endpoint.search || endpoint.hash) {
      throw new TypeError('endpoint must be an HTTP(S) origin without credentials or a path');
    }
    if (options.maxRetries !== undefined
        && (!Number.isInteger(options.maxRetries)
            || options.maxRetries < 0 || options.maxRetries > 10)) {
      throw new TypeError('maxRetries must be an integer in [0, 10]');
    }
    if (options.timeoutMs !== undefined
        && (!Number.isFinite(options.timeoutMs) || options.timeoutMs <= 0)) {
      throw new TypeError('timeoutMs must be a positive finite number');
    }
    this.endpoint = endpoint.origin;
    this.maxRetries = options.maxRetries ?? 3;
    this.timeoutMs = options.timeoutMs ?? 5000;
    this.authToken = options.authToken;
    this.authTokenProvider = options.authTokenProvider;
    // Connection pooling
    this.agent = new Agent({
      keepAliveTimeout: 10000,
      keepAliveMaxTimeout: 10000,
    });
  }

  private async readResponse(res: Awaited<ReturnType<typeof fetch>>): Promise<string> {
    const contentLength = Number(res.headers.get('content-length'));
    if (Number.isFinite(contentLength)
        && contentLength > SketchLogClient.MAX_RESPONSE_BYTES) {
      await res.body?.cancel();
      throw new SketchLogError(502, 'Response body exceeds 1 MiB');
    }
    if (!res.body) return '';

    const reader = res.body.getReader();
    const chunks: Uint8Array[] = [];
    let total = 0;
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      total += value.byteLength;
      if (total > SketchLogClient.MAX_RESPONSE_BYTES) {
        await reader.cancel();
        throw new SketchLogError(502, 'Response body exceeds 1 MiB');
      }
      chunks.push(value);
    }
    const combined = new Uint8Array(total);
    let offset = 0;
    for (const chunk of chunks) {
      combined.set(chunk, offset);
      offset += chunk.byteLength;
    }
    return new TextDecoder().decode(combined);
  }

  private async request(method: string, path: string, body?: any, signal?: AbortSignal): Promise<any> {
    const url = `${this.endpoint}${path}`;
    const isIdempotent = method === 'GET' || method === 'PUT' || method === 'DELETE';
    let attempt = 0;

    while (true) {
      attempt++;
      let timeoutId: ReturnType<typeof setTimeout> | undefined;
      try {
        const controller = new AbortController();
        timeoutId = setTimeout(() => controller.abort(), this.timeoutMs);

        // Combine provided signal with our timeout signal so neither is ignored
        const activeSignal = signal ? AbortSignal.any([signal, controller.signal]) : controller.signal;

        const token = this.authTokenProvider
          ? await this.authTokenProvider()
          : this.authToken;
        const headers: Record<string, string> = {};
        if (body) headers['Content-Type'] = 'application/json';
        if (token) headers['X-SketchLog-Auth-Token'] = token;

        const res = await fetch(url, {
          method,
          headers: Object.keys(headers).length ? headers : undefined,
          body: body ? JSON.stringify(body) : undefined,
          dispatcher: this.agent,
          signal: activeSignal as any,
          redirect: 'error',
        });

        if (res.status >= 200 && res.status < 300) {
          if (res.status === 204) return null;
          const responseText = await this.readResponse(res);
          try {
            return responseText ? JSON.parse(responseText) : null;
          } catch {
            throw new SketchLogError(502, 'Server returned invalid JSON');
          }
        }

        // Retry on 5xx or 429
        if ((res.status >= 500 || res.status === 429) && isIdempotent && attempt <= this.maxRetries) {
          await this.readResponse(res); // Consume to free the pooled socket.
          await this.delay(attempt);
          continue;
        }

        throw new SketchLogError(res.status, await this.readResponse(res));

      } catch (err: any) {
        if (err instanceof SketchLogError) {
          throw err;
        }
        if (err.name === 'AbortError') {
          if (signal?.aborted) {
            throw signal.reason || err;
          }
          throw new SketchLogError(408, 'Request Timeout');
        }
        if (isIdempotent && attempt <= this.maxRetries) {
          await this.delay(attempt);
          continue;
        }
        throw new SketchLogError(0, err.message || 'Unknown network error');
      } finally {
        if (timeoutId !== undefined) clearTimeout(timeoutId);
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
    await this.request('POST', `/v1/streams/${encodeURIComponent(streamId)}/events`, batch, signal);
  }

  async close(): Promise<void> {
    await this.agent.close();
  }
}
