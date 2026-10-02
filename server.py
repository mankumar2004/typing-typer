#!/usr/bin/env python3
import json
import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(ROOT, "data")
DB_FILE = os.path.join(DATA_DIR, "luciii.sqlite3")

os.makedirs(DATA_DIR, exist_ok=True)


def initialize_database():
    with sqlite3.connect(DB_FILE) as connection:
        connection.execute("""CREATE TABLE IF NOT EXISTS scores (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            wpm INTEGER NOT NULL,
            accuracy INTEGER NOT NULL,
            mode TEXT NOT NULL,
            at INTEGER NOT NULL
        )""")
        connection.execute("""CREATE TABLE IF NOT EXISTS streaks (
            name TEXT PRIMARY KEY,
            days INTEGER NOT NULL,
            last_completed TEXT NOT NULL
        )""")


def sanitize_name(value):
    if not isinstance(value, str):
        return "Operator"
    cleaned = value.strip().replace("\n", " ")
    cleaned = " ".join(cleaned.split())
    return cleaned[:8] or "Operator"


def get_board():
    with sqlite3.connect(DB_FILE) as connection:
        rows = connection.execute(
            "SELECT name, wpm, accuracy, mode, at FROM scores "
            "ORDER BY wpm DESC, accuracy DESC, at ASC LIMIT 10"
        ).fetchall()
    return [dict(zip(("name", "wpm", "accuracy", "mode", "at"), row)) for row in rows]


def get_streak(name):
    with sqlite3.connect(DB_FILE) as connection:
        row = connection.execute(
            "SELECT days, last_completed FROM streaks WHERE name = ?", (name,)
        ).fetchone()
    return {"days": row[0], "lastCompleted": row[1]} if row else {"days": 0, "lastCompleted": None}


def persist_score(payload):
    name = sanitize_name(payload.get("name"))
    wpm = int(payload.get("wpm", 0))
    accuracy = int(payload.get("accuracy", 100))
    if not 0 <= wpm <= 500 or not 0 <= accuracy <= 100:
        raise ValueError("Score values are out of range")
    today = datetime.now(timezone.utc).date()
    entry = {
        "name": name,
        "wpm": wpm,
        "accuracy": accuracy,
        "mode": str(payload.get("mode", "easy")),
        "at": int(payload.get("at", 0)) or int(time.time() * 1000),
    }
    with sqlite3.connect(DB_FILE) as connection:
        connection.execute(
            "INSERT INTO scores (name, wpm, accuracy, mode, at) VALUES (?, ?, ?, ?, ?)",
            (entry["name"], entry["wpm"], entry["accuracy"], entry["mode"], entry["at"]),
        )
        row = connection.execute(
            "SELECT days, last_completed FROM streaks WHERE name = ?", (name,)
        ).fetchone()
        last_completed = datetime.fromisoformat(row[1]).date() if row else None
        if last_completed == today:
            days = row[0]
        elif last_completed == today - timedelta(days=1):
            days = row[0] + 1
        else:
            days = 1
        connection.execute(
            "INSERT INTO streaks (name, days, last_completed) VALUES (?, ?, ?) "
            "ON CONFLICT(name) DO UPDATE SET days = excluded.days, last_completed = excluded.last_completed",
            (name, days, today.isoformat()),
        )
    return {"ok": True, "leaderboard": get_board(), "streak": {"days": days, "lastCompleted": today.isoformat()}}


class AppHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=ROOT, **kwargs)

    def log_message(self, format, *args):
        return

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/leaderboard":
            payload = {"leaderboard": get_board()}
            self.send_json(payload)
            return
        if path == "/api/streak":
            name = sanitize_name(parse_qs(urlparse(self.path).query).get("name", [""])[0])
            self.send_json({"streak": get_streak(name)})
            return
        super().do_GET()

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/score":
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length)
            try:
                body = json.loads(raw.decode("utf-8"))
            except Exception:
                self.send_json({"ok": False, "error": "Invalid JSON"}, 400)
                return
            try:
                result = persist_score(body)
            except (TypeError, ValueError):
                self.send_json({"ok": False, "error": "Invalid score"}, 400)
                return
            self.send_json(result)
            return
        self.send_error(404, "Not found")

    def send_json(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    initialize_database()
    port = int(os.environ.get("PORT", "8000"))
    server = ThreadingHTTPServer(("0.0.0.0", port), AppHandler)
    print(f"Serving at http://0.0.0.0:{port}")
    server.serve_forever()
