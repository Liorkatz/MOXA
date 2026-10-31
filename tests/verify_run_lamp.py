"""Run lamp follows automation lifecycle, excluding manual output control."""
import _bootstrap
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
import main
app=QApplication([])
c=main.Controller()
c.pollTimer.stop()
d=c.devices[0]
p,other=c.panels
assert p.deviceBar.indexOf(p.runLamp)==p.deviceBar.indexOf(p.connectionStatus)+1
assert c.lampState=='idle' and not c.lampTimer.isActive()
c.manual(d,0,True)
assert c.lampState=='idle'
c.run_selected(d)
assert c.lampState=='running' and c.lampLit and c.lampTimer.isActive()
assert p.lampState=='running' and p.lampLit
assert other.lampState=='idle' and not other.lampLit
QTest.qWait(500)
assert not c.lampLit
assert not p.lampLit and other.lampState=='idle'
c.pause_device(d)
assert c.lampState=='paused' and not c.lampTimer.isActive()
assert p.lampState=='paused' and other.lampState=='idle'
c.continue_or_run(d)
assert c.lampState=='running' and c.lampTimer.isActive()
c.stop_device(d)
assert c.lampState=='idle' and not c.lampLit and not c.lampTimer.isActive()
assert p.lampState=='idle' and not p.lampLit
for cfg in d['config']: cfg.update(active=False)
d['config'][0].update(active=True,first=0,pulse=5,between=0,repeat=1)
c.run_selected(d)
QTest.qWait(150)
assert not c.jobs and c.lampState=='idle'
c.hardware.close()
c.window.deleteLater()
print('PASS: lamp idle, running blink, pause, resume, reset and natural completion')
