ARG PYTHON_IMAGE=python:3.11-slim@sha256:cdbd05fb6f457ca275ff51ce00d93d865ca0b6a25f5ffb08262d94f6835771e5
FROM ${PYTHON_IMAGE} AS builder

# Install build dependencies required for pybind11 and C++ extensions
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Copy only package build inputs.
COPY pyproject.toml setup.py README.md ./
COPY cpp ./cpp
COPY include ./include
COPY python ./python

# Build and install the package with server extras into a virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN python -m pip install --no-cache-dir --upgrade \
        "pip==26.1.2" "setuptools==82.0.1" && \
    python -m pip install --no-cache-dir .[server]

# Stage 2: Runtime
FROM ${PYTHON_IMAGE}

# Create a fixed, non-root runtime identity.
RUN groupadd -r sketchlog && useradd -r -g sketchlog -u 10001 sketchlog

# Copy the virtual environment from the builder
COPY --from=builder --chown=sketchlog:sketchlog /opt/venv /opt/venv

# Ensure the virtual environment is in the PATH
ENV PATH="/opt/venv/bin:$PATH"

# Set sensible default environment variables
ENV SKETCHLOG_MAX_STREAMS="1000" \
    SKETCHLOG_MEMORY_THRESHOLD="90" \
    PYTHONDONTWRITEBYTECODE="1" \
    PYTHONUNBUFFERED="1"

# Switch to the non-root user
USER 10001:10001

WORKDIR /app

# Expose the API port
EXPOSE 8000

# Healthcheck against the health probe
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).read()"]

# Start the server using uvicorn
CMD ["uvicorn", "sketchlog.server:app", "--host", "0.0.0.0", "--port", "8000"]
