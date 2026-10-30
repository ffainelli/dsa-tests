"""Regressions from the complete EA4500 October 25 run."""
import importlib.util
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))
import linux_switch_suite as scenarios


def case(cls, method):
    obj = cls(method)
    obj.br = 'testbridge'
    obj.ports = ['port%d' % i for i in range(4)]
    obj.hosts = ['usb%d' % i for i in range(4)]
    obj.bridge = Mock()
    obj.probe = Mock()
    obj.cmd = Mock(return_value='')
    obj.sut = Mock()
    return obj


def test_stp_disabled_link_local_bridge_group_is_forwarded():
    obj = case(scenarios.LinuxBridge, 'test_reserved_link_local_not_forwarded')
    obj.test_reserved_link_local_not_forwarded()
    assert obj.probe.call_count == 16
    for i, call in enumerate(obj.probe.call_args_list):
        assert call.kwargs['destination'] == 0x0180c2000000 + i
        assert call.kwargs['receivers'] == ((1, 2) if i == 0 else ())


def test_short_ageing_checks_effective_value_and_offload_errors():
    obj = case(scenarios.LinuxFdb, 'test_dynamic_ageing')
    obj.read_json = Mock(return_value=[dict(linkinfo=dict(info_data=dict(ageing_time=3000)))])
    obj.short_ageing()
    assert 'ageing_time 3000' in obj.cmd.call_args.args[0]
    obj.cmd.return_value = 'bridge: failed (err=-34) to set attribute (id=6)'
    with pytest.raises(AssertionError, match='offload was rejected'):
        obj.short_ageing()
    obj.cmd.return_value = ''
    obj.read_json.return_value[0]['linkinfo']['info_data']['ageing_time'] = 30000
    with pytest.raises(AssertionError):
        obj.short_ageing()


def test_jumbo_skips_host_limit_before_changing_either_interface():
    obj = case(scenarios.LinuxMtu, 'test_jumbo_port0')
    obj.read_json = Mock(return_value=[dict(mtu=1500)])
    obj.local_ping = Mock()
    with patch('lab_support.run', return_value=Mock(stdout=json.dumps([dict(mtu=1500, max_mtu=1500)]))) as run:
        with pytest.raises(unittest.SkipTest, match='usb0 max_mtu=1500'):
            obj.jumbo(0)
        assert run.call_count == 1
    obj.cmd.assert_not_called()
    obj.local_ping.assert_not_called()


def test_jumbo_capable_host_runs_probe_and_restores_mtus():
    obj = case(scenarios.LinuxMtu, 'test_jumbo_port0')
    obj.read_json = Mock(return_value=[dict(mtu=1500)])
    obj.local_ping = Mock()
    with patch('lab_support.run', return_value=Mock(stdout=json.dumps([dict(mtu=1500, max_mtu=9000)]))) as run:
        obj.jumbo(0)
        obj.local_ping.assert_called_once_with('port0', host_index=0, size=1972)
        obj.doCleanups()
        assert run.call_args.args == ('ip', 'link', 'set', 'dev', 'usb0', 'mtu', '1500')
    assert obj.cmd.call_args.args[0] == 'ip link set dev port0 mtu 1500'


def test_flush_requires_a_master_entry_and_checks_static_forwarding():
    obj = case(scenarios.LinuxBridgeControl, 'test_fdb_flush_dynamic_preserves_static')
    obj.cmd.side_effect = ['', '02:00:00:00:bb:21 master testbridge', '', '',
        '02:00:00:00:bb:22 master testbridge static\n02:00:00:00:bb:21 vlan 4095 self']
    obj.test_fdb_flush_dynamic_preserves_static()
    assert obj.probe.call_args.kwargs == dict(source=1, destination=0x02000000bb22, receivers=(0,))


def test_backup_config_does_not_claim_hardware_transit_failover():
    obj = case(scenarios.LinuxBridgeControl, 'test_backup_port')
    obj.read_json = Mock(return_value=[dict(backup_port='port2')])
    obj.wait_links = Mock()
    obj.test_backup_port()
    assert obj.probe.call_count == 2
    assert all(call.kwargs['receivers'] == (1,) for call in obj.probe.call_args_list)
    obj.sut.down.assert_called_once_with('port1')
    assert obj.cmd.call_args.args[0] == 'bridge link set dev port1 nobackup_port'


def test_legacy_blocked_port_skips_missing_stp_ownership_before_changes():
    spec = importlib.util.spec_from_file_location('legacy_blocked_case', ROOT / 'tests/bridge_4_ports_test.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.SUT = Mock(exit_code=255)
    module.SUT.ssh.return_value = 'bridge: unknown command "stp_mode"?'
    obj = module.BridgeFourPorts('test_04_blocked_port')
    with pytest.raises(unittest.SkipTest):
        obj.test_04_blocked_port()
    module.SUT.checked.assert_not_called()
