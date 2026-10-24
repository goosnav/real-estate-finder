"""SQLite transactions are the unit of durability for searches and checkpoints."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .config import AppError
from .models import now


class Store:
    def __init__(self, root: Path):
        self.root = root
        (root / "data").mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = root / "data" / "jobs.sqlite3"
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                INSERT OR IGNORE INTO metadata VALUES ('schema_version','1');
                CREATE TABLE IF NOT EXISTS searches (id TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS opportunities (
                    id TEXT PRIMARY KEY, search_id TEXT NOT NULL REFERENCES searches(id),
                    identity TEXT NOT NULL, data TEXT NOT NULL,
                    UNIQUE(search_id, identity));
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, search_id TEXT NOT NULL REFERENCES searches(id),
                    status TEXT NOT NULL, data TEXT NOT NULL);
                CREATE UNIQUE INDEX IF NOT EXISTS one_active_run_per_search
                    ON runs(search_id) WHERE status IN ('running','queued');
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
                    at TEXT NOT NULL, level TEXT NOT NULL, message TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS checkpoints (
                    run_id TEXT NOT NULL, stage TEXT NOT NULL, item TEXT NOT NULL,
                    PRIMARY KEY(run_id,stage,item));
                CREATE TABLE IF NOT EXISTS suppressions (
                    kind TEXT NOT NULL, value TEXT NOT NULL, reason TEXT NOT NULL,
                    created_at TEXT NOT NULL, PRIMARY KEY(kind,value));
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY, search_id TEXT NOT NULL REFERENCES searches(id),
                    name TEXT NOT NULL, text TEXT NOT NULL, sha256 TEXT NOT NULL,
                    created_at TEXT NOT NULL, UNIQUE(search_id,sha256));
            """)
            version = db.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()[0]
            if version != "1":
                raise AppError("DATA-VERSION", "This database needs a newer app version.")
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def get(self, table: str, ident: str) -> dict:
        if table not in {"searches", "opportunities", "runs"}:
            raise ValueError("Unknown collection")
        with self.connect() as db:
            row = db.execute(f"SELECT data FROM {table} WHERE id=?", (ident,)).fetchone()
        if row is None:
            raise AppError("NOT-FOUND", "That item no longer exists.", 404)
        return json.loads(row[0])

    def list(self, table: str, search_id: str | None = None) -> list[dict]:
        if table not in {"searches", "opportunities", "runs"}:
            raise ValueError("Unknown collection")
        query, args = f"SELECT data FROM {table}", ()
        if search_id is not None:
            query, args = query + " WHERE search_id=?", (search_id,)
        with self.connect() as db:
            rows = db.execute(query, args).fetchall()
        return [json.loads(row[0]) for row in rows]

    def save(self, table: str, data: dict):
        with self.connect() as db:
            self.save_in(db, table, data)

    @staticmethod
    def save_in(db, table: str, data: dict):
        raw = json.dumps(data, ensure_ascii=False)
        if table == "searches":
            db.execute("INSERT INTO searches VALUES (?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data", (data["id"], raw))
        elif table == "opportunities":
            db.execute("INSERT INTO opportunities VALUES (?,?,?,?) ON CONFLICT(id) DO UPDATE SET identity=excluded.identity,data=excluded.data", (data["id"], data["search_id"], data["identity"], raw))
        elif table == "runs":
            db.execute("INSERT INTO runs VALUES (?,?,?,?) ON CONFLICT(id) DO UPDATE SET status=excluded.status,data=excluded.data", (data["id"], data["search_id"], data["status"], raw))
        else:
            raise ValueError("Unknown collection")

    def mutate_job(self, run_id: str, update):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT data FROM runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                raise AppError("NOT-FOUND", "Job not found.", 404)
            job = json.loads(row[0])
            update(job)
            job["updated_at"] = now()
            self.save_in(db, "runs", job)
            return job

    def event(self, run_id: str, message: str, level: str = "info"):
        with self.connect() as db:
            db.execute("INSERT INTO events(run_id,at,level,message) VALUES (?,?,?,?)", (run_id, now(), level, message[:1200]))

    def events(self, run_id: str, limit: int = 200) -> list[dict]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM (SELECT * FROM events WHERE run_id=? ORDER BY id DESC LIMIT ?) ORDER BY id", (run_id, limit)).fetchall()
        return [dict(row) for row in rows]

    def checkpoint(self, run_id: str, stage: str, item: str):
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO checkpoints VALUES (?,?,?)", (run_id, stage, item))

    def done(self, run_id: str, stage: str, item: str) -> bool:
        with self.connect() as db:
            return db.execute("SELECT 1 FROM checkpoints WHERE run_id=? AND stage=? AND item=?", (run_id, stage, item)).fetchone() is not None

    def suppressions(self) -> list[dict]:
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM suppressions ORDER BY created_at DESC")]

    def documents(self, search_id: str, include_text: bool = False) -> list[dict]:
        with self.connect() as db:
            rows = [dict(row) for row in db.execute("SELECT * FROM documents WHERE search_id=? ORDER BY created_at,id", (search_id,))]
        if not include_text:
            for row in rows:
                row["characters"] = len(row.pop("text"))
        return rows
