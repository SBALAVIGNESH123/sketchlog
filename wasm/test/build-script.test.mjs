import assert from 'node:assert/strict';
import test from 'node:test';

import {
  EMSDK_IMAGE,
  createBuildArguments,
  ensureDockerImage,
  main,
} from '../scripts/build.mjs';

function quietLogger() {
  return {
    error() {},
    log() {},
    warn() {},
  };
}

test('uses an existing digest-pinned image without registry access', async () => {
  const calls = [];
  const result = await ensureDockerImage(EMSDK_IMAGE, {
    run(args) {
      calls.push(args);
      return { status: 0 };
    },
    logger: quietLogger(),
  });

  assert.deepEqual(result, { source: 'cache', attempts: 0 });
  assert.deepEqual(calls, [
    ['info', '--format', '{{.ServerVersion}}'],
    ['image', 'inspect', EMSDK_IMAGE],
  ]);
});

test('retries a transient registry failure with bounded backoff', async () => {
  const calls = [];
  const delays = [];
  const statuses = [0, 1, 125, 0];
  const result = await ensureDockerImage(EMSDK_IMAGE, {
    run(args) {
      calls.push(args);
      return { status: statuses.shift() };
    },
    sleep(milliseconds) {
      delays.push(milliseconds);
    },
    logger: quietLogger(),
  });

  assert.deepEqual(result, { source: 'registry', attempts: 2 });
  assert.deepEqual(delays, [1_000]);
  assert.deepEqual(calls, [
    ['info', '--format', '{{.ServerVersion}}'],
    ['image', 'inspect', EMSDK_IMAGE],
    ['pull', EMSDK_IMAGE],
    ['pull', EMSDK_IMAGE],
  ]);
});

test('retries a timed-out image pull', async () => {
  const delays = [];
  const results = [
    { status: 0 },
    { status: 1 },
    {
      error: Object.assign(new Error('spawnSync docker ETIMEDOUT'), {
        code: 'ETIMEDOUT',
      }),
    },
    { status: 0 },
  ];
  const result = await ensureDockerImage(EMSDK_IMAGE, {
    run() {
      return results.shift();
    },
    sleep(milliseconds) {
      delays.push(milliseconds);
    },
    logger: quietLogger(),
  });

  assert.deepEqual(result, { source: 'registry', attempts: 2 });
  assert.deepEqual(delays, [1_000]);
});

test('fails clearly after the bounded pull budget is exhausted', async () => {
  const delays = [];
  const statuses = [0, 1, 125, 125, 125];
  await assert.rejects(
    ensureDockerImage(EMSDK_IMAGE, {
      run() {
        return { status: statuses.shift() };
      },
      sleep(milliseconds) {
        delays.push(milliseconds);
      },
      logger: quietLogger(),
    }),
    /after 3 attempts.*last failure: exit code 125/,
  );
  assert.deepEqual(delays, [1_000, 2_000]);
});

test('reports a missing Docker executable without retrying', async () => {
  let calls = 0;
  await assert.rejects(
    ensureDockerImage(EMSDK_IMAGE, {
      run() {
        calls += 1;
        return {
          error: Object.assign(new Error('spawn docker ENOENT'), {
            code: 'ENOENT',
          }),
        };
      },
      logger: quietLogger(),
    }),
    /Unable to start Docker/,
  );
  assert.equal(calls, 1);
});

test('reports an unavailable Docker daemon without pulling', async () => {
  const calls = [];
  await assert.rejects(
    ensureDockerImage(EMSDK_IMAGE, {
      run(args) {
        calls.push(args);
        return { status: 1 };
      },
      logger: quietLogger(),
    }),
    /Unable to connect to the Docker daemon/,
  );
  assert.deepEqual(calls, [['info', '--format', '{{.ServerVersion}}']]);
});

test('runs only the locally acquired immutable image', () => {
  const args = createBuildArguments({
    root: '/workspace/sketchlog',
    uid: 1000,
    gid: 1001,
  });

  assert.equal(args[0], 'run');
  assert.ok(args.includes('--pull=never'));
  assert.ok(args.includes(EMSDK_IMAGE));
  assert.match(EMSDK_IMAGE, /@sha256:[a-f0-9]{64}$/);
  assert.deepEqual(
    args.slice(args.indexOf('-u'), args.indexOf('-u') + 2),
    ['-u', '1000:1001'],
  );
});

test('top-level build acquires the image before invoking the compiler', async () => {
  const calls = [];
  const result = await main({
    run(args, options) {
      calls.push({ args, options });
      return { status: 0 };
    },
    logger: quietLogger(),
  });

  assert.equal(result, 0);
  assert.deepEqual(
    calls.slice(0, 2).map(({ args }) => args),
    [
      ['info', '--format', '{{.ServerVersion}}'],
      ['image', 'inspect', EMSDK_IMAGE],
    ],
  );
  assert.equal(calls[2].args[0], 'run');
  assert.ok(calls[2].args.includes('--pull=never'));
  assert.ok(calls[2].args.includes('wasm/bindings.cpp'));
  assert.equal(calls[2].options.timeout, 300_000);
});
