"""Regressions for the actual EA4500 UART/FDB failures in the October 25 run."""
import asyncio
import json
from pathlib import Path
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import AsyncMock, Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))
import dsa_docs_suite
from uart import UART


class FdbCorrections(unittest.TestCase):
    def case(self, method):
        case = dsa_docs_suite.DsaDocumentation(method)
        case.ports = ['socket_a', 'socket_b', 'socket_c', 'socket_d']
        case.br = 'owned_bridge'
        case.bridge = Mock()
        case.probe = Mock()
        case.cmd = Mock()
        return case

    def test_self_static_expected_rejections_still_check_packet_forwarding(self):
        for output in ('RTNETLINK answers: Invalid argument', 'RTNETLINK answers: Operation not supported'):
            with self.subTest(output=output):
                case = self.case('test_self_static_is_not_a_hardware_forwarding_entry')
                case.attempt = Mock(return_value=(255, output))
                case.test_self_static_is_not_a_hardware_forwarding_entry()
                self.assertEqual(case.probe.call_count, 2)  # Baseline and after rejection.
                case.doCleanups()
                case.cmd.assert_not_called()  # Never delete an address we did not install.

    def test_self_static_unexpected_error_is_not_accepted(self):
        case = self.case('test_self_static_is_not_a_hardware_forwarding_entry')
        case.attempt = Mock(return_value=(255, 'RTNETLINK answers: Permission denied'))
        with self.assertRaises(AssertionError):
            case.test_self_static_is_not_a_hardware_forwarding_entry()
        self.assertEqual(case.probe.call_count, 1)

    def test_self_static_printk_interruption_preserves_expected_rejection(self):
        for output in (
            'RTNETLINK [14090.762262] b53-srab-switch 18007000.ethernet-switch lan2: '
            'default FDB implementation only supports local addresses\r\n'
            'answers: Invalid argument\r\n',
            'RTNET[123.456] diagnostic\nLINK answers:\n Operation not supported',
        ):
            with self.subTest(output=output):
                case = self.case('test_self_static_is_not_a_hardware_forwarding_entry')
                case.attempt = Mock(return_value=(2, output))
                case.test_self_static_is_not_a_hardware_forwarding_entry()
                self.assertEqual(case.probe.call_count, 2)

    def test_printk_does_not_hide_an_unexpected_fdb_error(self):
        case = self.case('test_self_static_is_not_a_hardware_forwarding_entry')
        case.attempt = Mock(return_value=(2,
            'RTNETLINK [123.456] Invalid argument\nanswers: Permission denied'))
        with self.assertRaises(AssertionError):
            case.test_self_static_is_not_a_hardware_forwarding_entry()
        self.assertEqual(case.probe.call_count, 1)

    def test_self_static_success_has_cleanup_and_forwarding_check(self):
        case = self.case('test_self_static_is_not_a_hardware_forwarding_entry')
        case.attempt = Mock(return_value=(0, ''))
        case.test_self_static_is_not_a_hardware_forwarding_entry()
        self.assertEqual(case.probe.call_count, 2)
        case.doCleanups()
        self.assertIn('self static', case.cmd.call_args.args[0])

    def test_self_local_roundtrip_does_not_assume_hardware_flood_suppression(self):
        case = self.case('test_implicit_local_fdb_is_device_local')
        case.attempt = Mock(return_value=(255, 'RTNETLINK answers: File exists'))
        # Both presence and absence of a hardware self entry are legitimate.
        for rows in ([], [{'mac': '02:00:00:00:dd:02', 'flags': ['self'], 'state': 'permanent'}]):
            case.read_json = Mock(return_value=rows)
            case.test_implicit_local_fdb_is_device_local()
            case.doCleanups()
        case.probe.assert_not_called()
        commands = [call.args[0] for call in case.cmd.call_args_list]
        self.assertEqual(sum('fdb add' in c for c in commands), 4)
        self.assertEqual(sum('fdb del' in c for c in commands), 4)

    def test_self_local_must_not_create_a_bridge_master_entry(self):
        case = self.case('test_implicit_local_fdb_is_device_local')
        case.attempt = Mock(return_value=(255, 'RTNETLINK answers: File exists'))
        case.read_json = Mock(return_value=[{'mac': '02:00:00:00:dd:02', 'master': 'owned_bridge'}])
        with self.assertRaisesRegex(AssertionError, 'unexpectedly entered the bridge FDB'):
            case.test_implicit_local_fdb_is_device_local()
        case.doCleanups()
        self.assertIn('fdb del', case.cmd.call_args.args[0])

    def test_self_local_duplicate_add_must_be_rejected(self):
        case = self.case('test_implicit_local_fdb_is_device_local')
        case.attempt = Mock(return_value=(0, ''))
        with self.assertRaisesRegex(AssertionError, 'Duplicate device-local'):
            case.test_implicit_local_fdb_is_device_local()
        case.doCleanups()


class UartCorrections(unittest.IsolatedAsyncioTestCase):
    def case(self):
        uart = object.__new__(UART)
        uart.printk_time_before = None
        uart.console_printk_before = None
        uart.console_loglevel = 5
        uart.console = Mock(connected=True, kernel_panic=None)
        uart.console.close = AsyncMock()
        return uart

    async def test_missing_marker_reports_shell_failure_and_never_retries(self):
        uart = self.case()
        uart.console.command = AsyncMock(return_value=(2,
            'sh: line 0: syntax error: unterminated quoted string'))
        with self.assertRaisesRegex(RuntimeError, 'exit status 2.*unterminated quoted string'):
            await uart._command('ip link set socket_a master bridge', 10)
        uart.console.command.assert_awaited_once()

    async def test_missing_marker_with_success_status_is_still_an_error(self):
        uart = self.case()
        uart.console.command = AsyncMock(return_value=(0, 'corrupted response'))
        with self.assertRaisesRegex(RuntimeError, 'exit status 0'):
            await uart._command('true', 10)
        uart.console.command.assert_awaited_once()

    async def test_console_level_preserves_other_values_and_restores_exactly(self):
        uart = self.case()
        uart._command = AsyncMock(side_effect=[(0, '7\t4\t1\t7'), (0, ''), (0, '')])
        await uart._configure_console_loglevel()
        self.assertEqual(uart.console_printk_before, '7 4 1 7')
        self.assertIn("'5 4 1 7'", uart._command.await_args_list[1].args[0])
        await uart._close()
        self.assertIn("'7 4 1 7'", uart._command.await_args_list[2].args[0])
        uart.console.close.assert_awaited_once()

    async def test_bad_console_readback_is_not_written(self):
        uart = self.case()
        uart._command = AsyncMock(return_value=(0, 'corrupted'))
        with self.assertRaisesRegex(RuntimeError, 'Cannot read kernel console loglevel'):
            await uart._configure_console_loglevel()
        uart._command.assert_awaited_once()
        self.assertIsNone(uart.console_printk_before)

    async def test_restore_failure_still_restores_timestamps_and_closes(self):
        uart = self.case()
        uart.console_printk_before = '7 4 1 7'
        uart.printk_time_before = 'N'
        uart._command = AsyncMock(side_effect=[(1, 'restore failed'), (0, '')])
        with self.assertRaisesRegex(RuntimeError, 'Cannot restore kernel console loglevel'):
            await uart._close()
        self.assertEqual(uart._command.await_count, 2)
        self.assertIn('/sys/module/printk/parameters/time', uart._command.await_args_list[1].args[0])
        uart.console.close.assert_awaited_once()

    def test_board_uart_overrides_preserve_global_defaults(self):
        cfg = tomllib.loads((ROOT / 'board-configs/lab.toml').read_text())
        defaults = cfg['linux_uart']
        ea = {**defaults, **cfg['boards']['ea4500']['linux_uart']}
        sr = {**defaults, **cfg['boards']['sr400ac'].get('linux_uart', {})}
        self.assertEqual((ea['write_chunk_size'], ea['write_delay_s'], ea['console_loglevel']), (4, .010, 5))
        self.assertEqual((sr['write_chunk_size'], sr['write_delay_s'], sr['console_loglevel']), (4, .010, 5))
        self.assertEqual((defaults['write_chunk_size'], defaults['write_delay_s']), (16, .005))
        self.assertNotIn('console_loglevel', defaults)
