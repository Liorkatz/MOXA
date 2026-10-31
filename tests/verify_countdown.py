"""RUN countdown, cancellation, one/all routing and configuration persistence."""
import _bootstrap
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic
from PySide6.QtWidgets import QApplication
import main
app=QApplication([])
c=main.Controller()
c.pollTimer.stop()
c.countdownEnabled.setChecked(True)
c.countdownSeconds.setValue(3)
d=c.devices[0]
c.automation_action(d)
assert c.countdownDialog is not None and not c.jobs and not any(d['states'])
assert c.countdownNumber.text()=='3'
c.countdownDeadline=monotonic()+1.5
c.tick_countdown()
assert c.countdownNumber.text()=='2'
c.countdownDeadline=monotonic()+0.5
c.tick_countdown()
assert c.countdownNumber.text()=='1'
c.cancel_countdown()
assert c.countdownDialog is None and not c.jobs
c.automation_action(d)
c.countdownDeadline=monotonic()-1
c.tick_countdown()
assert len(c.jobs)==6 and c.countdownDialog is None
c.automation_action(d)
assert all(j.get('paused') for j in c.jobs.values())
c.automation_action(d)
assert c.countdownDialog is None and all(not j.get('paused') for j in c.jobs.values())
c.stop_device(d)
c.run_both()
assert c.countdownDialog is not None and not c.jobs
c.countdownDeadline=monotonic()-1
c.tick_countdown()
assert len(c.jobs)==12
for unit in c.devices: c.stop_device(unit)
with TemporaryDirectory(dir=main.ROOT) as folder:
    c.config_path=Path(folder)/'settings.json'
    c.countdownSeconds.setValue(7)
    assert c.save_configuration()
    loaded=main.Controller(config_path=c.config_path)
    loaded.pollTimer.stop()
    assert loaded.countdownEnabled.isChecked() and loaded.countdownSeconds.value()==7
    loaded.saveTimer.stop()
    loaded.hardware.close()
    loaded.window.deleteLater()
c.config_path=None
c.hardware.close()
c.window.deleteLater()
print('PASS: 3/2/1 before outputs, cancel, RUN/RUN ALL, no countdown on resume and persisted settings')
