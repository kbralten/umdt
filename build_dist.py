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
SNIFF_ICON_PATH = os.path.join(ROOT, "umdt-sniff.ico")

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
    bridge_entry = os.path.join(ROOT, "bridge.py")
    mock_gui_entry = os.path.join(ROOT, "mock_server_gui.py")
    sniff_cli_entry = os.path.join(ROOT, "sniff_cli.py")
    sniff_gui_entry = os.path.join(ROOT, "sniff_gui.py")

    if not os.path.exists(cli_entry) or not os.path.exists(gui_entry):
        print("Error: entry points main_cli.py or main_gui.py not found in project root.")
        sys.exit(2)

    common_args = [sys.executable, "-m", "PyInstaller", "--onefile", "--paths", UMDT_PKG_PATH]
    # Ensure PyInstaller bundles dynamically imported serial/pymodbus modules
    hidden_imports = [
        "pymodbus.client.serial",
        "pymodbus.client.sync",
        "pymodbus.client",
        "serial",
        "serial.tools.list_ports",
    ]
    for hi in hidden_imports:
        common_args += ["--hidden-import", hi]

    print("Building CLI executable (console)...")
    subprocess.check_call(common_args + ["--name", cli_name, cli_entry])

    # Build bridge CLI
    if os.path.exists(bridge_entry):
        bridge_name = "umdt_bridge"
        print("Building bridge CLI executable (console)...")
        subprocess.check_call(common_args + ["--name", bridge_name, bridge_entry])
    else:
        print("Skipping bridge CLI: entry not found")

    # Build mock server CLI
    if os.path.exists(mock_cli_entry):
        mock_cli_name = "umdt_mock"
        print("Building mock server CLI executable...")
        subprocess.check_call(common_args + ["--name", mock_cli_name, mock_cli_entry])
    else:
        print("Skipping mock server CLI: entry not found")

    # Build sniff CLI
    if os.path.exists(sniff_cli_entry):
        sniff_cli_name = "umdt_sniff"
        print("Building sniff CLI executable...")
        subprocess.check_call(common_args + ["--name", sniff_cli_name, sniff_cli_entry])
    else:
        print("Skipping sniff CLI: entry not found")

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

    # Build sniff GUI (windowed)
    if os.path.exists(sniff_gui_entry):
        sniff_gui_name = "umdt_sniff_gui"
        print("Building sniff GUI executable (windowed/no-console) with icon...")
        sniff_gui_args = list(common_args)
        # Bundle sniff icon so runtime can load it
        if os.path.exists(SNIFF_ICON_PATH):
            sniff_gui_args += ["--add-data", f"{SNIFF_ICON_PATH}{os.pathsep}."]
        
        sniff_gui_args += ["--name", sniff_gui_name, "--noconsole", sniff_gui_entry]
        # Use sniff icon for executable
        if os.path.exists(SNIFF_ICON_PATH):
            sniff_gui_args[3:3] = ["--icon", SNIFF_ICON_PATH]
        
        print(f"PyInstaller command for {sniff_gui_name}: {' '.join(sniff_gui_args)}") # Debug print
        subprocess.check_call(sniff_gui_args)
    else:
        print("Skipping sniff GUI: entry not found")

    print("Build complete. Dist folder:")
    dist_dir = os.path.join(ROOT, "dist")
    print(dist_dir)


def clean():
    """Remove build/ and dist/ directories."""
    print("Cleaning build and dist directories...")
    if os.path.exists("build"):
        shutil.rmtree("build")
    if os.path.exists("dist"):
        shutil.rmtree("dist")
    print("Clean complete.")


if __name__ == "__main__":
    clean() # Clean before building
    ensure_pyinstaller()
    build()
