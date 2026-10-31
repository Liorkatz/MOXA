"""Hidden automation actions must not reserve manual-mode window width."""
import _bootstrap
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from PySide6.QtCore import Qt
import main

app = QApplication([])
app.setStyle('Fusion')
app.setStyleSheet(main.STYLE)
c = main.Controller()
c.pollTimer.stop()
c.window.show()
QTest.qWait(150)
initial = c.window.width()
assert not c.resetBothButton.sizePolicy().retainSizeWhenHidden()
for _ in range(3):
    c.home.showAutomation.setChecked(True)
    QTest.qWait(120)
    assert c.resetBothButton.sizePolicy().retainSizeWhenHidden()
    assert c.window.isMaximized() and not c.window.isFullScreen()
    assert c.window.windowFlags() & Qt.WindowType.WindowCloseButtonHint
    assert c.window.windowFlags() & Qt.WindowType.WindowTitleHint
    area = c.window.screen().availableGeometry()
    frame = c.window.frameGeometry()
    # The close button sits just inside the right edge of the title bar.
    assert area.contains(frame.right() - 25, frame.top() + 20)
    c.home.showAutomation.setChecked(False)
    QTest.qWait(150)
    assert not c.resetBothButton.sizePolicy().retainSizeWhenHidden()
    assert c.window.width() == initial
assert c.home.devicesScroll.viewport().width() - c.panels[0].width() < 100
for delay in (0, 10, 40, 110):
    c.home.showAutomation.click()
    QTest.qWait(delay)
    c.home.showAutomation.click()
    QTest.qWait(delay)
    c.home.showAutomation.click()
    QTest.qWait(250)
    assert c.home.showAutomation.isChecked()
    assert c.window.isMaximized(), f'Not maximized after rapid toggle ({delay}ms)'
    assert c.window.maximumWidth() == 16777215
    assert c.window.width() >= c.window.screen().availableGeometry().width() - 20
    c.home.showAutomation.click()
    QTest.qWait(150)
    assert c.window.width() == initial
c.hardware.close()
c.window.close()
print(f'PASS: compact width {initial}px, repeated automation toggles and action reservation')
