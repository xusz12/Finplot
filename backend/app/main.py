"""Loopback-only read-only ledger observatory API."""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, urlsplit

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

MAX_PAGE = 200
MAX_TAGS = 20
MAX_FILTER_TEXT = 128
INSTANCE_ID = uuid.uuid4().hex
ALLOWED_NATURES = {"日常", "投资", "往来", "调整"}
ALLOWED_DIRECTIONS = {"收入", "支出"}
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


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
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value or ""):
        raise HTTPException(422, "date must be YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(422, "date must be YYYY-MM-DD") from exc


def parse_year(value: str, kind: str) -> int:
    if not re.fullmatch(r"\d{4}", value or ""):
        raise HTTPException(422, f"{kind} must use a four-digit year")
    year = int(value)
    if not 1 <= year <= 9999:
        raise HTTPException(422, f"invalid {kind} value")
    return year


def next_month(year: int, month: int) -> date:
    try:
        return date(year + (month == 12), 1 if month == 12 else month + 1, 1)
    except ValueError as exc:
        raise HTTPException(422, "calendar scope is out of range") from exc


def validate_code(value: str | None, label: str) -> str | None:
    if value is None:
        return None
    if not value or len(value) > MAX_FILTER_TEXT or "\x00" in value:
        raise HTTPException(422, f"invalid {label}")
    return value


def scope(kind: str, value: str | None, start: str | None, end: str | None):
    if kind == "all":
        return None, None, "全部"
    if kind == "custom":
        if not start or not end:
            raise HTTPException(422, "custom scope requires start and end")
        first, last = parse_day(start), parse_day(end)
        if first > last:
            raise HTTPException(422, "start must not be later than end")
        try:
            following = last + timedelta(days=1)
        except OverflowError as exc:
            raise HTTPException(422, "end is out of range") from exc
        return first.isoformat(), following.isoformat(), f"{start} 至 {end}"
    if kind == "day":
        day = parse_day(value or "")
        try:
            following = day + timedelta(days=1)
        except OverflowError as exc:
            raise HTTPException(422, "day is out of range") from exc
        return day.isoformat(), following.isoformat(), day.isoformat()
    if kind == "month":
        if not re.fullmatch(r"\d{4}-\d{2}", value or ""):
            raise HTTPException(422, "month must be YYYY-MM")
        try:
            year, month = map(int, (value or "").split("-"))
            first = date(year, month, 1)
        except (ValueError, TypeError):
            raise HTTPException(422, "month must be YYYY-MM")
        return first.isoformat(), next_month(year, month).isoformat(), value
    if kind in {"quarter", "half", "year"}:
        if kind == "year":
            year = parse_year(value or "", kind)
            suffix = ""
        else:
            pattern = r"\d{4}-Q[1-4]" if kind == "quarter" else r"\d{4}-H[12]"
            if not re.fullmatch(pattern, value or ""):
                raise HTTPException(422, f"invalid {kind} value")
            year = parse_year((value or "").split("-")[0], kind)
            suffix = (value or "").split("-")[1]
        try:
            if kind == "year":
                first, following = date(year, 1, 1), date(year + 1, 1, 1)
            elif kind == "quarter":
                month = (int(suffix[1]) - 1) * 3 + 1
                first, following = date(year, month, 1), next_month(year, month + 2)
            else:
                month = 1 if suffix == "H1" else 7
                first = date(year, month, 1)
                following = date(year, 7, 1) if month == 1 else date(year + 1, 1, 1)
        except (ValueError, TypeError) as exc:
            raise HTTPException(422, f"invalid {kind} value") from exc
        return first.isoformat(), following.isoformat(), value
    raise HTTPException(422, "unknown scope")


def normalise_tags(tags: list[str]) -> list[str]:
    if len(tags) > MAX_TAGS:
        raise HTTPException(422, f"at most {MAX_TAGS} tags may be selected")
    result = sorted(set(tags))
    if any(not tag or len(tag) > MAX_FILTER_TEXT or "\x00" in tag for tag in result):
        raise HTTPException(422, "invalid tag")
    return result


def filters(start, end, tags: list[str], tag_mode: str, nature: str | None, direction: str | None,
            group_code: str | None = None, category_code: str | None = None,
            after: tuple[str, int] | None = None):
    clauses, params = [], []
    if start:
        clauses += ["t.occurred_at >= ?", "t.occurred_at < ?"]; params += [start, end]
    if nature:
        if nature not in ALLOWED_NATURES: raise HTTPException(422, "invalid nature")
        clauses.append("c.nature = ?"); params.append(nature)
    if direction:
        if direction not in ALLOWED_DIRECTIONS: raise HTTPException(422, "invalid direction")
        clauses.append("t.direction = ?"); params.append(direction)
    group_code = validate_code(group_code, "group")
    category_code = validate_code(category_code, "category")
    if group_code:
        clauses.append("g.code = ?"); params.append(group_code)
    if category_code:
        clauses.append("c.code = ?"); params.append(category_code)
    tags = normalise_tags(tags)
    if tags:
        placeholders = ",".join("?" for _ in tags)
        compare = "=" if tag_mode == "all" else ">="
        clauses.append(f"t.id IN (SELECT tt.transaction_id FROM transaction_tags tt JOIN tags tg ON tg.id=tt.tag_id WHERE tg.code IN ({placeholders}) GROUP BY tt.transaction_id HAVING count(DISTINCT tg.code) {compare} ?)")
        params += tags + [len(tags) if tag_mode == "all" else 1]
    if after:
        occurred_at, transaction_id = after
        clauses.append("(t.occurred_at < ? OR (t.occurred_at = ? AND t.id < ?))")
        params += [occurred_at, occurred_at, transaction_id]
    return (" WHERE " + " AND ".join(clauses) if clauses else ""), params


def dataset_version(conn: sqlite3.Connection) -> str:
    # Hash logical rows inside the same read transaction; data_version alone is
    # connection-local and file mtimes do not reliably advance for WAL commits.
    digest = hashlib.sha256()
    tables = (("transactions", "id,occurred_at,direction,amount_cents,category_id,note"),
              ("categories", "id,code,name,group_id,direction,nature,active,sort_order"),
              ("category_groups", "id,code,name,direction,active,sort_order"),
              ("tags", "id,code,name,active"),
              ("transaction_tags", "transaction_id,tag_id,source"))
    for table, columns in tables:
        # JSON framing avoids delimiter collisions in notes, names, and tags.
        digest.update(table.encode("utf-8") + b"\0")
        for row in conn.execute(f"SELECT {columns} FROM {table} ORDER BY rowid"):
            digest.update(json.dumps(list(row), ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
            digest.update(b"\n")
    return f"{INSTANCE_ID}:{digest.hexdigest()[:16]}"


def query_key(kind: str, value: str | None, start: str | None, end: str | None,
              tags: list[str], tag_mode: str, nature: str | None, direction: str | None,
              group_code: str | None, category_code: str | None, limit: int) -> str:
    payload = [kind, value, start, end, normalise_tags(tags), tag_mode, nature, direction,
               group_code, category_code, limit]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()[:16]


def encode_cursor(current_version: str, key: str, row: sqlite3.Row) -> str:
    payload = {"version": current_version, "key": key, "occurred_at": row["occurred_at"], "id": int(row["id"])}
    return base64.urlsafe_b64encode(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()).decode().rstrip("=")


def decode_cursor(value: str) -> dict:
    try:
        padded = value + "=" * (-len(value) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode()).decode())
        if (not isinstance(payload, dict) or not isinstance(payload.get("version"), str)
                or not isinstance(payload.get("key"), str) or not isinstance(payload.get("occurred_at"), str)
                or not isinstance(payload.get("id"), int) or isinstance(payload.get("id"), bool)):
            raise ValueError
        return payload
    except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError, binascii.Error) as exc:
        raise HTTPException(422, "invalid cursor") from exc


def checked_at() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def effective_port(scheme: str, port: int | None) -> int:
    return port or (443 if scheme.lower() == "https" else 80)


def same_origin(request: Request, origin: str) -> bool:
    try:
        origin_parts = urlsplit(origin)
        host_parts = urlsplit(f"//{request.headers.get('host', '')}")
        return (origin_parts.scheme.lower() in {"http", "https"}
                and not origin_parts.username and not origin_parts.password
                and origin_parts.path in {"", "/"} and not origin_parts.query and not origin_parts.fragment
                and not host_parts.username and not host_parts.password and not host_parts.path
                and origin_parts.scheme.lower() == request.url.scheme.lower()
                and origin_parts.hostname is not None and host_parts.hostname is not None
                and origin_parts.hostname.lower() == host_parts.hostname.lower()
                and effective_port(origin_parts.scheme, origin_parts.port) == effective_port(request.url.scheme, host_parts.port))
    except ValueError:
        return False


def require_schema(conn: sqlite3.Connection) -> None:
    try:
        value = conn.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()
        if value is None or not re.fullmatch(r"[0-9]+", str(value[0])) or int(value[0]) < 3:
            raise ValueError
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise ValueError
        if conn.execute("SELECT 1 FROM category_groups WHERE direction IS NULL OR direction NOT IN (?, ?) LIMIT 1", tuple(ALLOWED_DIRECTIONS)).fetchone() is not None:
            raise ValueError
        if conn.execute("SELECT 1 FROM categories c JOIN category_groups g ON g.id=c.group_id WHERE c.direction IS NULL OR c.direction NOT IN (?, ?) OR c.direction != g.direction LIMIT 1", tuple(ALLOWED_DIRECTIONS)).fetchone() is not None:
            raise ValueError
        if conn.execute("SELECT 1 FROM transactions t JOIN categories c ON c.id=t.category_id WHERE t.direction IS NULL OR t.direction NOT IN (?, ?) OR t.direction != c.direction LIMIT 1", tuple(ALLOWED_DIRECTIONS)).fetchone() is not None:
            raise ValueError
        if conn.execute("SELECT 1 FROM transaction_tags GROUP BY transaction_id, tag_id HAVING count(*) > 1 LIMIT 1").fetchone() is not None:
            raise ValueError
    except (sqlite3.Error, ValueError):
        raise HTTPException(503, "ledger unavailable")


app = FastAPI(title="Billing Observatory", docs_url="/docs")
app.add_middleware(CORSMiddleware, allow_origins=[], allow_methods=[], allow_headers=[])

@app.middleware("http")
async def privacy_headers(request: Request, call_next):
    try:
        host_parts = urlsplit(f"//{request.headers.get('host', '')}")
        host = (host_parts.hostname or "").lower()
        host_parts.port  # force malformed ports to fail closed
        valid_host = host in LOOPBACK_HOSTS and not host_parts.username and not host_parts.password and not host_parts.path
    except ValueError:
        valid_host = False
    origin = request.headers.get("origin")
    if not valid_host:
        return JSONResponse({"detail": "loopback host required"}, status_code=403)
    if origin and not same_origin(request, origin):
        return JSONResponse({"detail": "same-origin required"}, status_code=403)
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = "default-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
    return response

@app.get("/api/dashboard")
def dashboard(kind: str = "month", value: str | None = None, start: str | None = None, end: str | None = None,
              tag: list[str] = Query(default=[]), tag_mode: str = "any", nature: str | None = None,
              direction: str | None = None, group: str | None = None, category: str | None = None,
              cursor: str | None = None, version: str | None = Query(default=None, alias="version"),
              if_version: str | None = None, limit: int = 50):
    if tag_mode not in {"any", "all"}:
        raise HTTPException(422, "tag_mode must be any or all")
    if not 1 <= limit <= MAX_PAGE:
        raise HTTPException(422, "limit must be 1..200")
    start, end, label = scope(kind, value, start, end)
    tags = normalise_tags(tag)
    group = validate_code(group, "group")
    category = validate_code(category, "category")
    request_version = version or if_version
    if version and if_version and version != if_version:
        raise HTTPException(422, "version and if_version must match")
    key = query_key(kind, value, start, end, tags, tag_mode, nature, direction, group, category, limit)
    cursor_payload = decode_cursor(cursor) if cursor else None
    try:
        path = db_path()
    except RuntimeError as exc:
        raise HTTPException(503, "ledger unavailable") from exc
    try:
        with readonly_connection(path) as conn:
            require_schema(conn)
            current_version = dataset_version(conn)
            if request_version and request_version != current_version:
                raise HTTPException(409, {"reason": "dataset_changed", "version": current_version})
            if cursor_payload:
                if cursor_payload["version"] != current_version:
                    raise HTTPException(409, {"reason": "dataset_changed", "version": current_version})
                if cursor_payload["key"] != key:
                    raise HTTPException(409, {"reason": "cursor_scope_changed", "version": current_version})
                after = (cursor_payload["occurred_at"], cursor_payload["id"])
            else:
                after = None
            where, params = filters(start, end, tags, tag_mode, nature, direction, group, category)
            base = "FROM transactions t JOIN categories c ON c.id=t.category_id JOIN category_groups g ON g.id=c.group_id"
            totals = conn.execute(f"SELECT count(*) count, coalesce(sum(CASE WHEN t.direction='收入' THEN t.amount_cents END),0) income, coalesce(sum(CASE WHEN t.direction='支出' THEN t.amount_cents END),0) expense {base}{where}", params).fetchone()
            trend = conn.execute(f"SELECT substr(t.occurred_at,1,10) day, coalesce(sum(CASE WHEN t.direction='收入' THEN t.amount_cents END),0) income, coalesce(sum(CASE WHEN t.direction='支出' THEN t.amount_cents END),0) expense {base}{where} GROUP BY day ORDER BY day", params).fetchall()
            categories = conn.execute(f"SELECT t.direction,g.code group_code,g.name group_name,c.code,c.name,c.nature,count(*) count,sum(t.amount_cents) cents {base}{where} GROUP BY t.direction,g.code,g.name,c.code,c.name,c.nature ORDER BY cents DESC,c.code", params).fetchall()
            groups = conn.execute(f"SELECT t.direction,g.code group_code,g.name group_name,count(*) count,sum(t.amount_cents) cents {base}{where} GROUP BY t.direction,g.code,g.name ORDER BY cents DESC,g.code", params).fetchall()
            investment_where = where + (" AND " if where else " WHERE ") + "c.nature = ?"
            investment = conn.execute(f"SELECT count(*) count, coalesce(sum(CASE WHEN t.direction='收入' THEN t.amount_cents END),0) income, coalesce(sum(CASE WHEN t.direction='支出' THEN t.amount_cents END),0) expense {base}{investment_where}", params + ["投资"]).fetchone()
            rows_where, rows_params = filters(start, end, tags, tag_mode, nature, direction, group, category, after)
            rows = conn.execute(f"SELECT t.id,t.occurred_at,t.direction,t.amount_cents,g.code group_code,g.name group_name,c.code category_code,c.name category,c.nature,coalesce(group_concat(DISTINCT tg.code),'') tags {base} LEFT JOIN transaction_tags tt ON tt.transaction_id=t.id LEFT JOIN tags tg ON tg.id=tt.tag_id{rows_where} GROUP BY t.id ORDER BY t.occurred_at DESC,t.id DESC LIMIT ?", rows_params + [limit + 1]).fetchall()
            has_more = len(rows) > limit
            page = rows[:limit]
            next_cursor = encode_cursor(current_version, key, page[-1]) if has_more else None
            tag_rows = conn.execute("SELECT code,name FROM tags WHERE active=1 ORDER BY name").fetchall()
            cutoff = conn.execute("SELECT min(occurred_at) first,max(occurred_at) last FROM transactions").fetchone()
            income, expense = int(totals['income']), int(totals['expense'])
            return {"version": current_version, "instance_id": INSTANCE_ID, "synced_at": checked_at(), "data_cutoff": cutoff['last'], "scope": {"kind":kind,"label":label,"start":start,"end_exclusive":end,"timezone":"Asia/Shanghai"}, "data_range":{"first":cutoff['first'],"last":cutoff['last']}, "filtered_count":int(totals['count']), "totals":{"income_cents":str(income),"expense_cents":str(expense),"balance_cents":str(income-expense),"income_yuan":money(income),"expense_yuan":money(expense),"balance_yuan":money(income-expense)}, "investment":{"filtered_count":int(investment['count']),"income_cents":str(investment['income']),"expense_cents":str(investment['expense']),"net_cents":str(int(investment['income'])-int(investment['expense']))}, "trend":[{"day":r['day'],"income_cents":str(r['income']),"expense_cents":str(r['expense'])} for r in trend], "groups":[{**dict(r),"cents":str(r['cents'])} for r in groups], "categories":[{**dict(r),"cents":str(r['cents'])} for r in categories], "transactions":[{**dict(r),"amount_cents":str(r['amount_cents']),"tags": [x for x in r['tags'].split(',') if x]} for r in page], "next_cursor":next_cursor, "page_limit":limit, "tags":[dict(r) for r in tag_rows]}
    except sqlite3.Error as exc:
        raise HTTPException(503, "ledger unavailable") from exc


@app.get("/api/version")
def version_probe():
    try:
        path = db_path()
    except RuntimeError as exc:
        raise HTTPException(503, "ledger unavailable") from exc
    try:
        with readonly_connection(path) as conn:
            require_schema(conn)
            current_version = dataset_version(conn)
            cutoff = conn.execute("SELECT max(occurred_at) last FROM transactions").fetchone()["last"]
            return {"version": current_version, "instance_id": INSTANCE_ID, "data_cutoff": cutoff, "checked_at": checked_at()}
    except sqlite3.Error as exc:
        raise HTTPException(503, "ledger unavailable") from exc


app.mount("/", StaticFiles(directory=Path(__file__).parents[2] / "frontend", html=True), name="frontend")
