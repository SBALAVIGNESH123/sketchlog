import { parseArgs } from 'util';
import { SketchLogClient, SketchLogError } from './index';

async function main() {
  const { values, positionals } = parseArgs({
    args: process.argv.slice(2),
    options: {
      endpoint: { type: 'string', default: 'http://127.0.0.1:8999' },
      token: { type: 'string' },
    },
    allowPositionals: true
  });

  const command = positionals[0];
  const client = new SketchLogClient({
    endpoint: values.endpoint!,
    authToken: values.token,
    maxRetries: 3,
    timeoutMs: 5000
  });

  try {
    if (command === 'test-ingest') {
      await client.ingestEvents('test-stream', {
        latencies: [42.5],
        uniques: ['user_1'],
        events: { 'test_event': 1 }
      });
      console.log('Ingest success');
    } else if (command === 'test-retries') {
      await client.health();
      console.log('Retry/Health success');
    } else if (command === 'test-transport-retries') {
      const started = Date.now();
      try {
        await client.health();
        throw new Error('Expected transport failure');
      } catch (error) {
        if (error instanceof SketchLogError && error.status === 0
            && Date.now() - started >= 600) {
          console.log('Transport retries success');
        } else {
          throw error;
        }
      }
    } else if (command === 'test-auth-missing' || command === 'test-auth-invalid') {
      const unauthorized = new SketchLogClient({
        endpoint: values.endpoint!,
        authToken: command === 'test-auth-invalid' ? 'wrong-token' : undefined,
        maxRetries: 0,
      });
      try {
        await unauthorized.ingestEvents('auth-test', { latencies: [1] });
        throw new Error('Expected authentication failure');
      } catch (error) {
        if (!(error instanceof SketchLogError) || error.status !== 401) throw error;
        console.log('Authentication rejection success');
      }
    } else {
      console.error(`Unknown command: ${command}`);
      process.exit(1);
    }
  } catch (err) {
    if (err instanceof SketchLogError) {
      console.error(`SketchLogError: ${err.status} - ${err.message}`);
    } else {
      console.error(err);
    }
    process.exit(1);
  }
}
main().catch((err) => {
  console.error('Fatal startup/runtime failure:', err);
  process.exit(1);
});
