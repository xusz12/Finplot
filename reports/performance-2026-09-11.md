# Synthetic performance evidence

Command: `.venv/bin/python tests/benchmark.py`

Environment: MACMINI, arm64, macOS 26.6.2, Python 3.14.4. The benchmark creates isolated temporary SQLite databases; it never opens a real ledger.

- Fixture: 10,000 synthetic transactions; SHA-256 `1b88a26541fa6eb3fff89b24d18257926e5d939000530810abee18bbc79d97a3`.
- 20 requests each: initial dashboard maximum 58.20 ms (p95 39.32 ms); filtered dashboard maximum 33.82 ms (p95 33.82 ms); category drill-down maximum 31.88 ms (p95 31.58 ms).
- 100 commits without version polling: maximum 0.08 ms, p95 0.03 ms, 0 errors.
- 100 commits with `/api/version` polling before each commit: maximum 0.09 ms, p95 0.06 ms, 0 errors; p95 delta 0.03 ms.
- Benchmark process resource sample: 2.013 seconds CPU during the polling run and 61.28 MB peak RSS.

These timings measure the local API and SQLite path. A browser provider was unavailable in this runner, so they do not claim a separate browser paint/render measurement; the page itself remains a same-origin static asset with no third-party resources.
