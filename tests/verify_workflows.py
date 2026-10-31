"""Cross-screen simulation workflows with network access blocked."""
import _bootstrap
from copy import deepcopy
from unittest.mock import patch
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
import main

app = QApplication([])
app.setStyleSheet(main.STYLE)
errors = []
import sys
sys.excepthook = lambda *args: errors.append(args)
with patch('socket.create_connection', side_effect=AssertionError('Unexpected network access')):
    c = main.Controller()
    c.pollTimer.stop()
    c.window.show()
    for _ in range(3): c.add_simulated_device()
    assert len(c.devices) == 5
    c.home.showAutomation.setChecked(True)
    QTest.qWait(150)
    for d, p in zip(c.devices, c.panels):
        c.manual(d, 0, True)
        assert d['states'][0]
        c.collapse_unit(p, d)
        assert p.collapsed
        c.collapse_unit(p, d)
        assert not p.collapsed
        c.show_preview(p, d)
        assert not p.timeline.grab().isNull()
        p.previewDialog.close()
    for d in c.devices:
        for cfg in d['config']: cfg.update(first=0, pulse=500, repeat=2, between=100)
    baseline = [deepcopy(d['states']) for d in c.devices]
    c.show_all_pulses()
    QTest.qWait(30)
    c.combinedRunButton.click()
    QTest.qWait(50)
    assert len(c.jobs) == 30 and c.combinedRunButton.text() == 'PAUSE ALL'
    c.combinedRunButton.click()
    paused = [deepcopy(d['states']) for d in c.devices]
    QTest.qWait(80)
    assert [d['states'] for d in c.devices] == paused
    assert all(j.get('paused') for j in c.jobs.values())
    assert c.combinedRunButton.text() == 'CONTINUE ALL'
    assert not c.set_global_mode('real')
    c.combinedRunButton.click()
    assert all(not j.get('paused') for j in c.jobs.values())
    c.combinedRunButton.click()
    c.combinedResetButton.click()
    assert not c.jobs
    assert [d['states'] for d in c.devices] == baseline
    c.combinedDialog.grab().save(str(_bootstrap.SCREENSHOTS / 'qa-all-pulses.png'))
    c.combinedDialog.close()
    for _ in range(3):
        c.open_discovery()
        c.new_connection()
        QTest.qWait(20)
        assert c.connectionEditor.isVisible()
        assert not c.connections.saveConnectionButton.isEnabled()
        c.discoveryDialog.close()
    c.manage.deviceTable.selectRow(0)
    c.remove_device()
    c.open_discovery()
    c.new_connection()
    c.connections.nameInput.setText('QA replacement')
    c.connections.ipInput.setText('192.0.2.5')
    c.connections.modeInput.setCurrentIndex(0)
    c.save_connection()
    assert len(c.devices) == 5 and c.devices[-1]['name'] == 'QA replacement'
    c.discoveryDialog.close()
    c.show_all_pulses()
    QTest.qWait(30)
    assert not c.combinedDialog.grab().isNull()
    c.combinedDialog.close()
    assert c.set_global_mode('real')
    assert not c.clients and all(not d['connected'] for d in c.devices)
    assert c.set_global_mode('simulation')
    assert all(d['connected'] for d in c.devices)
    c.hardware.close()
    c.window.close()
assert not errors, errors
print('PASS: five-unit workflows, combined/individual previews, 30 jobs, pause holds outputs, continue/reset baseline, mode guards, repeated add/edit dialogs and unit replacement')
