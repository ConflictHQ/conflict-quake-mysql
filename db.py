"""Aurora MySQL Serverless v2, scaled to zero.

The twin of conflict-exo-pg on the other engine. Same point: min_acu = 0 with
auto-pause means compute stops between demos and only storage is billed, so
the first query afterwards pays a resume. This module measures that, because
the resume is the thing worth showing.
"""

from __future__ import annotations

import csv
import os
import time

import pymysql
from pymysql.cursors import DictCursor

# The mysql binding's env envelope (spec 05 §9.4).
HOST = os.environ.get("MYSQL_HOST", "")
PORT = int(os.environ.get("MYSQL_PORT", "3306"))
NAME = os.environ.get("MYSQL_DB", "mysql")
USER = os.environ.get("MYSQL_USER", "")
PASSWORD = os.environ.get("MYSQL_PASSWORD", "")

CONNECT_TIMEOUT = int(os.environ.get("DB_CONNECT_TIMEOUT", "45"))

_history: list[dict] = []


def configured() -> bool:
    return bool(HOST and USER)


def connect():
    """Connect, recording how long it took. No pool: a pooled connection would
    hide exactly the cold start this fixture exists to demonstrate."""
    started = time.time()
    conn = pymysql.connect(
        host=HOST, port=PORT, user=USER, password=PASSWORD, database=NAME,
        cursorclass=DictCursor, connect_timeout=CONNECT_TIMEOUT,
        read_timeout=CONNECT_TIMEOUT, write_timeout=CONNECT_TIMEOUT,
        ssl={"ssl": {}},
    )
    elapsed = round((time.time() - started) * 1000, 1)
    _history.append({
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "connect_ms": elapsed,
        # A warm connect to a running writer is tens of ms; seconds means the
        # cluster was paused and had to resume.
        "likely_resume": elapsed > 2000,
    })
    del _history[:-25]
    return conn


def history() -> list[dict]:
    return list(reversed(_history))


def q(sql: str, args=()) -> list[dict]:
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, args)
            return list(cur.fetchall())
    finally:
        conn.close()


SCHEMA = """
CREATE TABLE IF NOT EXISTS quakes (
    id VARCHAR(64) PRIMARY KEY,
    evt_time VARCHAR(32),
    latitude DOUBLE, longitude DOUBLE, depth DOUBLE, mag DOUBLE,
    mag_type VARCHAR(16), place VARCHAR(255), evt_type VARCHAR(32),
    INDEX idx_mag (mag), INDEX idx_time (evt_time)
)
"""

COLUMNS = ("id", "evt_time", "latitude", "longitude", "depth", "mag",
           "mag_type", "place", "evt_type")
CSV_FIELDS = ("id", "time", "latitude", "longitude", "depth", "mag",
              "magType", "place", "type")
NUMERIC = {"latitude", "longitude", "depth", "mag"}


def _num(value):
    value = (value or "").strip()
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def seed_if_empty() -> dict:
    """Load the vendored snapshot on first boot. Idempotent, so a pod cycling
    is a count query rather than a re-import."""
    started = time.time()
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(SCHEMA)
            conn.commit()
            cur.execute("SELECT count(*) AS n FROM quakes")
            existing = cur.fetchone()["n"]
            if existing:
                return {"seeded": False, "rows": existing,
                        "elapsed_s": round(time.time() - started, 2)}

            path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "data", "seed-quakes.csv")
            rows = []
            with open(path, newline="", encoding="utf-8") as fh:
                for row in csv.DictReader(fh):
                    rows.append(tuple(
                        _num(row.get(c)) if c in NUMERIC else (row.get(c) or "").strip()[:255]
                        for c in CSV_FIELDS
                    ))
            placeholders = ",".join(["%s"] * len(COLUMNS))
            # The feed revises events in place, so the same id can recur.
            cur.executemany(
                f"REPLACE INTO quakes ({','.join(COLUMNS)}) VALUES ({placeholders})",
                rows,
            )
            conn.commit()
            cur.execute("SELECT count(*) AS n FROM quakes")
            return {"seeded": True, "rows": cur.fetchone()["n"],
                    "elapsed_s": round(time.time() - started, 2)}
    finally:
        conn.close()
