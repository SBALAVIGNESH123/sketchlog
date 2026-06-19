# Contributing

We welcome contributions to SketchLog! This document outlines the process for setting up your development environment and contributing to the project.

## Development Setup

1. **Clone the repository:**
   ```bash
   git clone https://github.com/SBALAVIGNESH123/sketchlog.git
   cd sketchlog
   ```

2. **Install development dependencies:**
   It is recommended to use a virtual environment.
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows use `venv\Scripts\activate`
   pip install -e .[dev]
   ```

3. **Running the tests:**
   We use `pytest` for running our comprehensive test suite.
   ```bash
   pytest
   ```

## Local Documentation Build

This project uses [MkDocs](https://www.mkdocs.org/) with the Material theme for documentation. 

To build and preview the documentation locally:
```bash
# Install mkdocs and the material theme
pip install mkdocs-material

# Start the live-reloading local server
mkdocs serve
```
Then open your browser to `http://127.0.0.1:8000/`.

## Code Guidelines

- Ensure your code passes all existing tests.
- If you're adding a new feature, include tests covering both the pure Python fallback and C++ implementations if applicable.
- Make sure `git diff --check` passes cleanly (no trailing whitespaces).
- Ensure type hints pass `mypy --strict`.
