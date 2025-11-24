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
import shutil

ROOT = os.path.dirname(os.path.abspath(__file__))
UMDT_PKG_PATH = os.path.join(ROOT, "umdt")
ICON_PATH = os.path.join(ROOT, "umdt.ico")
MOCK_ICON_PATH = os.path.join(ROOT, "umdt_mock.ico")

def ensure_pyinstaller():
    try:
        import PyInstaller  # noqa: F401
        print("PyInstaller already installed.")
        return True
    except Exception:
        print("PyInstaller not found; installing via pip...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"]) 
        return True


def build(cli_name: str = "umdt", gui_name: str = "umdt_gui"):
    # Build CLI (console)
    cli_entry = os.path.join(ROOT, "main_cli.py")
    gui_entry = os.path.join(ROOT, "main_gui.py")
    mock_cli_entry = os.path.join(ROOT, "mock_server_cli.py")
    mock_gui_entry = os.path.join(ROOT, "mock_server_gui.py")

    if not os.path.exists(cli_entry) or not os.path.exists(gui_entry):
        print("Error: entry points main_cli.py or main_gui.py not found in project root.")
        sys.exit(2)

    common_args = [sys.executable, "-m", "PyInstaller", "--onefile", "--paths", UMDT_PKG_PATH]

    print("Building CLI executable (console)...")
    subprocess.check_call(common_args + ["--name", cli_name, cli_entry])

    # Build mock server CLI
    if os.path.exists(mock_cli_entry):
        mock_cli_name = "umdt_mock"
        print("Building mock server CLI executable...")
        subprocess.check_call(common_args + ["--name", mock_cli_name, mock_cli_entry])
    else:
        print("Skipping mock server CLI: entry not found")

    print("Building GUI executable (windowed/no-console) with icon...")
    gui_args = list(common_args)
    # Ensure the runtime icon file is bundled so the app can load it from the onefile bundle
    if os.path.exists(ICON_PATH):
        gui_args += ["--add-data", f"{ICON_PATH}{os.pathsep}."]
    gui_args += ["--name", gui_name, "--noconsole", gui_entry]
    # Use the icon as the executable icon as well (resource)
    if os.path.exists(ICON_PATH):
        gui_args[3:3] = ["--icon", ICON_PATH]
    subprocess.check_call(gui_args)

    # Build mock server GUI (windowed)
    if os.path.exists(mock_gui_entry):
        mock_gui_name = "umdt_mock_server_gui"
        print("Building mock server GUI executable (windowed/no-console) with icon...")
        mock_gui_args = list(common_args)
        # Bundle mock icon so runtime can load it
        if os.path.exists(MOCK_ICON_PATH):
            mock_gui_args += ["--add-data", f"{MOCK_ICON_PATH}{os.pathsep}."]
        # Also bundle main icon as fallback
        if os.path.exists(ICON_PATH):
            mock_gui_args += ["--add-data", f"{ICON_PATH}{os.pathsep}."]
        mock_gui_args += ["--name", mock_gui_name, "--noconsole", mock_gui_entry]
        # Prefer executable icon from mock icon if present
        if os.path.exists(MOCK_ICON_PATH):
            mock_gui_args[3:3] = ["--icon", MOCK_ICON_PATH]
        elif os.path.exists(ICON_PATH):
            mock_gui_args[3:3] = ["--icon", ICON_PATH]
        subprocess.check_call(mock_gui_args)
    else:
        print("Skipping mock server GUI: entry not found")

    print("Build complete. Dist folder:")
    dist_dir = os.path.join(ROOT, "dist")
    print(dist_dir)


if __name__ == "__main__":
    ensure_pyinstaller()
    build()
