# Billing Observatory

Local-only, read-only analytics for the SQLite ledger. It never runs migrations or writes to the configured database.

## Run

```sh
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt
LEDGER_DB=/absolute/path/to/ledger.sqlite3 .venv/bin/uvicorn backend.app.main:app --host 127.0.0.1 --port 8765
```

Keep the terminal running while using the page. Open `http://127.0.0.1:8765/` in a browser on this Mac. Stop the local instance with `Ctrl-C` in the uvicorn terminal; it does not modify the ledger.

For the synthetic demo database only:

```sh
python3 tests/make_fixture.py /tmp/observatory-demo.sqlite3
LEDGER_DB=/tmp/observatory-demo.sqlite3 .venv/bin/uvicorn backend.app.main:app --host 127.0.0.1 --port 8765
```

The dashboard is the vanilla JavaScript page in `frontend/` and is served by the same FastAPI process at `http://127.0.0.1:8765/`; the API documentation is at `/docs`. It supports Asia/Shanghai day/month/quarter/half-year/year/all/custom scopes, direction and nature filters, active-tag selection with any/all semantics, group → category → transaction drill-down, an investment sub-total card, and stable “load more” pagination. The page polls `/api/version` every two seconds and reloads only after the dataset fingerprint changes. No CDN or third-party analytics is used.

`/api/dashboard` returns decimal strings for every `*_cents` field. Pass `cursor=<next_cursor>` for the next stable page; a changed dataset returns `409` with the current version. A supplied `version` or `if_version` is also checked and returns `409` when stale.

## Verification

```sh
python3 -m unittest discover -s tests -v
```

The synthetic performance check is isolated and reproducible:

```sh
.venv/bin/python tests/benchmark.py
```

The browser-side 20-sample render and concurrent-refresh evidence is recorded in `reports/performance-2026-09-12.md`. The open diagnostics panel on the page shows the current initial/filter/drill timings, automatic-refresh count, and refresh epochs; these are browser render-complete measurements, not API-only timings. Chrome compositor-paint timing is not exposed by the local accessibility harness and remains a separately unverified item.

Tests create isolated temporary SQLite databases only. Do not use a real ledger for change/refresh tests. API responses have `Cache-Control: no-store`, loopback Host and same-origin Origin checks, and a same-origin CSP. Request logging deliberately excludes query parameters and transaction data.
