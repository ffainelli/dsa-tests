#!/usr/bin/env python3
"""Boot upstream kernels over UART, run legacy, VLAN and Linux switch scenario tests."""
import argparse
import datetime
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tomllib
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'tests'))
from host import HOST
from lab_support import HostLinks, check_b53_cpu_port, discover, run
from params import dotdict
from traffic import Traffic
from uart import UART, UartSUT, load_boot_smoke, user_path

SUITES = {
    'individual': 'ping_individual_4_ports_test.py',
    'bridge-ping': 'ping_bridges_4_ports_test.py',
    'bridge': 'bridge_4_ports_test.py',
    'two-bridges': '2_bridges_4_ports_test.py',
    'multicast': 'igmp_4_ports_test.py',
    'mac-learning': 'macs_4_ports_test.py',
}


# These classes own and clean up each scenario independently.
SUITE_CLASSES = {
    'b53-regressions': ('b53_regressions_suite.py', ('B53Regressions',)),
    'dsa-docs': ('dsa_docs_suite.py', ('DsaDocumentation',)),
    'home-office': ('home_office_suite.py', ('HomeOffice',)),
    'vlan': ('vlan_suite.py', ('VlanTermination', 'VlanLocalAdmission')),
    'vlan-admission': ('vlan_suite.py', ('VlanAdmission',)),
    'ping-sizes': ('vlan_suite.py', ('PingSizes',)),
    'linux-bridge': ('linux_switch_suite.py', ('LinuxBridge', 'LinuxBridgeControl', 'LinuxStpMode', 'LinuxVlanBinding', 'LinuxMtu')),
    'linux-fdb': ('linux_switch_suite.py', ('LinuxFdb',)),
    'linux-mdb': ('linux_switch_suite.py', ('LinuxMdb',)),
    'linux-locked-port': ('linux_switch_suite.py', ('LinuxLockedPort',)),
    'linux-no-forwarding': ('linux_switch_suite.py', ('LinuxNoForwarding',)),
    'linux-tc': ('linux_switch_suite.py', ('LinuxTc',)),
    'linux-routing': ('linux_switch_suite.py', ('LinuxRouting',)),
}
SUITES.update({name: entry[0] for name, entry in SUITE_CLASSES.items()})

PROFILES = {
    'home-office': ['home-office', 'dsa-docs', 'individual', 'multicast'],
    'all': list(SUITES),
}


def load_suite(name):
    spec = importlib.util.spec_from_file_location('dsa_' + name.replace('-', '_'), ROOT / 'tests' / SUITES[name])
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if name in SUITE_CLASSES:
        classes = SUITE_CLASSES[name][1]
        def load_tests(loader, _tests, _pattern):
            return unittest.TestSuite(loader.loadTestsFromTestCase(getattr(module, cls)) for cls in classes)
        module.load_tests = load_tests
    return module


class Results(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.records = []

    def startTest(self, test):
        super().startTest(test)
        self.current = {'name': test.id(), 'status': 'PASS'}
        self.records.append(self.current)

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self.current.update(status='FAIL', error=self._exc_info_to_string(err, test))

    def addError(self, test, err):
        super().addError(test, err)
        self.current.update(status='ERROR', error=self._exc_info_to_string(err, test))

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        if self.current['status'] == 'PASS':
            self.current.update(status='SKIP', reason=reason)

    def addSubTest(self, test, subtest, err):
        super().addSubTest(test, subtest, err)
        if err:
            self.current.update(status='FAIL', error=self._exc_info_to_string(err, subtest))


def test_ids(suite):
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            yield from test_ids(test)
        else:
            yield test.id()


def mark_skip(module, methods, reason):
    for obj in vars(module).values():
        if isinstance(obj, type) and issubclass(obj, unittest.TestCase):
            for method in methods:
                if hasattr(obj, method):
                    setattr(obj, method, unittest.skip(reason)(getattr(obj, method)))


def write_junit(path, result):
    records = [record for suite in result['suites'] for record in suite.get('tests', [])]
    infrastructure_error = result.get('error') or '; '.join(result.get('cleanup_errors', []))
    root = ET.Element('testsuite', name=result['board'], tests=str(len(records) + bool(infrastructure_error)),
        failures=str(sum(r['status'] == 'FAIL' for r in records)),
        errors=str(sum(r['status'] == 'ERROR' for r in records) + bool(infrastructure_error)),
        skipped=str(sum(r['status'] in ('SKIP', 'NOT_RUN') for r in records)))
    for record in records:
        case = ET.SubElement(root, 'testcase', name=record['name'])
        tag = {'FAIL': 'failure', 'ERROR': 'error', 'SKIP': 'skipped', 'NOT_RUN': 'skipped'}.get(record['status'])
        if tag:
            ET.SubElement(case, tag).text = record.get('error', record.get('reason', ''))
    if infrastructure_error:
        case = ET.SubElement(root, 'testcase', name='lab infrastructure')
        ET.SubElement(case, 'error').text = infrastructure_error
    ET.indent(root)
    ET.ElementTree(root).write(path, encoding='unicode', xml_declaration=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=ROOT / 'board-configs' / 'lab.toml')
    parser.add_argument('--board', required=True)
    parser.add_argument('--profile', choices=PROFILES, default='home-office',
                        help='Default: realistic home-office scenarios; all includes diagnostics/stress')
    parser.add_argument('--suite', action='append', choices=SUITES, help='Repeat to override profile suite selection')
    parser.add_argument('--output', type=Path, default=ROOT / 'results')
    parser.add_argument('--attach', action='store_true', help='Use an already booted root shell')
    parser.add_argument('--plan', action='store_true', help='List settings and tests without accessing hardware')
    parser.add_argument('--list-tests', action='store_true', help='List individual test IDs with --plan')
    parser.add_argument('--vlan-filtering', action='store_true')
    parser.add_argument('--strict-counters', action='store_true')
    parser.add_argument('--live-log', action='store_true', help='Show UART/command output during pytest progress')
    args = parser.parse_args()
    cfg = tomllib.loads(args.config.read_text())
    board = cfg['boards'][args.board]
    interfaces = board['host_interfaces']
    suites = args.suite or PROFILES[args.profile]
    profile = 'custom' if args.suite else args.profile
    if len(set(suites)) != len(suites):
        parser.error('Each suite may be selected only once')
    for value in interfaces + board['ports'] + [board['master']]:
        if not re.fullmatch(r'[A-Za-z0-9_.-]{1,15}', value):
            parser.error('Invalid network interface name: ' + value)
    if len(set(interfaces)) != len(interfaces):
        parser.error('Duplicate host interface')
    if args.plan:
        print(json.dumps({'board': args.board, 'profile': profile, 'settings': board,
            'ready': len(interfaces) == 4,
            'blockers': [] if len(interfaces) == 4 else ['Four host cables are required; configured: %d' % len(interfaces)],
            'suites': {name: unittest.defaultTestLoader.loadTestsFromModule(load_suite(name)).countTestCases()
                       for name in suites},
            **({'tests': {name: list(test_ids(unittest.defaultTestLoader.loadTestsFromModule(load_suite(name))))
                          for name in suites}} if args.list_tests else {})}, indent=2))
        return 0
    try:
        from pytest_lab import run_tests
    except ImportError:
        parser.error('Install pytest: .venv/bin/pip install -r requirements.txt')
    if len(interfaces) != 4:
        parser.error('Four dedicated host cables are required; %s has %d configured' % (args.board, len(interfaces)))
    if os.geteuid() != 0:
        parser.error('Run with sudo/root: NET_ADMIN and NET_RAW are needed for ip, mz and tcpdump')
    for binary in ('ip', 'bridge', 'ping', 'tcpdump'):
        if not shutil.which(binary):
            parser.error('Missing host utility: ' + binary)
    if not (shutil.which('mz') or shutil.which('mausezahn')):
        parser.error('Install mz or mausezahn')
    # Serialize whole lab, including link discovery and console/PDU access.
    lock = os.fdopen(os.open('/tmp/dsa-tests-lab.lock',
        os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600), 'w')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        parser.error('Another DSA lab run is active')
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    output = args.output / args.board / stamp
    output.mkdir(parents=True)
    result = {'board': args.board, 'profile': profile, 'status': 'FAIL', 'suites': [], 'cleanup_errors': [],
              'boot_config': cfg['boot_config'], 'output': str(output),
              'counter_map': board.get('counter_map', {}),
              'strict_counters': args.strict_counters,
              'linux_uart': {**cfg.get('linux_uart', {}), **board.get('linux_uart', {})}}
    links = HostLinks(interfaces, output)
    uart = sut = host = debug = None
    try:
        result['recovered_host_namespaces'] = links.recover()
        links.snapshot()  # Read only; keep TFTP host configuration until boot has completed.
        boot_config = user_path(cfg['boot_config'])
        result['boot_config'] = str(boot_config)
        lab = tomllib.loads(boot_config.read_text())
        boot = load_boot_smoke(cfg['boot_smoke'])
        uart = UART(boot, lab, board['target'], board.get('mode', 'arm'), output, args.attach,
                    linux_uart={**cfg.get('linux_uart', {}), **board.get('linux_uart', {})})
        uart.console.live_log = args.live_log
        uart.start()
        sut = UartSUT(uart, board['ports'], board['master'], args.strict_counters, board.get('counter_map'))
        sut.checked('command -v ip && command -v bridge && command -v ethtool')
        result['kernel'] = sut.checked('uname -a')
        result['cmdline'] = sut.checked('cat /proc/cmdline')
        result['cpu_port'] = check_b53_cpu_port(sut, board['ports'][0])
        links.isolate()
        result['host_namespaces'] = dict(links.namespaces)
        result['cabling'] = discover(sut, interfaces, board['ports'], output)
        (output / 'cabling.json').write_text(json.dumps(result['cabling'], indent=2) + '\n')
        config = dotdict(hostname=args.board, SUT_MASTER=board['master'])
        for index, interface in enumerate(interfaces):
            config['HOST_LAN%d' % index] = interface
            config['SUT_LAN%d' % index] = result['cabling'][interface]
        # Save and restore board port MACs because the individual-port test changes one.
        result['board_macs'] = {port: sut.getMacAddress(port) for port in board['ports']}
        def prepare_suite(name, module):
            nonlocal host, debug
            folder = output / name
            folder.mkdir(exist_ok=True)
            sut.cleanSystem()
            host = HOST()
            for interface in interfaces:
                host.addInterface(interface)
            host.cleanSystem()
            traffic = Traffic(interfaces, folder / 'captures')
            module.SUT, module.HOST, module.CONFIG, module.TRAFFIC = sut, host, config, traffic
            module.VLAN_FILTERING = args.vlan_filtering
            if name in SUITE_CLASSES:
                for cls in SUITE_CLASSES[name][1]:
                    getattr(module, cls).ENV = (sut, config, folder)
            if name == 'mac-learning':
                # Collision hashes in these two old tests target 6352/6390,
                # whereas EA4500 uses 6171 and the other boards use b53.
                mark_skip(module, ['test_07_atu_full_violation', 'test_08_atu_member_violation'],
                          'Legacy collision hashes require Marvell 6352/6390; none of these boards use them')
            if name == 'multicast':
                from multicast_debug import MulticastDebug
                debug = MulticastDebug(sut, config, folder)
                module.DEBUG = debug
                debug.start()
                sut.ssh('command -v join-mcast-group')
                if sut.exit_code:
                    mark_skip(module, ['test_08_host_join', 'test_09_multicast_lan1', 'test_10_host_leave'],
                              'Target lacks optional join-mcast-group helper; switch snooping tests still run')

        def finish_suite(name):
            nonlocal host, debug
            if debug is not None:
                debug.close()
                debug = None
            if host is not None:
                host.cleanSystem()
                host.sock.close()
                host = None
            sut.cleanSystem()

        specs = [(name, load_suite(name), name in SUITE_CLASSES) for name in suites]
        run_tests(specs, Results, prepare_suite, finish_suite, output, result, args.live_log)
        result['unavailable_counters'] = sorted(sut.unavailable_counters)
        result['status'] = ('PASS' if all(s['pass'] for s in result['suites'])
                            and result.get('pytest_exit_code') == 0 else 'FAIL')
    except Exception as exc:
        result['error'] = '%s: %s' % (type(exc).__name__, exc)
    finally:
        if debug is not None:
            try:
                debug.close()
            except Exception as exc:
                result['cleanup_errors'].append(str(exc))
        if host:
            try:
                host.cleanSystem()
                host.sock.close()
            except Exception as exc:
                result['cleanup_errors'].append(str(exc))
        if sut:
            result['unavailable_counters'] = sorted(sut.unavailable_counters)
            try:
                sut.cleanSystem()
                for port, mac in result.get('board_macs', {}).items():
                    sut.setMacAddress(port, mac)
            except Exception as exc:
                result['cleanup_errors'].append(str(exc))
        if uart:
            try:
                uart.close()
                if uart.console.kernel_panic:
                    result['error'] = uart.console.kernel_panic
            except Exception as exc:
                result['cleanup_errors'].append(str(exc))
        result['cleanup_errors'].extend(links.restore())
        if result.get('error') or result['cleanup_errors']:
            result['status'] = 'FAIL'
        (output / 'results.json').write_text(json.dumps(result, indent=2) + '\n')
        write_junit(output / 'junit.xml', result)
        lock.close()
    print('%s: %s (%s)' % (args.board, result['status'], output))
    if result.get('error'):
        print(result['error'], file=sys.stderr)
    return 0 if result['status'] == 'PASS' else 1


if __name__ == '__main__':
    sys.exit(main())
