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

        # Every output row can be assigned to any DI, not just its own number.
        p.trigger2.setCurrentIndex(p.trigger2.findData(3))
        assert d['config'][1]['trigger'] == 3
        assert p.trigger2.currentData() == 3
        for i in range(6):
            for prefix in ('firstDelay', 'pulseTime', 'repeat', 'between'):
                field = getattr(p, f'{prefix}{i+1}')
                assert field.width() >= field.fontMetrics().horizontalAdvance('1000') + 38
        p.repeat1.setValue(1000)
        assert '1000' in p.repeat1.text()
        p.repeat1.setValue(1)

        # DI1 -> START ALL starts all six active channels, even those whose
        # own DI selection is None or DI3. A single edge schedules each once.
        for cfg in d['config']:
            cfg.update(active=True, first=0, pulse=50, between=0,
                       repeat=1, trigger=0, action='start')
        d['config'][0].update(trigger=1, action='start_all')
        d['config'][1]['trigger'] = 3
        c.render()
        assert p.diAction1.currentData() == 'start_all'
        assert p.trigger1.currentData() == 1
        assert p.trigger2.currentData() == 3
        assert p.simulateDI1.isEnabled()
        assert p.simulateDI3.isEnabled()
        with patch.object(c, 'schedule', wraps=c.schedule) as scheduled:
            QTest.mouseClick(p.simulateDI1, Qt.MouseButton.LeftButton)
            assert scheduled.call_count == 6, 'One DI1 edge must start all six active channels exactly once'
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
        # Persist explicit DI routing on a real unit; the temporary sample above
        # must never be copied into the real configuration automatically.
        real['config'][0].update(trigger=1, action='start_all')
        real['config'][1]['trigger'] = 3
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
        assert saved['devices'][0]['config'][0]['action'] == 'start_all'
        assert saved['devices'][0]['config'][1]['trigger'] == 3
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
