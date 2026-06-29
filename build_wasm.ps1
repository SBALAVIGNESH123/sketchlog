# PowerShell script to build WASM using Docker

Write-Host "Compiling SketchLog to WASM..."

if (!(Test-Path "wasm/dist")) {
    New-Item -ItemType Directory -Path "wasm/dist" | Out-Null
}

$CurrentDir = (Get-Item .).FullName
$EmsdkImage = "emscripten/emsdk:5.0.4@sha256:ef91f658e0104636cf40a702c99169273969cf04d939f4f08e5d0223965d5788"

docker run --rm -v "${CurrentDir}:/src" -w /src $EmsdkImage emcc `
    -O3 `
    --bind `
    -Iinclude `
    wasm/bindings.cpp `
    -s MODULARIZE=1 `
    -s EXPORT_NAME="SketchLogModule" `
    -s ALLOW_MEMORY_GROWTH=1 `
    -o wasm/dist/sketchlog.js

if ($LASTEXITCODE -ne 0) {
    Write-Error "Docker compilation failed with exit code $LASTEXITCODE"
    exit $LASTEXITCODE
}

Write-Host "Successfully built wasm/dist/sketchlog.js and wasm/dist/sketchlog.wasm" -ForegroundColor Green
