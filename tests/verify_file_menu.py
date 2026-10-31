"""File Save/Save As/Open, atomic invalid Open and no automatic LIVE connect."""
import _bootstrap
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox
import main
app=QApplication([])
with TemporaryDirectory(dir=main.ROOT) as folder:
    root=Path(folder)
    c=main.Controller(config_path=root/'current.json')
    c.pollTimer.stop()
    assert c.save_file()
    c.devices[0]['name']='Saved copy'
    with patch.object(QFileDialog,'getSaveFileName',return_value=(str(root/'copy.json'),'')):
        assert c.save_file_as()
    assert c.config_path==root/'copy.json'
    assert main.config_store.load(root/'copy.json')['devices'][0]['name']=='Saved copy'
    assert main.config_store.load(root/'current.json')['devices'][0]['name']!='Saved copy'
    c.devices[0]['name']='Changed'
    assert c.save_file()
    assert main.config_store.load(root/'copy.json')['devices'][0]['name']=='Changed'
    with patch.object(QFileDialog,'getOpenFileName',return_value=(str(root/'current.json'),'')):
        assert c.open_configuration_file()
    assert c.devices[0]['name']!='Changed'
    before=deepcopy(c.devices)
    (root/'bad.json').write_text('{broken',encoding='utf-8')
    with patch.object(QFileDialog,'getOpenFileName',return_value=(str(root/'bad.json'),'')), patch.object(QMessageBox,'warning'):
        assert not c.open_configuration_file()
    assert c.devices==before and c.config_path==root/'current.json'
    assert (root/'bad.json').read_text()=='{broken'
    c.run_selected(c.devices[0])
    with patch.object(QMessageBox,'information'), patch.object(QFileDialog,'getOpenFileName') as dialog:
        assert not c.open_configuration_file()
        assert not dialog.called
    for d in c.devices: c.stop_device(d)
    c.set_global_mode('real')
    assert c.save_file()
    with patch.object(QFileDialog,'getOpenFileName',return_value=(str(root/'current.json'),'')):
        assert c.open_configuration_file()
    assert not c.clients and not c.jobs
    assert all(not d['connected'] for d in c.devices)
    assert len(c.window.menuBar().actions()[0].menu().actions())==3
    c.saveTimer.stop()
    c.hardware.close()
    c.window.deleteLater()
print('PASS: File menu, Save/Save As/Open, invalid-file preservation, active-job guard and disconnected LIVE load')
