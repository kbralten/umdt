from typing import Dict, Any, List, Optional
from dataclasses import dataclass
import struct
import logging

logger = logging.getLogger(__name__)

@dataclass
class StateUpdate:
    slave_id: int
    data_type: str # 'Coil', 'Discrete Input', 'Holding Register', 'Input Register'
    address: int
    value: Any
    timestamp: float

class TrafficAnalyzer:
    """
    Analyzes raw Modbus frames to maintain a shadow state of device registers.
    Matches Read Requests with Responses to infer register values.
    Parses Write Requests to infer register updates.
    """
    def __init__(self):
        # Pending read requests: slave_id -> {fc, addr, count, ts}
        self.pending_reads: Dict[int, Dict[str, Any]] = {}

    def process_packet(self, packet: Dict[str, Any]) -> List[StateUpdate]:
        """Process a raw packet dictionary and return a list of state updates."""
        updates = []
        raw = packet.get('raw')
        ts = packet.get('timestamp', 0.0)
        valid_crc = packet.get('valid_crc', False)

        if not raw:
            return []
            
        if not valid_crc:
            # logger.debug("Ignoring packet with invalid CRC")
            return []
            
        if len(raw) < 4:
            return []

        # Raw frame: [SlaveID, FC, ... PDU ..., CRC_Lo, CRC_Hi]
        slave_id = raw[0]
        fc = raw[1]
        
        length = len(raw)
        # logger.debug(f"Analyzer: processing ID={slave_id} FC={fc} Len={length}")

        if fc in (1, 2, 3, 4):
            # READ FUNCTIONS
            # Request: [ID, FC, AddrHi, AddrLo, CntHi, CntLo, CRC...] (8 bytes total)
            if length == 8:
                # Treat as Request
                addr = struct.unpack('>H', raw[2:4])[0]
                count = struct.unpack('>H', raw[4:6])[0]
                # logger.debug(f"Analyzer: stored pending read ID={slave_id} FC={fc} Addr={addr} Count={count}")
                self.pending_reads[slave_id] = {
                    'fc': fc, 'addr': addr, 'count': count, 'ts': ts, 'slave_id': slave_id
                }
            else:
                # Treat as Response
                # Response: [ID, FC, ByteCount, Data..., CRC...]
                req = self.pending_reads.get(slave_id)
                
                if req:
                    # logger.debug(f"Analyzer: found pending req for ID={slave_id}: {req}")
                    if req['fc'] == fc:
                        byte_count = raw[2]
                        data_len = length - 5
                        
                        if data_len == byte_count:
                            data_bytes = raw[3 : 3 + data_len]
                            new_updates = self._decode_read_response(req, data_bytes, ts)
                            # logger.debug(f"Analyzer: decoded {len(new_updates)} updates")
                            updates.extend(new_updates)
                        else:
                            pass
                            # logger.debug(f"Analyzer: length mismatch data_len={data_len} byte_count={byte_count}")
                    else:
                        pass
                        # logger.debug(f"Analyzer: FC mismatch req={req['fc']} res={fc}")
                else:
                    pass
                    # logger.debug(f"Analyzer: no pending read for ID={slave_id}")
                
                # Clear pending request (assuming half-duplex)
                if slave_id in self.pending_reads:
                    del self.pending_reads[slave_id]

        elif fc == 5:
            # WRITE SINGLE COIL
            # Req/Res: [ID, FC, AddrHi, AddrLo, ValHi, ValLo, CRC...] (8 bytes)
            if length == 8:
                addr = struct.unpack('>H', raw[2:4])[0]
                val_raw = struct.unpack('>H', raw[4:6])[0]
                # 0xFF00 = ON, 0x0000 = OFF
                val = (val_raw == 0xFF00)
                updates.append(StateUpdate(slave_id, 'Coil', addr, val, ts))

        elif fc == 6:
            # WRITE SINGLE REGISTER
            # Req/Res: [ID, FC, AddrHi, AddrLo, ValHi, ValLo, CRC...] (8 bytes)
            if length == 8:
                addr = struct.unpack('>H', raw[2:4])[0]
                val = struct.unpack('>H', raw[4:6])[0]
                updates.append(StateUpdate(slave_id, 'Holding Register', addr, val, ts))

        elif fc == 15:
            # WRITE MULTIPLE COILS
            # Req: [ID, FC, AddrHi, AddrLo, CntHi, CntLo, Bytes, Data..., CRC]
            # Res: [ID, FC, AddrHi, AddrLo, CntHi, CntLo, CRC] (8 bytes)
            if length > 8:
                # Request contains the data
                addr = struct.unpack('>H', raw[2:4])[0]
                count = struct.unpack('>H', raw[4:6])[0]
                byte_count = raw[6]
                data_bytes = raw[7 : 7 + byte_count]
                
                # Parse bits
                current_addr = addr
                for b in data_bytes:
                    for bit_idx in range(8):
                        if (current_addr - addr) < count:
                            val = bool((b >> bit_idx) & 1)
                            updates.append(StateUpdate(slave_id, 'Coil', current_addr, val, ts))
                            current_addr += 1

        elif fc == 16:
            # WRITE MULTIPLE REGISTERS
            # Req: [ID, FC, AddrHi, AddrLo, CntHi, CntLo, Bytes, Data..., CRC]
            # Res: [ID, FC, AddrHi, AddrLo, CntHi, CntLo, CRC] (8 bytes)
            if length > 8:
                # Request contains the data
                addr = struct.unpack('>H', raw[2:4])[0]
                count = struct.unpack('>H', raw[4:6])[0]
                byte_count = raw[6]
                data_bytes = raw[7 : 7 + byte_count]
                
                # Parse registers
                for i in range(count):
                    offset = i * 2
                    if offset + 2 <= len(data_bytes):
                        val = struct.unpack('>H', data_bytes[offset : offset+2])[0]
                        updates.append(StateUpdate(slave_id, 'Holding Register', addr + i, val, ts))

        return updates

    def _decode_read_response(self, req: Dict, data: bytes, ts: float) -> List[StateUpdate]:
        updates = []
        fc = req['fc']
        start_addr = req['addr']
        count = req['count']
        
        if fc in (1, 2): # Bits
            type_name = 'Coil' if fc == 1 else 'Discrete Input'
            current_addr = start_addr
            for b in data:
                for bit_idx in range(8):
                    if (current_addr - start_addr) < count:
                        val = bool((b >> bit_idx) & 1)
                        updates.append(StateUpdate(req['slave_id'], type_name, current_addr, val, ts))
                        current_addr += 1
                        
        elif fc in (3, 4): # Registers
            type_name = 'Holding Register' if fc == 3 else 'Input Register'
            for i in range(count):
                offset = i * 2
                if offset + 2 <= len(data):
                    val = struct.unpack('>H', data[offset : offset+2])[0]
                    updates.append(StateUpdate(req['slave_id'], type_name, start_addr + i, val, ts))
                    
        return updates
