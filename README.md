# MOXA Control

Windows/PySide6 application for up to five ioLogik E1214 units: six digital inputs and six relay outputs per unit.

## Run

For a fresh checkout, create a local Python environment and install `requirements.txt`. The private repository includes the current configuration, readable transfer package and MOXA vendor DLL at the owner's request. The configuration may contain plaintext passwords. For an empty configuration, copy `moxa_settings.example.json` to `moxa_settings.json`. The local virtual environment is excluded from Git.

Double-click `START.cmd`, or run `.venv\Scripts\python.exe main.py` from this folder. The local `.venv` is retained. The destination computer must already have 64-bit Python and PySide6 installed; do not copy the virtual environment between computers.

## Save and open

File → Save, Save As and Open handle one JSON configuration containing unit connections/passwords, channel names, Continuous baseline, selections, automation timings/DI actions and application preferences. Autosave runs after changes and on close, with Saving/Saved/error feedback. Passwords are plain text as requested. Real units load disconnected and automation never resumes automatically. Invalid configurations are preserved.

## Operation

- Home: Connect/Disconnect beside each IP, manual outputs and collapsible unit cards. Show Automation displays timings, RUN/PAUSE/CONTINUE/RESET, per-channel DI actions and pulse previews. Global automation buttons appear only on Home.
- Settings: scan/add/edit/remove units, confirmation preference, automatic pulse preview and optional configurable countdown before RUN. Enter a plain IPv4 address to scan its /24 range; the suffix is added internally.
- Physical units require an explicit Connect on Home. Offline simulation is opt-in from the **last setting** on the Settings page and disabled by default for old or new configuration files. No simulation-specific controls appear on Home while disabled (only the enabling checkbox is shown in Settings).
- Enable simulation only after disconnecting all real units and stopping automation. Enabling swaps existing units to local simulated states and, when no real units have been configured, creates a temporary sample unit. Simulated DI buttons are exposed only in automation view. Disabling cancels local jobs, discards the sample, restores the saved real-state snapshot and requires explicit reconnection for real output control.
- Simulation preference is saved in JSON; enabling persists across program restarts when explicitly selected. Simulated output values are not persisted over real hardware state. Legacy configurations load real units disconnected, and startup never sends hardware writes.
- Direct Modbus/TCP connections are reused across requests while healthy, reducing setup overhead for simultaneous relay commands. No write is automatically repeated after ambiguous network errors. The SDK password-authenticated backend already maintains a connection.
- Momentary: press to turn ON, hold to stay ON, release to turn OFF, individually or for selected channels. Release, hiding the control and window deactivation request OFF. Momentary is direct and does not open the optional confirmation dialog, which would consume the release event. Network communication and mechanical relay response still introduce latency.
- Writes queued in the same GUI event-loop turn are batched per unit. Contiguous selected relays use one multiple-coil write; gaps are never overwritten. One readback validates all requested outputs. Six contiguous channels need two Modbus transactions instead of twelve. Writes are never retried after ambiguous failures.
- Each channel can select None or its corresponding physical DI only. START/OFF affect the channel, PAUSE/CONTINUE affect the unit, RESET affects all units and restores their pre-run Continuous states.
- PAUSE holds output states. RESET cancels jobs and restores the saved Continuous baseline. Unit header lamps are green when connected, blink green during automation, stay amber while paused, and turn gray when disconnected.
- Queued automation writes wait during PAUSE and are resumed once on CONTINUE; phase timers start after acknowledgement. A hardware request already transmitted before PAUSE cannot be recalled. RESET preserves idle units' current Continuous states when they have no automation baseline.
- Configuration files are fully validated before replacing the current document. Runtime connection/job fields are never imported. Manual selection changes and card collapse preferences participate in autosave.
- Polling runs every 250 ms. Short DI pulses may be missed; cross-unit timing is not deterministic.

The direct Modbus backend supports E1214 default mapping only. Password authentication uses the official DLL in `vendor/`. Communication failure cancels local jobs but cannot guarantee a disconnected unit's physical output state.

## Current folders

- Root: application source, active `.ui` files, stylesheet, configuration and launcher.
- The simulation option is entirely local and cannot control hardware while enabled; a connected physical unit blocks switching modes. Rebuild the Windows EXE after updating the source.
- `assets/`: required interface icons.
- `vendor/`: required MOXA DLL and provenance note.
- `.venv/`: local installed Python environment, retained for this computer.
- `tests/`: run `.venv64\Scripts\python.exe tests\verify_live_controls.py` for current live-control, automation and protocol checks. Tests use isolated peers; no physical relay operation is performed. Run `tests/verify_simulation_opt_in.py` to check the disabled-by-default simulation setting, local DI/relay controls, saved preferences and protection against switching while hardware is connected.
- `transfer/readable-code/`: legacy text-only transfer snapshot. Rebuild before using it for this version; direct source downloads and the EXE build use the current root files.

## Updating an existing installation

Replace `main.py`, `moxa_backend.py`, `network_discovery.py` and `config_store.py` together. Keep your own settings JSON. No dependency change is required (PySide6 6.11.2 / Python 3.12 x64).

Build the standalone EXE again with:

```powershell
.\.venv64\Scripts\python.exe -m PyInstaller --noconfirm --clean MOXA.spec
```

Output: `dist/MOXA-Portable.exe`. Source changes do not modify an already-built EXE.

Rebuild the transfer package after source/configuration changes:

```powershell
.\.venv\Scripts\python.exe build_readable_transfer.py
```

Copy the contents of `MOXA-CODE-1.txt`, `MOXA-CODE-2.txt`, `MOXA-CODE-3.txt`, `MOXA-DLL.txt` and `MOXA-RESTORE.txt` into files with identical names in one destination folder. Follow the command at the top of `MOXA-RESTORE.txt`. It validates all files and restores into a new folder without overwriting existing files or installing software.

Old ZIP packages, redundant transfer formats, restore-test copies, SDK installers/examples, legacy UI sources and historical screenshots/reports were removed during cleanup. GUI tests can recreate temporary screenshots under `archive/screenshots/`; those images are not needed to run the application.

