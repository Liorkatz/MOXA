"""Scenario persistence and communication status checks; no hardware traffic."""
import _bootstrap  # Allow direct execution from the tests folder.
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic
from PySide6.QtWidgets import QApplication
import main

app=QApplication([])
app.setStyleSheet(main.STYLE)
c=main.Controller()
c.pollTimer.stop()
d=c.devices[0]
c.save_scenario('Test sequence')
saved=deepcopy(c.scenarios['Test sequence'])
d['config'][0]['pulse']=2222
d['names'][0]='Changed'
c.load_scenario('Test sequence')
assert d['config'][0]['pulse']==saved[0]['config'][0]['pulse']
assert d['names'][0]==saved[0]['names'][0]
assert not c.jobs and not any(d['states'])
c.run_selected(d)
for pause in (False,True):
    if pause: c.pause_device(d)
    try:
        c.load_scenario('Test sequence')
        raise AssertionError('Loading while running/paused accepted')
    except ValueError: pass
c.stop_device(d)
before=deepcopy(d['config'])
c.scenarios['Invalid']=deepcopy(saved)
c.scenarios['Invalid'][1]['config'][0]['pulse']=-1
try:
    c.load_scenario('Invalid')
    raise AssertionError('Invalid scenario accepted')
except ValueError: pass
assert d['config']==before
del c.scenarios['Invalid']
with TemporaryDirectory(dir=main.ROOT) as directory:
    path=Path(directory)/'settings.json'
    c.config_path=path
    c.save_configuration()
    loaded=main.Controller(config_path=path)
    loaded.pollTimer.stop()
    assert loaded.scenarios==c.scenarios
    loaded.devices.reverse()
    loaded.load_scenario('Test sequence')
    assert loaded.devices[1]['config']==saved[0]['config']
    assert not loaded.jobs
    loaded.saveTimer.stop()
    loaded.hardware.close()
    loaded.window.deleteLater()

assert c.communication_status(d)[0]=='simulation'
d.update(mode='real',connected=False)
assert c.communication_status(d)[0]=='disconnected'
d['connecting']=True
assert c.communication_status(d)[0]=='connecting'
d.update(connecting=False,connected=True,last_read='12:34:56',last_read_tick=monotonic())
assert c.communication_status(d)[0]=='connected'
assert '12:34:56' in c.communication_status(d)[2]
d['last_read_tick']=monotonic()-100
assert c.communication_status(d)[0]=='stale'
d.update(connected=False,error='Read timeout')
c.render()
assert c.communication_status(d)[0]=='error'
assert c.panels[0].connectionStatus.toolTip()=='Read timeout'
c.window.show()
c.show_scenarios()
app.processEvents()
c.scenarioDialog.grab().save(str(_bootstrap.SCREENSHOTS/'scenarios_preview.png'))
c.hardware.close()
print('PASS: scenario copy/load, restart persistence, IP matching, atomic validation, running/paused load prevention, no output activation; six communication states, last-read display and error details.')
