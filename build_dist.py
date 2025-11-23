#!/usr/bin/env python3
"""
Simple build script to create Windows executables using PyInstaller.
Creates two artifacts in ./dist/:
 - umdt_cli.exe  (console)
 - umdt_gui.exe  (windowed)

Usage:
    python build_dist.py

Notes:
 - This script will attempt to install PyInstaller if it's not present.
 - It supplies the project package path to PyInstaller via --paths.
"""
import os
import sys
import subprocess

ROOT = os.path.dirname(os.path.abspath(__file__))
UMDT_PKG_PATH = os.path.join(ROOT, "umdt")
ICON_PATH = os.path.join(ROOT, "umdt.ico")

def ensure_pyinstaller():
    try:
        import PyInstaller  # noqa: F401
        print("PyInstaller already installed.")
        return True
    except Exception:
        print("PyInstaller not found; installing via pip...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"]) 
        return True


def build(cli_name: str = "umdt_cli", gui_name: str = "umdt_gui"):
    # Build CLI (console)
    cli_entry = os.path.join(ROOT, "main_cli.py")
    gui_entry = os.path.join(ROOT, "main_gui.py")

    if not os.path.exists(cli_entry) or not os.path.exists(gui_entry):
        print("Error: entry points main_cli.py or main_gui.py not found in project root.")
        sys.exit(2)

    common_args = [sys.executable, "-m", "PyInstaller", "--onefile", "--paths", UMDT_PKG_PATH]

    print("Building CLI executable (console)...")
    subprocess.check_call(common_args + ["--name", cli_name, cli_entry])

    print("Building GUI executable (windowed/no-console) with icon...")
    gui_args = common_args + ["--name", gui_name, "--noconsole", gui_entry]
    if os.path.exists(ICON_PATH):
        gui_args[3:3] = ["--icon", ICON_PATH]
    subprocess.check_call(gui_args)

    print("Build complete. Dist folder:")
    print(os.path.join(ROOT, "dist"))


if __name__ == "__main__":
    ensure_pyinstaller()
    build()
