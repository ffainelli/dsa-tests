# SPDX-License-Identifier: GPL-2.0
"""One pytest session for ordered UART lab cases, with unittest subtest semantics."""
from collections import Counter
from dataclasses import dataclass
import inspect
import io
import json
from pathlib import Path
import time
import unittest

import pytest


LAB = pytest.StashKey()

STATUSES = ('PASS', 'FAIL', 'ERROR', 'SKIP', 'NOT_RUN')


def cases(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from cases(item)
        else:
            yield item


class ResultStream(io.StringIO):
    def writeln(self, text=''):
        self.write(text + '\n')


@dataclass
class Group:
    name: str
    module: object
    tests: list
    independent: bool
    report: dict
    blocked: bool = False
    setup_failed: bool = False


class LabFile(pytest.File):
    def collect(self):
        for group in self.config.stash[LAB].groups:
            for index, case in enumerate(group.tests):
                yield LabItem.from_parent(self, name=group.name + '::' + type(case).__name__ + '.' +
                    case._testMethodName, group=group, case=case, last=index == len(group.tests) - 1)


class LabItem(pytest.Item):
    def __init__(self, *, group, case, last, **kwargs):
        super().__init__(**kwargs)
        self.group, self.case, self.last = group, case, last
        self.lab = self.config.stash[LAB]
        self.record = next(r for r in group.report['tests'] if r['name'] == case.id())
        self.started = None

    def setup(self):
        self.started = time.monotonic()
        if self.group.setup_failed:
            pytest.skip('Suite initialization failed; topology is unavailable')
        if self.lab.active is None:
            self.lab.active = self.group
            try:
                self.lab.prepare(self.group.name, self.group.module)
            except Exception:
                self.group.setup_failed = True
                raise
        if self.lab.active is not self.group:
            raise RuntimeError('Lab cases must keep their collection order')

    def runtest(self):
        if self.group.blocked:
            self.record.update(status='NOT_RUN', reason='Suite stopped after an earlier failure')
            pytest.skip(self.record['reason'])
        result = self.lab.result_class(ResultStream(), True, 2)
        result.failfast = not self.group.independent
        try:
            self.case.run(result)  # Preserves unittest setup, cleanup and subTest outcomes.
        except KeyboardInterrupt:
            self.record.update(status='ERROR', error='Interrupted while executing this case')
            self.group.report['pass'] = False
            # unittest propagates KeyboardInterrupt before reaching doCleanups;
            # run ownership callbacks so VLAN uppers/addresses cannot be leaked.
            if not self.case.doCleanups():
                self.lab.result['cleanup_errors'].append('Cleanup failed after interrupt: ' + self.case.id())
            self.lab.save()
            raise
        self.group.report['executed'] += result.testsRun
        self.record.clear()
        self.record.update(result.records[0])
        tracebacks = [text for _, text in result.errors + result.failures]
        if tracebacks:
            self.record.update(status='ERROR' if result.errors else 'FAIL', error='\n'.join(tracebacks))
        elif result.unexpectedSuccesses:
            self.record.update(status='FAIL', error='Unexpected success of an expected-failure test')
        if not result.wasSuccessful():
            self.group.report['pass'] = False
            self.group.blocked = not self.group.independent
            pytest.fail(self.record.get('error', 'unittest case failed'), pytrace=False)
        if self.record['status'] == 'SKIP':
            pytest.skip(self.record.get('reason', 'Capability unavailable'))

    def teardown(self):
        if self.last and self.lab.active is self.group:
            try:
                self.lab.finish(self.group.name)
                self.lab.active = None
            except Exception as exc:
                self.lab.result['cleanup_errors'].append('%s: %s' % (self.group.name, exc))
                self.session.shouldstop = 'Suite cleanup failed; stopping lab access'
                raise

    def reportinfo(self):
        method = getattr(type(self.case), self.case._testMethodName)
        try:
            path = Path(inspect.getsourcefile(method))
            line = inspect.getsourcelines(method)[1] - 1
        except (OSError, TypeError):
            path, line = self.path, 0
        return path, line, self.name


class LabPlugin:
    def __init__(self, specs, result_class, prepare, finish, output, result):
        self.result_class, self.prepare, self.finish = result_class, prepare, finish
        self.output, self.result = Path(output).resolve(), result
        self.manifest = self.output / 'lab-cases.dsa'
        self.groups, self.active = [], None
        self.completed = 0
        for name, module, independent in specs:
            tests = list(cases(unittest.defaultTestLoader.loadTestsFromModule(module)))
            report = dict(name=name, independent=independent, planned=len(tests), executed=0,
                tests=[dict(name=t.id(), status='NOT_RUN', reason='Run ended before this case executed') for t in tests],
                **{'pass': True})
            result['suites'].append(report)
            self.groups.append(Group(name, module, tests, independent, report))
        self.total = sum(len(g.tests) for g in self.groups)

    def pytest_configure(self, config):
        self.config = config
        config.stash[LAB] = self

    def pytest_collect_file(self, file_path, parent):
        if file_path == self.manifest:
            return LabFile.from_parent(parent, path=file_path)

    def terminal(self, line):
        reporter = self.config.pluginmanager.get_plugin('terminalreporter')
        if reporter:
            reporter.write_line(line)

    def counts(self):
        return Counter(r['status'] for group in self.groups for r in group.report['tests'])

    def progress(self, label):
        width = 24
        filled = width * self.completed // max(1, self.total)
        counts = self.counts()
        self.terminal('[%s%s] %d/%d %3d%%  %s  P:%d F:%d E:%d S:%d' % (
            '#' * filled, '-' * (width - filled), self.completed, self.total,
            100 * self.completed // max(1, self.total), label,
            counts['PASS'], counts['FAIL'], counts['ERROR'], counts['SKIP']))

    def pytest_runtest_logstart(self, nodeid, location):
        self.progress('RUN ' + nodeid.split('::', 1)[-1])

    @pytest.hookimpl(wrapper=True)
    def pytest_runtest_makereport(self, item, call):
        report = yield
        if not isinstance(item, LabItem):
            return report
        if report.failed:
            item.group.report['pass'] = False
            if item.record['status'] not in ('FAIL', 'ERROR') or report.when != 'call':
                item.record.update(status='ERROR', error=str(report.longrepr))
            if not item.group.independent:
                item.group.blocked = True
        elif report.skipped and item.record['status'] == 'NOT_RUN':
            # Setup skips from a failed group stay NOT_RUN, with a useful cause.
            if item.group.setup_failed:
                item.record['reason'] = 'Suite initialization failed; topology is unavailable'
        if report.when == 'teardown':
            self.completed += 1
            item.record['duration_s'] = round(time.monotonic() - item.started, 3)
            # Capture includes subprocess stdout/stderr as well as UART commands.
            path = self.output / item.group.name / (type(item.case).__name__ + '.' +
                                                    item.case._testMethodName + '.log')
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(report.capstdout + report.capstderr)
            item.record['log'] = str(path)
            self.progress('%s %s (%.1fs)' % (item.record['status'], item.name, item.record['duration_s']))
            self.save()
        return report

    def pytest_report_teststatus(self, report, config):
        # Our bar replaces the dot stream; native pytest failure/skip summaries stay.
        if report.when == 'call' or report.failed or report.skipped:
            category = ('error' if report.failed and report.when != 'call' else
                        'failed' if report.failed else 'skipped' if report.skipped else 'passed')
            return category, '', category.upper()

    def pytest_terminal_summary(self, terminalreporter, exitstatus, config):
        terminalreporter.section('DSA suite summary')
        terminalreporter.write_line('%-22s %6s %6s %6s %6s %9s' %
            ('Suite', 'PASS', 'FAIL', 'ERROR', 'SKIP', 'NOT_RUN'))
        for group in self.groups:
            counts = Counter(r['status'] for r in group.report['tests'])
            terminalreporter.write_line('%-22s %6d %6d %6d %6d %9d' %
                (group.name, *(counts[status] for status in STATUSES)))
        terminalreporter.write_line('Artifacts: ' + str(self.output))
        terminalreporter.write_line('Detailed commands/output: per-test .log files; live board output: console.log')

    def save(self):
        for group in self.groups:
            group.report['pass'] = all(r['status'] in ('PASS', 'SKIP') for r in group.report['tests'])
        self.result['status'] = ('PASS' if all(g.report['pass'] for g in self.groups)
                                 and not self.result['cleanup_errors'] and not self.result.get('error')
                                 and self.result.get('pytest_exit_code', 0) == 0 else 'FAIL')
        path = self.output / 'results.json'
        temporary = path.with_suffix('.json.tmp')
        temporary.write_text(json.dumps(self.result, indent=2) + '\n')
        temporary.replace(path)


def run_tests(specs, result_class, prepare, finish, output, result, live=False):
    plugin = LabPlugin(specs, result_class, prepare, finish, output, result)
    plugin.manifest.write_text('Collected by the DSA lab pytest adapter.\n')
    config = plugin.output / 'pytest.ini'
    config.write_text('[pytest]\naddopts =\n')
    plugin.save()
    try:
        code = pytest.main([str(plugin.manifest), '-c', str(config), '--confcutdir', str(plugin.output),
            '--disable-plugin-autoload', '-p', 'no:cacheprovider', '-q', '-ra', '--tb=short',
            '--color=no', '-o', 'console_output_style=classic', '--capture=no' if live else '--capture=fd'],
            plugins=[plugin])
        result['pytest_exit_code'] = int(code)
        if code not in (pytest.ExitCode.OK, pytest.ExitCode.TESTS_FAILED):
            result['error'] = 'pytest ended with exit code %d' % code
        return int(code)
    finally:
        if plugin.active is not None:
            try:
                plugin.finish(plugin.active.name)
            except Exception as exc:
                result['cleanup_errors'].append(str(exc))
        plugin.save()
