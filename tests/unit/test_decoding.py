from umdt.utils.decoding import decode_registers, decode_to_table_dict


def test_decode_single_register_big_little():
    # 0x0001 should decode to UInt16 1 and Int16 1 in both Big/Little
    regs = [0x0001]
    result = decode_registers(regs, long_mode=False, include_all_formats=True)
    rows = decode_to_table_dict(result)
    assert any(r['Format'] == 'Big' and r['UInt16'] == '1' for r in rows)
    # Little-endian interpretation of 0x0001 becomes 0x0100 == 256
    assert any(r['Format'] == 'Little' and r['UInt16'] == '256' for r in rows)


def test_decode_32bit_float():
    # 0x4120 0x0000 as big endian bytes = 0x41200000 -> float32 == 10.0
    regs = [0x4120, 0x0000]
    result = decode_registers(regs, long_mode=True, include_all_formats=True)
    rows = decode_to_table_dict(result)
    # Find Big permutation and check float32
    big_row = next((r for r in rows if r['Format'] == 'Big'), None)
    assert big_row is not None
    # float32 string should represent 10.0 (allow possible formatting variations)
    f32 = big_row.get('Float32', '')
    assert f32 != '' and float(f32) == 10.0
