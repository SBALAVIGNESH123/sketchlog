#!/bin/bash
set -e

echo "Compiling SketchLog to WASM..."

mkdir -p wasm/dist

docker run --rm -v "$(pwd):/src" -u $(id -u):$(id -g) emscripten/emsdk emcc \
    -O3 \
    --bind \
    -Iinclude \
    wasm/bindings.cpp \
    -s MODULARIZE=1 \
    -s EXPORT_NAME="SketchLogModule" \
    -s ALLOW_MEMORY_GROWTH=1 \
    -o wasm/dist/sketchlog.js

echo "Successfully built wasm/dist/sketchlog.js and wasm/dist/sketchlog.wasm"
