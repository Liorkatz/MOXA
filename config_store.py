"""Local text configuration; passwords are stored as requested in plain text."""
import base64
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
from ipaddress import IPv4Address


def integer(value, minimum, maximum, field):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f'Invalid {field}')
    return value


def boolean(value, field):
    if type(value) is not bool: raise ValueError(f'Invalid {field}')
    return value


def strings(value, field):
    if not isinstance(value,list) or len(value)!=6 or any(not isinstance(item,str) for item in value):
        raise ValueError(f'Invalid {field}')
    return list(value)


def flags(value, field):
    if not isinstance(value,list) or len(value)!=6 or any(type(item) is not bool for item in value):
        raise ValueError(f'Invalid {field}')
    return list(value)


def channel_configuration(value):
    if not isinstance(value,list) or len(value)!=6: raise ValueError('Invalid channel configuration')
    result=[]
    for cfg in value:
        if not isinstance(cfg,dict): raise ValueError('Invalid channel configuration')
        record={'active':boolean(cfg.get('active'),'active flag')}
        for key in ('first','pulse','between'):
            record[key]=integer(cfg.get(key),0,3600000,key)
        record['repeat']=integer(cfg.get('repeat'),0,1000,'repeat')
        record['trigger']=integer(cfg.get('trigger'),0,6,'trigger')
        if 'action' in cfg:
            if cfg['action'] not in ('start','off','pause','continue','reset'): raise ValueError('Invalid DI action')
            record['action']=cfg['action']
        result.append(record)
    return result


def scenario_records(value):
    if not isinstance(value,list) or not 1<=len(value)<=5: raise ValueError('Invalid scenario units')
    records=[]
    seen=set()
    for record in value:
        if not isinstance(record,dict): raise ValueError('Invalid scenario unit')
        ip=str(IPv4Address(record.get('ip','')))
        if ip in seen: raise ValueError('Duplicate scenario unit')
        seen.add(ip)
        records.append(dict(ip=ip,names=strings(record.get('names'),'channel names'),
                            config=channel_configuration(record.get('config'))))
    return records


def validate_configuration(data):
    """Return only validated persistent fields; never import runtime state."""
    if not isinstance(data,dict) or type(data.get('version')) is not int or data['version']!=1:
        raise ValueError('Invalid configuration version')
    devices=data.get('devices')
    if not isinstance(devices,list) or len(devices)>5: raise ValueError('Invalid units')
    records=[]
    ids,ips,names=set(),set(),set()
    for raw in devices:
        if not isinstance(raw,dict): raise ValueError('Invalid unit')
        uid=integer(raw.get('uid'),1,5,'unit ID')
        name=raw.get('name')
        if not isinstance(name,str) or not name.strip(): raise ValueError('Invalid unit name')
        ip=str(IPv4Address(raw.get('ip','')))
        if uid in ids or ip in ips or name.casefold() in names: raise ValueError('Duplicate unit')
        ids.add(uid); ips.add(ip); names.add(name.casefold())
        mode=raw.get('mode')
        if mode not in ('real','simulation'): raise ValueError('Invalid unit mode')
        password=raw.get('password','')
        encrypted=raw.get('password_protected','')
        if not isinstance(password,str) or not isinstance(encrypted,str): raise ValueError('Invalid password field')
        if encrypted: password=protect(encrypted,True)
        selections=raw.get('manual_selection',{})
        if not isinstance(selections,dict) or any(key not in ('continuousSelect','momentarySelect') for key in selections):
            raise ValueError('Invalid manual selection')
        # Accept legacy documents but never restore a simulated connection.
        records.append(dict(uid=uid,name=name,ip=ip,mode='real',password=password,
            port=integer(raw.get('port'),1,65535,'port'),timeout=integer(raw.get('timeout'),1,60000,'timeout'),
            names=strings(raw.get('names'),'channel names'),config=channel_configuration(raw.get('config')),
            continuous_states=flags(raw.get('continuous_states',[False]*6),'Continuous states'),
            collapsed=boolean(raw.get('collapsed',False),'collapsed flag'),
            manual_selection={key:flags(value,'manual selection') for key,value in selections.items()}))
    scenarios=data.get('scenarios',{})
    if not isinstance(scenarios,dict): raise ValueError('Invalid scenarios')
    saved_scenarios={}
    for name,value in scenarios.items():
        if not isinstance(name,str) or not name.strip() or len(name)>80: raise ValueError('Invalid scenario name')
        saved_scenarios[name]=scenario_records(value)
    default=data.get('default_mode','real')
    if default not in ('real','simulation'): raise ValueError('Invalid default mode')
    countdown=data.get('countdown',{'enabled':False,'seconds':3})
    if not isinstance(countdown,dict): raise ValueError('Invalid countdown')
    return dict(version=1,devices=records,scenarios=saved_scenarios,default_mode='real',
        simulation_enabled=boolean(data.get('simulation_enabled',False),'simulation enabled'),
        pulse=integer(data.get('pulse',0),0,3600000,'manual pulse'),
        timestamps=boolean(data.get('timestamps',True),'timestamps flag'),
        confirm_actions=boolean(data.get('confirm_actions',False),'confirmation flag'),
        auto_pulse_preview=boolean(data.get('auto_pulse_preview',False),'preview flag'),
        countdown=dict(enabled=boolean(countdown.get('enabled'),'countdown flag'),
                       seconds=integer(countdown.get('seconds'),1,120,'countdown duration')))


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


def save(path, devices, pulse, timestamps, scenarios=None, default_mode='real', reset_input=None, confirm_actions=False, auto_pulse_preview=False, countdown=None, simulation_enabled=False):
    records = []
    for device in devices:
        if device.get('temporary_sim_unit'):
            continue  # The auto-generated sample is never saved as a real device.
        record = {key:device[key] for key in ('uid','name','ip','port','timeout','mode','names','config')}
        record['mode'] = 'real'  # The simulator is a global mode, not a hardware unit type.
        record['password'] = device.get('password','')
        record['continuous_states'] = list(device.get('physical_states', device.get('baseline', device['states'])))
        record['collapsed'] = bool(device.get('collapsed', False))
        record['manual_selection'] = device.get('manual_selection', {})
        records.append(record)
    path = Path(path)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(dict(version=1,devices=records,pulse=pulse,timestamps=timestamps,scenarios=scenarios or {},default_mode=default_mode,reset_input=reset_input or {'ip':'','channel':1},simulation_enabled=bool(simulation_enabled),confirm_actions=bool(confirm_actions),auto_pulse_preview=bool(auto_pulse_preview),countdown=countdown or {'enabled':False,'seconds':3}),ensure_ascii=False,indent=2),encoding='utf-8')
    os.replace(temporary,path)


def load(path):
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    return validate_configuration(data)

