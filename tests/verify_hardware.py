"""Protocol and controller regression checks; no physical device is contacted."""
import _bootstrap  # Allow direct execution from the tests folder.
import ctypes
import struct
import tempfile
import time
from pathlib import Path
from unittest.mock import patch
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
import config_store
import main
from moxa_backend import E1214Client, ModbusError, AuthenticatedE1214


class Device:
    def __init__(self):
        self.outputs = [True,False,False,False,False,False]
        self.inputs = [False]*6
        self.requests = []
        self.model = b'ioLogik E1214'
        self.bad_echo = False

    def socket(self, *args, **kwargs):
        device = self
        class Socket:
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def settimeout(self,value): pass
            def sendall(self,packet):
                tid,protocol,length,unit = struct.unpack('>HHHB',packet[:7])
                function = packet[7]
                address,count = struct.unpack('>HH',packet[8:12])
                device.requests.append((function,address,count))
                if function == 4:
                    assert address == 5000 and count == 10
                    body = b'\x04\x14' + device.model.ljust(20,b'\0')[:20]
                elif function in (1,2):
                    assert address == 0 and count == 6
                    states = device.outputs if function == 1 else device.inputs
                    body = bytes([function,1,sum(int(v)<<i for i,v in enumerate(states))])
                elif function == 5:
                    assert 0 <= address < 6 and count in (0,0xFF00)
                    device.outputs[address] = bool(count)
                    body = packet[7:12] if not device.bad_echo else b'\x05\0\0\0\0'
                else:
                    body = bytes([function|128,1])
                self.data = struct.pack('>HHHB',tid,0,len(body)+1,unit) + body
            def recv(self,size):
                result,self.data = self.data[:min(size,2)],self.data[min(size,2):]
                return result
        return Socket()


device = Device()
with patch('moxa_backend.socket.create_connection',device.socket):
    client = E1214Client('192.168.1.5')
    assert client.connect()['states'][0]
    assert all(request[0] != 5 for request in device.requests)
    assert client.write(1,True)[1]
    assert client.write(1,False)[1] is False
    device.bad_echo = True
    try:
        client.write(1,True)
        raise AssertionError('Bad echo accepted')
    except ModbusError: pass
    device.bad_echo = False
    device.model = b'E1212'
    try:
        client.connect()
        raise AssertionError('Wrong model accepted')
    except ModbusError: pass
    device.model = b'E1214'

app = QApplication([])
def wait_until(predicate):
    deadline = time.monotonic() + 2
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)
    assert predicate()
c = main.Controller()
c.pollTimer.stop()
d = c.devices[0]
p = c.panels[0]
for cfg in d['config']: cfg.update(active=False)
d['config'][0].update(active=True,first=20,pulse=180,repeat=2,between=50)
c.manual(d,0,True)
c.run_selected(d)
QTest.qWait(60)
c.pause_device(d)
snapshot = list(d['states'])
elapsed = p.timeline.elapsed
QTest.qWait(80)
assert d['states'] == snapshot and p.timeline.elapsed == elapsed
c.continue_or_run(d)
QTest.qWait(30)
assert p.timeline.elapsed > elapsed
c.stop_device(d)
assert d['states'][0] is True and p.timeline.elapsed is None
c.pulse(d,1)
assert p.runSelectedButton.text() == 'RUN'
c.cancel(d,1)

# Exercise the GUI-to-hardware queue against a fragmented in-memory Modbus peer.
with patch('moxa_backend.socket.create_connection',device.socket):
    d.update(mode='real',connected=False)
    c.manage.deviceTable.selectRow(0)
    device.requests.clear()
    c.toggle_connection()
    wait_until(lambda: d['connected'])
    assert d['connected'] and d['states'] == device.outputs
    assert not any(request[0] == 5 for request in device.requests)
    c.manual(d,2,True)
    wait_until(lambda: device.outputs[2] and d['states'][2])
    assert device.outputs[2] and d['states'][2]
    c.toggle_connection()
    assert not d['connected']

# Passwords survive reload in the requested plain-text settings file.
with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory:
    path = Path(directory)/'settings.json'
    d['password'] = 'test-secret-only'
    config_store.save(path,c.devices,1000,True)
    assert 'test-secret-only' in path.read_text(encoding='utf-8')
    data = config_store.load(path)
    assert data['devices'][0]['password'] == 'test-secret-only'
    loaded = main.Controller(config_path=path)
    assert loaded.devices[0]['connected'] is False
    assert not loaded.jobs
    loaded.pollTimer.stop()

# Verify password is passed only to the SDK login call; no fallback silently ignores it.
class SDK:
    def MXEIO_E1K_Connect(self,ip,port,timeout,handle,password):
        assert password == b'sdk-test'
        ctypes.cast(handle,ctypes.POINTER(ctypes.c_int32))[0] = 7
        return 0
    def MXIO_GetModuleType(self,handle,unused,model):
        ctypes.cast(model,ctypes.POINTER(ctypes.c_uint16))[0] = 0x1214
        return 0
    def MXIO_ReadCoils(self,handle,function,address,count,data):
        data[0] = 0
        return 0
    def MXEIO_Disconnect(self,handle): return 0

with patch.object(AuthenticatedE1214,'sdk',return_value=SDK()):
    client = AuthenticatedE1214('192.168.1.5',502,1000,'sdk-test')
    assert 'authenticated' in client.connect()['model']
    client.close()
assert client.handle is None
class WrongPassword(SDK):
    def MXEIO_E1K_Connect(self,*args): return 2007

with patch.object(AuthenticatedE1214,'sdk',return_value=WrongPassword()):
    try:
        AuthenticatedE1214('192.168.1.5',502,1000,'wrong').connect()
        raise AssertionError('Incorrect password accepted')
    except ModbusError as exc:
        assert 'Incorrect MOXA password' in str(exc)

sdk_path = main.ROOT/'vendor'/'MXIO.dll'
if sdk_path.exists():
    library = AuthenticatedE1214.sdk()
    library.MXIO_GetDllVersion.restype = ctypes.c_int32
    assert library.MXIO_GetDllVersion() > 0
    library.MXEIO_Exit()
    AuthenticatedE1214.library = None
c.hardware.close()
print('PASS: E1214 model check, read-only connect, validated writes/readback, fragmented replies, bad ACK, pause/continue, Continuous restore, cursor reset, manual isolation, async GUI connection, plain-text settings and SDK login adapter. No physical network traffic.')
