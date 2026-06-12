# Contributing to sketchlog

Thanks for your interest in contributing. Whether it's a bug fix, new feature, documentation improvement, or benchmark — all contributions are welcome.

## Getting started

```bash
git clone https://github.com/SBALAVIGNESH123/sketchlog.git
cd sketchlog
pip install -e .
pip install pytest
python -m pytest tests/test_sketchlog.py -v
```

All 21 core tests should pass.

## How to contribute

1. Fork the repository
2. Create a branch: `git checkout -b my-feature`
3. Make your changes
4. Run the tests: `python -m pytest tests/test_sketchlog.py -v`
5. Commit: `git commit -m "add my feature"`
6. Push: `git push origin my-feature`
7. Open a pull request

## What we're looking for

Check the [open issues](https://github.com/SBALAVIGNESH123/sketchlog/issues) for tasks labeled `good first issue` or `help wanted`.

Some areas where help is especially useful:

- **Benchmarks**: comparisons against T-Digest, Apache DataSketches, Prometheus histograms
- **Documentation**: examples, tutorials, API reference
- **Integrations**: Prometheus exporter, OpenTelemetry bridge, FastAPI middleware
- **Testing**: edge cases, fuzz testing, cross-platform validation
- **Performance**: profiling, optimizing the C++ backend

## Code style

- Keep it simple. Readable code over clever code.
- Add tests for new functionality.
- Document error bounds for any new sketch algorithm.

## Reporting bugs

Use the [bug report template](https://github.com/SBALAVIGNESH123/sketchlog/issues/new?template=bug_report.yml). Include a minimal reproduction if possible.

## Questions?

Open a [discussion](https://github.com/SBALAVIGNESH123/sketchlog/issues) or reach out. No question is too small.
