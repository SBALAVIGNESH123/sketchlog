import { parseArgs } from 'util';
import { SketchLogClient, SketchLogError } from './index';

async function main() {
  const { values, positionals } = parseArgs({
    args: process.argv.slice(2),
    options: {
      endpoint: { type: 'string', default: 'http://127.0.0.1:8999' }
    },
    allowPositionals: true
  });

  const command = positionals[0];
  const client = new SketchLogClient({
    endpoint: values.endpoint!,
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
      await (client as any).request('GET', '/test/flake');
      console.log('Retry/Health success');
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
