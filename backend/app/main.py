"""Loopback-only read-only ledger observatory API."""
from __future__ import annotations

import base64
import binascii
import calendar
import hashlib
import ipaddress
import json
import os
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from urllib.parse import quote, urlsplit
from zoneinfo import ZoneInfo

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
NATURE_ORDER = ("日常", "投资", "往来", "调整")
GRAIN_VALUES = {"day", "month", "quarter", "half", "year"}
COMPARE_VALUES = {"previous", "year_ago", "custom", "none"}
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
PUBLIC_HOST_ENV = "FINPLOT_PUBLIC_HOST"
PUBLIC_HOST = "xmac-mini-1.tailef8d6d.ts.net"
SHANGHAI = ZoneInfo("Asia/Shanghai")


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


def parse_host_header(value: str | None) -> tuple[str, int | None] | None:
    """Parse an HTTP Host value without accepting URL/path syntax."""
    if not value or value != value.strip() or any(char in value for char in "\r\n"):
        return None
    try:
        parts = urlsplit(f"//{value}")
        hostname = parts.hostname
        port = parts.port
    except ValueError:
        return None
    if (not hostname or parts.username or parts.password or parts.path
            or parts.query or parts.fragment):
        return None
    return hostname.lower(), port


def configured_public_host() -> str | None:
    """Return the one explicitly configured Tailscale host, if valid.

    An empty or malformed value deliberately disables proxied public access;
    there is no wildcard or implicit ``*.ts.net`` trust.
    """
    raw = os.environ.get(PUBLIC_HOST_ENV, "")
    if raw != PUBLIC_HOST:
        return None
    parsed = parse_host_header(PUBLIC_HOST)
    if parsed is None:
        return None
    hostname, port = parsed
    if port is not None or hostname in LOOPBACK_HOSTS:
        return None
    return hostname


def is_loopback_peer(request: Request) -> bool:
    client = request.client
    if client is None:
        return False
    host = (client.host or "").lower()
    if host in LOOPBACK_HOSTS:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def single_header(request: Request, name: str) -> tuple[bool, str | None]:
    """Read one header and reject duplicate, comma-joined, or empty values."""
    values = request.headers.getlist(name)
    if not values:
        return False, None
    if len(values) != 1:
        return True, None
    raw = values[0]
    value = raw.strip()
    if not value or "," in value or any(char in value for char in "\r\n"):
        return True, None
    return True, value


def valid_forwarded_for(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


def request_target(request: Request) -> tuple[str, str, int] | None:
    """Resolve the trusted browser-visible scheme/host/port.

    Finplot is normally called directly over loopback.  Tailscale Serve may
    connect from loopback while forwarding the browser host and HTTPS scheme;
    those headers are considered only when the immediate peer is loopback and
    the host is the exact configured ``FINPLOT_PUBLIC_HOST``.
    """
    host_present, host_value = single_header(request, "host")
    if not host_present or host_value is None:
        return None
    host_parts = parse_host_header(host_value)
    if host_parts is None:
        return None
    host, port = host_parts
    peer_is_loopback = is_loopback_peer(request)
    forwarded_names = {name.lower() for name in request.headers.keys()
                       if name.lower() == "forwarded" or name.lower().startswith("x-forwarded-")}
    if not forwarded_names.issubset({"x-forwarded-for", "x-forwarded-host", "x-forwarded-proto"}):
        return None
    forwarded_present = bool(forwarded_names)
    if forwarded_present and not peer_is_loopback:
        return None
    # Standard Forwarded syntax is not needed by the Tailscale Serve contract;
    # fail closed instead of accepting an alternate, less-tested grammar.
    if request.headers.get("forwarded") is not None:
        return None

    forwarded_for_present, forwarded_for = single_header(request, "x-forwarded-for")
    if forwarded_for_present and (forwarded_for is None or not valid_forwarded_for(forwarded_for)):
        return None
    forwarded_host_present, forwarded_host_raw = single_header(request, "x-forwarded-host")
    forwarded_proto_present, forwarded_proto = single_header(request, "x-forwarded-proto")
    if (forwarded_host_present and forwarded_host_raw is None) or (
            forwarded_proto_present and forwarded_proto is None):
        return None
    forwarded_host = parse_host_header(forwarded_host_raw) if forwarded_host_present else None
    if forwarded_host_present and forwarded_host is None:
        return None
    if forwarded_proto_present and forwarded_proto not in {"http", "https"}:
        return None

    public_host = configured_public_host()
    if forwarded_host_present:
        forwarded_hostname, forwarded_port = forwarded_host
        if (public_host is None or forwarded_hostname != public_host
                or (forwarded_port is not None and forwarded_port != 443)):
            return None
        effective_host = public_host
        effective_scheme = forwarded_proto or request.url.scheme.lower()
    elif host == public_host:
        if port is not None and port != 443:
            return None
        effective_host = public_host
        effective_scheme = forwarded_proto or request.url.scheme.lower()
    elif host in LOOPBACK_HOSTS:
        # When the proxy keeps the backend Host as loopback, the explicit
        # configured public host supplies the browser-visible hostname.  The
        # forwarded HTTPS scheme is still mandatory; without it this remains a
        # normal direct loopback request.
        if forwarded_proto_present:
            if public_host is None or forwarded_proto != "https":
                return None
            return "https", public_host, 443
        if not peer_is_loopback:
            return None
        return request.url.scheme.lower(), host, effective_port(request.url.scheme, port)
    else:
        return None

    if not peer_is_loopback or effective_scheme != "https":
        return None
    return "https", effective_host, 443


def same_origin(origin: str, target: tuple[str, str, int]) -> bool:
    try:
        origin_parts = urlsplit(origin)
        scheme, host, port = target
        return (origin_parts.scheme.lower() in {"http", "https"}
                and not origin_parts.username and not origin_parts.password
                and origin_parts.path in {"", "/"} and not origin_parts.query and not origin_parts.fragment
                and origin_parts.hostname is not None
                and origin_parts.hostname.lower() == host
                and origin_parts.scheme.lower() == scheme
                and effective_port(origin_parts.scheme, origin_parts.port) == port)
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
    target = request_target(request)
    origin_present, origin = single_header(request, "origin")
    if origin_present and origin is None:
        return JSONResponse({"detail": "same-origin required"}, status_code=403)
    if target is None:
        return JSONResponse({"detail": "loopback host required"}, status_code=403)
    if origin and not same_origin(origin, target):
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


def _analytics_today() -> date:
    return datetime.now(SHANGHAI).date()


def _date_value(value: str | None) -> date | None:
    return date.fromisoformat(value[:10]) if value else None


def _date_text(value: date | None) -> str | None:
    return value.isoformat() if value else None


def _next_month_date(value: date) -> date:
    return next_month(value.year, value.month)


def _shift_months(value: date, months: int) -> date:
    index = value.year * 12 + value.month - 1 + months
    year, month_index = divmod(index, 12)
    month = month_index + 1
    if year < 1 or year > 9999:
        raise HTTPException(422, "calendar scope is out of range")
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _shift_year(value: date, years: int) -> date:
    year = value.year + years
    if year < 1 or year > 9999:
        raise HTTPException(422, "calendar scope is out of range")
    return value.replace(year=year, day=min(value.day, calendar.monthrange(year, value.month)[1]))


def _decimal_value(numerator: int, denominator: int, places: str = "0.0001") -> str | None:
    if denominator == 0:
        return None
    value = (Decimal(numerator) / Decimal(denominator)).quantize(Decimal(places), rounding=ROUND_HALF_UP)
    return format(value, "f")


def _ratio(numerator: int, denominator: int) -> tuple[float | None, str | None]:
    if denominator <= 0:
        return None, "no_base"
    return round(numerator / denominator, 6), None


def _analytics_range(start: date | None, end: date | None, label: str,
                     requested_start: date | None = None,
                     requested_end: date | None = None,
                     partial: bool = False) -> dict:
    days = (end - start).days if start and end else 0
    return {
        "label": label,
        "start": _date_text(start),
        "end": _date_text(end - timedelta(days=1)) if start and end and end > start else None,
        "end_exclusive": _date_text(end),
        "days": max(0, days),
        "requested_start": _date_text(requested_start if requested_start is not None else start),
        "requested_end_exclusive": _date_text(requested_end if requested_end is not None else end),
        "is_partial": bool(partial),
        "timezone": "Asia/Shanghai",
    }


def _scope_dates(kind: str, value: str | None, start: str | None, end: str | None) -> tuple[date | None, date | None, str]:
    first, following, label = scope(kind, value, start, end)
    return _date_value(first), _date_value(following), label


def _effective_current_range(kind: str, value: str | None, start: str | None, end: str | None,
                             period_mode: str, data_first: date | None, data_last: date | None,
                             today: date) -> tuple[dict, date | None, date | None, date | None, date | None]:
    requested_start, requested_end, label = _scope_dates(kind, value, start, end)
    if requested_start is None:
        # “全部” has a concrete chart interval so that its daily denominator,
        # calendar and long-range series remain reproducible. An empty ledger
        # intentionally remains an empty range.
        actual_start = data_first
        actual_end = data_last + timedelta(days=1) if data_last else None
    else:
        actual_start, actual_end = requested_start, requested_end
    partial = False
    if (kind in {"month", "quarter", "half", "year"} and
            period_mode == "elapsed" and actual_start and actual_end and
            actual_start <= today < actual_end and actual_end > today + timedelta(days=1)):
        actual_end = today + timedelta(days=1)
        partial = True
    current = _analytics_range(actual_start, actual_end, label, requested_start, requested_end, partial)
    return current, requested_start, requested_end, actual_start, actual_end


def _prior_range(kind: str, current_start: date, current_end: date,
                 requested_start: date | None, requested_end: date | None,
                 period_mode: str) -> tuple[date, date]:
    length = max(1, (current_end - current_start).days)
    if kind == "day":
        return current_start - timedelta(days=1), current_start
    if kind == "custom":
        return current_start - timedelta(days=length), current_start
    if kind == "month":
        anchor = current_start.replace(day=1)
        previous = _shift_months(anchor, -1)
        if period_mode == "elapsed" and requested_end and current_end < requested_end:
            return previous, min(previous + timedelta(days=length), anchor)
        return previous, anchor
    if kind == "quarter":
        month = ((current_start.month - 1) // 3) * 3 + 1
        anchor = date(current_start.year, month, 1)
        previous = _shift_months(anchor, -3)
        if period_mode == "elapsed" and requested_end and current_end < requested_end:
            return previous, min(previous + timedelta(days=length), anchor)
        return previous, anchor
    if kind == "half":
        month = 1 if current_start.month <= 6 else 7
        anchor = date(current_start.year, month, 1)
        previous = _shift_months(anchor, -6)
        if period_mode == "elapsed" and requested_end and current_end < requested_end:
            return previous, min(previous + timedelta(days=length), anchor)
        return previous, anchor
    if kind == "year":
        anchor = date(current_start.year, 1, 1)
        previous = date(current_start.year - 1, 1, 1)
        if period_mode == "elapsed" and requested_end and current_end < requested_end:
            return previous, min(previous + timedelta(days=length), anchor)
        return previous, anchor
    raise HTTPException(422, "unknown scope")


def _year_ago_range(current_start: date, current_end: date,
                    full_natural_period: bool = False) -> tuple[date, date]:
    """Map a half-open range by calendar date, clipping leap day safely.

    `current_end` is exclusive. Shifting it directly can collapse a one-day
    2024-02-28 range to an empty 2023-02-28 range. Map the last included
    calendar day first, then reconstruct the exclusive boundary. This keeps
    2/28 and 2/29 as visible one-day comparisons and maps a complete leap
    February to the complete non-leap February.
    """
    if current_start >= current_end:
        raise HTTPException(422, "comparison range must not be empty")
    if full_natural_period:
        # Natural period boundaries are already exclusive (for example
        # 2025-02-01..2025-03-01). Mapping both boundaries preserves a full
        # month in either leap direction: 2025-02 maps to full 2024-02 and
        # 2024-02 maps to full 2023-02.
        return _shift_year(current_start, -1), _shift_year(current_end, -1)
    previous_start = _shift_year(current_start, -1)
    previous_last = _shift_year(current_end - timedelta(days=1), -1)
    previous_end = previous_last + timedelta(days=1)
    if previous_end <= previous_start:
        raise HTTPException(422, "comparison range is out of calendar bounds")
    return previous_start, previous_end


def _period_anchor(value: date, grain: str) -> date:
    if grain == "day":
        return value
    if grain == "month":
        return value.replace(day=1)
    if grain == "quarter":
        return date(value.year, ((value.month - 1) // 3) * 3 + 1, 1)
    if grain == "half":
        return date(value.year, 1 if value.month <= 6 else 7, 1)
    if grain == "year":
        return date(value.year, 1, 1)
    raise HTTPException(422, "grain must be day, month, quarter, half or year")


def _next_grain(value: date, grain: str) -> date:
    if grain == "day":
        return value + timedelta(days=1)
    if grain == "month":
        return _next_month_date(value)
    if grain == "quarter":
        return _shift_months(value, 3)
    if grain == "half":
        return _shift_months(value, 6)
    if grain == "year":
        return date(value.year + 1, 1, 1)
    raise HTTPException(422, "grain must be day, month, quarter, half or year")


def _grain_label(value: date, grain: str) -> str:
    if grain == "day":
        return value.isoformat()
    if grain == "month":
        return value.strftime("%Y-%m")
    if grain == "quarter":
        return f"{value.year:04d}-Q{((value.month - 1) // 3) + 1}"
    if grain == "half":
        return f"{value.year:04d}-H{1 if value.month <= 6 else 2}"
    return f"{value.year:04d}"


def _grain_buckets(start: date | None, end: date | None, grain: str) -> list[dict]:
    if not start or not end or start >= end:
        return []
    cursor = _period_anchor(start, grain)
    buckets = []
    while cursor < end:
        following = _next_grain(cursor, grain)
        bucket_start, bucket_end = max(start, cursor), min(end, following)
        if bucket_start < bucket_end:
            buckets.append({
                "key": _grain_label(cursor, grain),
                "label": _grain_label(cursor, grain),
                "natural_start": cursor.isoformat(),
                "natural_end_exclusive": following.isoformat(),
                "start": bucket_start.isoformat(),
                "end_exclusive": bucket_end.isoformat(),
                "days": (bucket_end - bucket_start).days,
            })
        cursor = following
    return buckets


def _analytics_rows(conn: sqlite3.Connection, start: date | None, end: date | None,
                    tags: list[str], tag_mode: str, nature: str | None,
                    direction: str | None, group_code: str | None,
                    category_code: str | None) -> list[dict]:
    where, params = filters(_date_text(start), _date_text(end), tags, tag_mode,
                            nature, direction, group_code, category_code)
    sql = ("SELECT t.id,t.occurred_at,t.direction,t.amount_cents,"
           "g.code group_code,g.name group_name,c.code category_code,"
           "c.name category_name,c.nature "
           "FROM transactions t JOIN categories c ON c.id=t.category_id "
           "JOIN category_groups g ON g.id=c.group_id" + where +
           " ORDER BY t.occurred_at,t.id")
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def _nature_empty() -> dict:
    return {nature: {"income": 0, "expense": 0, "count": 0} for nature in NATURE_ORDER}


def _summary(rows: list[dict]) -> dict:
    income = sum(int(row["amount_cents"]) for row in rows if row["direction"] == "收入")
    expense = sum(int(row["amount_cents"]) for row in rows if row["direction"] == "支出")
    nature = _nature_empty()
    for row in rows:
        item = nature[row["nature"]]
        item["count"] += 1
        item["income" if row["direction"] == "收入" else "expense"] += int(row["amount_cents"])

    def nature_item(name: str, item: dict) -> dict:
        balance = item["income"] - item["expense"]
        rate, reason = _ratio(balance, item["income"])
        return {
            "nature": name,
            "transaction_count": item["count"],
            "income_cents": str(item["income"]),
            "expense_cents": str(item["expense"]),
            "balance_cents": str(balance),
            "income_yuan": money(item["income"]),
            "expense_yuan": money(item["expense"]),
            "balance_yuan": money(balance),
            "balance_rate": rate,
            "balance_rate_reason": reason,
        }

    investment = nature["投资"]
    investment_net = investment["income"] - investment["expense"]
    return {
        "transaction_count": len(rows),
        "income_count": sum(1 for row in rows if row["direction"] == "收入"),
        "expense_count": sum(1 for row in rows if row["direction"] == "支出"),
        "income_cents": str(income),
        "expense_cents": str(expense),
        "balance_cents": str(income - expense),
        "income_yuan": money(income),
        "expense_yuan": money(expense),
        "balance_yuan": money(income - expense),
        "nature": [nature_item(name, nature[name]) for name in NATURE_ORDER],
        "investment": {
            "transaction_count": investment["count"],
            "gain_cents": str(investment["income"]),
            "loss_cents": str(investment["expense"]),
            "net_cents": str(investment_net),
            "gain_yuan": money(investment["income"]),
            "loss_yuan": money(investment["expense"]),
            "net_yuan": money(investment_net),
        },
    }


def _category_record(direction: str, code: str, name: str, group_code: str,
                     group_name: str, amount: int, count: int,
                     total: int, other: bool = False) -> dict:
    share, reason = _ratio(amount, total)
    average = _decimal_value(amount, count)
    return {
        "direction": direction,
        "category_code": code,
        "category_name": name,
        "group_code": group_code,
        "group_name": group_name,
        "amount_cents": str(amount),
        "amount_yuan": money(amount),
        "transaction_count": count,
        "average_cents": average,
        "average_numerator_cents": str(amount),
        "average_denominator": count,
        "average_yuan": _decimal_value(amount, count * 100, "0.0001") if count else None,
        "share": share,
        "share_reason": reason,
        "is_other": other,
    }


def _category_views(current_rows: list[dict], compare_rows: list[dict] | None,
                    top_n: int) -> tuple[dict, list[dict]]:
    def collect(rows: list[dict]) -> dict:
        result = {}
        for row in rows:
            key = (row["direction"], row["category_code"])
            item = result.setdefault(key, {
                "direction": row["direction"], "category_code": row["category_code"],
                "category_name": row["category_name"], "group_code": row["group_code"],
                "group_name": row["group_name"], "amount": 0, "count": 0,
            })
            item["amount"] += int(row["amount_cents"])
            item["count"] += 1
        return result

    current = collect(current_rows)
    view = {}
    for direction in ("收入", "支出"):
        all_items = [item for item in current.values() if item["direction"] == direction]
        all_items.sort(key=lambda item: (-item["amount"], item["category_code"]))
        total = sum(item["amount"] for item in all_items)
        selected = all_items if top_n == 0 else all_items[:top_n]
        items = [_category_record(direction, item["category_code"], item["category_name"],
                                  item["group_code"], item["group_name"], item["amount"],
                                  item["count"], total) for item in selected]
        other_amount = sum(item["amount"] for item in all_items[len(selected):])
        other_count = sum(item["count"] for item in all_items[len(selected):])
        other = _category_record(direction, "__other__", "其他", "__other__", "其他",
                                 other_amount, other_count, total, True)
        view["income" if direction == "收入" else "expense"] = {
            "direction": direction,
            "total_cents": str(total),
            "transaction_count": sum(item["count"] for item in all_items),
            "top_n": "all" if top_n == 0 else top_n,
            "items": items,
            "other": other,
        }

    compare_map = collect(compare_rows or [])
    changes = []
    keys = sorted(set(current) | set(compare_map), key=lambda key: (key[0], key[1]))
    for key in keys:
        cur = current.get(key, {})
        old = compare_map.get(key, {})
        amount = int(cur.get("amount", 0))
        prior = int(old.get("amount", 0))
        delta = amount - prior
        change_ratio, reason = _ratio(delta, prior)
        changes.append({
            "direction": key[0],
            "category_code": key[1],
            "category_name": cur.get("category_name", old.get("category_name")),
            "group_code": cur.get("group_code", old.get("group_code")),
            "group_name": cur.get("group_name", old.get("group_name")),
            "current_cents": str(amount),
            "compare_cents": str(prior),
            "delta_cents": str(delta),
            "current_transaction_count": int(cur.get("count", 0)),
            "compare_transaction_count": int(old.get("count", 0)),
            "current_average_cents": _decimal_value(amount, int(cur.get("count", 0))),
            "compare_average_cents": _decimal_value(prior, int(old.get("count", 0))),
            "current_average_numerator_cents": str(amount),
            "current_average_denominator": int(cur.get("count", 0)),
            "compare_average_numerator_cents": str(prior),
            "compare_average_denominator": int(old.get("count", 0)),
            "change_ratio": change_ratio,
            "change_ratio_reason": reason,
        })
    changes.sort(key=lambda item: (-abs(int(item["delta_cents"])), item["direction"], item["category_code"]))
    return view, changes


def _summary_delta(current: dict, compare: dict | None) -> dict | None:
    if compare is None:
        return None
    result = {}
    for field in ("income_cents", "expense_cents", "balance_cents", "transaction_count"):
        current_value = int(current[field]) if field.endswith("cents") else int(current[field])
        compare_value = int(compare[field]) if field.endswith("cents") else int(compare[field])
        delta = current_value - compare_value
        result[field] = {
            "current": str(current_value) if field.endswith("cents") else current_value,
            "compare": str(compare_value) if field.endswith("cents") else compare_value,
            "delta_cents": str(delta) if field.endswith("cents") else None,
            "delta": delta,
        }
        if field.endswith("cents"):
            if field == "balance_cents" and (current_value < 0 or compare_value < 0):
                ratio, reason = None, "negative_balance"
            else:
                ratio, reason = _ratio(delta, compare_value)
            result[field]["change_ratio"] = ratio
            result[field]["change_ratio_reason"] = reason
            if field == "balance_cents":
                def state(value: int) -> str:
                    return "surplus" if value > 0 else "deficit" if value < 0 else "zero"
                result[field]["current_state"] = state(current_value)
                result[field]["compare_state"] = state(compare_value)
                result[field]["state_change"] = (
                    "转盈" if current_value > 0 and compare_value <= 0 else
                    "转亏" if current_value < 0 and compare_value >= 0 else
                    "状态不变"
                )
    return result


def _period_summary_item(bucket: dict, rows: list[dict], today: date,
                         cumulative: int) -> tuple[dict, int]:
    start, end = date.fromisoformat(bucket["start"]), date.fromisoformat(bucket["end_exclusive"])
    period_rows = [row for row in rows if start <= _date_value(row["occurred_at"]) < end]
    future = start > today
    item = {
        **bucket,
        "is_future": future,
        "is_partial": bucket["start"] != bucket["natural_start"] or bucket["end_exclusive"] != bucket["natural_end_exclusive"],
    }
    if future:
        for field in ("transaction_count", "income_cents", "expense_cents", "balance_cents",
                      "income_yuan", "expense_yuan", "balance_yuan", "cumulative_balance_cents",
                      "income_count", "expense_count", "nature", "investment"):
            item[field] = None
        return item, cumulative
    summary = _summary(period_rows)
    cumulative += int(summary["balance_cents"])
    item.update({
        "transaction_count": summary["transaction_count"],
        "income_count": summary["income_count"],
        "expense_count": summary["expense_count"],
        "income_cents": summary["income_cents"],
        "expense_cents": summary["expense_cents"],
        "balance_cents": summary["balance_cents"],
        "income_yuan": summary["income_yuan"],
        "expense_yuan": summary["expense_yuan"],
        "balance_yuan": summary["balance_yuan"],
        "cumulative_balance_cents": str(cumulative),
        "nature": summary["nature"],
        "investment": summary["investment"],
    })
    return item, cumulative


def _trend(conn: sqlite3.Connection, current_start: date | None, current_end: date | None,
           compare_start: date | None, compare_end: date | None, grain: str,
           current_rows: list[dict], compare_rows: list[dict] | None, today: date) -> dict:
    def series(start: date | None, end: date | None, rows: list[dict] | None) -> list[dict]:
        cumulative = 0
        output = []
        for bucket in _grain_buckets(start, end, grain):
            item, cumulative = _period_summary_item(bucket, rows or [], today, cumulative)
            output.append(item)
        return output

    current = series(current_start, current_end, current_rows)
    compare = series(compare_start, compare_end, compare_rows) if compare_start and compare_end else []
    aligned = []
    for index in range(max(len(current), len(compare))):
        aligned.append({"index": index, "current": current[index] if index < len(current) else None,
                        "compare": compare[index] if index < len(compare) else None})
    return {
        "grain": grain,
        "current": current,
        "compare": compare,
        "aligned": aligned,
        "cumulative_basis": "selected current range start",
    }


def _daily_metrics(current: dict, start: date | None, end: date | None) -> dict:
    days = (end - start).days if start and end else 0
    expense = int(current["expense_cents"])
    return {
        "start": _date_text(start),
        "end_exclusive": _date_text(end),
        "effective_days": days,
        "denominator": "calendar_days",
        "expense_cents": str(expense),
        "daily_expense_numerator_cents": str(expense),
        "daily_expense_denominator_days": days,
        "daily_expense_cents": _decimal_value(expense, days),
        "daily_expense_yuan": _decimal_value(expense, days * 100) if days else None,
    }


def _coverage(data_first: date | None, data_last: date | None,
              start: date | None, end: date | None) -> dict:
    if not start or not end:
        return {"complete": False, "warning": "no_range", "covered_days": 0, "range_days": 0}
    range_days = max(0, (end - start).days)
    if not data_first or not data_last:
        return {
            "complete": False,
            "warning": "no_ledger_rows",
            "covered_days": 0,
            "range_days": range_days,
            "message": "按已记录账单计算；账本没有可核对的记录",
        }
    ledger_end = data_last + timedelta(days=1)
    overlap_start, overlap_end = max(start, data_first), min(end, ledger_end)
    covered_days = max(0, (overlap_end - overlap_start).days)
    boundary_gap = data_first > start or ledger_end < end
    return {
        "complete": not boundary_gap,
        "warning": "ledger_boundary_outside_range" if boundary_gap else None,
        "covered_days": covered_days,
        "range_days": range_days,
        "first_record_date": data_first.isoformat(),
        "last_record_date": data_last.isoformat(),
        "message": ("按已记录账单计算；账本数据未覆盖参照期完整边界" if boundary_gap
                    else "按已记录账单计算；仅以账本边界核对覆盖范围"),
    }


def _three_month_reference(conn: sqlite3.Connection, anchor: date | None,
                           tags: list[str], tag_mode: str, nature: str | None,
                           direction: str | None, group_code: str | None,
                           category_code: str | None, data_first: date | None,
                           data_last: date | None) -> dict | None:
    if not anchor:
        return None
    month = anchor.replace(day=1)
    start, end = _shift_months(month, -3), month
    rows = _analytics_rows(conn, start, end, tags, tag_mode, nature, direction, group_code, category_code)
    expense = sum(int(row["amount_cents"]) for row in rows if row["direction"] == "支出")
    days = (end - start).days
    return {
        "start": start.isoformat(),
        "end_exclusive": end.isoformat(),
        "months": 3,
        "natural_days": days,
        "expense_cents": str(expense),
        "expense_yuan": money(expense),
        "daily_expense_numerator_cents": str(expense),
        "daily_expense_denominator_days": days,
        "daily_expense_cents": _decimal_value(expense, days),
        "monthly_average_numerator_cents": str(expense),
        "monthly_average_denominator": 3,
        "daily_expense_yuan": _decimal_value(expense, days * 100),
        "monthly_average_expense_cents": _decimal_value(expense, 3),
        "monthly_average_expense_yuan": _decimal_value(expense, 300),
        "coverage": _coverage(data_first, data_last, start, end),
        "denominator": "weighted_calendar_days",
    }


def _long_overview(conn: sqlite3.Connection, anchor: date | None, current_end: date | None,
                   tags: list[str], tag_mode: str, nature: str | None,
                   direction: str | None, group_code: str | None,
                   category_code: str | None, today: date) -> dict | None:
    if not anchor:
        return None
    selected_month = anchor.replace(day=1)
    first_month = _shift_months(selected_month, -11)
    end = _next_month_date(selected_month)
    all_rows = _analytics_rows(conn, first_month, end, tags, tag_mode, nature,
                               direction, group_code, category_code)
    output = []
    cursor = first_month
    while cursor < end:
        following = _next_month_date(cursor)
        effective_end = following
        is_current = cursor.year == today.year and cursor.month == today.month
        # The selected month is still an unfinished natural month even when
        # the caller asks for a full-period view; the flag tells the UI not to
        # present it as a closed month. `display_end_exclusive` continues to
        # expose the actual queried boundary for either period mode.
        partial = is_current and today < following
        if is_current and current_end and current_end < following:
            effective_end = max(cursor, current_end)
        month_rows = [row for row in all_rows
                      if cursor <= _date_value(row["occurred_at"]) < effective_end]
        future = cursor > today
        item = {
            "month": cursor.strftime("%Y-%m"),
            "start": cursor.isoformat(),
            "end_exclusive": following.isoformat(),
            "display_end_exclusive": effective_end.isoformat(),
            "days": (following - cursor).days,
            "display_days": (effective_end - cursor).days,
            "is_current_month": is_current,
            "is_partial": partial,
            "is_future": future,
        }
        if future:
            for field in ("transaction_count", "income_cents", "expense_cents", "balance_cents",
                          "income_yuan", "expense_yuan", "balance_yuan"):
                item[field] = None
        else:
            item.update(_summary(month_rows))
        output.append(item)
        cursor = following
    return {
        "anchor_month": selected_month.strftime("%Y-%m"),
        "start": first_month.isoformat(),
        "end_exclusive": end.isoformat(),
        "months": output,
        "note": "当前未结束月份以实际已读取日期标记为部分月份；其余月份为完整自然月范围",
    }


def _calendar_view(current_start: date | None, current_end: date | None,
                   current_rows: list[dict], today: date) -> dict:
    anchor = (current_start or today).replace(day=1)
    following = _next_month_date(anchor)
    by_day = {}
    for row in current_rows:
        day = _date_value(row["occurred_at"])
        item = by_day.setdefault(day, {"expense": 0, "count": 0})
        if row["direction"] == "支出":
            item["expense"] += int(row["amount_cents"])
            item["count"] += 1
    days = []
    for offset in range((following - anchor).days):
        day = anchor + timedelta(days=offset)
        in_scope = bool(current_start and current_end and current_start <= day < current_end)
        future = day > today
        item = by_day.get(day, {"expense": 0, "count": 0})
        visible = in_scope and not future
        days.append({
            "date": day.isoformat(),
            "weekday": day.weekday(),
            "in_scope": in_scope,
            "is_future": future,
            "expense_cents": str(item["expense"]) if visible else None,
            "expense_yuan": money(item["expense"]) if visible else None,
            "transaction_count": item["count"] if visible else None,
            "empty_label": "无记录" if visible and item["count"] == 0 else None,
        })
    total = sum(int(item["expense_cents"]) for item in days if item["expense_cents"] is not None)
    return {
        "month": anchor.strftime("%Y-%m"),
        "start": anchor.isoformat(),
        "end_exclusive": following.isoformat(),
        "days": days,
        "total_expense_cents": str(total),
        "total_expense_yuan": money(total),
        "note": "按上海自然日；未来日期和范围外日期独立标记，不填充为零",
    }


def _comparison_range(kind: str, compare: str, current_start: date | None,
                      current_end: date | None, requested_start: date | None,
                      requested_end: date | None, period_mode: str,
                      compare_start: str | None, compare_end: str | None) -> tuple[date | None, date | None, str | None, str | None]:
    if compare == "none":
        if compare_start or compare_end:
            raise HTTPException(422, "compare_start and compare_end require compare=custom")
        return None, None, None, "disabled"
    if compare == "custom":
        if not compare_start or not compare_end:
            raise HTTPException(422, "custom comparison requires compare_start and compare_end")
        first, last = parse_day(compare_start), parse_day(compare_end)
        if first > last:
            raise HTTPException(422, "compare_start must not be later than compare_end")
        return first, last + timedelta(days=1), f"{compare_start} 至 {compare_end}", None
    if compare_start or compare_end:
        raise HTTPException(422, "compare_start and compare_end require compare=custom")
    if kind == "all":
        return None, None, None, "all_scope_requires_custom_dates"
    if not current_start or not current_end:
        return None, None, None, "all_scope_requires_custom_dates"
    if compare == "previous":
        first, last = _prior_range(kind, current_start, current_end, requested_start, requested_end, period_mode)
        return first, last, "上一期", None
    if compare == "year_ago":
        full_natural_period = (
            kind in {"month", "quarter", "half", "year"}
            and requested_start is not None and requested_end is not None
            and current_start == requested_start and current_end == requested_end
        )
        first, last = _year_ago_range(current_start, current_end, full_natural_period)
        return first, last, "去年同期", None
    raise HTTPException(422, "compare must be previous, year_ago, custom or none")


@app.get("/api/analytics")
def analytics(kind: str = "month", value: str | None = None,
              start: str | None = None, end: str | None = None,
              tag: list[str] = Query(default=[]), tag_mode: str = "any",
              nature: str | None = None, direction: str | None = None,
              group: str | None = None, category: str | None = None,
              compare: str = "previous", compare_start: str | None = None,
              compare_end: str | None = None, period_mode: str = "elapsed",
              grain: str | None = None, top_n: int = 10,
              version: str | None = Query(default=None, alias="version"),
              if_version: str | None = None):
    """Return the complete v1.1 analysis in one read-only SQLite snapshot.

    The response deliberately keeps the current and comparison intervals,
    actual date boundaries and decimal-string money fields explicit. The
    frontend can therefore use a chart point's real range when requesting
    dashboard detail rows instead of guessing from a display label.
    """
    if tag_mode not in {"any", "all"}:
        raise HTTPException(422, "tag_mode must be any or all")
    if period_mode not in {"elapsed", "full"}:
        raise HTTPException(422, "period_mode must be elapsed or full")
    if compare not in COMPARE_VALUES:
        raise HTTPException(422, "compare must be previous, year_ago, custom or none")
    if grain is not None and grain not in GRAIN_VALUES:
        raise HTTPException(422, "grain must be day, month, quarter, half or year")
    if not 0 <= top_n <= 200:
        raise HTTPException(422, "top_n must be 0..200")
    if version and if_version and version != if_version:
        raise HTTPException(422, "version and if_version must match")
    tags = normalise_tags(tag)
    group = validate_code(group, "group")
    category = validate_code(category, "category")
    # The product default is the current Shanghai month. Historical callers
    # continue to get the explicitly supplied value, while an empty request is
    # useful for the local page and deterministic in the deployed timezone.
    today = _analytics_today()
    if kind == "month" and value is None:
        value = today.strftime("%Y-%m")
    try:
        path = db_path()
    except RuntimeError as exc:
        raise HTTPException(503, "ledger unavailable") from exc
    try:
        with readonly_connection(path) as conn:
            require_schema(conn)
            current_version = dataset_version(conn)
            request_version = version or if_version
            if request_version and request_version != current_version:
                raise HTTPException(409, {"reason": "dataset_changed", "version": current_version})
            cutoff = conn.execute("SELECT min(occurred_at) first,max(occurred_at) last FROM transactions").fetchone()
            data_first = _date_value(cutoff["first"])
            data_last = _date_value(cutoff["last"])
            current_range, requested_start, requested_end, current_start, current_end = _effective_current_range(
                kind, value, start, end, period_mode, data_first, data_last, today)
            if grain is None:
                span = (current_end - current_start).days if current_start and current_end else 0
                grain = "day" if kind in {"day", "month"} or span <= 62 else "month"
            compare_start_date, compare_end_date, compare_label, compare_reason = _comparison_range(
                kind, compare, current_start, current_end, requested_start, requested_end,
                period_mode, compare_start, compare_end)
            current_rows = _analytics_rows(conn, current_start, current_end, tags, tag_mode,
                                           nature, direction, group, category)
            compare_rows = None
            if compare_start_date and compare_end_date:
                compare_rows = _analytics_rows(conn, compare_start_date, compare_end_date,
                                               tags, tag_mode, nature, direction, group, category)
            current_summary = _summary(current_rows)
            compare_summary = _summary(compare_rows) if compare_rows is not None else None
            category_view, category_changes = _category_views(current_rows, compare_rows, top_n)
            if compare_rows is None:
                category_changes = []
            category_changes_by_direction = {
                "income": [item for item in category_changes if item["direction"] == "收入"],
                "expense": [item for item in category_changes if item["direction"] == "支出"],
            }
            trend = _trend(conn, current_start, current_end, compare_start_date,
                           compare_end_date, grain, current_rows, compare_rows, today)
            daily = _daily_metrics(current_summary, current_start, current_end)
            daily_nature = next(item for item in current_summary["nature"] if item["nature"] == "日常")
            daily["daily_balance_rate"] = daily_nature["balance_rate"]
            daily["daily_balance_rate_reason"] = daily_nature["balance_rate_reason"]
            three_month = _three_month_reference(
                conn, current_start, tags, tag_mode, nature, direction, group, category,
                data_first, data_last)
            overview = _long_overview(
                conn, current_start, current_end, tags, tag_mode, nature, direction,
                group, category, today)
            calendar_view = _calendar_view(current_start, current_end, current_rows, today)
            current_range["coverage"] = _coverage(data_first, data_last, current_start, current_end)
            compare_range = None
            if compare_start_date and compare_end_date:
                compare_range = _analytics_range(compare_start_date, compare_end_date,
                                                 compare_label or "对比期", compare_start_date,
                                                 compare_end_date, False)
                compare_range["coverage"] = _coverage(data_first, data_last,
                                                        compare_start_date, compare_end_date)
            category_change_totals = {}
            if compare_summary is not None:
                for key, direction_name in (("income", "收入"), ("expense", "支出")):
                    items = category_changes_by_direction[key]
                    category_change_totals[key] = {
                        "current_cents": str(int(current_summary["income_cents" if key == "income" else "expense_cents"])),
                        "compare_cents": str(int(compare_summary["income_cents" if key == "income" else "expense_cents"])),
                        "delta_cents": str(sum(int(item["delta_cents"]) for item in items)),
                        "direction": direction_name,
                    }
            comparison = {
                "requested_mode": compare,
                "mode": compare if compare_range else "none",
                "available": compare_range is not None,
                "reason": compare_reason,
                "same_snapshot": True,
                "current": current_range,
                "compare": compare_range,
                "days_difference": ((current_end - current_start).days -
                                    (compare_end_date - compare_start_date).days)
                                   if current_start and current_end and compare_start_date and compare_end_date else None,
            }
            return {
                "contract_version": "1.1",
                "version": current_version,
                "instance_id": INSTANCE_ID,
                "synced_at": checked_at(),
                "data_cutoff": cutoff["last"],
                "data_range": {
                    "first": cutoff["first"], "last": cutoff["last"],
                    "first_date": _date_text(data_first), "last_date": _date_text(data_last),
                },
                "read_only": True,
                "scope": current_range,
                "filters": {
                    "kind": kind, "value": value, "start": start, "end": end,
                    "direction": direction, "nature": nature, "group": group,
                    "category": category, "tags": tags, "tag_mode": tag_mode,
                },
                "comparison": comparison,
                "summary": {
                    "current": current_summary,
                    "compare": compare_summary,
                    "delta": _summary_delta(current_summary, compare_summary),
                },
                # These aliases make the contract convenient for the existing
                # dashboard card renderer while summary remains the canonical
                # v1.1 shape.
                "filtered_count": current_summary["transaction_count"],
                "totals": {
                    "income_cents": current_summary["income_cents"],
                    "expense_cents": current_summary["expense_cents"],
                    "balance_cents": current_summary["balance_cents"],
                    "income_yuan": current_summary["income_yuan"],
                    "expense_yuan": current_summary["expense_yuan"],
                    "balance_yuan": current_summary["balance_yuan"],
                },
                "investment": current_summary["investment"],
                "nature_breakdown": current_summary["nature"],
                "categories": category_view,
                "category_changes": category_changes,
                "category_changes_by_direction": category_changes_by_direction,
                "category_change_totals": category_change_totals,
                "trend": trend,
                "periods": trend["current"],
                "daily_expense": daily,
                "three_month_reference": three_month,
                "overview_12_months": overview,
                "expense_calendar": calendar_view,
                "tags": [dict(row) for row in conn.execute(
                    "SELECT code,name FROM tags WHERE active=1 ORDER BY name").fetchall()],
            }
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
