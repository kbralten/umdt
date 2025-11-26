-- umdt_mbap.lua
-- Simple MBAP dissector for UMDT: parses MBAP header and basic Modbus PDUs

local p_mbap = Proto("mbap", "MBAP (UMDT)")

local f_trans = ProtoField.uint16("mbap.trans_id", "Transaction ID", base.DEC)
local f_proto = ProtoField.uint16("mbap.proto_id", "Protocol ID", base.DEC)
local f_len = ProtoField.uint16("mbap.length", "Length", base.DEC)
local f_unit = ProtoField.uint8("mbap.unit_id", "Unit ID", base.DEC)
local f_func = ProtoField.uint8("mbap.func", "Function Code", base.HEX)
local f_func_name = ProtoField.string("mbap.func_name", "Function")
local f_exception = ProtoField.uint8("mbap.exception", "Exception Code", base.HEX)
local f_exception_name = ProtoField.string("mbap.exception_name", "Exception")
local f_bytecount = ProtoField.uint8("mbap.byte_count", "Byte Count", base.DEC)
local f_reg = ProtoField.uint16("mbap.reg", "Register", base.DEC)
local f_start_addr = ProtoField.uint16("mbap.start_addr", "Start Address", base.DEC)
local f_qty = ProtoField.uint16("mbap.qty", "Quantity", base.DEC)

p_mbap.fields = { f_trans, f_proto, f_len, f_unit, f_func, f_func_name, f_exception, f_exception_name, f_bytecount, f_reg, f_start_addr, f_qty }

local EXC_NAMES = {
  [1] = "Illegal Function",
  [2] = "Illegal Data Address",
  [3] = "Illegal Data Value",
  [4] = "Slave Device Failure",
  [5] = "Acknowledge",
  [6] = "Slave Device Busy",
  [8] = "Memory Parity Error",
  [10] = "Gateway Path Unavailable",
  [11] = "Gateway Target Device Failed to Respond",
}

local FUNC_NAMES = {
  [1] = "Read Coils",
  [2] = "Read Discrete Inputs",
  [3] = "Read Holding Registers",
  [4] = "Read Input Registers",
  [5] = "Write Single Coil",
  [6] = "Write Single Register",
  [15] = "Write Multiple Coils",
  [16] = "Write Multiple Registers",
}

local function parse_pdu(tvb, tree)
  -- tvb starts at Unit ID
  if tvb:len() < 2 then return end
  local unit = tvb(0,1):uint()
  local func = tvb(1,1):uint()
  tree:add(f_unit, tvb(0,1))
  tree:add(f_func, tvb(1,1))

  -- Exception response: function code with MSB set (0x80)
  if func >= 0x80 then
    local orig_func = func - 0x80
    tree:add(f_func_name, tvb(1,1)):append_text(string.format(" Exception of 0x%02X", orig_func))
    if tvb:len() >= 3 then
      local exc = tvb(2,1):uint()
      tree:add(f_exception, tvb(2,1))
      tree:add(f_exception_name, tvb(2,1)):append_text(" (" .. (EXC_NAMES[exc] or "Unknown") .. ")")
      tree:add_expert_info(PI_MALFORMED, PI_ERROR, string.format("Modbus exception: %s (0x%02X)", EXC_NAMES[exc] or "Unknown", exc))
    else
      tree:add_expert_info(PI_MALFORMED, PI_ERROR, "Modbus exception: truncated PDU")
    end
    return
  else
    tree:add(f_func, tvb(1,1)):append_text(" (" .. (FUNC_NAMES[func] or "Unknown") .. ")")
  end

  -- Simple decoding for func 3/4 responses and requests
  if func == 3 or func == 4 then
    -- If response: next byte is byte count
    if tvb:len() >= 3 then
      local bytecount = tvb(2,1):uint()
      tree:add(f_bytecount, tvb(2,1))
      local regs = math.floor(bytecount / 2)
      local off = 3
      for i = 1, regs do
        if off + 1 <= tvb:len() then
          tree:add(f_reg, tvb(off,2))
        end
        off = off + 2
      end
    end
  else
    -- For requests decode simple address/quantity when enough bytes
    if tvb:len() >= 5 then
      -- start addr 2 bytes, quantity 2 bytes at offset 2
      local start_addr = tvb(2,2):uint()
      local qty = tvb(4,2):uint()
      tree:add(f_start_addr, tvb(2,2))
      tree:add(f_qty, tvb(4,2))
    end
  end
end

function p_mbap.dissector(tvbuf, pktinfo, root)
  if tvbuf:len() < 7 then
    -- not enough for MBAP
    return 0
  end
  local subtree = root:add(p_mbap, tvbuf())
  subtree:add(f_trans, tvbuf(0,2))
  subtree:add(f_proto, tvbuf(2,2))
  subtree:add(f_len, tvbuf(4,2))
  -- Unit id and PDU follow
  parse_pdu(tvbuf:range(6):tvb(), subtree)
  pktinfo.cols.protocol = "MBAP"
  return tvbuf:len()
end

-- The dissector is exposed via its Proto name ('mbap') so it can be
-- looked up with Dissector.get("mbap") by the wrapper. Do NOT register
-- it as a postdissector — the wrapper calls it explicitly on payloads.
