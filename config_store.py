"""Local text configuration; passwords are stored as requested in plain text."""
import base64
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path


class Blob(ctypes.Structure):
    _fields_ = [('size',wintypes.DWORD),('data',ctypes.POINTER(ctypes.c_ubyte))]


def protect(value, decrypt=False):
    raw = base64.b64decode(value) if decrypt else value.encode('utf-8')
    buffer = ctypes.create_string_buffer(raw)
    source = Blob(len(raw),ctypes.cast(buffer,ctypes.POINTER(ctypes.c_ubyte)))
    target = Blob()
    crypt = ctypes.WinDLL('crypt32',use_last_error=True)
    function = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    function.argtypes = [ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(Blob)]
    function.restype = wintypes.BOOL
    if not function(ctypes.byref(source),None,None,None,None,1,ctypes.byref(target)):
        raise OSError(ctypes.get_last_error(),'Windows password protection failed')
    try:
        result = ctypes.string_at(target.data,target.size)
        return result.decode('utf-8') if decrypt else base64.b64encode(result).decode('ascii')
    finally:
        kernel = ctypes.WinDLL('kernel32')
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree(ctypes.cast(target.data,ctypes.c_void_p))


def save(path, devices, pulse, timestamps, scenarios=None, default_mode='simulation', reset_input=None, confirm_actions=False, auto_pulse_preview=False, countdown=None):
    records = []
    for device in devices:
        record = {key:device[key] for key in ('uid','name','ip','port','timeout','mode','names','config')}
        record['password'] = device.get('password','')
        record['continuous_states'] = list(device.get('baseline', device['states']))
        record['collapsed'] = bool(device.get('collapsed', False))
        record['manual_selection'] = device.get('manual_selection', {})
        records.append(record)
    path = Path(path)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(dict(version=1,devices=records,pulse=pulse,timestamps=timestamps,scenarios=scenarios or {},default_mode=default_mode,reset_input=reset_input or {'ip':'','channel':1},confirm_actions=bool(confirm_actions),auto_pulse_preview=bool(auto_pulse_preview),countdown=countdown or {'enabled':False,'seconds':3}),ensure_ascii=False,indent=2),encoding='utf-8')
    os.replace(temporary,path)


def load(path):
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    if data.get('version') != 1 or not isinstance(data.get('devices'),list) or len(data['devices']) > 5:
        raise ValueError('Invalid saved configuration')
    for record in data['devices']:
        encrypted = record.pop('password_protected','')
        if encrypted:
            record['password'] = protect(encrypted,True)
        else:
            record.setdefault('password','')
    return data
