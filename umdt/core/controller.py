import asyncio
import time
from typing import List, Callable, Dict, Optional
from umdt.transports.base import TransportInterface
from umdt.transports.manager import ConnectionManager
from umdt.database.logging import DBLogger


class CoreController:
    def __init__(self, transport: Optional[TransportInterface] = None, uri: Optional[str] = None, *, db_path: Optional[str] = None, logger: Optional[DBLogger] = None):
        self.transport = transport
        self.uri = uri
        self.logs: List[Dict] = []
        self.observers: List[Callable[[Dict], None]] = []
        self.running = False
        self._rx_task = None
        # Resource locking for scanner vs user-initiated commands
        self.transport_lock: asyncio.Lock = asyncio.Lock()
        self._scanner_task = None
        self._scanner_resume: asyncio.Event = asyncio.Event()
        self._scanner_resume.set()
        self._scanner_running = False
        self._use_manager = False
        self._manager = None
        # DB logger (optional)
        self._logger: Optional[DBLogger] = logger
        self._db_path = db_path

        if transport is None and uri is not None:
            self._use_manager = True
            self._manager = ConnectionManager.instance()
            # subscribe to manager status updates
            self._manager.add_status_callback(self._on_status)

        # lazily create DBLogger if a path was provided
        if self._logger is None and self._db_path:
            self._logger = DBLogger(db_path=self._db_path)

    def add_observer(self, callback: Callable[[Dict], None]):
        self.observers.append(callback)

    def _log(self, direction: str, data: bytes):
        entry = {"direction": direction, "data": data.hex().upper()}
        self.logs.append(entry)
        for observer in self.observers:
            try:
                observer(entry)
            except Exception:
                pass
        # enqueue into DBLogger if available
        if self._logger:
            pkt = {"timestamp": time.time(), "direction": direction, "raw": data, "parsed": None}
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self._logger.enqueue(pkt))
            except RuntimeError:
                # no running loop in this thread; try submitting to logger's loop if present
                if getattr(self._logger, "loop", None):
                    try:
                        asyncio.run_coroutine_threadsafe(self._logger.enqueue(pkt), self._logger.loop)
                    except Exception:
                        pass

    def _on_status(self, msg: str):
        # status messages from ConnectionManager
        entry = {"direction": "STATUS", "data": msg}
        self.logs.append(entry)
        for observer in self.observers:
            try:
                observer(entry)
            except Exception:
                pass
        if self._logger:
            pkt = {"timestamp": time.time(), "direction": "STATUS", "raw": msg.encode("utf-8"), "parsed": None}
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self._logger.enqueue(pkt))
            except RuntimeError:
                if getattr(self._logger, "loop", None):
                    try:
                        asyncio.run_coroutine_threadsafe(self._logger.enqueue(pkt), self._logger.loop)
                    except Exception:
                        pass

    async def start(self):
        self.running = True
        if self._use_manager and self._manager and self.uri:
            await self._manager.start(self.uri)
            # start DB logger before rx loop so incoming packets are captured
            if self._logger:
                await self._logger.start()
            self._rx_task = asyncio.create_task(self._rx_loop())
        else:
            await self.transport.connect()
            if self._logger:
                await self._logger.start()
            self._rx_task = asyncio.create_task(self._rx_loop())

    # Scanner management
    def start_scanner(self, interval: float = 1.0):
        """Start the background scanner task which acquires the transport lock
        for short batches to allow user-initiated commands to take priority.
        """
        if self._scanner_task and not self._scanner_task.done():
            return
        self._scanner_running = True
        self._scanner_task = asyncio.create_task(self._scanner_loop(interval))

    async def stop_scanner(self):
        self._scanner_running = False
        if self._scanner_task:
            self._scanner_task.cancel()
            try:
                await self._scanner_task
            except asyncio.CancelledError:
                pass
        self._scanner_resume.set()

    async def _scanner_loop(self, interval: float):
        while self._scanner_running:
            await self._scanner_resume.wait()
            try:
                async with self.transport_lock:
                    # Placeholder: single scan iteration; keep short
                    await asyncio.sleep(0)  # real scan work goes here
                # yield between batches
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                break
            except Exception:
                await asyncio.sleep(0.1)

    class _WriteAccess:
        def __init__(self, controller: "CoreController"):
            self._c = controller

        async def __aenter__(self):
            # pause scanner before acquiring lock
            try:
                self._c._scanner_resume.clear()
            except Exception:
                pass
            await self._c.transport_lock.acquire()
            return self

        async def __aexit__(self, exc_type, exc, tb):
            try:
                self._c.transport_lock.release()
            except Exception:
                pass
            try:
                self._c._scanner_resume.set()
            except Exception:
                pass

    def request_write_access(self):
        """Return an async context manager to acquire exclusive write access.

        Usage:
            async with controller.request_write_access():
                await controller.send_data(...)
        """
        return CoreController._WriteAccess(self)

    async def stop(self):
        self.running = False
        if self._rx_task:
            self._rx_task.cancel()
            try:
                await self._rx_task
            except asyncio.CancelledError:
                pass

        if self._use_manager and self._manager:
            await self._manager.stop()
        else:
            await self.transport.disconnect()
        if self._logger:
            try:
                await self._logger.stop()
            except Exception:
                pass

    async def send_data(self, data: bytes):
        self._log("TX", data)
        if self._use_manager and self._manager:
            await self._manager.send(data)
        else:
            await self.transport.send(data)

    async def _rx_loop(self):
        while self.running:
            try:
                if self._use_manager and self._manager:
                    data = await self._manager.receive()
                else:
                    data = await self.transport.receive()
                self._log("RX", data)
            except asyncio.CancelledError:
                break
            except Exception:
                await asyncio.sleep(0.1)
