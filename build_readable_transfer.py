"""Package literal readable source in three text files, plus a restore script."""
from pathlib import Path
import base64
import hashlib
import json
import re

ROOT=Path(__file__).resolve().parent
OUT=ROOT/'transfer'/'readable-code'
OUT.mkdir(parents=True,exist_ok=True)
names=['main.py','config_store.py','moxa_backend.py','network_discovery.py',
       'automation_timeline.py','combined_timeline.py','moxa_control.ui','home.ui',
       'manage_moxa.ui','add_moxa.ui','log.ui','settings.ui','device_panel.ui',
       'style.qss','moxa_settings.json','requirements.txt']
files=[ROOT/name for name in names]
files += sorted(p for folder in ('assets','vendor') for p in (ROOT/folder).rglob('*') if p.is_file())
contents={p.relative_to(ROOT).as_posix():p.read_bytes() for p in files}
contents['START.cmd']=b'''@echo off\r
cd /d "%~dp0"\r
if exist ".venv\\Scripts\\python.exe" (\r
  ".venv\\Scripts\\python.exe" main.py\r
) else (\r
  where py >nul 2>nul\r
  if errorlevel 1 (python main.py) else (py -3 main.py)\r
)\r
if errorlevel 1 pause\r
'''
contents['TRANSFER-NOTES.txt']=b'Run START.cmd. Uses existing 64-bit Python and PySide6. No downloads or package installation. Settings and separate MOXA DLL included.\r\n'
dll=contents.pop('vendor/MXIO.dll')
(OUT/'MXIO.dll').write_bytes(dll)
encoded_dll=base64.b64encode(dll).decode('ascii')
(OUT/'MOXA-DLL.txt').write_text('\n'.join(encoded_dll[i:i+100] for i in range(0,len(encoded_dll),100))+'\n',encoding='ascii')
groups=[[],[],[]]
for name,data in contents.items():
    group=0 if name=='main.py' else (2 if name.startswith('vendor/') or name in ('moxa_settings.json','START.cmd','requirements.txt','TRANSFER-NOTES.txt') else 1)
    try:
        text=data.decode('utf-8-sig').replace('\r\n','\n')
        encoding='utf8'
        restored=text.encode('utf-8')
    except UnicodeDecodeError:
        encoded=base64.b64encode(data).decode('ascii')
        text='\n'.join(encoded[n:n+100] for n in range(0,len(encoded),100))
        encoding='base64'
        restored=data
    meta=json.dumps({'path':name,'encoding':encoding,'sha256':hashlib.sha256(restored).hexdigest()},ensure_ascii=True)
    groups[group].append(f'@@MOXA_FILE {meta}\n{text}\n@@MOXA_END\n')
for i,blocks in enumerate(groups,1):
    with (OUT/f'MOXA-CODE-{i}.txt').open('w',encoding='utf-8-sig',newline='') as stream:
        stream.write(''.join(blocks))
script=r'''# Readable MOXA source restore. No downloads, installation or automatic launch.
# Put MOXA-CODE-1.txt, MOXA-CODE-2.txt and MOXA-CODE-3.txt in this folder.
# Also include MXIO.dll, or MOXA-DLL.txt when transferring text only.
# Run in PowerShell from that folder:
# powershell -NoProfile -ExecutionPolicy Bypass -Command "& ([scriptblock]::Create((Get-Content -LiteralPath '.\MOXA-RESTORE.txt' -Raw -Encoding UTF8)))"
$ErrorActionPreference = 'Stop'
$source = (Get-Location).Path
$destination = Read-Host 'New destination folder (Enter = MOXA-Control)'
if ([string]::IsNullOrWhiteSpace($destination)) { $destination = Join-Path $source 'MOXA-Control' }
$destination = [IO.Path]::GetFullPath($destination)
if (Test-Path -LiteralPath $destination) { throw 'Destination already exists. Choose a NEW folder.' }
$utf8 = [Text.UTF8Encoding]::new($false)
$records = @()
$seen = @{}
$pattern = '(?ms)^@@MOXA_FILE ([^\n]+)\n(.*?)\n@@MOXA_END(?:\n|$)'
foreach ($number in 1..3) {
    $path = Join-Path $source ("MOXA-CODE-{0}.txt" -f $number)
    if (-not (Test-Path -LiteralPath $path)) { throw "Missing file: $path" }
    $text = (Get-Content -LiteralPath $path -Raw -Encoding UTF8).Replace("`r`n", "`n")
    $matches = [regex]::Matches($text, $pattern)
    if ($matches.Count -eq 0) { throw "No file blocks in $path. Copy the entire file." }
    foreach ($match in $matches) {
        $meta = $match.Groups[1].Value | ConvertFrom-Json
        if ($meta.encoding -eq 'utf8') { $bytes = $utf8.GetBytes($match.Groups[2].Value) }
        elseif ($meta.encoding -eq 'base64') { $bytes = [Convert]::FromBase64String($match.Groups[2].Value) }
        else { throw "Unsupported encoding: $($meta.path)" }
        $sha = [Security.Cryptography.SHA256]::Create()
        try { $hash = [BitConverter]::ToString($sha.ComputeHash($bytes)).Replace('-', '').ToLowerInvariant() }
        finally { $sha.Dispose() }
        if ($hash -ne $meta.sha256) { throw "Incomplete or changed content: $($meta.path)" }
        $target = [IO.Path]::GetFullPath((Join-Path $destination $meta.path))
        if (-not $target.StartsWith($destination.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Invalid file path.' }
        if ($seen.ContainsKey($target)) { throw "Duplicate file: $target" }
        $seen[$target] = $true
        $records += [pscustomobject]@{ Target=$target; Bytes=$bytes }
    }
}
if ($records.Count -ne __COUNT__) { throw 'Files are missing. Copy all three complete code files.' }
$dllPath = Join-Path $source 'MXIO.dll'
$dllTextPath = Join-Path $source 'MOXA-DLL.txt'
if (Test-Path -LiteralPath $dllPath) { $dllBytes = [IO.File]::ReadAllBytes($dllPath) }
elseif (Test-Path -LiteralPath $dllTextPath) { $dllBytes = [Convert]::FromBase64String((Get-Content -LiteralPath $dllTextPath -Raw)) }
else { throw 'Missing MXIO.dll or MOXA-DLL.txt. Keep the separate DLL file in this folder.' }
$sha = [Security.Cryptography.SHA256]::Create()
try { $dllHash = [BitConverter]::ToString($sha.ComputeHash($dllBytes)).Replace('-', '').ToLowerInvariant() }
finally { $sha.Dispose() }
if ($dllHash -ne '__DLL_HASH__') { throw 'The separate DLL file is incomplete or changed.' }
$records += [pscustomobject]@{ Target=(Join-Path $destination 'vendor\MXIO.dll'); Bytes=$dllBytes }
# Validate every block before creating the destination.
foreach ($record in $records) {
    [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($record.Target)) | Out-Null
    [IO.File]::WriteAllBytes($record.Target, $record.Bytes)
}
Write-Host ("Restored {0} files in {1}" -f $records.Count, $destination)
Write-Host 'Run START.cmd. Uses your existing Python and PySide6; nothing is downloaded.'
'''.replace('__COUNT__',str(len(contents))).replace('__DLL_HASH__',hashlib.sha256(dll).hexdigest())
(OUT/'MOXA-RESTORE.txt').write_text(script,encoding='utf-8-sig')
# Verify visible-source blocks reconstruct the full package correctly.
count=0
for i in range(1,4):
    text=(OUT/f'MOXA-CODE-{i}.txt').read_text(encoding='utf-8-sig')
    for match in re.finditer(r'(?ms)^@@MOXA_FILE ([^\n]+)\n(.*?)\n@@MOXA_END(?:\n|$)',text):
        meta=json.loads(match[1])
        data=match[2].encode('utf-8') if meta['encoding']=='utf8' else base64.b64decode(match[2])
        assert hashlib.sha256(data).hexdigest()==meta['sha256']
        expected=contents[meta['path']]
        if meta['encoding']=='utf8': expected=expected.decode('utf-8-sig').replace('\r\n','\n').encode('utf-8')
        assert data==expected
        count+=1
assert count==len(contents)
print(f'PASS: {count} files in readable source blocks')
for path in sorted(OUT.glob('*.txt')): print(path.name, path.stat().st_size)
