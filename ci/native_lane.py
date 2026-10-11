"""Build exact public release source, audit wheel, test installed native CP312."""
import base64
import csv
import hashlib
import importlib.metadata
import io
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import sysconfig
import zipfile

PACKAGE, VERSION, SOURCE_SHA = sys.argv[1:]
ROOT = Path.cwd()
SOURCE = ROOT / 'source'
OUT = Path(os.environ['RUNNER_TEMP']) / 'native-evidence'
OUT.mkdir(exist_ok=True)
REPORT = {'package': PACKAGE, 'version': VERSION, 'source_sha': SOURCE_SHA,
          'wrapper_sha': os.environ['GITHUB_SHA'], 'commands': [], 'status': 'running'}

def save():
    (OUT / 'result.json').write_text(json.dumps(REPORT, indent=2), encoding='utf-8')

def run(args, cwd=None, expect=0):
    args = list(map(str, args))
    print('+', subprocess.list2cmdline(args), flush=True)
    proc = subprocess.run(args, cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    log = f'command-{len(REPORT["commands"]):02d}.log'
    (OUT / log).write_text(proc.stdout, encoding='utf-8')
    print(proc.stdout, flush=True)
    REPORT['commands'].append({'argv': args, 'cwd': str(cwd), 'exit_code': proc.returncode, 'log': log})
    save()
    assert proc.returncode == expect, (args, proc.returncode)
    return proc.stdout

def machine(data):
    assert data[:2] == b'MZ'
    offset = struct.unpack_from('<I', data, 0x3c)[0]
    assert data[offset:offset+4] == b'PE\0\0'
    return struct.unpack_from('<H', data, offset+4)[0]

def wheel_audit(wheel):
    native = []
    with zipfile.ZipFile(wheel) as z:
        names = z.namelist()
        record = next(n for n in names if n.endswith('.dist-info/RECORD'))
        rows = list(csv.reader(io.StringIO(z.read(record).decode())))
        assert {r[0] for r in rows} == {n for n in names if not n.endswith('/')}
        for name, digest, size in rows:
            data = z.read(name)
            if name != record:
                assert digest and size
                algorithm, encoded = digest.split('=', 1)
                actual = base64.urlsafe_b64encode(hashlib.new(algorithm, data).digest()).rstrip(b'=').decode()
                assert actual == encoded, name
                assert len(data) == int(size), name
            if name.endswith(('.pyd', '.dll', '.exe')):
                pe = machine(data)
                native.append({'path': name, 'machine': hex(pe), 'sha256': hashlib.sha256(data).hexdigest()})
                assert pe == 0xaa64, (name, hex(pe))
        expected = 9 if PACKAGE == 'PyOpenGL-accelerate' else 1
        assert len(native) == expected, native
    return {'filename': wheel.name, 'sha256': hashlib.sha256(wheel.read_bytes()).hexdigest(),
            'record_entries': len(rows), 'record_verified': True, 'native': native}

try:
    assert sys.version_info[:2] == (3, 12)
    assert sysconfig.get_platform() == 'win-arm64', sysconfig.get_platform()
    assert machine(Path(sys.executable).read_bytes()) == 0xaa64
    assert run(['git', 'rev-parse', 'HEAD'], SOURCE).strip() == SOURCE_SHA
    REPORT['submodules'] = run(['git', 'submodule', 'status', '--recursive'], SOURCE)
    REPORT['interpreter'] = {'version': sys.version, 'platform': sysconfig.get_platform(), 'pe': '0xaa64'}
    run([sys.executable, '-m', 'venv', str(ROOT/'build-venv')])
    bp = ROOT/'build-venv/Scripts/python.exe'
    run([bp, '-m', 'pip', 'install', 'pip==26.2.1', 'setuptools==84.0.0', 'wheel==0.48.0',
         'build==1.6.1', 'Cython==3.1.3', 'numpy==2.5.4'])
    project = SOURCE/'accelerate' if PACKAGE == 'PyOpenGL-accelerate' else SOURCE
    run([bp, '-m', 'build', '--wheel', '--no-isolation', '--outdir', OUT, project], SOURCE)
    wheels = list(OUT.glob('*.whl'))
    assert len(wheels) == 1
    wheel = wheels[0]
    assert wheel.name.endswith('-cp312-cp312-win_arm64.whl'), wheel
    REPORT['wheel'] = wheel_audit(wheel)
    save()
    run([sys.executable, '-m', 'venv', ROOT/'test-venv'])
    tp = ROOT/'test-venv/Scripts/python.exe'
    run([tp, '-m', 'pip', 'install', 'pytest==9.1.1', 'numpy==2.5.4', wheel])
    if PACKAGE == 'PyOpenGL-accelerate':
        run([tp, '-m', 'pip', 'install', 'PyOpenGL==3.1.10'])
    run([tp, '-m', 'pip', 'freeze'])
    tests = OUT/'tests'
    tests.mkdir(exist_ok=True)
    shutil.copy(ROOT/'ci/native-functional.py', tests/'test_native.py')
    os.environ['NATIVE_PACKAGE'] = PACKAGE
    os.environ['NATIVE_VERSION'] = VERSION
    if PACKAGE == 'PyOpenGL-accelerate':
        for f in (SOURCE/'accelerate/tests').glob('test_*.py'):
            shutil.copy(f, tests/f.name)
        # VBO methods requiring a GL context are outside this CPU lane.
        selectors = [str(tests/'test_numpyaccel.py'), str(tests/'test_arraydatatypeaccel.py')+'::TestNumpy',
                     str(tests/'test_arraydatatypeaccel.py')+'::TestNones', str(tests/'test_native.py')]
    elif PACKAGE == 'hidapi':
        shutil.copy(SOURCE/'tests.py', tests/'test_upstream.py')
        selectors = [str(tests)]
    else:
        selectors = ['--pyargs', 'tree', str(tests/'test_native.py')]
    run([tp, '-m', 'pytest', '-v', *selectors, '--junitxml='+str(OUT/'tests.xml')], OUT)
    import xml.etree.ElementTree as ET
    suites = ET.parse(OUT/'tests.xml').getroot()
    cases = list(suites.iter('testcase'))
    REPORT['tests'] = {'count': len(cases), 'failures': len(list(suites.iter('failure'))),
                       'errors': len(list(suites.iter('error'))), 'skips': len(list(suites.iter('skipped')))}
    assert len(cases) > 0 and REPORT['tests']['failures'] == REPORT['tests']['errors'] == 0
    if PACKAGE != 'dm-tree':
        assert REPORT['tests']['skips'] == 0
    REPORT['status'] = 'PASS'
finally:
    save()
