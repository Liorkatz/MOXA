"""Simulation controls disappear in LIVE and return in simulation."""
import _bootstrap
from PySide6.QtWidgets import QApplication
import main
app=QApplication([])
c=main.Controller()
c.pollTimer.stop()
c.set_global_mode('real')
assert c.addSimulationButton.isHidden()
for p in c.panels:
    assert p.diSimulationHeader.isHidden()
    assert all(getattr(p,f'simulateDI{i}').isHidden() for i in range(1,7))
c.set_global_mode('simulation')
assert not c.addSimulationButton.isHidden()
for p in c.panels:
    assert not p.diSimulationHeader.isHidden()
    assert all(not getattr(p,f'simulateDI{i}').isHidden() for i in range(1,7))
for _ in range(2):
    c.manage.deviceTable.selectRow(0)
    c.remove_device()
c.set_global_mode('real')
assert c.emptySimulationButton.isHidden()
c.set_global_mode('simulation')
assert not c.emptySimulationButton.isHidden()
c.hardware.close()
c.window.deleteLater()
print('PASS: LIVE hides simulation controls, including empty state; simulation restores them')
