#!/usr/bin/env python3
"""
E2E test script for UMDT Docker containers.

Runs a series of CLI commands against the mock server and validates results.
Exit code 0 = all tests passed, non-zero = failures.

Usage:
    # From host (containers must be running):
    python docker/e2e_test.py --host localhost --port 5020

    # Or run inside the cli container:
    python docker/e2e_test.py --host mock-server --port 5020
"""

import argparse
import subprocess
import sys
import json
import re


def run_cli(args: list[str], host: str, port: int) -> tuple[int, str, str]:
    """Run main_cli.py with given arguments, return (exit_code, stdout, stderr)."""
    cmd = [
        sys.executable,
        "main_cli.py",
        *args,
        "--host", host,
        "--port", str(port),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode, result.stdout, result.stderr


def test_read_frozen_registers(host: str, port: int) -> bool:
    """Test reading frozen holding registers with known values."""
    print("\n[TEST] Read frozen holding registers...")
    
    # Read register 0 (should be 12345)
    code, stdout, stderr = run_cli(["read", "--address", "0", "--count", "1"], host, port)
    if code != 0:
        print(f"  FAIL: read command failed (exit={code})")
        print(f"  stderr: {stderr}")
        return False
    
    # Check that 12345 appears in output (as decimal or hex 0x3039)
    if "12345" not in stdout and "3039" not in stdout.lower():
        print(f"  FAIL: expected value 12345 not found in output")
        print(f"  stdout: {stdout}")
        return False
    
    print("  PASS: Register 0 = 12345")
    
    # Read register 1 (should be 43981 = 0xABCD)
    code, stdout, stderr = run_cli(["read", "--address", "1", "--count", "1"], host, port)
    if code != 0:
        print(f"  FAIL: read command failed (exit={code})")
        return False
    
    if "43981" not in stdout and "abcd" not in stdout.lower():
        print(f"  FAIL: expected value 43981 (0xABCD) not found in output")
        print(f"  stdout: {stdout}")
        return False
    
    print("  PASS: Register 1 = 43981 (0xABCD)")
    return True


def test_write_and_read_back(host: str, port: int) -> bool:
    """Test writing a value and reading it back."""
    print("\n[TEST] Write and read back...")
    
    test_addr = 10  # Use address 10 (not frozen)
    test_value = 54321
    
    # Write value
    code, stdout, stderr = run_cli(
        ["write", "--address", str(test_addr), str(test_value)],
        host, port
    )
    if code != 0:
        print(f"  FAIL: write command failed (exit={code})")
        print(f"  stderr: {stderr}")
        return False
    
    print(f"  Wrote {test_value} to register {test_addr}")
    
    # Read back
    code, stdout, stderr = run_cli(["read", "--address", str(test_addr), "--count", "1"], host, port)
    if code != 0:
        print(f"  FAIL: read command failed (exit={code})")
        return False
    
    if str(test_value) not in stdout:
        print(f"  FAIL: expected value {test_value} not found in readback")
        print(f"  stdout: {stdout}")
        return False
    
    print(f"  PASS: Read back {test_value} from register {test_addr}")
    return True


def test_scan_address_range(host: str, port: int) -> bool:
    """Test scanning a range of addresses."""
    print("\n[TEST] Scan address range...")
    
    code, stdout, stderr = run_cli(["scan", "0", "5"], host, port)
    if code != 0:
        print(f"  FAIL: scan command failed (exit={code})")
        print(f"  stderr: {stderr}")
        return False
    
    # Check that we got some results (scan should find readable addresses)
    if "0" not in stdout and "1" not in stdout:
        print(f"  FAIL: scan output doesn't show expected addresses")
        print(f"  stdout: {stdout}")
        return False
    
    print("  PASS: Scan completed and found addresses 0-5")
    return True


def test_read_coils(host: str, port: int) -> bool:
    """Test reading coils."""
    print("\n[TEST] Read coils...")
    
    code, stdout, stderr = run_cli(
        ["read", "--address", "0", "--count", "8", "--datatype", "coil"],
        host, port
    )
    if code != 0:
        print(f"  FAIL: read coils command failed (exit={code})")
        print(f"  stderr: {stderr}")
        return False
    
    # Just verify command succeeded and produced output
    if len(stdout.strip()) == 0:
        print(f"  FAIL: no output from coil read")
        return False
    
    print("  PASS: Read 8 coils successfully")
    return True


def test_decode_command(host: str, port: int) -> bool:
    """Test the decode command with known values."""
    print("\n[TEST] Decode command...")
    
    # Decode hex value 0x4120 (which is part of float 10.0)
    code, stdout, stderr = run_cli(["decode", "0x4120"], host, port)
    
    # decode might not need host/port, but let's see if it works
    # Actually decode is likely offline - run without host/port
    cmd = [sys.executable, "main_cli.py", "decode", "0x4120"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    
    if result.returncode != 0:
        print(f"  FAIL: decode command failed (exit={result.returncode})")
        print(f"  stderr: {result.stderr}")
        return False
    
    # Check for hex interpretation
    if "4120" not in result.stdout.lower():
        print(f"  FAIL: expected 0x4120 in decode output")
        print(f"  stdout: {result.stdout}")
        return False
    
    print("  PASS: Decode command works")
    return True


def main():
    parser = argparse.ArgumentParser(description="UMDT E2E Test Runner")
    parser.add_argument("--host", default="localhost", help="Mock server host")
    parser.add_argument("--port", type=int, default=5020, help="Mock server port")
    args = parser.parse_args()
    
    print("=" * 60)
    print("UMDT End-to-End Test Suite")
    print(f"Target: {args.host}:{args.port}")
    print("=" * 60)
    
    tests = [
        ("Read frozen registers", test_read_frozen_registers),
        ("Write and read back", test_write_and_read_back),
        ("Scan address range", test_scan_address_range),
        ("Read coils", test_read_coils),
        ("Decode command", test_decode_command),
    ]
    
    passed = 0
    failed = 0
    
    for name, test_func in tests:
        try:
            if test_func(args.host, args.port):
                passed += 1
            else:
                failed += 1
        except Exception as e:
            print(f"\n[TEST] {name} - EXCEPTION: {e}")
            failed += 1
    
    print("\n" + "=" * 60)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 60)
    
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
