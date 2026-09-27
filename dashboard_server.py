"""DB Monitoring Agent - local web dashboard for the wind turbine installation database.

    python dashboard_server.py     ->  http://127.0.0.1:8050

Serves static/dashboard.html plus a small JSON API. Reads MySQL with the read-only monitor account and
reads the agent's alerts from alerts.jsonl / heartbeat from monitor_state.json.
"""
import json
import os
import threading
import time
from datetime import date, datetime, timedelta
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pymysql

from db import DB_HOST, DB_NAME, DB_PORT, monitor_connection

HOST = "127.0.0.1"
PORT = int(os.getenv("DASHBOARD_PORT", "8050"))
BASE_DIR = Path(__file__).parent
STATIC_DIR = (BASE_DIR / "static").resolve()
STATIC_TYPES = {".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml"}
DASHBOARD_HTML = STATIC_DIR / "dashboard.html"
ALERTS_FILE = BASE_DIR / "alerts.jsonl"
STATE_FILE = BASE_DIR / "monitor_state.json"

# range key -> (window length, bucket size)
RANGES = {"24h": (timedelta(hours=24), "hour"), "7d": (timedelta(days=7), "day"), "30d": (timedelta(days=30), "day")}
BUCKET_FORMATS = {"hour": ("%Y-%m-%d %H:00", "%Y-%m-%d %H:00"), "day": ("%Y-%m-%d", "%Y-%m-%d")}  # (MySQL, Python)
LATEST_PER_TURBINE = """(SELECT w.* FROM workflow_details w
    JOIN (SELECT turbine_id, MAX(id) AS mid FROM workflow_details GROUP BY turbine_id) t ON w.id = t.mid)"""


def to_jsonable(value):
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat(sep=" ") if isinstance(value, datetime) else value.isoformat()
    raise TypeError(f"not serialisable: {type(value)}")


class Db:
    def __init__(self):
        self.conn = monitor_connection()

    def all(self, sql, params=()):
        with self.conn.cursor() as cur:
            cur.execute(sql, params)
            return list(cur.fetchall())

    def one(self, sql, params=()):
        rows = self.all(sql, params)
        return rows[0] if rows else {}

    def close(self):
        self.conn.close()


def read_alerts() -> list[dict]:
    try:
        lines = ALERTS_FILE.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    alerts = []
    for seq, line in enumerate(lines):
        try:
            alert = json.loads(line)
        except json.JSONDecodeError:
            continue
        alert["seq"] = seq
        alerts.append(alert)
    return alerts


def agent_status() -> dict:
    try:
        state = json.loads(STATE_FILE.read_text())
        last_check = datetime.fromisoformat(state["last_check"])
    except (FileNotFoundError, KeyError, ValueError):
        return {"online": False, "last_check": None, "model": None}
    grace = 3 * int(state.get("poll_interval", 10)) + 5
    return {
        "online": (datetime.now() - last_check).total_seconds() <= grace,
        "last_check": state["last_check"],
        "model": state.get("model"),
        "poll_interval": state.get("poll_interval"),
    }


def kpis(db: Db, start: datetime, end: datetime | None) -> dict:
    where, params = "processed_at >= %s", [start]
    if end is not None:
        where += " AND processed_at < %s"
        params.append(end)
    return db.one(
        f"""SELECT COUNT(*) AS events,
                   COALESCE(SUM(status = 'OPEN'), 0) AS opened,
                   COALESCE(SUM(status = 'PROCESSING'), 0) AS processing,
                   COALESCE(SUM(status = 'COMPLETED'), 0) AS completed,
                   COALESCE(SUM(issue IS NOT NULL), 0) AS issues,
                   COALESCE(SUM(issue IS NOT NULL AND fix_applied IS NOT NULL), 0) AS fixed,
                   COUNT(DISTINCT turbine_id) AS turbines
            FROM workflow_details WHERE {where}""",
        params,
    )


def gauges(db: Db, start: datetime, now: datetime) -> dict:
    """Headline numbers for the gauge dials."""
    t0 = time.perf_counter()
    db.one("SELECT COUNT(*) AS n FROM workflow_details")
    response_ms = (time.perf_counter() - t0) * 1000

    issues = db.one(
        """SELECT COUNT(*) AS reported, COALESCE(SUM(fix_applied IS NOT NULL), 0) AS fixed
           FROM workflow_details WHERE issue IS NOT NULL AND processed_at >= %s""",
        (start,),
    )
    install = db.one(
        """SELECT AVG(TIMESTAMPDIFF(MINUTE, f.first_at, c.done_at)) AS avg_minutes, COUNT(*) AS commissioned
           FROM (SELECT turbine_id, MAX(processed_at) AS done_at FROM workflow_details
                 WHERE status = 'COMPLETED' AND processed_at >= %s GROUP BY turbine_id) c
           JOIN (SELECT turbine_id, MIN(processed_at) AS first_at FROM workflow_details GROUP BY turbine_id) f
             ON f.turbine_id = c.turbine_id""",
        (start,),
    )
    last_hour = db.one("SELECT COUNT(*) AS n FROM workflow_details WHERE processed_at >= %s",
                       (now - timedelta(hours=1),))["n"]
    return {
        "fix_rate": (100 * issues["fixed"] / issues["reported"]) if issues["reported"] else None,
        "issues_reported": issues["reported"],
        "issues_fixed": issues["fixed"],
        "avg_install_minutes": install["avg_minutes"],
        "commissioned": install["commissioned"],
        "events_last_hour": last_hour,
        "db_response_ms": round(response_ms, 1),
    }


def db_info(db: Db) -> dict:
    status = {r["Variable_name"]: r["Value"] for r in db.all(
        "SHOW GLOBAL STATUS WHERE Variable_name IN ('Uptime', 'Threads_connected')")}
    table = db.one(
        """SELECT DATA_LENGTH + INDEX_LENGTH AS size_bytes
           FROM information_schema.TABLES WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'workflow_details'""",
        (DB_NAME,),
    )
    counts = db.one("""SELECT COUNT(*) AS total_rows, COUNT(DISTINCT turbine_id) AS turbines,
                              COUNT(DISTINCT site_name) AS sites FROM workflow_details""")
    return {
        "host": f"{DB_HOST}:{DB_PORT}",
        "database": DB_NAME,
        "table": "workflow_details",
        "version": db.one("SELECT VERSION() AS v")["v"],
        "uptime_seconds": int(status.get("Uptime", 0)),
        "threads_connected": int(status.get("Threads_connected", 0)),
        "rows": int(counts["total_rows"]),
        "turbines": int(counts["turbines"]),
        "sites": int(counts["sites"]),
        "size_bytes": int(table.get("size_bytes") or 0),
    }


def snapshot(range_key: str) -> dict:
    span, unit = RANGES.get(range_key, RANGES["24h"])
    mysql_fmt, py_fmt = BUCKET_FORMATS[unit]
    now = datetime.now().replace(microsecond=0)
    start = now - span
    db = Db()
    try:
        gauge_values = gauges(db, start, now)
        current, previous = kpis(db, start, None), kpis(db, start - span, start)

        timeline_rows = db.all(
            """SELECT DATE_FORMAT(processed_at, %s) AS bucket, status, COUNT(*) AS n
               FROM workflow_details WHERE processed_at >= %s GROUP BY bucket, status""",
            (mysql_fmt, start),
        )
        # Continuous axis: every bucket from start to now, zero-filled.
        step = timedelta(hours=1) if unit == "hour" else timedelta(days=1)
        cursor = start.replace(minute=0, second=0) if unit == "hour" else start.replace(hour=0, minute=0, second=0)
        buckets = []
        while cursor <= now:
            buckets.append(cursor.strftime(py_fmt))
            cursor += step
        timeline = {b: {} for b in buckets}
        for r in timeline_rows:
            if r["bucket"] in timeline:
                timeline[r["bucket"]][r["status"]] = r["n"]

        current_status = db.all(f"SELECT status, COUNT(*) AS n FROM {LATEST_PER_TURBINE} l GROUP BY status")
        by_site = db.all(
            f"""SELECT l.site_name, l.location,
                       COALESCE(SUM(l.status = 'OPEN'), 0) AS opened,
                       COALESCE(SUM(l.status = 'PROCESSING'), 0) AS processing,
                       COALESCE(SUM(l.status = 'COMPLETED'), 0) AS completed,
                       COUNT(*) AS total
                FROM {LATEST_PER_TURBINE} l GROUP BY l.site_name, l.location ORDER BY total DESC"""
        )
        by_phase = db.all(
            """SELECT installation_phase, COUNT(*) AS total,
                      COALESCE(SUM(issue IS NOT NULL), 0) AS issues,
                      COALESCE(SUM(issue IS NOT NULL AND fix_applied IS NOT NULL), 0) AS fixed
               FROM workflow_details WHERE processed_at >= %s
               GROUP BY installation_phase
               ORDER BY FIELD(installation_phase, 'Foundation', 'Tower Erection', 'Nacelle Installation',
                              'Rotor & Blade Installation', 'Electrical & Grid Connection', 'Commissioning')""",
            (start,),
        )
        recent = db.all("SELECT * FROM workflow_details ORDER BY id DESC LIMIT 200")
        max_id = int(db.one("SELECT COALESCE(MAX(id), 0) AS m FROM workflow_details")["m"])
        info = db_info(db)
    finally:
        db.close()

    alerts = read_alerts()
    return {
        "range": range_key,
        "generated_at": now,
        "gauges": gauge_values,
        "db": info,
        "kpis": {"current": current, "previous": previous},
        "timeline": {"unit": unit, "buckets": buckets, "series": timeline},
        "current_status": current_status,
        "by_site": by_site,
        "by_phase": by_phase,
        "recent": recent,
        "alerts": alerts[-100:],
        "alert_count": len(alerts),
        "max_id": max_id,
        "agent": agent_status(),
    }


def updates(after_id: int, after_alert: int) -> dict:
    db = Db()
    try:
        new_records = db.all("SELECT * FROM workflow_details WHERE id > %s ORDER BY id LIMIT 100", (after_id,))
    finally:
        db.close()
    alerts = read_alerts()
    return {
        "new_records": new_records,
        "new_alerts": [a for a in alerts if a["seq"] >= after_alert],
        "alert_count": len(alerts),
        "agent": agent_status(),
        "server_time": datetime.now().replace(microsecond=0),
    }


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        url = urlparse(self.path)
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        try:
            if url.path in ("/", "/index.html"):
                self.send_body(DASHBOARD_HTML.read_bytes(), "text/html; charset=utf-8")
            elif url.path.startswith("/static/"):
                self.send_static(url.path.removeprefix("/static/"))
            elif url.path == "/api/snapshot":
                self.send_json(snapshot(query.get("range", "24h")))
            elif url.path == "/api/updates":
                self.send_json(updates(int(query.get("after_id", 0)), int(query.get("after_alert", 0))))
            else:
                self.send_json({"error": "not found"}, 404)
        except pymysql.MySQLError as e:
            self.send_json({"error": f"database unavailable: {e}"}, 503)
        except ValueError:
            self.send_json({"error": "bad request"}, 400)

    def send_static(self, rel_path: str):
        path = (STATIC_DIR / rel_path).resolve()
        if not path.is_file() or STATIC_DIR not in path.parents or path.suffix not in STATIC_TYPES:
            self.send_json({"error": "not found"}, 404)
            return
        self.send_body(path.read_bytes(), STATIC_TYPES[path.suffix])

    def send_json(self, payload, status=200):
        self.send_body(json.dumps(payload, default=to_jsonable).encode(), "application/json", status)

    def send_body(self, body: bytes, content_type: str, status=200):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):  # keep the console quiet
        pass


def start_in_background() -> str:
    """Start the dashboard on a daemon thread (used by monitor_agent.py) and return its URL.

    If the port is already taken, assume the dashboard is already running and just return the URL.
    """
    url = f"http://{HOST}:{PORT}"
    try:
        server = ThreadingHTTPServer((HOST, PORT), Handler)
    except OSError:
        return url
    threading.Thread(target=server.serve_forever, name="dashboard", daemon=True).start()
    return url


def main():
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Dashboard running at http://{HOST}:{PORT}  (Ctrl+C to stop)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
