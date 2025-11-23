Repository onboarding notes for Copilot coding agents
===============================================

Purpose
-------
- Short, actionable instructions so a coding agent can make safe CI-friendly changes without long exploratory searches.

High-level summary
-------------------
- Project: Universal Modbus Diagnostic Tool (UMDT).
- Language: Python (CPython 3.13 target). Async-first architecture using `asyncio`.
- UI: PySide6 + `qasync` for Qt + asyncio integration.
- CLI: `typer` + `rich` for console UX.
- Transports: abstract `TransportInterface` with concrete `mock`, `tcp`, `serial_async` implementations and a `ConnectionManager`.
- Packaging: PyInstaller builds `umdt_cli.exe` and `umdt_gui.exe`. Installer generation via Inno Setup (`ISCC.exe`) with ZIP fallback.

Environment & tools (always use these)
------------------------------------
- Python executable used for local development in this workspace: `C:/Users/kevin/Dev/umdt/.conda/python.exe` (a conda-managed Python 3.13). When running commands in CI or locally prefer explicit interpreter: e.g.
  - `C:/Users/kevin/Dev/umdt/.conda/python.exe -m pip install -r requirements.txt`
  - `C:/Users/kevin/Dev/umdt/.conda/python.exe -m pytest -q`
- If you run `python` in the terminal, ensure it maps to the same interpreter.
- Required runtime packages (examples): `typer`, `pyside6`, `qasync`, `pymodbus`, `rich`, `pyserial`.
- Dev-only packages: `pyinstaller`, `pytest` (put in `requirements-dev.txt`).
- Windows only: Inno Setup (`ISCC.exe`) if building native installer.

Bootstrap / install
-------------------
1. Create / activate the workspace environment (user-managed). Then install dependencies:
   - `C:/Users/kevin/Dev/umdt/.conda/python.exe -m pip install -r requirements.txt`
   - `C:/Users/kevin/Dev/umdt/.conda/python.exe -m pip install -r requirements-dev.txt`
2. If you need PyInstaller builds, ensure `pyinstaller` present (in `requirements-dev.txt`).

Build / Run / Test (canonical commands)
--------------------------------------
- Run CLI POC: `C:/Users/kevin/Dev/umdt/.conda/python.exe main_cli.py mock-test`
- Run GUI (desktop): `python main_gui.py` (requires `PySide6`; run from the environment above)
- Build executables (PyInstaller helper): `python build_dist.py` — this wraps `PyInstaller` and will add `--icon umdt.ico` for the GUI if present.
- Build installer: `python build_installer.py` — requires `ISCC.exe` on PATH for native installer; otherwise produces a ZIP fallback containing exes + .iss.
- Run tests: `C:/Users/kevin/Dev/umdt/.conda/python.exe -m pytest -q`.

Quick validation checklist (before proposing a PR)
------------------------------------------------
1. Static sanity: `python -m py_compile <modified files>` (quick syntax check).
2. Run unit tests: `python -m pytest -q`. Fix failures locally.
3. Run `python main_cli.py mock-test` to exercise transport/CoreController end-to-end for POCs.
4. If you changed GUI behavior, start `python main_gui.py` locally to ensure no blocking or import errors (requires `PySide6`).
5. If you modified packaging: run `python build_dist.py` and, if building installer, `python build_installer.py` (expect Inno Setup or ZIP fallback).

Project layout (important files)
--------------------------------
- `main_cli.py` — Typer CLI entrypoint (commands like `mock-test`).
- `main_gui.py` — Qt GUI entrypoint (uses `qasync`).
- `umdt/` — package:
  - `umdt/core/controller.py` — `CoreController`, observers, scanner lock, request_write_access API.
  - `umdt/transports/` — `base.py`, `mock.py`, `tcp.py`, `serial_async.py`, `manager.py`, `passive.py`.
  - `umdt/protocols/framers.py` — permissive pymodbus framers + `register_raw_hook`.
- `build_dist.py` — PyInstaller wrapper (produces `dist/umdt_cli.exe`, `dist/umdt_gui.exe`).
- `build_installer.py` — generates Inno Setup `.iss` and builds installer or ZIP fallback.
- `umdt.ico` — application icon (project root) used by builds & installer.
- `tests/` — pytest unit tests.
- `requirements.txt` / `requirements-dev.txt` — runtime and dev dependencies.

Notes, pitfalls, and best practices for code changes
-------------------------------------------------
- Always run the test suite after edits. Unit tests exist and were added for core transport/controller behavior.
- Prefer small, focused changes. Avoid sweeping refactors without tests — CI is light locally, but the author expects runnable tests and working builds.
- Use `apply_patch` to edit files (the repo tools expect that). Use the `manage_todo_list` tool to declare/track multi-step work.
- If you add imports of optional dependencies (e.g., `serial_asyncio`, `pymodbus`), prefer lazy imports to avoid import-time failures in environments that don't have them.
- When adding async tasks, be careful with singletons (e.g., `ConnectionManager`) — tests can interfere if singletons persist between runs. Prefer to stop and/or clear singletons in test teardown.
- For packaging changes: test locally with `build_dist.py`. Building single-file exes with PyInstaller may require additional hooks for PySide6; if GUI fails at runtime, check PyInstaller warnings and `--paths` usage.

When to search the repo
-----------------------
- Trust these instructions first. Only run searches when you cannot find required information here or when a behavior is clearly inconsistent with these notes.

End of file.
