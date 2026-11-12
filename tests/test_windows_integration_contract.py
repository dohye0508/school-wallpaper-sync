import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from xml.etree import ElementTree as ET

from adapters import scheduler
from core.storage import desktop_transaction


class SchedulerTests(unittest.TestCase):
    def test_xml_handles_spaces_and_special_characters(self):
        xml = scheduler.task_xml('C:\\Users\\테스트 & User\\School App.exe', '--background', 'S-1-5-21-123')
        root = ET.fromstring(xml)
        ns = {'t': scheduler.NS}
        self.assertEqual(root.find('t:Actions/t:Exec/t:Command', ns).text,
                         'C:\\Users\\테스트 & User\\School App.exe')
        self.assertEqual(root.find('t:Triggers/t:TimeTrigger/t:Repetition/t:Interval', ns).text, 'PT5M')
        self.assertEqual(root.find('t:Settings/t:DisallowStartIfOnBatteries', ns).text, 'false')

    def test_registration_is_idempotent_and_detects_changed_path(self):
        xml = scheduler.task_xml('C:\\App\\School.exe', '--background', 'S-1')
        self.assertTrue(scheduler.equivalent(xml, xml))
        self.assertFalse(scheduler.equivalent(xml, scheduler.task_xml('C:\\Other.exe', '--background', 'S-1')))
        self.assertFalse(scheduler.equivalent(b'invalid', xml))

    def test_resume_trigger_contains_power_event(self):
        xml = scheduler.task_xml('C:\\App.exe', '--background', 'S-1', resume=True)
        root = ET.fromstring(xml)
        subscription = root.find('.//{'+scheduler.NS+'}Subscription').text
        self.assertIn('EventID=1', subscription)
        self.assertIn('Power-Troubleshooter', subscription)

    def test_schtasks_xml_declaration_does_not_match_output_encoding(self):
        xml = scheduler.task_xml('C:\\App.exe', '--background', 'S-1')
        acp_output = xml.decode('utf-16').encode('mbcs')
        self.assertTrue(scheduler.equivalent(acp_output, xml))

    def test_windows_omitted_default_trigger_values(self):
        xml = scheduler.task_xml('C:\\App.exe', '--background', 'S-1')
        actual = ET.fromstring(xml)
        trigger = actual.find('{'+scheduler.NS+'}Triggers')[0]
        trigger.remove(trigger.find('{'+scheduler.NS+'}Enabled'))
        repetition = trigger.find('{'+scheduler.NS+'}Repetition')
        repetition.remove(repetition.find('{'+scheduler.NS+'}StopAtDurationEnd'))
        self.assertTrue(scheduler.equivalent(ET.tostring(actual,encoding='utf-16'), xml))

    def test_registration_failure_is_not_silenced(self):
        with patch.object(subprocess, 'run', return_value=Mock(returncode=1, stderr=b'Access denied', stdout=b'')):
            with self.assertRaisesRegex(RuntimeError, 'Access denied'):
                scheduler.run(['schtasks.exe'])

    def test_matching_tasks_are_not_recreated(self):
        def command(args):
            if args[0] == 'whoami.exe':
                return b'"user","S-1"'
            if '/Query' in args:
                return scheduler.task_xml('C:\\App.exe', '--background', 'S-1', args[3].endswith('_OnResume'))
            raise AssertionError('Unexpected task registration')
        with patch.object(scheduler, 'run', side_effect=command):
            scheduler.ensure_tasks('C:\\App.exe', '--background')


class LockTests(unittest.TestCase):
    def test_another_process_waits_for_transaction(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory)/'acquired'
            code = ('from core.storage import desktop_transaction; from pathlib import Path; '
                    'import sys; '\
                    '\nwith desktop_transaction(sys.argv[1]): Path(sys.argv[2]).write_text("ok")')
            with desktop_transaction(directory):
                child = subprocess.Popen([sys.executable, '-s', '-c', code, directory, str(marker)],
                                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                try:
                    child.wait(timeout=0.4)
                    self.fail('Child did not wait for the transaction lock')
                except subprocess.TimeoutExpired:
                    self.assertFalse(marker.exists())
            stdout, stderr = child.communicate(timeout=10)
            self.assertEqual(child.returncode, 0, stderr.decode(errors='replace'))
            self.assertEqual(marker.read_text(), 'ok')


if __name__ == '__main__':
    unittest.main()
