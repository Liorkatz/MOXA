"""Autosave feedback, persistence and close-failure choices."""
import _bootstrap
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtTest import QTest
import main
app=QApplication([])
with TemporaryDirectory(dir=main.ROOT) as folder:
    path=Path(folder)/'settings.json'
    c=main.Controller(config_path=path)
    c.pollTimer.stop()
    c.home.askConfirm.setChecked(True)
    assert c.saveStatus.text()=='Saving…'
    QTest.qWait(550)
    assert c.saveStatus.text()=='Saved' and path.exists()
    assert c.home.askConfirm.parentWidget().objectName()=='confirmationSection'
    restored=main.Controller(config_path=path)
    restored.pollTimer.stop()
    assert restored.home.askConfirm.isChecked()
    restored.saveTimer.stop()
    restored.hardware.close()
    restored.window.deleteLater()
    with patch.object(main.config_store,'save',side_effect=OSError('Disk unavailable')):
        assert not c.save_configuration()
        assert c.saveStatus.text()=='Save failed'
        with patch.object(QMessageBox,'warning',return_value=QMessageBox.StandardButton.Cancel) as warning:
            event=QEvent(QEvent.Type.Close)
            assert c.saveCloseGuard.eventFilter(c.window,event)
            assert not event.isAccepted()
            warning.assert_called_once()
        with patch.object(QMessageBox,'warning',return_value=QMessageBox.StandardButton.Discard):
            assert not c.saveCloseGuard.eventFilter(c.window,QEvent(QEvent.Type.Close))
            assert c.closeHandled
    assert c.save_configuration() and c.saveStatus.text()=='Saved'
    c.saveTimer.stop()
    c.hardware.close()
    c.window.deleteLater()
print('PASS: saving/saved/failed, confirmation persistence, separate section, close cancel/discard and recovery')
