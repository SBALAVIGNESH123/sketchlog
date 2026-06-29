const assert = require('node:assert/strict');
const http = require('node:http');
const { after, test } = require('node:test');
const { SketchLogClient, SketchLogError } = require('../dist/index.js');

const servers = [];

async function listen(handler) {
  const server = http.createServer(handler);
  servers.push(server);
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const address = server.address();
  return `http://127.0.0.1:${address.port}`;
}

after(async () => {
  await Promise.all(servers.map(
    (server) => new Promise((resolve) => server.close(resolve))));
});

test('rejects unsafe endpoints', () => {
  for (const endpoint of [
    'file:///tmp/state',
    'https://user:password@example.com',
    'https://example.com/base',
    'https://example.com?token=secret',
  ]) {
    assert.throws(
      () => new SketchLogClient({ endpoint }),
      /HTTP\(S\) origin/,
    );
  }
});

test('does not follow redirects', async () => {
  let redirected = false;
  const target = await listen((_request, response) => {
    redirected = true;
    response.end('{}');
  });
  const source = await listen((_request, response) => {
    response.writeHead(307, { location: target });
    response.end();
  });
  const client = new SketchLogClient({ endpoint: source, maxRetries: 0 });
  await assert.rejects(() => client.health(), SketchLogError);
  assert.equal(redirected, false);
  await client.close();
});

test('bounds response bodies', async () => {
  const endpoint = await listen((_request, response) => {
    response.writeHead(200, { 'content-type': 'application/json' });
    response.end('x'.repeat(1024 * 1024 + 1));
  });
  const client = new SketchLogClient({ endpoint, maxRetries: 0 });
  await assert.rejects(
    () => client.health(),
    (error) => error instanceof SketchLogError && error.status === 502,
  );
  await client.close();
});

test('does not expose ingestion request bodies in errors', async () => {
  const endpoint = await listen((_request, response) => {
    response.writeHead(400, { 'content-type': 'text/plain' });
    response.end('invalid request');
  });
  const client = new SketchLogClient({ endpoint, maxRetries: 0 });
  await assert.rejects(
    () => client.ingestEvents('stream', { uniques: ['private-user-id'] }),
    (error) => error instanceof SketchLogError
      && !error.message.includes('private-user-id'),
  );
  await client.close();
});
