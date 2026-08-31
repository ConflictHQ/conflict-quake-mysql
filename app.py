"""conflict-quake-mysql — Aurora MySQL Serverless v2 at 0 ACU.

The MySQL twin of conflict-exo-pg. The fixture is about a database that costs
nothing while nobody is looking at it; the dashboard surfaces the cold start
that follows, rather than hiding it behind a pool.
"""

import json
import os
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import db

PORT = int(os.environ.get("PORT", "8080"))
STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
BOOT = time.time()
APP = "conflict-quake-mysql"
VERSION = os.environ.get("GIT_SHA", "dev")

MIME = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
        ".js": "application/javascript; charset=utf-8"}

_seed = {"state": "pending", "detail": None}


def seed_once():
    try:
        _seed["detail"] = db.seed_if_empty()
        _seed["state"] = "ready"
    except Exception as exc:
        _seed["state"] = "failed"
        _seed["detail"] = {"error": str(exc)}
    print(json.dumps({"app": APP, "msg": "seed complete", **_seed}), flush=True)


def summary():
    row = db.q(
        "SELECT count(*) AS events, max(mag) AS max_mag, "
        "round(avg(depth), 1) AS avg_depth, min(evt_time) AS first_time, "
        "max(evt_time) AS last_time FROM quakes"
    )[0]
    row["significant"] = db.q(
        "SELECT count(*) AS n FROM quakes WHERE mag >= 4.5"
    )[0]["n"]
    strongest = db.q(
        "SELECT place FROM quakes WHERE mag IS NOT NULL ORDER BY mag DESC LIMIT 1"
    )
    row["strongest_place"] = strongest[0]["place"] if strongest else None
    latest = db.history()
    row["last_connect_ms"] = latest[0]["connect_ms"] if latest else None
    row["engine"] = "Aurora MySQL Serverless v2"
    row["title"] = "Seismic activity on Aurora MySQL"
    row["unit"] = "events"
    return row


def by_bucket():
    return db.q(
        "SELECT FLOOR(mag * 2) / 2 AS bucket, count(*) AS n FROM quakes "
        "WHERE mag IS NOT NULL GROUP BY bucket ORDER BY bucket"
    )


def top_rows():
    return db.q(
        "SELECT place AS label, mag AS value, depth, evt_time FROM quakes "
        "WHERE mag IS NOT NULL ORDER BY mag DESC LIMIT 12"
    )


def connections():
    return {"history": db.history(), "seed": _seed}


def debug_payload():
    seen = sorted(k for k in os.environ
                  if not any(s in k.upper() for s in ("SECRET", "PASSWORD", "TOKEN", "KEY")))
    return {
        "app": APP, "version": VERSION, "kind": "deployment",
        "hostname": socket.gethostname(),
        "uptime_s": round(time.time() - BOOT, 1),
        "now": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "env_seen": seen,
        "bindings": {"mysql": "present" if db.configured() else "absent"},
        "seed": _seed,
        "connect_history": db.history()[:5],
    }


def selftest():
    checks = []
    if not db.configured():
        checks.append({"service": "mysql", "ok": False, "latency_ms": None,
                       "detail": None, "error": "MYSQL_HOST/USER unset -- no mysql bound"})
        return {"app": APP, "ok": False, "checks": checks}

    t0 = time.time()
    try:
        one = db.q("SELECT 1 AS one")[0]["one"]
        latency = round((time.time() - t0) * 1000, 2)
        checks.append({"service": "mysql", "ok": one == 1, "latency_ms": latency,
                       "detail": f"SELECT 1 against {db.HOST}"
                                 + (" (included an Aurora resume)" if latency > 2000 else ""),
                       "error": None})
    except Exception as exc:
        checks.append({"service": "mysql", "ok": False,
                       "latency_ms": round((time.time() - t0) * 1000, 2),
                       "detail": None, "error": str(exc)})

    t0 = time.time()
    try:
        n = db.q("SELECT count(*) AS n FROM quakes")[0]["n"]
        checks.append({"service": "dataset", "ok": n > 0,
                       "latency_ms": round((time.time() - t0) * 1000, 2),
                       "detail": f"{n} rows", "error": None})
    except Exception as exc:
        checks.append({"service": "dataset", "ok": False,
                       "latency_ms": round((time.time() - t0) * 1000, 2),
                       "detail": None, "error": str(exc)})

    return {"app": APP, "ok": all(c["ok"] is not False for c in checks), "checks": checks}


ROUTES = {
    "/api/summary": summary, "/api/buckets": by_bucket, "/api/top": top_rows,
    "/api/connections": connections, "/debug": debug_payload, "/selftest": selftest,
}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, code, body, ctype="application/json"):
        raw = body if isinstance(body, bytes) else json.dumps(body, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/health":
            return self._send(200, {"status": "ok", "seed": _seed["state"]})
        if path in ROUTES:
            try:
                return self._send(200, ROUTES[path]())
            except Exception as exc:
                return self._send(503, {"error": str(exc), "hint": "Aurora may be resuming from 0 ACU"})
        if path == "/":
            path = "/index.html"
        target = os.path.normpath(os.path.join(STATIC, path.lstrip("/")))
        if target.startswith(STATIC) and os.path.isfile(target):
            with open(target, "rb") as fh:
                return self._send(200, fh.read(),
                                  MIME.get(os.path.splitext(target)[1], "application/octet-stream"))
        return self._send(404, {"error": "not found", "path": path})

    def log_message(self, fmt, *args):
        print(json.dumps({"app": APP, "msg": fmt % args,
                          "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}), flush=True)


if __name__ == "__main__":
    print(json.dumps({
        "app": APP, "version": VERSION, "port": PORT,
        "bindings": {"mysql": "present" if db.configured() else "absent"},
        "msg": "listening",
    }), flush=True)
    if db.configured():
        threading.Thread(target=seed_once, daemon=True).start()
    else:
        _seed["state"] = "skipped"
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
