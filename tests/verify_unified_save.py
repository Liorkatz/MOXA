"""One configuration contains main controls, automation and app settings."""
import _bootstrap
from pathlib import Path
from tempfile import TemporaryDirectory
from PySide6.QtWidgets import QApplication
import main
app=QApplication([])
with TemporaryDirectory(dir=main.ROOT) as folder:
    path=Path(folder)/'complete.json'
    c=main.Controller(config_path=path)
    c.pollTimer.stop()
    d,p=c.devices[0],c.panels[0]
    c.manual(d,0,True)
    p.continuousSelect2.setChecked(True)
    p.momentarySelect3.setChecked(True)
    d['names'][0]='Saved channel'
    d['config'][1].update(trigger=2,action='pause',pulse=2222)
    c.home.askConfirm.setChecked(True)
    c.autoPulsePreview.setChecked(True)
    c.run_selected(d)
    assert c.save_file()
    loaded=main.Controller(config_path=path)
    loaded.pollTimer.stop()
    restored,panel=loaded.devices[0],loaded.panels[0]
    assert restored['states']==[True,False,False,False,False,False]
    assert restored['names'][0]=='Saved channel'
    assert restored['config'][1]['action']=='pause' and restored['config'][1]['pulse']==2222
    assert panel.continuousSelect2.isChecked() and panel.momentarySelect3.isChecked()
    assert loaded.home.askConfirm.isChecked() and loaded.autoPulsePreview.isChecked()
    assert not loaded.jobs and not loaded.clients
    assert not hasattr(loaded,'scenariosButton')
    for controller in (c,loaded):
        controller.saveTimer.stop()
        controller.autoPulseTimer.stop()
        controller.hardware.close()
        controller.window.deleteLater()
print('PASS: unified File save, Continuous baseline, manual selection, channel names, DI/automation settings and app preferences; no automatic run')
