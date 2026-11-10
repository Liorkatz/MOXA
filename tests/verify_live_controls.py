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
assert [f for f, _ in peer.requests] == [5, 15, 5, 1]
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
c.start_momentary(p.pulse1, d, [0])
c.release_momentary(p.pulse1)
test_client.writes.clear()
wait_until(lambda: bool(test_client.writes))
assert test_client.writes == [{0: False}]

QTest.mousePress(p.pulse1, Qt.MouseButton.LeftButton)
wait_until(lambda: test_client.states[0])
app.sendEvent(c.window, QEvent(QEvent.Type.WindowDeactivate))
wait_until(lambda: not test_client.states[0])
QTest.mouseRelease(p.pulse1, Qt.MouseButton.LeftButton)

c.home.showAutomation.setChecked(True)
app.processEvents()
assert c.home.runBothButton.isVisible() and c.allPulsesButton.isVisible()
c.window.screens.setCurrentIndex(1)
assert c.home.runBothButton.isHidden() and c.allPulsesButton.isHidden() and c.resetBothButton.isHidden()
c.window.screens.setCurrentIndex(0)
assert c.home.runBothButton.isVisible() and c.allPulsesButton.isVisible()

# Automation still completes with batching and acknowledgement-based timing.
for cfg in d['config']: cfg.update(active=True, first=0, pulse=40, between=10, repeat=2, trigger=0)
test_client.writes.clear()
c.run_selected(d)
wait_until(lambda: not c.jobs)
assert not any(test_client.states)
assert any(len(values) == 6 and all(values.values()) for values in test_client.writes)
assert p.lampState == 'connected'

# Paused writes remain deferred; manual control cancels only its own channel.
for cfg in d['config']: cfg.update(pulse=150, repeat=1)
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
print('PASS: live controls, press/hold/release, group batching, sparse coil writes, SDK packing, ACK/readback validation, paused automation, connection lamp, page visibility, legacy migration and plain-IP scanning.')
