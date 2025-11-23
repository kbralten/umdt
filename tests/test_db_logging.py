import asyncio
import os
import tempfile
from umdt.database.logging import DBLogger


def test_db_logger_basic(tmp_path):
    dbfile = tmp_path / "test_traffic.db"
    logger = DBLogger(db_path=str(dbfile), prune_limit_bytes=1024 * 1024)

    async def run():
        await logger.start()
        await logger.enqueue({"timestamp": 1.23, "direction": "RX", "raw": b"\x01\x02", "parsed": "{}"})
        await logger.enqueue({"timestamp": 2.34, "direction": "TX", "raw": b"\x03\x04", "parsed": None})
        # allow worker to flush
        await asyncio.sleep(0.2)
        await logger.stop()

    asyncio.run(run())

    assert dbfile.exists()
    # basic size check
    assert dbfile.stat().st_size > 0
