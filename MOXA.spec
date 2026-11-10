from pathlib import Path

root = Path(SPECPATH)
ui_files = sorted(root.glob("*.ui"))
data_files = [(str(path), ".") for path in ui_files]
data_files += [(str(root / "style.qss"), "."), (str(root / "assets"), "assets")]

analysis = Analysis(
    [str(root / "main.py")],
    binaries=[(str(root / "vendor" / "MXIO.dll"), "vendor")],
    datas=data_files,
)
archive = PYZ(analysis.pure)
executable = EXE(
    archive,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name="MOXA-Portable",
    console=False,
)
