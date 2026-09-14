"""Reproducible synthetic performance check; never points at a real ledger."""
from __future__ import annotations

import os
import hashlib
import resource
import sqlite3
import sys
import tempfile
import threading
import time
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parents[1]))
from make_fixture import make

os.environ.setdefault("LEDGER_DB", "/tmp/observatory-benchmark.sqlite3")
from backend.app.main import app


HEADERS = {"host": "127.0.0.1"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def seed(path: Path, count: int = 10_000) -> None:
    make(path)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    rows = []
    for transaction_id in range(6, count + 1):
        day = (transaction_id % 28) + 1
        category_id = 1 if transaction_id % 4 else 2
        direction = "支出" if category_id == 1 else "收入"
        amount = (transaction_id % 10_000) + 1
        rows.append((transaction_id, f"2026-09-{day:02d} 12:00:00", direction, amount, category_id, "synthetic"))
    conn.executemany("INSERT INTO transactions VALUES(?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()


def p95(values: list[float]) -> float:
    return sorted(values)[max(0, int(len(values) * 0.95) - 1)]


def endpoint_ms(client: TestClient, params: dict[str, str], repetitions: int = 20) -> tuple[float, float]:
    elapsed = []
    for _ in range(repetitions):
        started = time.perf_counter()
        response = client.get("/api/dashboard", params=params, headers=HEADERS)
        response.raise_for_status()
        elapsed.append((time.perf_counter() - started) * 1000)
    return max(elapsed), p95(elapsed)


def _poll_versions(stop: threading.Event, started: threading.Event, stats: dict[str, int]) -> None:
    """Run an uninterrupted version-probe client beside the SQLite writer."""
    with TestClient(app, client=("127.0.0.1", 50011)) as client:
        while not stop.is_set():
            try:
                response = client.get("/api/version", headers=HEADERS)
                stats["requests"] += 1
                if response.status_code != 200:
                    stats["errors"] += 1
            except Exception:
                stats["errors"] += 1
            finally:
                started.set()


def commit_ms(path: Path, polling: bool, repetitions: int = 100, inter_commit_pause: float = 0.02) -> dict[str, float | int]:
    os.environ["LEDGER_DB"] = str(path)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    elapsed = []
    errors = 0
    lock_errors = 0
    first_id = 30_000 if polling else 20_000
    stop = threading.Event()
    poll_started = threading.Event()
    poll_stats = {"requests": 0, "errors": 0}
    poller = None
    if polling:
        poller = threading.Thread(target=_poll_versions, args=(stop, poll_started, poll_stats), name="version-poller")
        poller.start()
        if not poll_started.wait(timeout=10):
            stop.set()
            poller.join(timeout=10)
            raise RuntimeError("version poller did not start")
    try:
        for offset in range(repetitions):
            started = time.perf_counter()
            try:
                transaction_id = first_id + offset
                conn.execute("INSERT INTO transactions VALUES(?,?,?,?,?,?)", (transaction_id, "2026-09-11 12:00:00", "支出", 1, 1, "synthetic"))
                conn.commit()
                elapsed.append((time.perf_counter() - started) * 1000)
            except sqlite3.Error as exc:
                errors += 1
                if "locked" in str(exc).lower():
                    lock_errors += 1
                conn.rollback()
            if inter_commit_pause:
                # Keep the reader load overlapping the full 100-commit run;
                # this pause is outside the measured commit interval.
                time.sleep(inter_commit_pause)
    finally:
        if polling:
            stop.set()
            poller.join(timeout=10)
            if poller.is_alive():
                raise RuntimeError("version poller did not stop")
    conn.close()
    return {
        "max_ms": max(elapsed) if elapsed else float("nan"),
        "p95_ms": p95(elapsed) if elapsed else float("nan"),
        "errors": errors,
        "lock_errors": lock_errors,
        "poll_requests": poll_stats["requests"],
        "poll_errors": poll_stats["errors"],
    }


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="observatory-benchmark-") as directory:
        baseline = Path(directory) / "baseline.sqlite3"
        seed(baseline)
        os.environ["LEDGER_DB"] = str(baseline)
        client = TestClient(app, client=("127.0.0.1", 50010))
        initial = endpoint_ms(client, {"kind": "all"})
        filtered = endpoint_ms(client, {"kind": "all", "nature": "日常", "direction": "支出"})
        drill = endpoint_ms(client, {"kind": "all", "category": "exp_food"})

        off = Path(directory) / "poll-off.sqlite3"
        on = Path(directory) / "poll-on.sqlite3"
        seed(off)
        seed(on)
        off_cpu_started = time.process_time()
        off_result = commit_ms(off, polling=False)
        off_cpu_seconds = time.process_time() - off_cpu_started
        started_cpu = time.process_time()
        on_result = commit_ms(on, polling=True)
        cpu_seconds = time.process_time() - started_cpu
        rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024)

        print(f"synthetic_transactions=10000")
        print(f"fixture_sha256={sha256(baseline)}")
        print(f"initial_ms_max={initial[0]:.2f} initial_ms_p95={initial[1]:.2f}")
        print(f"filter_ms_max={filtered[0]:.2f} filter_ms_p95={filtered[1]:.2f}")
        print(f"drill_ms_max={drill[0]:.2f} drill_ms_p95={drill[1]:.2f}")
        print(f"commit_off_ms_max={off_result['max_ms']:.2f} commit_off_ms_p95={off_result['p95_ms']:.2f} errors={off_result['errors']} lock_errors={off_result['lock_errors']}")
        print(f"commit_on_ms_max={on_result['max_ms']:.2f} commit_on_ms_p95={on_result['p95_ms']:.2f} errors={on_result['errors']} lock_errors={on_result['lock_errors']} poll_requests={on_result['poll_requests']} poll_errors={on_result['poll_errors']}")
        print(f"commit_inter_commit_pause_ms=20.00 (outside measured commit interval)")
        print(f"commit_p95_delta_ms={on_result['p95_ms'] - off_result['p95_ms']:.2f} off_cpu_seconds={off_cpu_seconds:.3f} polling_cpu_seconds={cpu_seconds:.3f} peak_rss_mb={rss_mb:.2f}")


if __name__ == "__main__":
    main()
