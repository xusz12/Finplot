"""Loopback-only read-only ledger observatory API."""
from __future__ import annotations

import hashlib
import os
import sqlite3
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

MAX_PAGE = 200
ALLOWED_NATURES = {"日常", "投资", "往来", "调整"}
ALLOWED_DIRECTIONS = {"收入", "支出"}


def db_path() -> Path:
    raw = os.environ.get("LEDGER_DB")
    if not raw:
        raise RuntimeError("LEDGER_DB must name the ledger database")
    path = Path(raw).expanduser().resolve()
    if not path.is_file():
        raise RuntimeError("configured ledger database is unavailable")
    return path


@contextmanager
def readonly_connection(path: Path):
    uri = f"file:{quote(path.as_posix())}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("BEGIN")  # one snapshot for an entire dashboard response
    try:
        yield conn
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def money(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    cents = abs(cents)
    return f"{sign}{cents // 100}.{cents % 100:02d}"


def parse_day(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(422, "date must be YYYY-MM-DD") from exc


def scope(kind: str, value: str | None, start: str | None, end: str | None):
    today = date.today()
    if kind == "all":
        return None, None, "全部"
    if kind == "custom":
        if not start or not end:
            raise HTTPException(422, "custom scope requires start and end")
        first, last = parse_day(start), parse_day(end)
        if first > last:
            raise HTTPException(422, "start must not be later than end")
        return first.isoformat(), (last + timedelta(days=1)).isoformat(), f"{start} 至 {end}"
    if kind == "day":
        day = parse_day(value or "")
        return day.isoformat(), (day + timedelta(days=1)).isoformat(), day.isoformat()
    if kind == "month":
        try:
            year, month = map(int, (value or "").split("-"))
            first = date(year, month, 1)
        except (ValueError, TypeError):
            raise HTTPException(422, "month must be YYYY-MM")
        following = date(year + (month == 12), 1 if month == 12 else month + 1, 1)
        return first.isoformat(), following.isoformat(), value
    if kind in {"quarter", "half", "year"}:
        try:
            year = int((value or "").split("-")[0])
            suffix = (value or "").split("-")[1] if "-" in (value or "") else ""
        except ValueError:
            raise HTTPException(422, "invalid calendar scope")
        if kind == "year":
            first, following = date(year, 1, 1), date(year + 1, 1, 1)
        elif kind == "quarter" and suffix in {"Q1", "Q2", "Q3", "Q4"}:
            month = (int(suffix[1]) - 1) * 3 + 1
            first = date(year, month, 1)
            following = date(year + (month == 10), 1 if month == 10 else month + 3, 1)
        elif kind == "half" and suffix in {"H1", "H2"}:
            first = date(year, 1 if suffix == "H1" else 7, 1)
            following = date(year + (suffix == "H2"), 1 if suffix == "H2" else 7, 1)
        else:
            raise HTTPException(422, f"invalid {kind} value")
        return first.isoformat(), following.isoformat(), value
    raise HTTPException(422, "unknown scope")


def filters(start, end, tags: list[str], tag_mode: str, nature: str | None, direction: str | None):
    clauses, params = [], []
    if start:
        clauses += ["t.occurred_at >= ?", "t.occurred_at < ?"]; params += [start, end]
    if nature:
        if nature not in ALLOWED_NATURES: raise HTTPException(422, "invalid nature")
        clauses.append("c.nature = ?"); params.append(nature)
    if direction:
        if direction not in ALLOWED_DIRECTIONS: raise HTTPException(422, "invalid direction")
        clauses.append("t.direction = ?"); params.append(direction)
    tags = sorted(set(tags))
    if tags:
        placeholders = ",".join("?" for _ in tags)
        compare = "=" if tag_mode == "all" else ">="
        clauses.append(f"t.id IN (SELECT tt.transaction_id FROM transaction_tags tt JOIN tags tg ON tg.id=tt.tag_id WHERE tg.code IN ({placeholders}) GROUP BY tt.transaction_id HAVING count(DISTINCT tg.code) {compare} ?)")
        params += tags + [len(tags) if tag_mode == "all" else 1]
    return (" WHERE " + " AND ".join(clauses) if clauses else ""), params


def version(conn: sqlite3.Connection, path: Path) -> str:
    ident = path.stat()
    change = conn.execute("PRAGMA data_version").fetchone()[0]
    return hashlib.sha256(f"{ident.st_dev}:{ident.st_ino}:{ident.st_mtime_ns}:{ident.st_size}:{change}".encode()).hexdigest()[:16]


def require_schema(conn: sqlite3.Connection) -> None:
    try:
        value = conn.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()
        if value is None or int(value[0]) < 3:
            raise ValueError
        checks = ["PRAGMA integrity_check", "PRAGMA foreign_key_check"]
        if any(conn.execute(check).fetchone()[0] != "ok" for check in checks[:1]) or conn.execute(checks[1]).fetchone() is not None:
            raise ValueError
    except (sqlite3.Error, ValueError):
        raise HTTPException(503, "ledger unavailable")


app = FastAPI(title="Billing Observatory", docs_url="/docs")
app.add_middleware(CORSMiddleware, allow_origins=[], allow_methods=[], allow_headers=[])

@app.middleware("http")
async def privacy_headers(request: Request, call_next):
    host = request.headers.get("host", "")
    if host and not (host.startswith("127.0.0.1") or host.startswith("localhost")):
        return JSONResponse({"detail": "loopback host required"}, status_code=403)
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = "default-src 'self'; connect-src 'self'"
    return response

@app.get("/api/dashboard")
def dashboard(kind: str = "month", value: str | None = None, start: str | None = None, end: str | None = None,
              tag: list[str] = Query(default=[]), tag_mode: str = "any", nature: str | None = None,
              direction: str | None = None, limit: int = 50):
    if tag_mode not in {"any", "all"}: raise HTTPException(422, "tag_mode must be any or all")
    if not 1 <= limit <= MAX_PAGE: raise HTTPException(422, "limit must be 1..200")
    start, end, label = scope(kind, value, start, end)
    try: path = db_path()
    except RuntimeError as exc: raise HTTPException(503, "ledger unavailable") from exc
    try:
        with readonly_connection(path) as conn:
            require_schema(conn)
            where, params = filters(start, end, tag, tag_mode, nature, direction)
            base = "FROM transactions t JOIN categories c ON c.id=t.category_id JOIN category_groups g ON g.id=c.group_id"
            totals = conn.execute(f"SELECT count(*) count, coalesce(sum(CASE WHEN t.direction='收入' THEN t.amount_cents END),0) income, coalesce(sum(CASE WHEN t.direction='支出' THEN t.amount_cents END),0) expense {base}{where}", params).fetchone()
            trend = conn.execute(f"SELECT substr(t.occurred_at,1,10) day, coalesce(sum(CASE WHEN t.direction='收入' THEN t.amount_cents END),0) income, coalesce(sum(CASE WHEN t.direction='支出' THEN t.amount_cents END),0) expense {base}{where} GROUP BY day ORDER BY day", params).fetchall()
            categories = conn.execute(f"SELECT t.direction,g.code group_code,g.name group_name,c.code,c.name,c.nature,count(*) count,sum(t.amount_cents) cents {base}{where} GROUP BY t.direction,g.code,g.name,c.code,c.name,c.nature ORDER BY cents DESC,c.code", params).fetchall()
            rows = conn.execute(f"SELECT t.id,t.occurred_at,t.direction,t.amount_cents,g.code group_code,g.name group_name,c.code category_code,c.name category,c.nature,coalesce(group_concat(DISTINCT tg.code),'') tags {base} LEFT JOIN transaction_tags tt ON tt.transaction_id=t.id LEFT JOIN tags tg ON tg.id=tt.tag_id{where} GROUP BY t.id ORDER BY t.occurred_at DESC,t.id DESC LIMIT ?", params + [limit]).fetchall()
            tag_rows = conn.execute("SELECT code,name FROM tags WHERE active=1 ORDER BY name").fetchall()
            cutoff = conn.execute("SELECT min(occurred_at) first,max(occurred_at) last FROM transactions").fetchone()
            income, expense = int(totals['income']), int(totals['expense'])
            return {"version": version(conn,path), "scope": {"kind":kind,"label":label,"start":start,"end_exclusive":end,"timezone":"Asia/Shanghai"}, "data_range":{"first":cutoff['first'],"last":cutoff['last']}, "filtered_count":int(totals['count']), "totals":{"income_cents":str(income),"expense_cents":str(expense),"balance_cents":str(income-expense),"income_yuan":money(income),"expense_yuan":money(expense),"balance_yuan":money(income-expense)}, "trend":[{"day":r['day'],"income_cents":str(r['income']),"expense_cents":str(r['expense'])} for r in trend], "categories":[{**dict(r),"cents":str(r['cents'])} for r in categories], "transactions":[{**dict(r),"amount_cents":str(r['amount_cents']),"tags": [x for x in r['tags'].split(',') if x]} for r in rows], "tags":[dict(r) for r in tag_rows]}
    except sqlite3.Error as exc:
        raise HTTPException(503, "ledger unavailable") from exc


app.mount("/", StaticFiles(directory=Path(__file__).parents[2] / "frontend", html=True), name="frontend")
