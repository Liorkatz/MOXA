"""Optional automatic previews: unit, all units and manual exclusion."""
import _bootstrap
from unittest.mock import patch
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
import main
app=QApplication([])
c=main.Controller()
c.pollTimer.stop()
d=c.devices[0]
with patch.object(c,'show_preview') as unit, patch.object(c,'show_all_pulses') as combined:
    c.run_selected(d)
    QTest.qWait(20)
    assert not unit.called and not combined.called
    c.stop_device(d)
    c.autoPulsePreview.setChecked(True)
    c.run_selected(d)
    QTest.qWait(20)
    unit.assert_called_once()
    assert not combined.called
    for device in c.devices: c.stop_device(device)
    unit.reset_mock()
    c.run_both()
    QTest.qWait(20)
    combined.assert_called_once()
    assert not unit.called
    for device in c.devices: c.stop_device(device)
    combined.reset_mock()
    c.pulse(d,0)
    QTest.qWait(20)
    assert not combined.called and not unit.called
c.hardware.close()
c.window.deleteLater()
print('PASS: optional auto pulse preview, single/combined routing and manual pulse exclusion')
