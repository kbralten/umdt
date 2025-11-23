import asyncio
import sqlite3
import os
import time
from typing import Any, Dict, Optional


DEFAULT_DB = "umdt_traffic.db"


class DBLogger:
    """Simple async-aware SQLite logger with WAL and pruning support.

    Usage:
      logger = DBLogger(path="/path/to/db")
      await logger.start()
      await logger.enqueue({"timestamp":..., "direction":"RX", "raw": b"..", "parsed": "..."})
      await logger.stop()
    """

    def __init__(self, db_path: Optional[str] = None, loop: Optional[asyncio.AbstractEventLoop] = None, prune_limit_bytes: int = 10 * 1024 * 1024):
        self.db_path = db_path or DEFAULT_DB
        self.loop = loop
        self.queue: asyncio.Queue = asyncio.Queue()
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()
        self.prune_limit = prune_limit_bytes
        # Ensure parent dir exists when path contains dirs
        parent = os.path.dirname(self.db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)

    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        return conn

    def _init_schema(self, conn: sqlite3.Connection):
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS traffic_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                direction TEXT NOT NULL,
                raw_bytes BLOB NOT NULL,
                parsed_json TEXT
            );
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_traffic_timestamp ON traffic_log(timestamp);")

    async def start(self):
        # run init synchronously in thread to avoid blocking loop
        def init():
            conn = self._connect()
            self._init_schema(conn)
            conn.close()
        loop = self.loop or asyncio.get_running_loop()
        self.loop = loop
        await loop.run_in_executor(None, init)
        self._stop.clear()
        # schedule worker on the running loop
        self._task = asyncio.create_task(self._worker())

    async def stop(self):
        self._stop.set()
        if self._task:
            await self._task

    async def enqueue(self, packet: Dict[str, Any]):
        await self.queue.put(packet)

    async def _prune_if_needed(self, conn: sqlite3.Connection):
        try:
            size = os.path.getsize(self.db_path)
        except OSError:
            return
        if size <= self.prune_limit:
            return
        # Simple pruning: delete oldest rows until under limit (batch)
        # Compute how many rows to delete: delete 10% at a time
        cur = conn.execute("SELECT COUNT(*) FROM traffic_log")
        total = cur.fetchone()[0]
        if total == 0:
            return
        delete_count = max(1, total // 10)
        conn.execute("DELETE FROM traffic_log WHERE id IN (SELECT id FROM traffic_log ORDER BY timestamp ASC LIMIT ?)", (delete_count,))
        conn.execute("VACUUM;")

    async def _worker(self):
        # Single-threaded DB access in worker; use sqlite3 in this thread
        conn = self._connect()
        try:
            while not self._stop.is_set() or not self.queue.empty():
                batch = []
                try:
                    # gather up to 100 items or 0.5s
                    item = await asyncio.wait_for(self.queue.get(), timeout=0.5)
                    batch.append(item)
                    while len(batch) < 100:
                        try:
                            item = self.queue.get_nowait()
                            batch.append(item)
                        except asyncio.QueueEmpty:
                            break
                except asyncio.TimeoutError:
                    pass

                if not batch:
                    continue

                cur = conn.cursor()
                cur.execute("BEGIN IMMEDIATE;")
                try:
                    for pkt in batch:
                        ts = float(pkt.get("timestamp", time.time()))
                        direction = str(pkt.get("direction", "RX"))
                        raw = pkt.get("raw") or pkt.get("raw_bytes") or b""
                        if isinstance(raw, str):
                            raw = raw.encode("utf-8")
                        parsed = pkt.get("parsed")
                        cur.execute(
                            "INSERT INTO traffic_log(timestamp, direction, raw_bytes, parsed_json) VALUES (?, ?, ?, ?)",
                            (ts, direction, sqlite3.Binary(raw), parsed),
                        )
                    conn.commit()
                except Exception:
                    conn.rollback()
                finally:
                    cur.close()

                # check pruning occasionally
                if time.time() % 1 < 0.5:
                    await self._prune_if_needed(conn)
        finally:
            conn.close()
