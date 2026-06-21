ARG PYTHON_VERSION=3.11
FROM python:${PYTHON_VERSION}-slim AS builder

# Install build dependencies required for pybind11 and C++ extensions
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    cmake \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Copy project files needed for the build
COPY . .

# Build and install the package with server extras into a virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN pip install --no-cache-dir build wheel && \
    pip install --no-cache-dir .[server]

# Stage 2: Runtime
FROM python:${PYTHON_VERSION}-slim

# Install curl for healthcheck and create a non-root user
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -r sketchlog && useradd -r -g sketchlog -u 10001 sketchlog

# Copy the virtual environment from the builder
COPY --from=builder --chown=sketchlog:sketchlog /opt/venv /opt/venv

# Ensure the virtual environment is in the PATH
ENV PATH="/opt/venv/bin:$PATH"

# Set sensible default environment variables
ENV SKETCHLOG_MAX_STREAMS="1000" \
    SKETCHLOG_MEMORY_THRESHOLD="90" \
    PYTHONUNBUFFERED="1"

# Switch to the non-root user
USER 10001:10001

WORKDIR /app

# Expose the API port
EXPOSE 8000

# Healthcheck against the health probe
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Start the server using uvicorn
CMD ["uvicorn", "sketchlog.server:app", "--host", "0.0.0.0", "--port", "8000"]
