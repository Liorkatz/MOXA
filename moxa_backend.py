"""ioLogik E1214 default Modbus mapping. Writes are never retried."""
import socket
import struct
import ctypes
from pathlib import Path
from threading import RLock
from concurrent.futures import ThreadPoolExecutor
from PySide6.QtCore import QObject, Signal


class ModbusError(Exception):
    pass


class E1214Client:
    def __init__(self, ip, port=502, timeout=2000, unit=1):
        self.ip, self.port, self.timeout, self.unit = ip, port, timeout / 1000, unit
        self.transaction = 0

    @staticmethod
    def receive(sock, size):
        data = b''
        while len(data) < size:
            part = sock.recv(size - len(data))
            if not part:
                raise ModbusError('Connection closed before complete reply')
            data += part
        return data

    def request(self, function, payload):
        self.transaction = (self.transaction + 1) % 65536
        pdu = bytes([function]) + payload
        packet = struct.pack('>HHHB', self.transaction, 0, len(pdu) + 1, self.unit) + pdu
        with socket.create_connection((self.ip, self.port), self.timeout) as sock:
            sock.settimeout(self.timeout)
            sock.sendall(packet)
            transaction, protocol, length, unit = struct.unpack('>HHHB', self.receive(sock, 7))
            if (transaction, protocol, unit) != (self.transaction, 0, self.unit) or not 2 <= length <= 254:
                raise ModbusError('Invalid Modbus reply header')
            reply = self.receive(sock, length - 1)
        if reply[0] == function | 0x80:
            raise ModbusError(f'Modbus exception {reply[1] if len(reply)>1 else "?"}; check device configuration / IP access rules')
        if reply[0] != function:
            raise ModbusError('Unexpected Modbus function')
        return reply[1:]

    def bits(self, function, count=6):
        result = self.request(function, struct.pack('>HH', 0, count))
        size = (count + 7) // 8
        if len(result) != size + 1 or result[0] != size:
            raise ModbusError('Invalid bit response')
        return [bool(result[1 + i // 8] & (1 << (i % 8))) for i in range(count)]

    def identify(self):
        result = self.request(4, struct.pack('>HH', 5000, 10))
        if len(result) != 21 or result[0] != 20:
            raise ModbusError('Invalid model identification')
        model = result[1:].replace(b'\0', b'').decode('ascii', 'replace').strip()
        if 'E1214' not in model.upper():
            raise ModbusError(f'Unsupported model: {model}; only E1214 default mapping is enabled')
        return model

    def connect(self):
        model = self.identify()
        return dict(model=model, states=self.bits(1), inputs=self.bits(2))

    def write(self, channel, value):
        if not 0 <= channel < 6:
            raise ModbusError('Invalid relay channel')
        payload = struct.pack('>HH', channel, 0xFF00 if value else 0)
        if self.request(5, payload) != payload:
            raise ModbusError('Relay write acknowledgement mismatch; output state is unknown')
        states = self.bits(1)
        if states[channel] != bool(value):
            raise ModbusError('Relay readback differs from requested value')
        return states


class HardwareQueue(QObject):
    delivered = Signal(object, object, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.executors = {}
        self.closed = False
        self.delivered.connect(self.dispatch)

    def submit(self, uid, operation, success, failure):
        if self.closed: return
        if uid not in self.executors:
            self.executors[uid] = ThreadPoolExecutor(max_workers=1)
        executor = self.executors[uid]
        future = executor.submit(operation)
        def finished(future):
            try:
                result = future.result()
            except Exception as exc:
                try: self.delivered.emit(failure, str(exc), None)
                except RuntimeError: pass
            else:
                try: self.delivered.emit(success, result, None)
                except RuntimeError: pass
        future.add_done_callback(finished)

    def dispatch(self, callback, result, unused):
        if not self.closed: callback(result)

    def close(self):
        self.closed = True
        for executor in self.executors.values():
            executor.shutdown(wait=False, cancel_futures=True)


class AuthenticatedE1214:
    """Password login through MOXA's MXIO SDK, rather than invented Modbus auth."""
    lock = RLock()
    library = None

    def __init__(self, ip, port, timeout, password):
        self.ip, self.port, self.timeout, self.password = ip, port, timeout, password
        self.handle = None

    @classmethod
    def sdk(cls):
        if cls.library is None:
            path = Path(__file__).parent / 'vendor' / 'MXIO.dll'
            if not path.is_file():
                raise ModbusError('Password login requires the official 64-bit MOXA MXIO SDK: place MXIO.dll and its dependencies in C:/moxa/vendor. Password was not sent.')
            try:
                cls.library = ctypes.WinDLL(str(path))
            except OSError as exc:
                raise ModbusError('Cannot load the MOXA MXIO SDK; verify 64-bit DLL and dependencies') from exc
            lib = cls.library
            specs = {
                'MXEIO_Init': [],
                'MXEIO_E1K_Connect': [ctypes.c_char_p, ctypes.c_uint16, ctypes.c_uint32, ctypes.POINTER(ctypes.c_int32), ctypes.c_char_p],
                'MXEIO_Disconnect': [ctypes.c_int32],
                'MXIO_GetModuleType': [ctypes.c_int32, ctypes.c_uint8, ctypes.POINTER(ctypes.c_uint16)],
                'MXIO_ReadCoils': [ctypes.c_int32, ctypes.c_uint8, ctypes.c_uint16, ctypes.c_uint16, ctypes.POINTER(ctypes.c_uint8)],
                'MXIO_WriteCoils': [ctypes.c_int32, ctypes.c_uint16, ctypes.c_uint16, ctypes.POINTER(ctypes.c_uint8)],
            }
            try:
                for name, args in specs.items():
                    function = getattr(lib,name)
                    function.argtypes, function.restype = args, ctypes.c_int32
                cls.check(lib.MXEIO_Init())
            except Exception:
                cls.library = None
                raise
        return cls.library

    @staticmethod
    def check(code):
        if code != 0:
            messages = {2001:'Device response timed out',2004:'Invalid device response',
                        2005:'Connection closed by device',2007:'Incorrect MOXA password',
                        1002:'Invalid register mapping; default E1214 mapping is required',
                        4001:'Device / function not supported by the SDK',4002:'Invalid connection handle'}
            raise ModbusError(f'{messages.get(code,"MOXA SDK error")} (code {code})')

    def connect(self):
        with self.lock:
            lib = self.sdk()
            handle = ctypes.c_int32()
            try:
                password = self.password.encode('ascii')
            except UnicodeEncodeError:
                raise ModbusError('MOXA SDK password must use ASCII characters') from None
            if b'\0' in password:
                raise ModbusError('Password contains an unsupported character')
            if len(password) > 16:
                raise ModbusError('This MOXA SDK supports passwords of up to 16 ASCII characters')
            self.check(lib.MXEIO_E1K_Connect(self.ip.encode('ascii'),self.port,self.timeout,ctypes.byref(handle),password))
            self.handle = handle.value
            try:
                model = ctypes.c_uint16()
                self.check(lib.MXIO_GetModuleType(self.handle,0,ctypes.byref(model)))
                if model.value != 0x1214:
                    raise ModbusError(f'Unsupported model E{model.value:X}; E1214 required')
                return dict(model='ioLogik E1214 (MXIO authenticated)',states=self.bits(1),inputs=self.bits(2))
            except Exception:
                self.close()
                raise

    def bits(self,function,count=6):
        with self.lock:
            if self.handle is None:
                raise ModbusError('Not connected')
            data = (ctypes.c_uint8 * ((count+7)//8))()
            self.check(self.sdk().MXIO_ReadCoils(self.handle,function,0,count,data))
            return [bool(data[i//8] & (1 << (i%8))) for i in range(count)]

    def write(self,channel,value):
        with self.lock:
            if self.handle is None or not 0 <= channel < 6:
                raise ModbusError('Not connected or invalid channel')
            data = (ctypes.c_uint8 * 1)(1 if value else 0)
            self.check(self.sdk().MXIO_WriteCoils(self.handle,channel,1,data))
            states = self.bits(1)
            if states[channel] != bool(value):
                raise ModbusError('Relay readback differs from requested state')
            return states

    def close(self):
        with self.lock:
            if self.handle is not None:
                self.sdk().MXEIO_Disconnect(self.handle)
                self.handle = None


def make_client(values):
    if values.get('password'):
        return AuthenticatedE1214(values['ip'],values['port'],values['timeout'],values['password'])
    return E1214Client(values['ip'],values['port'],values['timeout'])
