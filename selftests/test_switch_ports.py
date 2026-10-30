"""Offline checks for VLAN wire encoding, scenario discovery and failure cleanup."""
import importlib.util
import io
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))
from traffic import Traffic, checksum, tag_frame, udp_frame, vlan_stack, signed_frame
from switch_case import SwitchCase


def runner():
    spec = importlib.util.spec_from_file_location('lab_runner', ROOT / 'run_lab.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class VlanWireTests(unittest.TestCase):
    def test_stacked_tags_preserve_checksums_and_priority(self):
        original = udp_frame(1, 2, '192.0.2.1', '192.0.2.2', b'current-run')
        tags = ((0x88a8, 0xb003), (0x8100, 0xa064))
        frame = tag_frame(original, tags)
        self.assertEqual(frame[12:22], bytes.fromhex('88a8b0038100a0640800'))
        self.assertEqual(vlan_stack(frame), tags)
        self.assertEqual(frame[:12] + frame[20:], original)
        self.assertEqual(checksum(frame[22:42]), 0)
        self.assertEqual(vlan_stack(original), ())

    def test_quoted_test_packet_is_not_counted_as_delivery(self):
        signature = b'DSATEST:quoted'
        packet = udp_frame(1, 2, '192.0.2.1', '192.0.2.2', signature)
        self.assertTrue(signed_frame(packet, signature))
        self.assertTrue(signed_frame(tag_frame(packet, ((0x8100, 100),)), signature))
        # An ICMP error can include the entire original UDP payload.
        quoted = bytearray(packet[:34] + b'\x03' + b'\0' * 7 + packet[14:])
        quoted[23] = 1
        self.assertIn(signature, quoted)
        self.assertFalse(signed_frame(quoted, signature))

    def test_priority_and_boundary_vids(self):
        frame = udp_frame(1, 2, 'fd42::1', 'fd42::2', b'signed')
        for tci in (0, 1, 100, 4094, 0xa000, 0xffff):
            tagged = tag_frame(frame, ((0x8100, tci),))
            self.assertEqual(vlan_stack(tagged), ((0x8100, tci),))
            self.assertEqual(tagged[16:], frame[12:])
        for tags in (((0x1234, 1),), ((0x8100, -1),), ((0x8100, 65536),)):
            with self.assertRaises(ValueError):
                tag_frame(frame, tags)

    @unittest.skipUnless(shutil.which('tcpdump'), 'tcpdump not installed')
    def test_real_tcpdump_decodes_signed_tagged_and_nonip_frames(self):
        signature = b'DSATEST:tagged'
        frame = udp_frame(1, 2, '192.0.2.1', '192.0.2.2', signature)
        frames = [tag_frame(frame, ((0x8100, 100),)),
                  tag_frame(frame, ((0x88a8, 3), (0x8100, 5))),
                  frame[:12] + b'\x88\xb5' + signature.ljust(114, b'\0')]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'wire.pcap'
            data = b'\xd4\xc3\xb2\xa1' + struct.pack('<HHIIII', 2, 4, 0, 0, 65535, 1)
            for packet in frames:
                data += struct.pack('<IIII', 0, 0, len(packet), len(packet)) + packet
            path.write_bytes(data)
            result = subprocess.run(['tcpdump', '-ennvv', '-r', str(path)], capture_output=True,
                                    text=True, check=True)
            self.assertIn('vlan 100', result.stdout)
            self.assertIn('vlan 3', result.stdout)
            self.assertIn('vlan 5', result.stdout)
            self.assertIn('0x88b5', result.stdout)
            self.assertEqual(result.stdout.count('udp sum ok'), 2)

    @patch('traffic.shutil.which', return_value='/usr/bin/tool')
    def test_mz_serializes_tagged_and_nonip_streams(self, _which):
        with tempfile.TemporaryDirectory() as folder:
            traffic = Traffic(['one'], folder, mz='mz'); traffic.addInterface('one')
            traffic.addPacketStream('one', tags=((0x8100, 4094),))
            traffic.addPacketStream('one', ethertype=0x88b5)
            with patch('traffic.subprocess.run') as run:
                traffic._send(list(enumerate(traffic.streams)), b'ours', Path(folder))
            frames = [bytes.fromhex(call.args[0][-1].replace(':', '')) for call in run.call_args_list]
            self.assertEqual(vlan_stack(frames[0]), ((0x8100, 4094),))
            self.assertEqual(frames[1][12:14], b'\x88\xb5')
            self.assertTrue(all(b'ours' in f for f in frames))


class ScenarioTests(unittest.TestCase):
    def test_every_board_plans_new_suites_without_hardware(self):
        lab = runner()
        names = []
        for name in lab.SUITE_CLASSES:
            suite = unittest.defaultTestLoader.loadTestsFromModule(lab.load_suite(name))
            ids = list(lab.test_ids(suite))
            self.assertTrue(ids, name)
            names += ids
        self.assertEqual(len(names), len(set(names)))
        for board in ('ea4500', 'sr400ac', 'lamobo-r1'):
            result = subprocess.run([sys.executable, str(ROOT / 'run_lab.py'), '--board', board,
                '--plan', '--list-tests', '--profile', 'all'], capture_output=True, text=True, check=True)
            plan = json.loads(result.stdout)
            self.assertTrue(plan['ready'])
            self.assertEqual(sum(plan['suites'][s] for s in lab.SUITE_CLASSES), len(names))

    def test_default_profile_focuses_on_home_office(self):
        result = subprocess.run([sys.executable, str(ROOT / 'run_lab.py'), '--board', 'sr400ac',
                                 '--plan'], capture_output=True, text=True, check=True)
        plan = json.loads(result.stdout)
        self.assertEqual(plan['profile'], 'home-office')
        self.assertEqual(set(plan['suites']), {'home-office', 'dsa-docs', 'individual', 'multicast'})
        self.assertNotIn('mac-learning', plan['suites'])
        self.assertNotIn('linux-tc', plan['suites'])

    def test_documented_topologies_select_runtime_tagger_recipe(self):
        lab = runner()
        cls = lab.load_suite('dsa-docs').DsaDocumentation
        for protocol in ('dsa', 'brcm', 'none'):
            for mode, method in (('single', 'test_single_port_showcase'),
                                 ('bridge', 'test_bridge_showcase'),
                                 ('gateway', 'test_gateway_showcase')):
                with self.subTest(protocol=protocol, mode=mode):
                    case = cls(method)
                    case.ports = ['socket_a', 'socket_b', 'socket_c', 'socket_d']
                    case.tagging = Mock(return_value=protocol)
                    case.bridge = Mock(return_value='owned_bridge')
                    case.none_layout = Mock(return_value={
                        'single': ['cpu_vlan1', 'cpu_vlan2', 'cpu_vlan3'],
                        'bridge': 'owned_bridge',
                        'gateway': ('owned_bridge', 'cpu_vlan2')}[mode])
                    case.local_ping = Mock(); case.probe = Mock()
                    getattr(case, method)()
                    self.assertEqual(case.local_ping.call_count, 3)
                    if protocol == 'none':
                        case.none_layout.assert_called_once_with(mode)
                    else:
                        case.none_layout.assert_not_called()
                    if mode == 'bridge':
                        self.assertEqual([call.kwargs['assign_board'] for call in
                                          case.local_ping.call_args_list], [True, False, False])

    def test_independent_scenarios_continue_after_failure(self):
        lab = runner()
        class Scenarios(unittest.TestCase):
            def test_a(self):
                self.fail('first scenario')
            def test_b(self):
                pass
        completed = unittest.TextTestRunner(stream=io.StringIO(), resultclass=lab.Results).run(
            unittest.defaultTestLoader.loadTestsFromTestCase(Scenarios))
        self.assertEqual([r['status'] for r in completed.records], ['FAIL', 'PASS'])

    def test_cleanup_runs_when_setup_fails_after_ports_are_touched(self):
        sut = Mock()
        sut.cleanSystem.side_effect = [None, None]
        sut.up.side_effect = RuntimeError('UART lost during setup')
        config = {'SUT_MASTER': 'eth2', **{'SUT_LAN%d' % i: 'lan%d' % i for i in range(4)},
                  **{'HOST_LAN%d' % i: 'host%d' % i for i in range(4)}}
        class Scenario(SwitchCase):
            def test_one(self):
                self.fail('must not run')
        with tempfile.TemporaryDirectory() as folder:
            Scenario.ENV = (sut, type('Config', (dict,), {'__getattr__': dict.__getitem__})(config), folder)
            completed = unittest.TextTestRunner(stream=io.StringIO()).run(Scenario('test_one'))
        self.assertEqual(len(completed.errors), 1)
        self.assertEqual(sut.cleanSystem.call_count, 2)

    def test_cpu_probe_removes_host_address_immediately_on_capture_error(self):
        case = SwitchCase(); case.cmd = Mock(); case.hosts = ['host0']
        with tempfile.TemporaryDirectory() as folder:
            case.folder = Path(folder)
            with patch('switch_case.run') as host_run, patch('switch_case.time.sleep'), \
                 patch('switch_case.subprocess.Popen', side_effect=RuntimeError('capture unavailable')):
                with self.assertRaisesRegex(RuntimeError, 'capture unavailable'):
                    case.local_ping('lan1')
                self.assertEqual(host_run.call_count, 2)
                self.assertEqual(host_run.call_args.args[:3], ('ip', 'addr', 'del'))
                case.doCleanups()
                self.assertEqual(host_run.call_count, 2)  # cleanup remains idempotent

    def test_json_uart_noise_is_removed_without_hiding_invalid_json(self):
        case = SwitchCase()
        case.cmd = Mock(return_value='[{"mtu": 15[   12.004] lan1: Link is Up\n00}]')
        self.assertEqual(case.read_json('ip -j link'), [{'mtu': 1500}])
        case.cmd.return_value = '[{"mtu": broken}]'
        with self.assertRaises(json.JSONDecodeError):
            case.read_json('ip -j link')

    def test_optional_capability_skip_does_not_mask_other_errors(self):
        sut = Mock()
        case = SwitchCase(); case.sut = sut; case.commands = []
        with tempfile.TemporaryDirectory() as folder:
            case.folder = Path(folder)
            sut.uart.command.return_value = (2, 'RTNETLINK answers: Operation not supported')
            with self.assertRaises(unittest.SkipTest):
                case.cmd('feature', optional=True)
            sut.uart.command.return_value = (2, 'RTNETLINK answers: Invalid argument')
            with self.assertRaises(AssertionError):
                case.cmd('malformed feature', optional=True)
            sut.uart.command.return_value = (2, 'RTNETLINK answers: Operation not supported')
            with self.assertRaises(AssertionError):
                case.cmd('mandatory feature')
