import asyncio
import logging
import time
import struct
from typing import Optional, List, Dict, Any, Callable
from umdt.transports.serial_async import SerialTransport
from umdt.transports.passive import PassiveTransport
from umdt.database.logging import DBLogger

# Try to import CRC utils
try:
    from pymodbus.utilities import computeCRC
except ImportError:
    # Fallback CRC16 implementation (Polynomial 0xA001)
    def computeCRC(data: bytes) -> int:
        crc = 0xFFFF
        for pos in data:
            crc ^= pos
            for i in range(8):
                if (crc & 1) != 0:
                    crc >>= 1
                    crc ^= 0xA001
                else:
                    crc >>= 1
        return crc

logger = logging.getLogger(__name__)

class SlidingWindowDecoder:
    """
    Heuristic decoder for Modbus RTU traffic.
    Reassembles frames from a continuous byte stream by sliding a window
    and checking CRCs against potential frame structures.
    """
    def __init__(self):
        self.buffer = bytearray()

    def ingest(self, data: bytes):
        self.buffer.extend(data)

    def parse(self) -> List[Dict[str, Any]]:
        """
        Parse available frames from the buffer.
        Returns list of dicts with 'raw', 'timestamp', 'valid_crc'.
        """
        frames = []
        while len(self.buffer) >= 4: # Minimum possible frame size (e.g. exception) is 5, but let's be safe
            # 1. Check Slave ID (1-247, 0 is broadcast)
            slave_id = self.buffer[0]
            if slave_id > 247: # invalid ID
                self.buffer.pop(0)
                continue

            # 2. Check Function Code
            fc = self.buffer[1]
            is_exception = False
            if fc > 0x80:
                is_exception = True
                clean_fc = fc & 0x7F
            else:
                clean_fc = fc

            # 3. Estimate potential lengths
            potential_lengths = []
            
            # A. Exception Frame: 5 bytes (ID, FC+0x80, Err, CRClo, CRChi)
            if is_exception:
                potential_lengths.append(5)
            
            # B. Fixed Length Frames (Requests/Responses for some FCs)
            # FC 01, 02, 03, 04, 05, 06 Request: 8 bytes
            # FC 05, 06 Response: 8 bytes
            # FC 15, 16 Response: 8 bytes
            if clean_fc in (1, 2, 3, 4, 5, 6, 15, 16):
                potential_lengths.append(8)

            # C. Variable Length Frames
            # FC 01, 02, 03, 04 Response: ID, FC, ByteCount, Data..., CRC. Length = 3 + ByteCount + 2
            # ByteCount is at index 2
            if clean_fc in (1, 2, 3, 4) and len(self.buffer) >= 3:
                byte_count = self.buffer[2]
                # Sanity check byte count (max 255, usually <= 250)
                if 0 < byte_count <= 255:
                    length = 3 + byte_count + 2
                    potential_lengths.append(length)
            
            # FC 15, 16 Request: ID, FC, AddrHi, AddrLo, QtyHi, QtyLo, ByteCount, Data..., CRC
            # ByteCount is at index 6
            if clean_fc in (15, 16) and len(self.buffer) >= 7:
                byte_count = self.buffer[6]
                if 0 < byte_count <= 255:
                    length = 7 + byte_count + 2
                    potential_lengths.append(length)

            # Sort lengths to check shortest first? Or check all?
            # If multiple match CRC, it's ambiguous, but usually CRC is strong enough.
            match_found = False
            
            # Filter lengths that exceed current buffer (we can't check them yet)
            # But if we have a potential length that IS bigger than buffer, we should WAIT, not discard.
            # Unless we determine the start is invalid.
            # If we have valid candidates that fit, check them.
            # If we have candidates that don't fit, we wait.
            
            candidates_to_check = [l for l in potential_lengths if l <= len(self.buffer)]
            candidates_waiting = [l for l in potential_lengths if l > len(self.buffer)]

            for length in sorted(candidates_to_check):
                candidate_frame = self.buffer[:length]
                
                # Check CRC
                # Modbus CRC is LSB first in the packet
                recv_crc_bytes = candidate_frame[-2:]
                recv_crc = int.from_bytes(recv_crc_bytes, byteorder='little')
                
                calc_crc = computeCRC(candidate_frame[:-2])
                
                if calc_crc == recv_crc:
                    # Found a valid frame!
                    frames.append({
                        "raw": bytes(candidate_frame),
                        "timestamp": time.time(),
                        "valid_crc": True
                    })
                    # Remove from buffer
                    del self.buffer[:length]
                    match_found = True
                    break
            
            if match_found:
                continue

            # If no match found among available bytes:
            # If we have potential lengths that are waiting for more data, we stop processing and wait.
            if candidates_waiting:
                # But wait, what if the start byte is garbage? 
                # If we assume it's a valid start of a long packet, we wait.
                # But if it's garbage, we lock up until buffer fills.
                # The architecture mentions "Sliding Window". 
                # If CRC fails for ALL candidates, we slide.
                # But we can't check CRC for waiting candidates.
                # Heuristic: If the header looks plausible (e.g. valid ID, valid FC), wait a bit.
                # If buffer gets huge (>256 bytes) and still no match, drop byte.
                if len(self.buffer) > 260: # slightly more than max RTU frame
                    self.buffer.pop(0)
                else:
                    break # Wait for more data
            else:
                # No potential lengths (unknown FC) OR all checked candidates failed CRC
                # Advance window
                self.buffer.pop(0)
        
        return frames

class Sniffer:
    def __init__(self, port: str, baudrate: int = 9600, db_path: Optional[str] = None, on_frame: Optional[Callable[[Dict[str, Any]], None]] = None):
        self.port = port
        self.baudrate = baudrate
        # Initialize Transport
        self.serial_transport = SerialTransport(port=port, baudrate=baudrate)
        self.transport = PassiveTransport(self.serial_transport)
        
        # Initialize Logger
        self.logger = DBLogger(db_path=db_path)
        
        # Initialize Decoder
        self.decoder = SlidingWindowDecoder()
        
        self.on_frame = on_frame
        self.running = False
        self._task: Optional[asyncio.Task] = None

    async def start(self):
        await self.logger.start()
        await self.transport.connect()
        self.running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info(f"Sniffer started on {self.port} @ {self.baudrate}")

    async def stop(self):
        self.running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        await self.transport.disconnect()
        await self.logger.stop()
        logger.info("Sniffer stopped")

    async def _run_loop(self):
        while self.running:
            try:
                # Read from transport (chunks)
                data = await self.transport.receive()
                if not data:
                    continue
                
                # Ingest into decoder
                self.decoder.ingest(data)
                
                # Parse frames
                frames = self.decoder.parse()
                
                # Log frames
                for frame in frames:
                    # Log to DB
                    await self.logger.enqueue({
                        "timestamp": frame["timestamp"],
                        "direction": "RX", # Sniffer sees everything as RX technically, or we can try to infer
                        "raw_bytes": frame["raw"],
                        "parsed_json": None # TODO: Add deep decoding later
                    })
                    
                    if self.on_frame:
                        try:
                            self.on_frame(frame)
                        except Exception:
                            pass
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                if not self.running:
                    break
                logger.error(f"Sniffer loop error: {e}")
                # Prevent tight loop on error
                try:
                    await asyncio.sleep(0.1)
                except (RuntimeError, asyncio.CancelledError):
                    break
