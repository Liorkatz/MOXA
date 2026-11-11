"""Live UI/queue and protocol regression tests; no physical relays are accessed."""
import _bootstrap
import ctypes
import struct
import tempfile
import time
from pathlib import Path
from unittest.mock import patch
from PySide6.QtCore import Qt, QEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton
import main
import config_store
from moxa_backend import E1214Client, AuthenticatedE1214, ModbusError
from network_discovery import DiscoveryDialog


class Peer:
    def __init__(self):
        self.states = [False] * 6
        self.requests = []
        self.bad_ack = False
        self.bad_read = False

    def request(self, function, payload):
        self.requests.append((function, payload))
        if function == 5:
            start, value = struct.unpack('>HH', payload)
            self.states[start] = bool(value)
            return b'bad' if self.bad_ack else payload
        if function == 15:
            start, count, size, bits = struct.unpack('>HHBB', payload)
            assert size == 1 and start + count <= 6
            for n in range(count): self.states[start+n] = bool(bits & (1 << n))
            return b'bad' if self.bad_ack else payload[:4]
        assert function == 1
        bits = sum(int(v) << i for i, v in enumerate(self.states))
        return bytes([1, bits ^ (1 if self.bad_read else 0)])


peer = Peer()
client = E1214Client('127.0.0.1')
client.request = peer.request
assert all(client.write_many(dict.fromkeys(range(6), True)))
assert [f for f, _ in peer.requests] == [15, 1]
peer.requests.clear()
client.write_many({0: False, 2: False, 3: False, 5: False})
assert peer.states == [False, True, False, False, True, False]
# Sparse selected relays share one FC15; the gap comes from a live FC1 read.
assert [f for f, _ in peer.requests] == [1, 15, 1]
start, count, size, bits = struct.unpack('>HHBB', peer.requests[1][1])
assert (start, count, size, bits) == (0, 6, 1, 0b010010)
peer.requests.clear()
client.write_many({2: True})
assert [f for f, _ in peer.requests] == [5, 1]
peer.requests.clear()
client.write_many({0: True, 2: False, 5: True})
assert peer.states == [True, True, False, False, True, True]
assert [f for f, _ in peer.requests] == [1, 15, 1]
assert struct.unpack('>HHBB', peer.requests[1][1]) == (0, 6, 1, 0b110011)
peer.requests.clear()
for flag in ('bad_ack', 'bad_read'):
    setattr(peer, flag, True)
    try:
        client.write_many({0: True, 1: True})
        raise AssertionError('Malformed acknowledgement/readback accepted')
    except ModbusError: pass
    setattr(peer, flag, False)


class SDK:
    def __init__(self):
        self.states = [False] * 6
        self.writes = []
    def MXIO_WriteCoils(self, handle, start, count, data):
        self.writes.append((start, count, data[0]))
        for n in range(count): self.states[start+n] = bool(data[0] & (1 << n))
        return 0
    def MXIO_ReadCoils(self, handle, function, start, count, data):
        data[0] = sum(int(v) << i for i, v in enumerate(self.states))
        return 0


sdk = SDK()
auth = AuthenticatedE1214('127.0.0.1', 502, 1000, 'test')
auth.handle = 1
with patch.object(auth, 'sdk', return_value=sdk):
    assert all(auth.write_many(dict.fromkeys(range(6), True)))
    assert sdk.writes == [(0, 6, 63)]
    auth.write_many({0: False, 2: False})
    assert sdk.states == [False, True, False, True, True, True]
    assert sdk.writes[-1] == (0, 3, 0b010), 'One SDK write must keep skipped channel 1 ON'
    auth.write_many({0: True, 3: False, 5: True})
    assert sdk.states == [True, True, False, False, True, True]
    assert sdk.writes[-1] == (0, 6, 0b110011), 'Mixed ON/OFF changes must use one SDK write'


class TestClient:
    def __init__(self):
        self.states = [False] * 6
        self.writes = []
    def connect(self): return dict(model='E1214 test peer', states=list(self.states), inputs=[False]*6)
    def bits(self, function): return list(self.states) if function == 1 else [False]*6
    def write_many(self, values):
        self.writes.append(dict(values))
        for i, value in values.items(): self.states[i] = value
        return list(self.states)


# Two read requests use one TCP connection; close releases it.
class FakeSocket:
    def __init__(self):
        self.pending = b''
        self.closed = False

    def settimeout(self, timeout):
        pass

    def sendall(self, packet):
        transaction, protocol, length, unit = struct.unpack('>HHHB', packet[:7])
        assert protocol == 0 and packet[7] == 1
        response = bytes([1, 1, 0])
        self.pending += struct.pack('>HHHB', transaction, 0, len(response) + 1, unit) + response

    def recv(self, n):
        result, self.pending = self.pending[:n], self.pending[n:]
        return result

    def close(self):
        self.closed = True


fake_socket = FakeSocket()
with patch('moxa_backend.socket.create_connection', return_value=fake_socket) as connect:
    persistent_client = E1214Client('127.0.0.1')
    assert persistent_client.bits(1) == [False] * 6
    assert persistent_client.bits(1) == [False] * 6
    assert connect.call_count == 1
    persistent_client.close()
    assert fake_socket.closed

app = QApplication([])
app.setStyle('Fusion')
app.setStyleSheet(main.STYLE)
c = main.Controller()
c.pollTimer.stop()
assert c.devices == []
d = c.new_device(1)
c.devices = [d]
c.rebuild()
c.window.show()
app.processEvents()
p = c.panels[0]
QTest.qWait(125)
def debug_layout(stage):
    bar = c.toolbarScroll.mapTo(c.window, c.toolbarScroll.rect().topLeft()).y()
    brand = c.window.brandLabel.mapTo(c.window, c.window.brandLabel.rect().bottomLeft()).y()
    viewport = c.home.devicesScroll.viewport()
    card = p.mapTo(viewport, p.rect().topLeft())
    report = (f'LAYOUT {stage}: window={c.window.width()}x{c.window.height()}, '
              f'brand_bottom={brand}, toolbar_top={bar}, gap={bar-brand}, '
              f'viewport={viewport.width()}x{viewport.height()}, '
              f'panel={p.width()}x{p.height()} at ({card.x()},{card.y()}), '
              f'manual={p.manualGroup.height()}, '
              f'hscroll={c.home.devicesScroll.horizontalScrollBar().maximum()}, '
              f'vscroll={c.home.devicesScroll.verticalScrollBar().maximum()}')
    print(report, flush=True)
    return bar - brand
gap = debug_layout('initial compact home')
compact_width = c.window.width()
assert [a.text().replace('&', '') for a in c.window.menuBar().actions()][:4] == [
    'File', 'Home', 'Settings', 'LOG'], 'File / page navigation must share the top menu row'
assert all(a.isVisible() for a in c.navigationButtons)
assert c.navigationButtons[0].isChecked()
assert c.window.brandLabel.height() <= 50, 'Brand area expands vertically above navigation'
assert gap < 70, 'Large empty area above Home toolbar after launch'
assert c.home.devicesScroll.horizontalScrollBar().maximum() == 0, 'Home card extends horizontally beyond viewport'
assert c.home.devicesScroll.verticalScrollBar().maximum() <= 10, 'Six output rows are clipped on startup'
assert not d['connected']
assert not any('simulat' in b.text().lower() for b in c.window.findChildren(QPushButton))
assert not c.manage.toggleDeviceButton.isVisible()


def wait_until(predicate):
    end = time.monotonic() + 3
    while not predicate() and time.monotonic() < end:
        app.processEvents()
        time.sleep(.002)
    assert predicate()


test_client = TestClient()
with patch.object(main, 'make_client', return_value=test_client):
    QTest.mouseClick(p.connectButton, Qt.MouseButton.LeftButton)
    wait_until(lambda: d['connected'])
assert p.lampState == 'connected' and '#20c875' in p.runLamp.styleSheet()
assert p.connectButton.text() == 'Disconnect'
# Solid green stays visible without any automation jobs.
c.render()
assert p.lampState == 'connected' and '#20c875' in p.runLamp.styleSheet()
assert test_client.writes == []  # Connecting must remain read-only.

# ON occurs during the press, stays ON, and OFF follows release.
QTest.mousePress(p.pulse1, Qt.MouseButton.LeftButton)
wait_until(lambda: test_client.states[0])
QTest.qWait(70)
assert test_client.states[0]
QTest.mouseRelease(p.pulse1, Qt.MouseButton.LeftButton)
wait_until(lambda: not test_client.states[0])

# The group selection is captured at press time, even if changed before release.
for i in (1, 2, 3): getattr(p, f'momentarySelect{i}').setChecked(True)
test_client.writes.clear()
QTest.mousePress(p.momentaryButton, Qt.MouseButton.LeftButton)
wait_until(lambda: all(test_client.states[:3]))
assert test_client.writes == [{0: True, 1: True, 2: True}]
p.momentarySelect2.setChecked(False)
QTest.mouseRelease(p.momentaryButton, Qt.MouseButton.LeftButton)
wait_until(lambda: not any(test_client.states))
assert test_client.writes[-1] == {0: False, 1: False, 2: False}

# Releasing before a queued ON starts must not produce a delayed ON.
# Clear only previous writes, BEFORE issuing the press/release sequence.
test_client.writes.clear()
c.start_momentary(p.pulse1, d, [0])
c.release_momentary(p.pulse1)
wait_until(lambda: bool(test_client.writes))
assert test_client.writes == [{0: False}]

QTest.mousePress(p.pulse1, Qt.MouseButton.LeftButton)
wait_until(lambda: test_client.states[0])
app.sendEvent(c.window, QEvent(QEvent.Type.WindowDeactivate))
wait_until(lambda: not test_client.states[0])
QTest.mouseRelease(p.pulse1, Qt.MouseButton.LeftButton)

c.home.showAutomation.setChecked(True)
app.processEvents()
for row in range(1, 7):
    for prefix in ('firstDelay', 'pulseTime', 'repeat', 'between'):
        field = getattr(p, f'{prefix}{row}')
        assert field.alignment() & Qt.AlignmentFlag.AlignRight, (
            f'{prefix}{row} must display numeric content aligned to the right'
        )
    for col in range(8):
        item = p.automationGrid.itemAtPosition(row, col)
        if item is not None:
            assert item.alignment() & Qt.AlignmentFlag.AlignRight, (
                f'Automation grid row {row}, column {col} must align right'
            )
assert c.home.runBothButton.isVisible() and c.allPulsesButton.isVisible()
toolbar = c.homeToolbar.layout()
assert toolbar.indexOf(c.home.runBothButton) + 1 == toolbar.indexOf(c.allPulsesButton), (
    'RUN ALL must be immediately next to All pulses on the right side'
)
assert toolbar.indexOf(c.home.runBothButton) > toolbar.indexOf(c.automationLegend), (
    'RUN ALL must be after the centered legend, not next to LOG'
)
c.window.screens.setCurrentIndex(1)
QTest.qWait(85)
assert c.navigationButtons[1].isChecked() and not c.navigationButtons[0].isChecked()
assert c.window.width() == compact_width, 'Settings must retain compact Home width'
assert c.home.runBothButton.isHidden() and c.allPulsesButton.isHidden() and c.resetBothButton.isHidden()
assert c.settingsColumns.count() == 2
assert c.settingsUnitsGroup.isVisible() and c.settingsRightPane.isVisible()
assert c.settingsUnitsGroup.mapTo(c.window, c.settingsUnitsGroup.rect().topLeft()).x() < (
    c.settingsRightPane.mapTo(c.window, c.settingsRightPane.rect().topLeft()).x()
), 'Unit management must be to the left of application settings'
assert c.simulationCheckbox.parentWidget() is c.simulationSettingsGroup
assert c.settings.parentWidget() is c.applicationSettingsGroup
assert c.window.minimumHeight() <= 300, 'Settings must not force a 650px minimum height'
c.window.screens.setCurrentIndex(0)
c.home.showAutomation.setChecked(False)
QTest.qWait(85)
assert c.window.width() == compact_width, 'Closing automation must restore compact width'
assert not c.window.isMaximized()
assert c.window.minimumHeight() <= 300, 'Compact Home must not inherit the Settings minimum height'
assert c.window.height() <= max(320, c.window.screen().availableGeometry().height() - 40)
c.home.showAutomation.setChecked(True)
app.processEvents()
assert c.home.runBothButton.isVisible() and c.allPulsesButton.isVisible()
QTest.qWait(135)
c.home.showAutomation.setChecked(False)
QTest.qWait(100)
assert not c.window.isMaximized(), 'Automation OFF must restore the normal window'
assert c.window.width() == compact_width, 'Automation OFF must return to the same compact width'
assert c.home.devicesScroll.horizontalScrollBar().maximum() == 0
c.navigationButtons[1].trigger()
QTest.qWait(90)
assert c.window.screens.currentIndex() == 1
assert c.window.width() == compact_width
c.navigationButtons[2].trigger()
assert c.window.screens.currentIndex() == 2, 'LOG must be available from the menu bar'
c.navigationButtons[0].trigger()
assert c.window.screens.currentIndex() == 0
QTest.qWait(90)
assert c.window.width() == compact_width

# User-expanded Home must remain maximized through Settings and LOG.
c.window.showMaximized()
QTest.qWait(80)
assert c.window.isMaximized()
for page_index in (1, 2, 0):
    c.navigationButtons[page_index].trigger()
    QTest.qWait(85)
    assert c.window.screens.currentIndex() == page_index
    assert c.window.isMaximized(), 'Page navigation must preserve user-selected maximized window'
c.window.showNormal()
QTest.qWait(70)
assert not c.window.isMaximized()

# Automation's expanded window is also retained when visiting Settings.
# Explicitly turning automation OFF is the only operation that compacts it.
c.home.showAutomation.setChecked(True)
QTest.qWait(135)
assert c.window.isMaximized()
c.navigationButtons[1].trigger()
QTest.qWait(80)
assert c.window.isMaximized(), 'Settings must not unmaximize an expanded automation window'
c.navigationButtons[0].trigger()
QTest.qWait(80)
assert c.window.isMaximized()
c.home.showAutomation.setChecked(False)
QTest.qWait(100)
assert not c.window.isMaximized()
assert c.window.width() == compact_width
c.home.showAutomation.setChecked(True)
app.processEvents()

# Automation still completes with batching and acknowledgement-based timing.
for cfg in d['config']: cfg.update(active=True, first=0, pulse=40, between=10, repeat=2, trigger=0)
test_client.writes.clear()
c.run_selected(d)
wait_until(lambda: not c.jobs)
assert not any(test_client.states)
assert any(len(values) == 6 and all(values.values()) for values in test_client.writes)
assert p.lampState == 'connected'

# Same-time transitions for non-adjacent channels must share one batch.
# Keep channel 2 ON before the run: inactive relays must be preserved.
c.manual(d, 1, True)
wait_until(lambda: test_client.states[1])
for i, cfg in enumerate(d['config']):
    cfg.update(active=i in (0, 2, 4), first=0, pulse=50, between=20, repeat=2, trigger=0)
test_client.writes.clear()
c.run_selected(d)
wait_until(lambda: not c.jobs)
assert test_client.states == [False, True, False, False, False, False]
expected_on = {0: True, 2: True, 4: True}
expected_off = {0: False, 2: False, 4: False}
assert sum(values == expected_on for values in test_client.writes) == 2, test_client.writes
assert sum(values == expected_off for values in test_client.writes) >= 2, test_client.writes

# Different scheduled instants must not be merged merely because they share a unit.
c.manual(d, 1, False)
wait_until(lambda: not test_client.states[1])
for i, cfg in enumerate(d['config']):
    cfg.update(active=i in (0, 4), first=0 if i == 0 else 80,
               pulse=30, between=0, repeat=1, trigger=0)
test_client.writes.clear()
c.run_selected(d)
wait_until(lambda: not c.jobs)
assert {0: True} in test_client.writes and {4: True} in test_client.writes, test_client.writes
assert not any(values.get(0) and values.get(4) for values in test_client.writes)

# Paused writes remain deferred; manual control cancels only its own channel.
for cfg in d['config']: cfg.update(active=True, first=0, pulse=150, repeat=1)
c.run_selected(d)
c.pause_device(d)
QTest.qWait(30)
assert c.jobs and all(j.get('paused') for j in c.jobs.values())
c.continue_or_run(d)
wait_until(lambda: all(test_client.states))
c.manual(d, 0, False)
assert (d['uid'], 0) not in c.jobs and len(c.jobs) == 5
wait_until(lambda: not c.jobs)
assert not any(test_client.states)

# A physical DI1 rising edge can start ALL six active channels, even when
# another output uses DI3 or has no trigger. Ignore a repeat edge while running.
for cfg in d['config']:
    cfg.update(active=True, first=0, pulse=80, between=0,
               repeat=1, trigger=0, action='start')
d['config'][0].update(trigger=1, action='start_all')
d['config'][1].update(trigger=1, action='start')
d['config'][2].update(trigger=3, action='start')
c.render()
assert p.trigger3.findData(1) >= 0 and p.trigger3.findData(6) >= 0
assert p.diAction1.currentData() == 'start_all'
test_client.writes.clear()
d['inputs'] = [False] * 6
c.handle_input_sample(d, [True, False, False, False, False, False])
assert len(c.jobs) == 6
c.handle_input_sample(d, [False] * 6)
c.handle_input_sample(d, [True, False, False, False, False, False])
assert len(c.jobs) == 6, 'A second edge must not restart an active unit run'
wait_until(lambda: not c.jobs)
assert not any(test_client.states)
assert any(len(values) == 6 and all(values.values())
           for values in test_client.writes), test_client.writes

with tempfile.TemporaryDirectory() as tmp:
    path = Path(tmp) / 'settings.json'
    old = dict(d, mode='simulation')
    config_store.save(path, [old], 0, True, default_mode='simulation')
    loaded = main.Controller(config_path=path)
    loaded.pollTimer.stop()
    assert loaded.devices[0]['mode'] == 'real' and not loaded.devices[0]['connected']
    loaded.hardware.close()
    loaded.window.close()

dialog = DiscoveryDialog([])
assert '/' not in dialog.network.currentText()
with patch('network_discovery.ScanWorker') as worker:
    dialog.network.setEditText('192.168.12.34')
    dialog.start_scan()
    hosts = worker.call_args.args[0]
    assert len(hosts) == 254 and hosts[0] == '192.168.12.1' and hosts[-1] == '192.168.12.254'
dialog.worker = None
dialog.close()

# Disconnecting while holding waits for OFF before removing the client.
c.start_momentary(p.pulse1, d, [0])
wait_until(lambda: test_client.states[0])
c.toggle_connection(d)
wait_until(lambda: not d['connected'])
assert not test_client.states[0] and p.lampState == 'idle'
# Closing also drains a pending momentary OFF instead of cancelling it.
d['connected'] = True
c.clients[d['uid']] = test_client
c.start_momentary(p.pulse1, d, [0])
wait_until(lambda: test_client.states[0])
c.window.close()
c.hardware.close()
deadline = time.monotonic() + 3
while test_client.states[0] and time.monotonic() < deadline: time.sleep(.002)
assert not test_client.states[0]
print('PASS: live controls, press/hold/release, one FC15 for sparse relays, SDK preservation of gaps, same-time automation batching, separate-time transitions, ACK/readback validation, paused automation, connection lamp, page visibility, legacy migration and plain-IP scanning.')
