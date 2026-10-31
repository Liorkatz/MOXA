# MOXA Control

Windows/PySide6 application for up to five ioLogik E1214 units: six digital inputs and six relay outputs per unit.

## Run

For a fresh checkout, create a local Python environment and install `requirements.txt`. The private repository includes the current configuration, readable transfer package and MOXA vendor DLL at the owner's request. The configuration may contain plaintext passwords. For an empty configuration, copy `moxa_settings.example.json` to `moxa_settings.json`. The local virtual environment is excluded from Git.

Double-click `START.cmd`, or run `.venv\Scripts\python.exe main.py` from this folder. The local `.venv` is retained. The destination computer must already have 64-bit Python and PySide6 installed; do not copy the virtual environment between computers.

## Save and open

File → Save, Save As and Open handle one JSON configuration containing unit connections/passwords, channel names, Continuous baseline, selections, automation timings/DI actions and application preferences. Autosave runs after changes and on close, with Saving/Saved/error feedback. Passwords are plain text as requested. Real units load disconnected and automation never resumes automatically. Invalid configurations are preserved.

## Operation

- Home: manual outputs and collapsible unit cards. Show Automation displays timings, RUN/PAUSE/CONTINUE/RESET, per-channel DI actions and pulse previews.
- Settings: scan/add/edit/remove units, connect/disconnect, confirmation preference, automatic pulse preview and optional configurable countdown before RUN.
- LIVE requires an explicit Connect and hides simulation controls. Simulation does not send hardware commands.
- Each channel can select None or its corresponding DI only. Simulation uses one button per input. START/OFF affect the channel, PAUSE/CONTINUE affect the unit, RESET affects all units and restores their pre-run Continuous states.
- PAUSE holds output states. RESET cancels jobs and restores the saved Continuous baseline. Unit header lamps blink green while running and stay amber while paused.
- Polling runs every 250 ms. Short DI pulses may be missed; cross-unit timing is not deterministic.

The direct Modbus backend supports E1214 default mapping only. Password authentication uses the official DLL in `vendor/`. Communication failure cancels local jobs but cannot guarantee a disconnected unit's physical output state.

## Current folders

- Root: application source, active `.ui` files, stylesheet, configuration and launcher.
- `assets/`: required interface icons.
- `vendor/`: required MOXA DLL and provenance note.
- `.venv/`: local installed Python environment, retained for this computer.
- `tests/`: maintained verification scripts. Run with `.venv\Scripts\python.exe tests\verify_file_menu.py`, for example. Hardware checks use mocks; no physical relay operation is performed.
- `transfer/readable-code/`: the current text-only transfer package. It contains three readable code files, one restore-command text file, a separate encoded DLL text file and an optional direct `MXIO.dll` copy.

Rebuild the transfer package after source/configuration changes:

```powershell
.\.venv\Scripts\python.exe build_readable_transfer.py
```

Copy the contents of `MOXA-CODE-1.txt`, `MOXA-CODE-2.txt`, `MOXA-CODE-3.txt`, `MOXA-DLL.txt` and `MOXA-RESTORE.txt` into files with identical names in one destination folder. Follow the command at the top of `MOXA-RESTORE.txt`. It validates all files and restores into a new folder without overwriting existing files or installing software.

Old ZIP packages, redundant transfer formats, restore-test copies, SDK installers/examples, legacy UI sources and historical screenshots/reports were removed during cleanup. GUI tests can recreate temporary screenshots under `archive/screenshots/`; those images are not needed to run the application.
