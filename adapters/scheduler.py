"""Idempotent per-user Task Scheduler registration with checked results."""
import csv
import hashlib
import json
import os
import subprocess
import tempfile
from datetime import datetime, timedelta
from xml.etree import ElementTree as ET

NS = 'http://schemas.microsoft.com/windows/2004/02/mit/task'
ET.register_namespace('', NS)
TASKS = ('SchoolWallpaperSync', 'SchoolWallpaperSync_OnResume')

def run(args):
    result = subprocess.run(args, capture_output=True, timeout=30,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        # Do not hide registration errors behind a successful wallpaper result.
        detail = (result.stderr or result.stdout).decode('mbcs', errors='replace')
        raise RuntimeError(f'{args[0]} failed ({result.returncode}): {detail.strip()}')
    return result.stdout

def task_xml(executable, arguments, sid, resume=False):
    root = ET.Element(f'{{{NS}}}Task', version='1.2')
    def node(parent, name, text=None, **attributes):
        element = ET.SubElement(parent, f'{{{NS}}}{name}', attributes)
        element.text = text
        return element
    fingerprint = hashlib.sha256(json.dumps([executable, arguments, sid, resume, 1]).encode()).hexdigest()
    registration = node(root, 'RegistrationInfo')
    node(registration, 'Description', 'SchoolWallpaperSync:' + fingerprint)
    triggers = node(root, 'Triggers')
    trigger = node(triggers, 'EventTrigger' if resume else 'TimeTrigger')
    if not resume:
        repetition = node(trigger, 'Repetition')
        node(repetition, 'Interval', 'PT5M')
        node(repetition, 'StopAtDurationEnd', 'false')
        node(trigger, 'StartBoundary', (datetime.now() + timedelta(minutes=5)).isoformat(timespec='seconds'))
    node(trigger, 'Enabled', 'true')
    if resume:
        node(trigger, 'Subscription', "<QueryList><Query Id='0' Path='System'><Select Path='System'>"
             "*[System[Provider[@Name='Microsoft-Windows-Power-Troubleshooter'] and EventID=1]]"
             '</Select></Query></QueryList>')
    principals = node(root, 'Principals')
    principal = node(principals, 'Principal', id='Author')
    node(principal, 'UserId', sid)
    node(principal, 'LogonType', 'InteractiveToken')
    node(principal, 'RunLevel', 'LeastPrivilege')
    settings = node(root, 'Settings')
    for name, value in [('MultipleInstancesPolicy', 'IgnoreNew'),
                        ('DisallowStartIfOnBatteries', 'false'), ('StopIfGoingOnBatteries', 'false'),
                        ('StartWhenAvailable', 'true'), ('ExecutionTimeLimit', 'PT3M')]:
        node(settings, name, value)
    actions = node(root, 'Actions', Context='Author')
    execute = node(actions, 'Exec')
    node(execute, 'Command', executable)
    node(execute, 'Arguments', arguments)
    node(execute, 'WorkingDirectory', os.path.dirname(executable))
    return ET.tostring(root, encoding='utf-16', xml_declaration=True)

def equivalent(existing, expected):
    """Ignore only start time; re-registration must not postpone the next run."""
    try:
        def parse(value):
            if isinstance(value, bytes) and not value.startswith((b'\xff\xfe', b'\xfe\xff')):
                # schtasks prints ACP text while claiming UTF-16 in its declaration.
                value = value.decode('mbcs')
            return ET.fromstring(value)
        a, b = parse(existing), parse(expected)
        def normalized(root):
            def visit(element):
                if element.tag.endswith('StartBoundary'):
                    return None
                return (element.tag, (element.text or '').strip(),
                        tuple(sorted(element.attrib.items())),
                        tuple(item for child in element if (item := visit(child)) is not None))
            return visit(root)
        # Windows adds default Settings nodes. Check explicit desired nodes only.
        for path in ('RegistrationInfo/Description', 'Principals/Principal/UserId',
                     'Principals/Principal/LogonType', 'Actions/Exec/Command',
                     'Actions/Exec/Arguments', 'Actions/Exec/WorkingDirectory',
                     'Settings/MultipleInstancesPolicy', 'Settings/DisallowStartIfOnBatteries',
                     'Settings/StopIfGoingOnBatteries', 'Settings/StartWhenAvailable',
                     'Settings/ExecutionTimeLimit'):
            path = '/'.join('{'+NS+'}'+part for part in path.split('/'))
            left, right = a.find(path), b.find(path)
            if left is None or right is None or left.text != right.text:
                return False
        def value(root, path, default=None):
            element = root.find('/'.join('{'+NS+'}'+part for part in path.split('/')))
            return element.text if element is not None else default
        if value(a, 'Settings/Enabled', 'true') != 'true':
            return False
        ta, tb = a.find('{'+NS+'}Triggers'), b.find('{'+NS+'}Triggers')
        if ta is None or len(ta) != 1 or ta[0].tag != tb[0].tag:
            return False
        kind = 'EventTrigger' if tb[0].tag.endswith('EventTrigger') else 'TimeTrigger'
        if value(a, f'Triggers/{kind}/Enabled', 'true') != 'true':
            return False
        if kind == 'EventTrigger':
            return value(a, 'Triggers/EventTrigger/Subscription') == value(b, 'Triggers/EventTrigger/Subscription')
        return (value(a, 'Triggers/TimeTrigger/Repetition/Interval') == 'PT5M'
                and value(a, 'Triggers/TimeTrigger/Repetition/StopAtDurationEnd', 'false') == 'false')
    except (ET.ParseError, AttributeError):
        return False

def ensure_tasks(executable, arguments):
    user = run(['whoami.exe', '/user', '/fo', 'csv', '/nh']).decode('mbcs')
    sid = next(csv.reader([user.strip()]))[1]
    for name, resume in zip(TASKS, (False, True)):
        xml = task_xml(executable, arguments, sid, resume)
        try:
            existing = run(['schtasks.exe', '/Query', '/TN', name, '/XML'])
        except RuntimeError:
            existing = b''
        if equivalent(existing, xml):
            continue
        fd, path = tempfile.mkstemp(suffix='.xml')
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(xml)
            run(['schtasks.exe', '/Create', '/TN', name, '/XML', path, '/F'])
        finally:
            os.unlink(path)
