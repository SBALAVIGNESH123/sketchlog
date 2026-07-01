ARG PYTHON_IMAGE=python:3.11-slim@sha256:cdbd05fb6f457ca275ff51ce00d93d865ca0b6a25f5ffb08262d94f6835771e5
FROM ${PYTHON_IMAGE}

RUN groupadd --system sketchlog && useradd --system --gid sketchlog --uid 10001 sketchlog
WORKDIR /demo
COPY --chown=10001:10001 telemetry.py smoke.py ./
USER 10001:10001

CMD ["python", "/demo/telemetry.py"]
