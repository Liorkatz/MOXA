"""Management rows/actions/settings never overlap at different unit counts."""
import _bootstrap
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
import main
app=QApplication([])
app.setStyleSheet(main.STYLE)
c=main.Controller()
c.pollTimer.stop()
c.window.screens.setCurrentIndex(1)
c.window.show()
for count in range(6):
    c.devices=[c.new_device(i+1) for i in range(count)]
    c.rebuild()
    c.window.screens.setCurrentIndex(1)
    for height in (650,950):
        c.window.resize(1100,height)
        QTest.qWait(40)
        table=c.manage.deviceTable
        button=c.manage.editDeviceButton
        assert button.y() > table.geometry().bottom(), (count,height,table.geometry(),button.geometry())
        assert c.manage.deviceHint.y() > button.geometry().bottom()
        assert c.settings.y() >= c.connections.manageContainer.geometry().bottom()
        assert table.viewport().height() >= sum(table.rowHeight(i) for i in range(count))
        section=c.home.askConfirm.parentWidget()
        assert section.height() >= section.minimumSizeHint().height()
        for child in section.findChildren(main.QLabel):
            assert child.height() >= child.sizeHint().height()
        assert c.autoPulsePreview.geometry().bottom() < c.settings.height()
        c.connectionsScroll.ensureWidgetVisible(c.home.askConfirm)
        QTest.qWait(10)
        assert c.home.askConfirm.isVisible()
c.window.grab().save(str(_bootstrap.SCREENSHOTS/'qa-manage-layout.png'))
c.hardware.close()
c.window.close()
print('PASS: 0–5 units, 650/950px windows, all rows visible, no table/action/settings overlap')
