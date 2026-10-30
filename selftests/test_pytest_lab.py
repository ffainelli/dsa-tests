"""Real, rootless pytest sessions verify ordering, outcomes, capture and cleanup."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))
try:
    from pytest_lab import run_tests
except ImportError:
    run_tests = None
spec = importlib.util.spec_from_file_location('pytest_lab_runner', ROOT / 'run_lab.py')
lab = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lab)


@unittest.skipIf(run_tests is None, 'Install requirements.txt to test the pytest adapter')
class PytestLabTests(unittest.TestCase):
    def exercise(self, groups, prepare=None, finish=None):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        output = Path(directory.name)
        result = {'board': 'offline', 'suites': [], 'cleanup_errors': []}
        events = []
        specs = []
        for name, cls, independent in groups:
            module = types.ModuleType('offline_' + name)
            module.Case = cls
            specs.append((name, module, independent))
        code = run_tests(specs, lab.Results,
            prepare or (lambda name, module: events.append('prepare ' + name)),
            finish or (lambda name: events.append('finish ' + name)), output, result)
        self.assertEqual(json.loads((output / 'results.json').read_text()), result)
        return code, result, events, output

    def statuses(self, result):
        return [[r['status'] for r in s['tests']] for s in result['suites']]

    def test_legacy_failure_blocks_only_its_suite_and_new_cases_continue(self):
        events = []
        class Scenarios(unittest.TestCase):
            def test_a(self):
                self.addCleanup(events.append, 'case cleanup')
                print('UART command details')
                subprocess.run([sys.executable, '-c', 'print("child process details")'], check=True)
            def test_b(self):
                self.fail('intentional failure')
            def test_c(self):
                pass
        code, result, lifecycle, output = self.exercise([
            ('legacy', Scenarios, False), ('independent', Scenarios, True)])
        self.assertEqual(code, 1)
        self.assertEqual(self.statuses(result), [['PASS', 'FAIL', 'NOT_RUN'], ['PASS', 'FAIL', 'PASS']])
        self.assertEqual([s['executed'] for s in result['suites']], [2, 3])
        self.assertEqual(lifecycle, ['prepare legacy', 'finish legacy', 'prepare independent', 'finish independent'])
        self.assertEqual(events, ['case cleanup', 'case cleanup'])
        text = (output / 'legacy/Scenarios.test_a.log').read_text()
        self.assertIn('UART command details', text)
        self.assertIn('child process details', text)
        lab.write_junit(output / 'junit.xml', result)
        xml = (output / 'junit.xml').read_text()
        self.assertIn('failures="2"', xml)
        self.assertIn('skipped="1"', xml)

    def test_skip_and_subtest_failure_keep_correct_severity(self):
        class Scenarios(unittest.TestCase):
            def test_a(self):
                self.skipTest('optional capability unavailable')
            def test_b(self):
                with self.subTest(part='failing'):
                    self.fail('subtest failed')
                with self.subTest(part='unavailable'):
                    self.skipTest('subtest skipped')
            def test_c(self):
                raise RuntimeError('board command failed')
        code, result, _, _ = self.exercise([('cases', Scenarios, True)])
        self.assertEqual(code, 1)
        self.assertEqual(self.statuses(result), [['SKIP', 'FAIL', 'ERROR']])
        self.assertIn('subtest failed', result['suites'][0]['tests'][1]['error'])

    def test_unittest_setup_error_runs_cleanup_and_next_independent_case(self):
        cleaned = []
        class Scenarios(unittest.TestCase):
            def setUp(self):
                self.addCleanup(cleaned.append, self._testMethodName)
                if self._testMethodName == 'test_a':
                    raise RuntimeError('partial topology setup')
            def test_a(self):
                pass
            def test_b(self):
                pass
        _, result, _, _ = self.exercise([('cases', Scenarios, True)])
        self.assertEqual(self.statuses(result), [['ERROR', 'PASS']])
        self.assertEqual(cleaned, ['test_a', 'test_b'])

    def test_suite_setup_error_blocks_all_its_cases_then_cleans_up(self):
        class Scenarios(unittest.TestCase):
            def test_a(self):
                pass
            def test_b(self):
                pass
        calls = []
        def prepare(name, module):
            if name == 'bad':
                raise RuntimeError('host setup unavailable')
        _, result, _, _ = self.exercise([('bad', Scenarios, True), ('good', Scenarios, True)],
            prepare=prepare, finish=calls.append)
        self.assertEqual(self.statuses(result), [['ERROR', 'NOT_RUN'], ['PASS', 'PASS']])
        self.assertEqual(calls, ['bad', 'good'])

    def test_suite_cleanup_error_stops_later_hardware_access(self):
        class Scenarios(unittest.TestCase):
            def test_a(self):
                pass
        prepared = []
        def finish(name):
            raise RuntimeError('cannot restore links')
        code, result, _, _ = self.exercise([('bad', Scenarios, True), ('later', Scenarios, True)],
            prepare=lambda name, module: prepared.append(name), finish=finish)
        self.assertNotEqual(code, 0)
        self.assertEqual(prepared, ['bad'])
        self.assertEqual(self.statuses(result), [['ERROR'], ['NOT_RUN']])
        self.assertTrue(result['cleanup_errors'])

    def test_interrupt_runs_case_and_suite_cleanup_and_records_failure(self):
        cleaned = []
        class Scenarios(unittest.TestCase):
            def test_a(self):
                self.addCleanup(cleaned.append, 'case')
                raise KeyboardInterrupt()
            def test_b(self):
                self.fail('must not run')
        code, result, _, _ = self.exercise([('cases', Scenarios, True)], finish=lambda name: cleaned.append('suite'))
        self.assertEqual(code, 2)
        self.assertEqual(cleaned, ['case', 'suite'])
        self.assertEqual(self.statuses(result), [['ERROR', 'NOT_RUN']])
        self.assertEqual(result['status'], 'FAIL')
