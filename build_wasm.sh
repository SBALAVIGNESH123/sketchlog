#!/usr/bin/env bash
set -euo pipefail

echo "Compiling SketchLog to WASM..."

mkdir -p wasm/dist

readonly EMSDK_IMAGE="emscripten/emsdk:5.0.4@sha256:ef91f658e0104636cf40a702c99169273969cf04d939f4f08e5d0223965d5788"

docker run --rm -v "$(pwd):/src" -w /src -u "$(id -u):$(id -g)" \
    "${EMSDK_IMAGE}" emcc \
    -O3 \
    --bind \
    -Iinclude \
    wasm/bindings.cpp \
    -s MODULARIZE=1 \
    -s EXPORT_NAME="SketchLogModule" \
    -s ALLOW_MEMORY_GROWTH=1 \
    -o wasm/dist/sketchlog.js

echo "Successfully built wasm/dist/sketchlog.js and wasm/dist/sketchlog.wasm"
