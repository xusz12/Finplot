# Billing Observatory

Local-only, read-only analytics for the SQLite ledger. It never runs migrations or writes to the configured database.

## Run

```sh
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt
LEDGER_DB=/absolute/path/to/ledger.sqlite3 .venv/bin/uvicorn backend.app.main:app --host 127.0.0.1 --port 8765
```

For the synthetic demo database only:

```sh
python3 tests/make_fixture.py /tmp/observatory-demo.sqlite3
LEDGER_DB=/tmp/observatory-demo.sqlite3 .venv/bin/uvicorn backend.app.main:app --host 127.0.0.1 --port 8765
```

The API is at `http://127.0.0.1:8765/docs`. Configure a local React build in `frontend/` to call `/api/dashboard`; it must be served from the same loopback origin (no CDN or third-party analytics).

## Verification

```sh
python3 -m unittest discover -s tests -v
```

Tests create isolated temporary SQLite databases only. Do not use a real ledger for change/refresh tests. API responses have `Cache-Control: no-store`, and request logging deliberately excludes query parameters and transaction data.
