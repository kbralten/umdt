#!/usr/bin/env python3
"""
Create a Windows installer for UMDT using Inno Setup if available.
If Inno Setup (`ISCC.exe`) is not found, produce a ZIP fallback containing the two executables
and the generated Inno Setup script.

Outputs:
 - dist/UMDT_Setup.exe    (if ISCC found and succeeds)
 - dist/umdt_installer.zip (fallback)

Run:
    python build_installer.py
"""
import os
import sys
import shutil
import subprocess
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
DIST = os.path.join(ROOT, "dist")
BUILD = os.path.join(ROOT, "build")
os.makedirs(DIST, exist_ok=True)
os.makedirs(BUILD, exist_ok=True)

CLI_EXE = os.path.join(DIST, "umdt.exe")
GUI_EXE = os.path.join(DIST, "umdt_gui.exe")
MOCK_CLI_EXE = os.path.join(DIST, "umdt_mock.exe")
MOCK_GUI_EXE = os.path.join(DIST, "umdt_mock_server_gui.exe")
SNIFF_CLI_EXE = os.path.join(DIST, "umdt_sniff.exe")
SNIFF_GUI_EXE = os.path.join(DIST, "umdt_sniff_gui.exe")
LICENSE_SRC = os.path.join(ROOT, "LICENSE")

ISS_PATH = os.path.join(BUILD, "umdt_installer.iss")
OUT_NAME = "UMDT_Setup"
OUT_EXE = os.path.join(DIST, OUT_NAME + ".exe")
ZIP_OUT = os.path.join(DIST, "umdt_installer.zip")

APP_NAME = "Universal Modbus Diagnostic Tool"
APP_VERSION = "0.1.0"

def gather_sources():
    missing = []
    for p in (CLI_EXE, GUI_EXE, MOCK_CLI_EXE, MOCK_GUI_EXE, SNIFF_CLI_EXE, SNIFF_GUI_EXE):
        if not os.path.exists(p):
            missing.append(p)
    return missing

def generate_iss(cli_path, gui_path, iss_path):
    # Build a raw string template and use {commonpf} for the default program files folder
    template = r"""
[Setup]
; Per-user installation: do not require elevation
PrivilegesRequired=lowest
AppName={app_name}
AppVersion={app_version}
SetupIconFile={icon}
DefaultDirName={commonpf}\UMDT
DefaultGroupName=UMDT
OutputBaseFilename={out_name}
Compression=lzma
SolidCompression=yes

[Files]
Source: "{CLI}"; DestDir: "{app}"; Flags: ignoreversion
Source: "{GUI}"; DestDir: "{app}"; Flags: ignoreversion
Source: "{MOCK_CLI}"; DestDir: "{app}"; Flags: ignoreversion
Source: "{MOCK_GUI}"; DestDir: "{app}"; Flags: ignoreversion
Source: "{SNIFF_CLI}"; DestDir: "{app}"; Flags: ignoreversion
Source: "{SNIFF_GUI}"; DestDir: "{app}"; Flags: ignoreversion
Source: "{ICON}"; DestDir: "{app}"; Flags: ignoreversion
Source: "{MOCK_ICON}"; DestDir: "{app}"; Flags: ignoreversion
Source: "{SNIFF_ICON}"; DestDir: "{app}"; Flags: ignoreversion

[Tasks]
Name: addtopath; Description: "Add UMDT install directory to the user PATH"; GroupDescription: "Additional tasks:"; Flags: unchecked

[Icons]
Name: "{group}\UMDT GUI"; Filename: "{app}\{GUI_BASENAME}"; IconFilename: "{app}\{ICON_BASENAME}"
Name: "{group}\UMDT CLI (Console)"; Filename: "{app}\{CLI_BASENAME}"; IconFilename: "{app}\{ICON_BASENAME}"
Name: "{group}\UMDT Mock Server GUI"; Filename: "{app}\{MOCK_GUI_BASENAME}"; IconFilename: "{app}\{MOCK_ICON_BASENAME}"
Name: "{group}\UMDT Mock Server CLI"; Filename: "{app}\{MOCK_CLI_BASENAME}"; IconFilename: "{app}\{ICON_BASENAME}"
Name: "{group}\UMDT Sniffer GUI"; Filename: "{app}\{SNIFF_GUI_BASENAME}"; IconFilename: "{app}\{SNIFF_ICON_BASENAME}"
Name: "{group}\UMDT Sniffer CLI"; Filename: "{app}\{SNIFF_CLI_BASENAME}"; IconFilename: "{app}\{SNIFF_ICON_BASENAME}"

[Run]
; Launch the GUI after install
Filename: "{app}\\{GUI_BASENAME}"; Description: "Launch UMDT GUI"; Flags: nowait postinstall skipifsilent
Filename: "{app}\\{MOCK_GUI_BASENAME}"; Description: "Launch Mock Server GUI"; Flags: nowait postinstall skipifsilent
Filename: "{app}\\{SNIFF_GUI_BASENAME}"; Description: "Launch Sniffer GUI"; Flags: nowait postinstall skipifsilent

[Registry]
; If the user selected the Add to PATH task, append the install dir to the user PATH (HKCU). Note: user will need to re-login to pick up new PATH.
Root: HKCU; Subkey: "Environment"; ValueType: string; ValueName: "PATH"; ValueData: "{reg:HKCU\\Environment,PATH};{app}"; Flags: preservestringtype; Tasks: addtopath
"""

    content = template.replace("{app_name}", APP_NAME)
    content = content.replace("{app_version}", APP_VERSION)
    content = content.replace("{out_name}", OUT_NAME)
    content = content.replace("{CLI}", cli_path)
    content = content.replace("{GUI}", gui_path)
    # Attempt to include mock server executables if present in dist
    content = content.replace("{MOCK_CLI}", os.path.join(DIST, os.path.basename(MOCK_CLI_EXE)))
    content = content.replace("{MOCK_GUI}", os.path.join(DIST, os.path.basename(MOCK_GUI_EXE)))
    content = content.replace("{SNIFF_CLI}", os.path.join(DIST, os.path.basename(SNIFF_CLI_EXE)))
    content = content.replace("{SNIFF_GUI}", os.path.join(DIST, os.path.basename(SNIFF_GUI_EXE)))
    
    # Icons
    content = content.replace("{MOCK_ICON}", os.path.join(ROOT, "umdt_mock.ico"))
    content = content.replace("{MOCK_ICON_BASENAME}", os.path.basename(os.path.join(ROOT, "umdt_mock.ico")))
    content = content.replace("{SNIFF_ICON}", os.path.join(ROOT, "umdt-sniff.ico"))
    content = content.replace("{SNIFF_ICON_BASENAME}", os.path.basename(os.path.join(ROOT, "umdt-sniff.ico")))
    
    content = content.replace("{LICENSE}", LICENSE_SRC)
    content = content.replace("{CLI_BASENAME}", os.path.basename(cli_path))
    content = content.replace("{GUI_BASENAME}", os.path.basename(gui_path))
    content = content.replace("{MOCK_CLI_BASENAME}", os.path.basename(MOCK_CLI_EXE))
    content = content.replace("{MOCK_GUI_BASENAME}", os.path.basename(MOCK_GUI_EXE))
    content = content.replace("{SNIFF_CLI_BASENAME}", os.path.basename(SNIFF_CLI_EXE))
    content = content.replace("{SNIFF_GUI_BASENAME}", os.path.basename(SNIFF_GUI_EXE))
    
    content = content.replace("{ICON}", os.path.join(ROOT, "umdt.ico"))
    content = content.replace("{ICON_BASENAME}", os.path.basename(os.path.join(ROOT, "umdt.ico")))
    # Ensure lowercase {icon} placeholder (used in template) is also replaced
    content = content.replace("{icon}", os.path.join(ROOT, "umdt.ico"))

    # Write the final .iss file
    with open(iss_path, "w", encoding="utf-8") as f:
        f.write(content)
    return iss_path


def find_compiler():
    # Check ISCC (Inno Setup Compiler) first
    iscc = shutil.which("ISCC") or shutil.which("ISCC.exe")
    if iscc:
        return ("iscc", iscc)
    # Check NSIS (makensis) as a fallback (note: script is for Inno; makensis won't compile .iss)
    makensis = shutil.which("makensis") or shutil.which("makensis.exe")
    if makensis:
        return ("makensis", makensis)
    return (None, None)


def compile_iss(iscc_path, iss_path):
    # Run ISCC with the script
    try:
        subprocess.check_call([iscc_path, iss_path], cwd=BUILD)
        # Inno writes output to BUILD by default; move it to dist if found
        # Check common output locations
        possibles = [
            os.path.join(BUILD, OUT_NAME + ".exe"),
            os.path.join(BUILD, "Output", OUT_NAME + ".exe"),
            os.path.join(BUILD, "output", OUT_NAME + ".exe"),
        ]
        for possible in possibles:
            if os.path.exists(possible):
                shutil.move(possible, OUT_EXE)
                return True
        return True
    except subprocess.CalledProcessError:
        return False


def make_zip(files, extras):
    with zipfile.ZipFile(ZIP_OUT, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for f in files:
            if os.path.exists(f):
                z.write(f, arcname=os.path.basename(f))
        for extra in extras:
            if os.path.exists(extra):
                z.write(extra, arcname=os.path.basename(extra))
    return ZIP_OUT


def main():
    missing = gather_sources()
    if missing:
        print("Warning: the following required files are missing in dist/:\n")
        for m in missing:
            print(" - ", os.path.relpath(m, ROOT))
        print("\nRun the build script to produce the exes before creating an installer.")
        # Continue: still generate ISS for inspection

    print("Generating Inno Setup script...")
    generate_iss(CLI_EXE, GUI_EXE, ISS_PATH)
    print("Generated:", ISS_PATH)

    kind, path = find_compiler()
    if kind == "iscc":
        print("Found Inno Setup Compiler:", path)
        print("Compiling installer...")
        ok = compile_iss(path, ISS_PATH)
        if ok and os.path.exists(OUT_EXE):
            print("Installer built:", OUT_EXE)
            return
        else:
            print("Inno Setup failed to build installer or output not found.")
    elif kind == "makensis":
        print("NSIS (makensis) found, but script is Inno Setup format. Skipping compile.")
    else:
        print("No Inno Setup (ISCC) found on PATH.")
        print("To create a proper installer, install Inno Setup and add ISCC.exe to PATH:")
        print("https://jrsoftware.org/isinfo.php")

    print("Creating ZIP fallback with exes and script...")
    files = [CLI_EXE, GUI_EXE, MOCK_CLI_EXE, MOCK_GUI_EXE, SNIFF_CLI_EXE, SNIFF_GUI_EXE]
    extras = [ISS_PATH, os.path.join(ROOT, "umdt.ico"), os.path.join(ROOT, "umdt_mock.ico"), os.path.join(ROOT, "umdt-sniff.ico")]
    z = make_zip(files, extras)
    print("Created:", z)
    print("Done. If you want a native installer, install Inno Setup and re-run this script.")

if __name__ == "__main__":
    main()
