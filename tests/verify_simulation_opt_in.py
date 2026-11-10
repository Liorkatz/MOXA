"""Regression checks for the opt-in, offline-only simulator.

No socket or physical MOXA is contacted. Requires PySide6 and a Qt test runtime.
Run: .venv64\\Scripts\\python.exe tests\\verify_simulation_opt_in.py
"""
import _bootstrap
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

import config_store
import main


def wait_until(app, predicate):
    end = time.monotonic() + 3
    while not predicate() and time.monotonic() < end:
        app.processEvents()
        time.sleep(.003)
    assert predicate(), 'Timed out waiting for a local simulation event'


app = QApplication([])
app.setStyle('Fusion')
app.setStyleSheet(main.STYLE)

with tempfile.TemporaryDirectory() as folder:
    path = Path(folder) / 'settings.json'
    c = main.Controller(config_path=path)
    c.pollTimer.stop()
    c.window.show()
    app.processEvents()

    # The setting is opt-in; no simulated unit or simulated DI UI exists yet.
    assert not c.simulation_enabled
    assert not c.simulationCheckbox.isChecked()
    assert not c.simulationHelp.isVisible()
    assert not c.devices

    with patch.object(main, 'make_client', side_effect=AssertionError('Physical backend invoked in simulation')):
        c.simulationCheckbox.setChecked(True)
        app.processEvents()
        assert c.simulation_enabled
        assert len(c.devices) == 1 and c.devices[0]['temporary_sim_unit']
        d = c.devices[0]
        p = c.panels[0]
        assert d['connected'] and d['mode'] == 'simulation'
        assert not p.connectButton.isVisible()
        assert p.connectionStatus.text().startswith('Simulation')

        QTest.mousePress(p.pulse1, Qt.MouseButton.LeftButton)
        assert d['states'][0]
        QTest.mouseRelease(p.pulse1, Qt.MouseButton.LeftButton)
        assert not d['states'][0]

        c.home.showAutomation.setChecked(True)
        app.processEvents()
        assert p.simulateDI1.isVisible()
        d['config'][0].update(active=True, first=0, pulse=20, repeat=1,
                              between=0, trigger=1)
        c.render()
        assert p.simulateDI1.isEnabled()
        QTest.mouseClick(p.simulateDI1, Qt.MouseButton.LeftButton)
        wait_until(app, lambda: not c.jobs)
        assert not any(d['states'])
        c.save_configuration()
        saved = config_store.load(path)
        assert saved['simulation_enabled']
        assert saved['devices'] == []  # Temporary unit must not become real hardware.

        # A configuration explicitly saved with the opt-in enabled reopens offline.
        reopened = main.Controller(config_path=path)
        reopened.pollTimer.stop()
        assert reopened.simulation_enabled
        assert len(reopened.devices) == 1 and reopened.devices[0]['temporary_sim_unit']
        assert not reopened.clients
        reopened.window.close()
        reopened.hardware.close()

        # Switching off removes the sample and all simulator-only controls.
        c.simulationCheckbox.setChecked(False)
        app.processEvents()
        assert not c.simulation_enabled and not c.devices
        assert not c.simulationHelp.isVisible()
        assert not c.simulationCheckbox.isChecked()
        c.save_configuration()
        assert not config_store.load(path)['simulation_enabled']

        # A saved real-unit snapshot cannot be replaced by a simulated output.
        real = c.new_device(1)
        real['states'] = [True, False, False, False, False, False]
        c.devices = [real]
        c.rebuild()
        c.simulationCheckbox.setChecked(True)
        assert not any(real['states'])
        real['states'][2] = True
        c.save_configuration()
        saved = config_store.load(path)
        assert saved['devices'][0]['mode'] == 'real'
        assert saved['devices'][0]['continuous_states'] == [True, False, False, False, False, False]
        c.simulationCheckbox.setChecked(False)
        assert real['states'] == [True, False, False, False, False, False]
        assert not real['connected']

        # Switching while real hardware is marked connected must be refused.
        real['connected'] = True
        with patch.object(main.QMessageBox, 'information') as warning:
            c.simulationCheckbox.setChecked(True)
            assert warning.called
            assert not c.simulation_enabled
            assert not c.simulationCheckbox.isChecked()
        real['connected'] = False

    c.window.close()
    c.hardware.close()

print('PASS: hidden-by-default simulation, press/release, DI trigger, virtual isolation, persistence, and safe mode switching.')
