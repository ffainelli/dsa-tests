"""Offline regression checks: packet checksums, capture accounting and UART echo."""
import asyncio
import importlib.util
import io
import json
from pathlib import Path
import struct
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import AsyncMock, Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))
from traffic import Traffic, checksum, count_pcap, udp_frame
from uart import UART, UartSUT, user_path
from lab_support import discover


class UserPathTests(unittest.TestCase):
    @patch('uart.os.geteuid', return_value=0)
    @patch.dict('os.environ', {'SUDO_UID': '1000', 'HOME': '/root'})
    def test_sudo_expands_all_lab_paths_in_invoking_home(self, _uid):
        import pwd
        home = Path(pwd.getpwuid(1000).pw_dir)
        for relative in ('lab.toml', 'boot_smoke.py', 'dev/github/misc-scripts/wti-nps.py'):
            self.assertEqual(user_path('~/' + relative), home / relative)
        self.assertEqual(user_path('~'), home)
        self.assertEqual(user_path('/srv/tftp/image'), Path('/srv/tftp/image'))
        self.assertEqual(user_path('relative/config'), Path('relative/config'))

    @patch('uart.os.geteuid', return_value=1000)
    @patch.dict('os.environ', {'HOME': '/home/test-user', 'SUDO_UID': '0'})
    def test_normal_user_uses_current_home(self, _uid):
        self.assertEqual(user_path('~/lab.toml'), Path('/home/test-user/lab.toml'))

    @patch('uart.os.geteuid', return_value=0)
    @patch.dict('os.environ', {'HOME': '/root'}, clear=True)
    def test_root_without_sudo_uses_root_home(self, _uid):
        self.assertEqual(user_path('~/lab.toml'), Path('/root/lab.toml'))


class PacketTests(unittest.TestCase):
    def test_ipv4_checksums_and_signature(self):
        signature = b'DSATEST:unique'
        frame = udp_frame(0x1020304000, 0x1020304001, '192.168.58.10', '192.168.58.11', signature)
        self.assertEqual(len(frame), 128)
        ip, udp = frame[14:34], frame[34:]
        self.assertEqual(checksum(ip), 0)
        pseudo = ip[12:20] + struct.pack('!BBH', 0, 17, len(udp))
        self.assertEqual(checksum(pseudo + udp), 0)
        self.assertIn(signature, udp)
        self.assertEqual(struct.unpack('!H', udp[2:4])[0], 17185)

    def test_ipv6_checksum(self):
        frame = udp_frame(1, 2, 'fd42::10', 'fd42::11', b'IPv6')
        ip, udp = frame[14:54], frame[54:]
        self.assertEqual(len(frame), 128)
        pseudo = ip[8:40] + struct.pack('!I3xB', len(udp), 17)
        self.assertEqual(checksum(pseudo + udp), 0)

    def test_pcap_counts_only_current_run_both_endians(self):
        for endian, magic in [('<', b'\xd4\xc3\xb2\xa1'), ('>', b'\xa1\xb2\xc3\xd4')]:
            with self.subTest(endian=endian), tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / 'capture.pcap'
                data = magic + struct.pack(endian + 'HHIIII', 2, 4, 0, 0, 65535, 1)
                for marker in (b'ours', b'old-run', b'ours'):
                    frame = udp_frame(1, 2, '192.0.2.1', '192.0.2.2', marker)
                    data += struct.pack(endian + 'IIII', 0, 0, len(frame), len(frame)) + frame
                path.write_bytes(data)
                self.assertEqual(count_pcap(path, b'ours'), 2)
                path.write_bytes(data[:-1])
                with self.assertRaisesRegex(RuntimeError, 'Truncated'):
                    count_pcap(path, b'ours')

    @unittest.skipUnless(shutil.which('tcpdump'), 'tcpdump not installed')
    def test_real_tcpdump_decodes_both_ip_versions(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'packets.pcap'
            data = b'\xd4\xc3\xb2\xa1' + struct.pack('<HHIIII', 2, 4, 0, 0, 65535, 1)
            for src, dst in [('192.0.2.1', '192.0.2.2'), ('fd42::1', 'fd42::2')]:
                frame = udp_frame(1, 2, src, dst, b'DSATEST:tcpdump')
                data += struct.pack('<IIII', 0, 0, len(frame), len(frame)) + frame
            path.write_bytes(data)
            result = subprocess.run(['tcpdump', '-nn', '-vv', '-r', str(path),
                                     'udp', 'dst', 'port', '17185'],
                                    capture_output=True, text=True, check=True)
            self.assertEqual(result.stdout.count('udp sum ok'), 2, result.stdout)
            self.assertIn('192.0.2.2.17185', result.stdout)
            self.assertIn('fd42::2.17185', result.stdout)

    @patch('traffic.shutil.which', return_value='/usr/bin/tool')
    def test_mz_mac_stepping_and_finite_counts(self, _which):
        with tempfile.TemporaryDirectory() as folder:
            traffic = Traffic(['one', 'two'], folder, mz='mz')
            traffic.addInterface('one')
            traffic.addUDPMacIncStream('one', 'two', 0x001120300000, 3, 50, 2, 7)
            with patch('traffic.subprocess.run') as run, patch('traffic.time.sleep'):
                traffic._send(list(enumerate(traffic.streams)), b'ours', Path(folder))
            frames = [bytes.fromhex(call.args[0][-1].replace(':', '')) for call in run.call_args_list]
            self.assertEqual([int.from_bytes(f[6:12], 'big') for f in frames],
                             [0x001120300000, 0x001120300007, 0x001120300000])
            for call in run.call_args_list:
                argv = call.args[0]
                self.assertEqual(argv[argv.index('-c') + 1], '1')
                self.assertTrue(call.kwargs['check'])


class CaptureLifecycleTests(unittest.TestCase):
    def exercise(self, drop=False, startup_failure=False):
        processes = []
        class Capture:
            def __init__(self, argv, **kwargs):
                self.log = kwargs['stderr']
                self.path = Path(argv[argv.index('-w') + 1])
                self.interface = argv[argv.index('-i') + 1]
                self.direction = argv[argv.index('-Q') + 1]
                self.returncode = 1 if startup_failure else None
                self.log.write(b'permission denied\n' if startup_failure else b'listening on test\n')
                self.log.flush()
                processes.append(self)
            def poll(self):
                return self.returncode
            def send_signal(self, signal):
                self.returncode = 0
                signature = bytes.fromhex(json.loads((self.path.parent / 'streams.json').read_text())['signature'])
                frame = udp_frame(1, 2, '192.0.2.1', '192.0.2.2', signature)
                count = 3 if (self.interface, self.direction) in [('one', 'out'), ('two', 'in')] else 0
                data = b'\xd4\xc3\xb2\xa1' + struct.pack('<HHIIII', 2, 4, 0, 0, 65535, 1)
                data += (struct.pack('<IIII', 0, 0, len(frame), len(frame)) + frame) * count
                self.path.write_bytes(data)
                self.log.write(b'%d packets dropped by kernel\n' % (1 if drop else 0))
                self.log.flush()
            def wait(self, timeout=None):
                return self.returncode
        with tempfile.TemporaryDirectory() as folder, \
                patch('traffic.shutil.which', return_value='/bin/tool'), \
                patch('traffic.subprocess.Popen', Capture), \
                patch.object(Traffic, '_send'), patch('traffic.time.sleep'):
            traffic = Traffic(['one', 'two', 'three'], folder, mz='mz')
            for name in traffic.interfaces:
                traffic.addInterface(name)
            traffic.addUDPStream('one', 'two', 3, 100)
            traffic.run()
            self.assertEqual(traffic.getStats('one'), {'tx_pkts': 3, 'rx_pkts': 0})
            self.assertEqual(traffic.getStats('two'), {'tx_pkts': 0, 'rx_pkts': 3})
            self.assertEqual(traffic.getStats('three'), {'tx_pkts': 0, 'rx_pkts': 0})
            self.assertTrue(all(proc.returncode == 0 and proc.log.closed for proc in processes))

    def test_directional_capture_and_zero_receivers(self):
        self.exercise()

    def test_capture_drops_fail(self):
        with self.assertRaisesRegex(RuntimeError, 'dropped packets'):
            self.exercise(drop=True)

    def test_capture_start_failure_fails(self):
        with self.assertRaisesRegex(RuntimeError, 'did not start'):
            self.exercise(startup_failure=True)


class SerialTests(unittest.TestCase):
    def test_begin_marker_tolerates_kernel_output(self):
        class Console:
            async def command(self, command, timeout):
                import shlex
                parts = shlex.split(command.split(';', 1)[0])
                begin = parts[2] + parts[3]
                kernel = '[   67.701963] ethernet1: Link is Up\r\n'
                return 0, 'echoed: ' + command + '\r\n' + begin[:8] + kernel + begin[8:] + '\r\n123\r\n'
        uart = object.__new__(UART)
        uart.console = Console()
        self.assertEqual(asyncio.run(uart._command('example', 10)), (0, '123'))

    def test_echo_is_removed_exit_code_retained(self):
        class Console:
            async def command(self, command, timeout):
                # Simulate echoed command plus the split printf marker's output.
                import shlex
                parts = shlex.split(command.split(';', 1)[0])
                begin = parts[2] + parts[3]
                return 7, "# echoed: " + command + '\r\n' + begin + '\r\n123\r\n'
        uart = object.__new__(UART)
        uart.console = Console()
        code, output = asyncio.run(uart._command('cat /sys/test', 10))
        self.assertEqual((code, output), (7, '123'))

    def test_missing_counters_explicit_and_strict(self):
        sut = object.__new__(UartSUT)
        sut.strict_counters = False
        sut.unavailable_counters = set()
        self.assertEqual(sut._available({}, {}, {'in_unicast': (1, 2)}), {})
        self.assertEqual(sut.unavailable_counters, {'in_unicast'})
        sut.strict_counters = True
        with self.assertRaises(AssertionError):
            sut._available({}, {}, {'in_unicast': (1, 2)})


class ConsoleTimestampTests(unittest.IsolatedAsyncioTestCase):
    def make_uart(self):
        uart = object.__new__(UART)
        uart.printk_time_before = None
        uart.console = Mock(connected=True, kernel_panic=None)
        uart.console.close = AsyncMock()
        return uart

    async def test_untimestamped_console_is_enabled_then_restored(self):
        uart = self.make_uart()
        uart._command = AsyncMock(side_effect=[(0, 'N'), (0, ''), (0, '')])
        await uart._enable_kernel_timestamps()
        self.assertEqual(uart.printk_time_before, 'N')
        await uart._close()
        commands = [call.args[0] for call in uart._command.await_args_list]
        self.assertIn("printf 'Y", commands[1])
        self.assertIn(" N > /sys/module/printk/parameters/time", commands[2])
        uart.console.close.assert_awaited_once()

    async def test_existing_timestamps_need_no_changes(self):
        uart = self.make_uart()
        uart._command = AsyncMock(return_value=(0, 'Y'))
        await uart._enable_kernel_timestamps()
        await uart._close()
        self.assertIsNone(uart.printk_time_before)
        self.assertEqual(uart._command.await_count, 1)
        uart.console.close.assert_awaited_once()

    async def test_restore_error_still_closes_console(self):
        uart = self.make_uart()
        uart.printk_time_before = 'N'
        uart._command = AsyncMock(return_value=(1, 'write failed'))
        with self.assertRaisesRegex(RuntimeError, 'restore kernel console timestamps'):
            await uart._close()
        uart.console.close.assert_awaited_once()


class B53CounterTests(unittest.TestCase):
    def make_sut(self):
        sut = object.__new__(UartSUT)
        sut.interfaces = ['lan1']
        cfg = tomllib.loads((ROOT / 'board-configs/lab.toml').read_text())
        sut.counter_map = cfg['boards']['sr400ac']['counter_map']
        sut.strict_counters = True
        sut.unavailable_counters = set()
        sut.ssh = Mock()
        return sut

    def test_b53_packet_classes_are_checked_without_replacing_software_counts(self):
        sut = self.make_sut()
        sut.ssh.return_value = ('NIC statistics:\n    rx_packets: 17\n    tx_packets: 19\n'
            '    RxUnicastPkts: 100\n    TxUnicastPkts: 200\n'
            '    RxMulticastPkts: 300\n    TxMulticastPkts: 400\n')
        before = sut.getEthtoolStats('lan1')
        self.assertEqual((before['rx_packets'], before['tx_packets']), (17, 19))
        sut.ssh.return_value = ('NIC statistics:\n    rx_packets: 17\n    tx_packets: 19\n'
            '    RxUnicastPkts: 130\n    TxUnicastPkts: 210\n'
            '    RxMulticastPkts: 800\n    TxMulticastPkts: 900\n')
        expected = {'in_unicast': (30, 30), 'out_unicast': (10, 10),
                    'in_multicasts': (500, 500), 'out_multicasts': (500, 500)}
        sut.checkEthtoolStatsRange('lan1', before, expected, self)
        self.assertEqual(sut.unavailable_counters, set())
        with self.assertRaises(AssertionError):
            sut.checkEthtoolStatsRange('lan1', before, {'in_unicast': (31, 31)}, self)

    def test_missing_b53_counter_is_an_error_in_strict_mode(self):
        sut = self.make_sut()
        sut.ssh.return_value = 'NIC statistics:\n    RxUnicastPkts: 10\n'
        before = sut.getEthtoolStats('lan1')
        with self.assertRaisesRegex(AssertionError, 'out_unicast'):
            sut.checkEthtoolStatsRange('lan1', before, {'out_unicast': (0, 0)}, self)


class DiscoveryTests(unittest.TestCase):
    def exercise(self, mode='unique'):
        sut = Mock(master='eth0')
        traffic = Mock()
        interfaces = ['host-a', 'host-b']
        candidates = ['lan1', 'lan2']
        def fdb(command):
            interface = traffic.addUDPBroadcastStream.call_args.args[0]
            source = traffic.addUDPBroadcastStream.call_args.kwargs['source_mac']
            from traffic import mac_text
            mac = mac_text(source)
            port = 'lan2' if interface == 'host-a' or mode == 'duplicate' else 'lan1'
            if mode == 'missing':
                return '02:00:00:00:00:99 dev lan1 master other-bridge'
            if mode == 'ambiguous':
                return '%s dev lan1 master brdsa\n%s dev lan2 master brdsa' % (mac, mac)
            # Driver self/master entries on the same port are not ambiguous.
            return '%s dev %s master brdsa offload\n%s dev %s self' % (mac, port, mac, port)
        sut.checked.side_effect = fdb
        if mode == 'tx-error':
            traffic.run.side_effect = RuntimeError('mz failed')
        with tempfile.TemporaryDirectory() as folder, \
                patch('lab_support.time.sleep'), patch('lab_support.run') as host_run, \
                patch('lab_support.Traffic', return_value=traffic):
            try:
                mapping = discover(sut, interfaces, candidates, folder)
                self.assertEqual(mapping, {'host-a': 'lan2', 'host-b': 'lan1'})
                for call in traffic.addUDPBroadcastStream.call_args_list:
                    self.assertEqual(call.args[1:], (3, 100))
                    self.assertEqual(call.kwargs['source_mac'] >> 40, 2)
                self.assertEqual(traffic.run.call_count, 2)
            finally:
                sut.deleteBridge.assert_called_once_with(sut.addBridge.call_args.args[0])
                self.assertTrue(all(call.args[-1] == 'up' for call in host_run.call_args_list))

    def test_unique_packet_ingress_without_carrier_changes(self):
        self.exercise()

    def test_missing_probe_fails_and_removes_bridge(self):
        with self.assertRaisesRegex(RuntimeError, 'unique packet ingress'):
            self.exercise('missing')

    def test_ambiguous_probe_fails_and_removes_bridge(self):
        with self.assertRaisesRegex(RuntimeError, 'unique packet ingress'):
            self.exercise('ambiguous')

    def test_duplicate_board_port_fails(self):
        with self.assertRaisesRegex(RuntimeError, 'same board port'):
            self.exercise('duplicate')

    def test_generator_error_removes_temporary_bridge(self):
        with self.assertRaisesRegex(RuntimeError, 'mz failed'):
            self.exercise('tx-error')


if __name__ == '__main__':
    unittest.main()
