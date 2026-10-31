"""Empty configuration recovery and navigation; no hardware traffic."""
import _bootstrap  # Allow direct execution from the tests folder.
from pathlib import Path
from tempfile import TemporaryDirectory
from PySide6.QtWidgets import QApplication
import main

app = QApplication([])
with TemporaryDirectory(dir=main.ROOT) as folder:
    path = Path(folder) / 'settings.json'
    c = main.Controller(config_path=path)
    c.pollTimer.stop()
    for _ in range(2):
        c.manage.deviceTable.selectRow(0)
        c.remove_device()
    assert not c.devices and not c.panels
    assert not c.emptySimulationButton.isHidden()
    c.modeButton.click()
    assert c.modeButton.text() == 'LIVE'
    assert c.current_mode() == 'real'
    c.save_configuration()
    restored = main.Controller(config_path=path)
    restored.pollTimer.stop()
    assert not restored.devices and restored.modeButton.text() == 'LIVE'
    restored.saveTimer.stop()
    restored.hardware.close()
    restored.window.deleteLater()
    c.modeButton.click()
    assert c.modeButton.text() == 'SIMULATION'
    c.fit_window_height()
    c.window.screens.setCurrentIndex(1)
    assert c.window.minimumHeight() >= 600
    assert c.window.height() >= c.window.minimumHeight()
    c.open_discovery()
    c.new_connection()
    app.processEvents()
    assert c.discoveryDialog.editorTabs.currentIndex() == 1
    assert not c.connectionEditor.isHidden()
    c.discoveryDialog.close()
    c.emptySimulationButton.click()
    assert len(c.devices) == len(c.panels) == 1
    assert c.devices[0]['mode'] == 'simulation' and c.devices[0]['connected']
    assert c.window.screens.currentIndex() == 0
    for _ in range(4):
        c.addSimulationButton.click()
    assert len(c.devices) == 5
    assert len({d['ip'] for d in c.devices}) == 5
    assert not c.addSimulationButton.isEnabled()
    assert not c.clients and not c.jobs
    c.saveTimer.stop()
    c.hardware.close()
    c.window.deleteLater()
print('PASS: empty units, mode persistence, simulated addition, editor and page sizing')
