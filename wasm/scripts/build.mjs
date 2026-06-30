import { spawnSync } from 'node:child_process';
import { mkdirSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const packageDir = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const repositoryRoot = resolve(packageDir, '..');
mkdirSync(resolve(packageDir, 'dist'), { recursive: true });

const image =
  'emscripten/emsdk:5.0.4@sha256:ef91f658e0104636cf40a702c99169273969cf04d939f4f08e5d0223965d5788';
const args = [
  'run',
  '--rm',
  '-v',
  `${repositoryRoot}:/src`,
  '-w',
  '/src',
];

if (typeof process.getuid === 'function' && typeof process.getgid === 'function') {
  args.push('-u', `${process.getuid()}:${process.getgid()}`);
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

console.log(`Building @sketchlog/wasm with ${image}`);
const result = spawnSync('docker', args, { stdio: 'inherit' });
if (result.error) {
  console.error(
    'Unable to start Docker. Install and start Docker, then rerun npm test.',
  );
  throw result.error;
}
if (result.status !== 0) {
  process.exit(result.status ?? 1);
}
