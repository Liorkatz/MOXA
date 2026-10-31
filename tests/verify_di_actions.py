"""Original layout plus DI action column; simulation only."""
import _bootstrap
from PySide6.QtWidgets import QApplication
import main
app=QApplication([])
c=main.Controller()
c.pollTimer.stop()
d,other=c.devices
assert c.window.screens.count()==3
assert c.panels[0].automationGroup.isAncestorOf(c.panels[0].trigger1)
assert c.panels[0].automationGroup.isAncestorOf(c.panels[0].diAction1)
for cfg in d['config']: cfg.update(active=False,first=0,pulse=1000,repeat=2,between=100)
cfg=d['config'][0]
cfg.update(active=True,trigger=1,action='start')
def edge(action):
    cfg['action']=action
    c.handle_input_sample(d,[False]*6)
    c.handle_input_sample(d,[True,False,False,False,False,False])
edge('start')
assert (d['uid'],0) in c.jobs and len(c.jobs)==1
edge('pause')
assert c.jobs[d['uid'],0].get('paused')
edge('continue')
assert not c.jobs[d['uid'],0].get('paused')
edge('off')
assert not c.jobs and not d['states'][0]
c.manual(other,0,True)
c.run_selected(other)
edge('reset')
assert not c.jobs and other['states'][0]
c.hardware.close()
c.window.deleteLater()
print('PASS: original layout, DI action column, start/off/pause/continue/reset')
