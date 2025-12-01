-- umdt_modbus_wrapper.lua
-- UMDT: strip 4-byte metadata header and dispatch Modbus TCP or convert RTU->MBAP
-- Hardened: bit32/bit compatibility, safe fallbacks, startup logging for debugging.

-- debugging prints removed

local p_umdt = Proto("umdt_modbus","UMDT Modbus wrapper")

local f_dir = ProtoField.uint8("umdt.direction","Direction",base.DEC)
local f_proto = ProtoField.uint8("umdt.proto","ProtoHint",base.DEC)
local f_crc = ProtoField.bool("umdt.rtu_crc","RTU CRC Present")

p_umdt.fields = { f_dir, f_proto, f_crc }

local DIR_NAMES = { [0]="UNKNOWN", [1]="INBOUND", [2]="OUTBOUND" }
local PROTO_NAMES = { [0]="UNKNOWN", [1]="MODBUS_RTU", [2]="MODBUS_TCP" }

local FUNC_NAMES = {
  [1] = "Read Coils",
  [2] = "Read Discrete Inputs",
  [3] = "Read Holding Registers",
  [4] = "Read Input Registers",
  [5] = "Write Single Coil",
  [6] = "Write Single Register",
  [15] = "Write Multiple Coils",
  [16] = "Write Multiple Registers",
  [23] = "Read/Write Multiple Registers",
}

-- bit compatibility: try bit32 then bit (LuaJIT), else nil
local bitm = nil
do
  local ok
  ok, bitm = pcall(function() return bit32 end)
  if not ok or not bitm then
    ok, bitm = pcall(function() return require("bit") end)
  end
end
  if not bitm then
    -- CRC detection disabled (no bit library)
  end

-- compute CRC only if bitm available
local function compute_crc(tvb, len)
  if not bitm then
    return nil
  end
  local crc = 0xFFFF
  for i = 0, len - 1 do
    local b = tvb(i,1):uint()
    crc = bitm.bxor(crc, b)
    for _ = 1, 8 do
      if bitm.band(crc, 0x0001) ~= 0 then
        crc = bitm.rshift(crc, 1)
        crc = bitm.bxor(crc, 0xA001)
      else
        crc = bitm.rshift(crc, 1)
      end
    end
  end
  return bitm.band(crc, 0xFFFF)
end

-- helper to build a ByteArray from a Lua table of byte ints
local function byte_table_to_ba(tbl)
  -- Build hex string (ByteArray.new expects hex, not raw bytes)
  local hex = ""
  for i = 1, #tbl do
    hex = hex .. string.format("%02x", tbl[i])
  end
  return ByteArray.new(hex)
end

function p_umdt.dissector(tvbuf, pktinfo, root)
  local tvlen = tvbuf:len()
  if tvlen < 4 then
    Dissector.get("data"):call(tvbuf, pktinfo, root)
    return
  end

  local dir = tvbuf(0,1):uint()
  local proto = tvbuf(1,1):uint()
  local payload = tvbuf:range(4)
  local payload_len = payload:len()

  pktinfo.cols.protocol = "UMDT-MODBUS"

  local subtree = root:add(p_umdt, tvbuf())
  subtree:add(f_dir, tvbuf(0,1)):append_text(" (" .. (DIR_NAMES[dir] or "UNK") .. ")")
  subtree:add(f_proto, tvbuf(1,1)):append_text(" (" .. (PROTO_NAMES[proto] or "UNK") .. ")")
  
  -- Populate source/destination columns for easier reading
  if dir == 1 then
    pktinfo.cols.src = "client"
    pktinfo.cols.dst = "server"
  elseif dir == 2 then
    pktinfo.cols.src = "server"
    pktinfo.cols.dst = "client"
  else -- dir == 0 (UNKNOWN) or any other unexpected value
    pktinfo.cols.src = "unknown"
    pktinfo.cols.dst = "unknown"
  end

  local data_dissector = Dissector.get("data")
  local mbap_dissector = Dissector.get("mbap")

  -- If hint says MODBUS_TCP (2) -> payload should already be MBAP
  if proto == 2 then
    local tvb_payload = payload:tvb()
    -- try to populate info column from MBAP: unit at offset 6, func at offset 7
    if payload_len >= 8 then
      local unit = payload(6,1):uint()
      local func = payload(7,1):uint()
      pktinfo.cols.info = string.format("FC 0x%02X %s unit=%d", func, FUNC_NAMES[func] or "Unknown", unit)
    end
    if mbap_dissector then
      mbap_dissector:call(tvb_payload, pktinfo, root)
    else
      data_dissector:call(tvb_payload, pktinfo, subtree)
    end
    return
  end

  -- If RTU/unknown -> attempt RTU->MBAP conversion
  if payload_len < 2 then
      data_dissector:call(payload:tvb(), pktinfo, root)
    return
  end

  local crc_present = false
  local pdu_end = payload_len
  if payload_len >= 3 and compute_crc then
    local potential_crc_low = payload(payload_len - 2, 1):uint()
    local potential_crc_high = payload(payload_len - 1, 1):uint()
    local stored_crc = potential_crc_low + potential_crc_high * 256
    local ok, computed_crc = pcall(function() return compute_crc(payload, payload_len - 2) end)
    if ok and computed_crc and computed_crc == stored_crc then
      crc_present = true
      pdu_end = payload_len - 2
    end
  end

  subtree:add(f_crc, crc_present)

  local unit = payload(0,1):uint()
  local pdu_len = pdu_end - 1
  if pdu_len < 1 then
      data_dissector:call(payload:tvb(), pktinfo, root)
    return
  end

  -- populate info column from RTU PDU (unit + function at offset 1)
  if pdu_len >= 1 then
    local func = payload(1,1):uint()
    pktinfo.cols.info = string.format("FC 0x%02X %s unit=%d", func, FUNC_NAMES[func] or "Unknown", unit)
  end

  local mbap_len = 1 + pdu_len
  local len_hi = math.floor(mbap_len / 256) % 256
  local len_lo = mbap_len % 256

  local header_tbl = { 0, 0, 0, 0, len_hi, len_lo, unit }
  for i = 1, pdu_end - 1 do
    header_tbl[#header_tbl + 1] = payload(i,1):uint()
  end

  local ba = byte_table_to_ba(header_tbl)
  local mbap_tvb = nil
  local ok_tvb, err_tvb = pcall(function()
    mbap_tvb = ByteArray.tvb(ba, "MBAP")
  end)

  if not ok_tvb or not mbap_tvb then
    -- fallback: try building tvb from string
    local hex = ""
    for i = 1, #header_tbl do hex = hex .. string.format("%02x", header_tbl[i]) end
    mbap_tvb = ByteArray.tvb(ByteArray.new(hex), "MBAP")
  end

  -- Call our mbap dissector directly with the constructed MBAP TVB
  if mbap_dissector and mbap_tvb then
    mbap_dissector:call(mbap_tvb, pktinfo, root)
  else
    data_dissector:call(payload:tvb(), pktinfo, subtree)
  end
end

-- Register for user DLT (DLT_USER0) with numeric fallback 147
local wtap_table = DissectorTable.get("wtap_encap")
local ok, user0 = pcall(function() return wtap.USER0 end)
if not ok or user0 == nil then user0 = 147 end
wtap_table:add(user0, p_umdt)