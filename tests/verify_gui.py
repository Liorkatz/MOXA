"""Exercise GUI simulation and render the principal pages."""
import _bootstrap  # Allow direct execution from the tests folder.
import sys
import traceback
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
import main
app=QApplication([])
app.setStyle('Fusion')
app.setStyleSheet(main.STYLE)
c=main.Controller()
c.window.show()

def check():
    try:
        assert c.window.screens.count()==3
        assert len(c.panels)==2
        assert not c.home.showAutomation.isChecked()
        assert all(p.automationGroup.isHidden() for p in c.panels)
        c.home.showAutomation.setChecked(True)
        assert all(not p.automationGroup.isHidden() for p in c.panels)
        c.home.showAutomation.setChecked(False)
        assert all(p.automationGroup.isHidden() for p in c.panels)
        p = c.panels[0]
        for i in range(1,7):
            assert p.manualColumn3.itemAtPosition(i,0).alignment() == main.Qt.AlignmentFlag.AlignCenter
            assert p.manualColumn4.itemAtPosition(i,0).alignment() == main.Qt.AlignmentFlag.AlignCenter
        p.continuousSelect1.setChecked(True)
        app.processEvents()
        before = p.turnOnButton.geometry()
        p.turnOnButton.click()
        app.processEvents()
        assert p.turnOnButton.geometry() == before
        assert p.turnOnButton.isChecked() and p.turnOnButton.text()=='Turn Off'
        p.turnOnButton.click()
        assert not p.turnOnButton.isChecked()
        assert c.manage.parentWidget() is c.connections.manageContainer
        d=c.devices[0]
        c.manual(d,0,True)
        assert d['states'][0]
        c.default_pulse=30
        c.pulse(d,0)
        QTimer.singleShot(100,after_pulse)
    except Exception: fail()

def after_pulse():
    try:
        assert not c.devices[0]['states'][0] and not c.jobs
        d=c.devices[0]
        for config in d['config']: config['active']=False
        d['config'][1].update(active=True,first=10,pulse=20,repeat=2,between=10)
        c.run_selected(d)
        QTimer.singleShot(200,after_routine)
    except Exception: fail()

def after_routine():
    try:
        assert not c.jobs and not any(c.devices[0]['states'])
        d=c.devices[0]
        d['config'][1]['trigger']=1
        c.run_selected(d)
        assert not c.jobs
        c.run_selected(d,True)
        assert c.jobs
        c.stop_device(d)
        for _ in range(3):
            c.new_connection()
            c.save_connection()
        assert len(c.devices)==5
        assert not c.connections.saveConnectionButton.isEnabled() if c.editing is None else True
        c.new_connection()
        c.save_connection()
        assert len(c.devices)==5
        c.connections.unitSelector.setCurrentIndex(0)
        original=c.devices[0]['ip']
        c.connections.ipInput.setText('invalid')
        c.save_connection()
        assert c.devices[0]['ip']==original
        c.connections.ipInput.setText('10.0.0.10')
        c.connections.nameInput.setText('Stage unit')
        c.save_connection()
        assert c.devices[0]['ip']=='10.0.0.10'
        assert c.panels[0].deviceName.text()=='Stage unit'
        c.logs.filterInput.setText('Connection settings saved')
        assert 'Connection settings saved' in c.logs.logOutput.toPlainText()
        c.logs.filterInput.clear()
        c.window.screens.setCurrentIndex(0)
        app.processEvents()
        app.processEvents()
        c.window.grab().save(str(_bootstrap.SCREENSHOTS/'gui_preview.png'))
        c.home.showAutomation.setChecked(True)
        app.processEvents()
        app.processEvents()
        c.window.grab().save(str(_bootstrap.SCREENSHOTS/'automation_preview.png'))
        c.home.showAutomation.setChecked(False)
        c.window.screens.setCurrentIndex(1)
        c.window.grab().save(str(_bootstrap.SCREENSHOTS/'connections_preview.png'))
        c.window.screens.setCurrentIndex(2)
        c.window.grab().save(str(_bootstrap.SCREENSHOTS/'log_preview.png'))
        for _ in range(5):
            c.manage.deviceTable.selectRow(0)
            c.remove_device()
        assert not c.devices
        c.new_connection()
        c.save_connection()
        assert len(c.devices)==1
        print('PASS: four screens, unified ADD MOXA, hidden automation, centered selections, toggle button, timed pulses, repeated automation, DI simulation, five-unit limit, connection validation/editing, logs, empty-device recovery.',flush=True)
        app.exit(0)
    except Exception: fail()

def fail():
    traceback.print_exc()
    app.exit(1)
QTimer.singleShot(300,check)
QTimer.singleShot(10000,lambda: app.exit(2))
sys.exit(app.exec())
