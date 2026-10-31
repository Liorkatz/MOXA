"""Only matching DI selectable, individual simulation and no LIVE simulation."""
import _bootstrap
from PySide6.QtWidgets import QApplication
import main
app=QApplication([])
c=main.Controller()
c.pollTimer.stop()
d=c.devices[0]
p=c.panels[0]
for i in range(6):
    selector=getattr(p,f'trigger{i+1}')
    assert selector.count()==2
    assert selector.itemText(1)==f'DI {i+1}'
    selector.setCurrentIndex(1)
    assert d['config'][i]['trigger']==i+1
    d['config'][i].update(first=0,pulse=1000,repeat=3)
getattr(p,'simulateDI3').click()
assert set(c.jobs)=={(d['uid'],2)}
assert not any(d['inputs'])
c.stop_device(d)
getattr(p,'simulateDI6').click()
assert set(c.jobs)=={(d['uid'],5)}
c.stop_device(d)
c.set_global_mode('real')
assert all(not getattr(p,f'simulateDI{i+1}').isEnabled() for i in range(6))
assert p.triggerButton.isHidden()
c.hardware.close()
c.window.deleteLater()
print('PASS: matching DI only, independent simulation per channel, LIVE disabled and bulk simulation hidden')
