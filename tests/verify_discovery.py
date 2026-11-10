"""Verify discovery protocol handling and form integration without network traffic."""
import _bootstrap  # Allow direct execution from the tests folder.
import struct
from unittest.mock import patch
from PySide6.QtWidgets import QApplication
import network_discovery as discovery
import main


class Reply:
    def __init__(self, body, transaction=1):
        self.data = struct.pack('>HHHB', transaction, 0, len(body)+1, 1) + body
        self.sent = None

    def __enter__(self): return self
    def __exit__(self, *args): pass
    def settimeout(self, timeout): pass
    def sendall(self, data): self.sent = data
    def recv(self, count):
        part, self.data = self.data[:min(count, 3)], self.data[min(count, 3):]
        return part


body = bytes.fromhex('2b0e0101000002') + b'\x00\x04MOXA\x01\x07E1214-X'
reply = Reply(body)
with patch.object(discovery.socket, 'create_connection', return_value=reply):
    result = discovery.probe('192.168.1.20', 502)
assert result['identity'] == 'MOXA · E1214-X'
assert reply.sent == bytes.fromhex('000100000005012b0e0100')
with patch.object(discovery.socket, 'create_connection', return_value=Reply(b'\xab\x01')):
    assert 'unverified' in discovery.probe('192.168.1.20', 502)['identity']
with patch.object(discovery.socket, 'create_connection', return_value=Reply(body, transaction=9)):
    assert discovery.probe('192.168.1.20', 502) is None
with patch.object(discovery.socket, 'create_connection', side_effect=TimeoutError):
    assert discovery.probe('192.168.1.20', 502) is None

app = QApplication([])
c = main.Controller()
dialog = discovery.DiscoveryDialog(c.devices)
assert dialog.table.rowCount() == len(c.devices)
dialog.add_result(result)
dialog.add_result(result)
assert dialog.table.rowCount() == len(c.devices) + 1
assert dialog.table.item(len(c.devices), 3).text().startswith('New')
c.use_discovered(result)
assert c.editing is None and c.connections.ipInput.text() == result['ip']
c.save_connection()
assert any(d['ip'] == result['ip'] for d in c.devices)
c.use_discovered(result)
assert c.editing['ip'] == result['ip']
print('PASS: fragmented identification replies, exception replies, invalid transactions, timeout, saved/new results, deduplication, add/edit flow. No network traffic sent.')

