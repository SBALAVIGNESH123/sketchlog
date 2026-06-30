import { spawnSync } from 'node:child_process';
import { mkdirSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const packageDir = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const repositoryRoot = resolve(packageDir, '..');

export const EMSDK_IMAGE =
  'emscripten/emsdk:5.0.4@sha256:ef91f658e0104636cf40a702c99169273969cf04d939f4f08e5d0223965d5788';
const IMAGE_PULL_ATTEMPTS = 3;
const INITIAL_PULL_BACKOFF_MS = 1_000;
const DOCKER_PROBE_TIMEOUT_MS = 15_000;
const DOCKER_PULL_TIMEOUT_MS = 300_000;
const DOCKER_BUILD_TIMEOUT_MS = 300_000;

export function runDocker(args, options = {}) {
  return spawnSync('docker', args, options);
}

function defaultSleep(milliseconds) {
  return new Promise((resolveSleep) => setTimeout(resolveSleep, milliseconds));
}

function dockerStartError(error) {
  return new Error(
    'Unable to start Docker. Install and start Docker, then rerun npm run build (or npm test).',
    { cause: error },
  );
}

function dockerDaemonError(status) {
  return new Error(
    'Unable to connect to the Docker daemon '
    + `(Docker exit code: ${status ?? 'unknown'}). Start Docker, then rerun `
    + 'npm run build (or npm test).',
  );
}

function dockerTimeoutError(action, timeoutMs) {
  return new Error(
    `Docker ${action} timed out after ${Math.round(timeoutMs / 1_000)} seconds.`,
  );
}

export async function ensureDockerImage(
  image,
  {
    run = runDocker,
    sleep = defaultSleep,
    logger = console,
    attempts = IMAGE_PULL_ATTEMPTS,
    initialBackoffMs = INITIAL_PULL_BACKOFF_MS,
  } = {},
) {
  if (!Number.isInteger(attempts) || attempts < 1) {
    throw new RangeError('Docker pull attempts must be a positive integer.');
  }
  if (!Number.isFinite(initialBackoffMs) || initialBackoffMs < 0) {
    throw new RangeError('Docker pull backoff must be a non-negative number.');
  }

  const daemon = run(['info', '--format', '{{.ServerVersion}}'], {
    stdio: 'ignore',
    timeout: DOCKER_PROBE_TIMEOUT_MS,
  });
  if (daemon.error) {
    if (daemon.error.code === 'ETIMEDOUT') {
      throw dockerTimeoutError('daemon check', DOCKER_PROBE_TIMEOUT_MS);
    }
    throw dockerStartError(daemon.error);
  }
  if (daemon.status !== 0) {
    throw dockerDaemonError(daemon.status);
  }
  const inspection = run(['image', 'inspect', image], {
    stdio: 'ignore',
    timeout: DOCKER_PROBE_TIMEOUT_MS,
  });
  if (inspection.error) {
    if (inspection.error.code === 'ETIMEDOUT') {
      throw dockerTimeoutError('image inspection', DOCKER_PROBE_TIMEOUT_MS);
    }
    throw dockerStartError(inspection.error);
  }
  if (inspection.status === 0) {
    logger.log(`Using cached Emscripten image ${image}`);
    return { source: 'cache', attempts: 0 };
  }

  let lastFailure = `exit code ${inspection.status ?? 'unknown'}`;
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    logger.log(`Pulling Emscripten image (attempt ${attempt}/${attempts})`);
    const pull = run(['pull', image], {
      stdio: 'inherit',
      timeout: DOCKER_PULL_TIMEOUT_MS,
    });
    if (pull.error) {
      if (pull.error.code === 'ENOENT') {
        throw dockerStartError(pull.error);
      }
      if (pull.error.code === 'ETIMEDOUT') {
        lastFailure = `timeout after ${DOCKER_PULL_TIMEOUT_MS / 1_000} seconds`;
      } else {
        throw new Error(`Unable to execute Docker pull: ${pull.error.message}`, {
          cause: pull.error,
        });
      }
    } else {
      lastFailure = `exit code ${pull.status ?? 'unknown'}`;
    }
    if (!pull.error && pull.status === 0) {
      return { source: 'registry', attempts: attempt };
    }
    if (attempt < attempts) {
      const backoffMs = initialBackoffMs * (2 ** (attempt - 1));
      logger.warn(
        `Docker pull failed (${lastFailure}); retrying in ${backoffMs}ms.`,
      );
      await sleep(backoffMs);
    }
  }

  throw new Error(
    `Unable to pull pinned Emscripten image after ${attempts} attempts `
    + `(last failure: ${lastFailure}).`,
  );
}

export function createBuildArguments({
  image = EMSDK_IMAGE,
  root = repositoryRoot,
  uid = typeof process.getuid === 'function' ? process.getuid() : undefined,
  gid = typeof process.getgid === 'function' ? process.getgid() : undefined,
} = {}) {
  const args = [
    'run',
    '--rm',
    '--pull=never',
    '-v',
    `${root}:/src`,
    '-w',
    '/src',
  ];

  if (uid !== undefined && gid !== undefined) {
    args.push('-u', `${uid}:${gid}`);
  }

  args.push(
    image,
    'emcc',
    '-O3',
    '--bind',
    '-Iinclude',
    'wasm/bindings.cpp',
    '-s',
    'MODULARIZE=1',
    '-s',
    'EXPORT_NAME=SketchLogModule',
    '-s',
    'ALLOW_MEMORY_GROWTH=1',
    '-o',
    'wasm/dist/sketchlog.js',
  );
  return args;
}

export async function main({
  run = runDocker,
  sleep = defaultSleep,
  logger = console,
} = {}) {
  mkdirSync(resolve(packageDir, 'dist'), { recursive: true });
  logger.log(`Building @sketchlog/wasm with ${EMSDK_IMAGE}`);

  try {
    await ensureDockerImage(EMSDK_IMAGE, { run, sleep, logger });
  } catch (error) {
    logger.error(error instanceof Error ? error.message : String(error));
    return 1;
  }

  const result = run(createBuildArguments(), {
    stdio: 'inherit',
    timeout: DOCKER_BUILD_TIMEOUT_MS,
  });
  if (result.error) {
    if (result.error.code === 'ETIMEDOUT') {
      logger.error(dockerTimeoutError('WASM build', DOCKER_BUILD_TIMEOUT_MS).message);
      return 1;
    }
    logger.error(dockerStartError(result.error).message);
    return 1;
  }
  return result.status ?? 1;
}

const invokedPath = process.argv[1] ? resolve(process.argv[1]) : '';
if (invokedPath === fileURLToPath(import.meta.url)) {
  process.exitCode = await main();
}
