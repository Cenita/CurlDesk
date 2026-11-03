from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import RequestData, ResponseData


def data_directory() -> Path:
    base = os.environ.get("APPDATA")
    if base:
        root = Path(base)
    else:
        root = Path.home() / "AppData" / "Roaming"
    target = root / "CurlDesk"
    target.mkdir(parents=True, exist_ok=True)
    return target


class Storage:
    def __init__(self, database: Path | None = None) -> None:
        self.database = database or data_directory() / "curldesk.sqlite3"
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=15)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS collections (
                    name TEXT PRIMARY KEY,
                    request_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    status_code INTEGER NOT NULL,
                    elapsed_ms REAL NOT NULL,
                    response_headers TEXT NOT NULL,
                    response_body BLOB NOT NULL,
                    final_url TEXT NOT NULL,
                    error TEXT NOT NULL
                );
                """
            )

    def save_collection(self, name: str, request: RequestData) -> None:
        timestamp = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO collections(name, request_json, updated_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(name) DO UPDATE SET
                       request_json=excluded.request_json,
                       updated_at=excluded.updated_at""",
                (name, json.dumps(request.to_dict(), ensure_ascii=False), timestamp),
            )

    def list_collections(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT name, updated_at FROM collections ORDER BY name COLLATE NOCASE"
            ).fetchall()
        return [dict(row) for row in rows]

    def get_collection(self, name: str) -> RequestData | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT request_json FROM collections WHERE name = ?", (name,)
            ).fetchone()
        return RequestData.from_dict(json.loads(row["request_json"])) if row else None

    def delete_collection(self, name: str) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM collections WHERE name = ?", (name,))

    def add_history(self, request: RequestData, response: ResponseData) -> int:
        timestamp = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            cursor = connection.execute(
                """INSERT INTO history(
                       created_at, request_json, status_code, elapsed_ms,
                       response_headers, response_body, final_url, error
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    timestamp,
                    json.dumps(request.to_dict(), ensure_ascii=False),
                    response.status_code,
                    response.elapsed_ms,
                    json.dumps(response.headers, ensure_ascii=False),
                    sqlite3.Binary(response.body),
                    response.final_url,
                    response.error,
                ),
            )
            return int(cursor.lastrowid)

    def list_history(self, limit: int = 500) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT id, created_at, request_json, status_code, elapsed_ms,
                          final_url, error
                   FROM history ORDER BY id DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_history_request(self, history_id: int) -> RequestData | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT request_json FROM history WHERE id = ?", (history_id,)
            ).fetchone()
        return RequestData.from_dict(json.loads(row["request_json"])) if row else None

    def get_history_response(self, history_id: int) -> ResponseData | None:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT status_code, elapsed_ms, response_headers, response_body,
                          final_url, error FROM history WHERE id = ?""",
                (history_id,),
            ).fetchone()
        if not row:
            return None
        headers = json.loads(row["response_headers"])
        return ResponseData(
            status_code=row["status_code"],
            elapsed_ms=row["elapsed_ms"],
            headers=[tuple(item) for item in headers],
            body=bytes(row["response_body"]),
            final_url=row["final_url"],
            error=row["error"],
        )

    def delete_history(self, history_id: int) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM history WHERE id = ?", (history_id,))

    def clear_history(self) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM history")
