"""Ensure the hardware scenarios detect each fault and verify their fixtures."""
import binascii
import ipaddress
from pathlib import Path
import shlex
import sys
import unittest
from unittest.mock import Mock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))
import b53_regressions_suite as scenarios


class Hardware:
    def __init__(self, case, fault=None, saturate=True, offload=True):
        self.case, self.fault = case, fault
        self.saturate, self.offload = saturate, offload
        self.bins = [None] * 4
        self.entries, self.rules, self.masks = {}, {}, set()
        self.target = None
        self.commands = []

    def command(self, command):
        self.commands.append(command)
        args = shlex.split(command)
        if args[:2] == ['bridge', 'mdb']:
            operation, group = args[2], args[args.index('grp') + 1]
            port = args[args.index('port') + 1]
            if operation == 'add':
                if all(self.bins) and self.saturate:
                    return 2, 'RTNETLINK answers: No space left on device'
                mask = {port}
                if self.fault == 'r6' and self.bins[-1] is not None:
                    mask |= self.entries[self.bins[-1]]
                self.entries[group] = mask
                if None in self.bins:
                    self.bins[self.bins.index(None)] = group
            else:
                self.entries.pop(group)
                if group in self.bins:
                    self.bins[self.bins.index(group)] = None
            return 0, ''
        if args[:3] == ['tc', 'filter', 'add']:
            port, direction = args[4], args[5]
            pref, target = int(args[args.index('pref') + 1]), args[-1]
            if self.target is not None and self.target != target and self.fault != 'r7':
                return 2, 'Only one mirroring destination is supported: Operation not supported'
            self.target = target
            self.rules[(port, direction, pref)] = target
            self.masks.add((port, direction))
        elif args[:3] == ['tc', 'filter', 'del']:
            port, direction = args[4], args[5]
            pref = int(args[args.index('pref') + 1])
            del self.rules[(port, direction, pref)]
            if self.fault == 'r8' or not any(key[:2] == (port, direction) for key in self.rules):
                self.masks.discard((port, direction))
            if not self.masks:
                self.target = None
        elif args[:3] == ['tc', 'filter', 'show'] or args[:4] == ['tc', '-s', 'filter', 'show']:
            port, pref = args[args.index('dev') + 1], int(args[args.index('pref') + 1])
            direction = args[args.index('dev') + 2]
            return 0, 'in_hw' if (port, direction, pref) in self.rules else ''
        return 0, ''

    def cmd(self, command, optional=False):
        status, output = self.command(command)
        if status:
            raise AssertionError(output)
        return output

    def read_json(self, _command):
        return [dict(mdb=[dict(grp=group, port=port, flags=['offload'] if self.offload else [])
                          for group, ports in self.entries.items() for port in ports])]

    def probe(self, *, destination, receivers, destination_ip=None):
        if destination_ip is not None:
            observed = {self.case.ports.index(p) for p in self.entries.get(destination_ip, ())}
        else:
            observed = {1}
            if ('port0', 'ingress') in self.masks or ('port1', 'egress') in self.masks:
                observed.add(self.case.ports.index(self.target))
        assert observed == set(receivers), 'packet leakage or lost mirror: %s != %s' % (observed, receivers)


def make_case(method, tmp_path, **options):
    case = scenarios.B53Regressions(method)
    case.br, case.folder = 'testbridge', tmp_path
    case.ports = ['port%d' % i for i in range(4)]
    case.bridge = Mock()
    hardware = Hardware(case, **options)
    case.cmd, case.attempt = hardware.cmd, hardware.command
    case.read_json, case.probe = hardware.read_json, hardware.probe
    return case, hardware


METHODS = unittest.defaultTestLoader.getTestCaseNames(scenarios.B53Regressions)


@pytest.mark.parametrize('method', METHODS)
def test_correct_hardware_passes_each_scenario(method, tmp_path):
    case, hardware = make_case(method, tmp_path)
    with patch.object(scenarios.time, 'sleep'):
        getattr(case, method)()
    assert hardware.commands
    case.doCleanups()


@pytest.mark.parametrize('method', [m for m in METHODS if 'direction_memberships' not in m])
def test_each_regression_rejects_its_original_fault(method, tmp_path):
    fault = method.split('_')[1]
    case, _ = make_case(method, tmp_path, fault=fault)
    with patch.object(scenarios.time, 'sleep'), pytest.raises(AssertionError):
        getattr(case, method)()
    case.doCleanups()


def test_r6_skips_unverified_collision_fixture(tmp_path):
    case, _ = make_case('test_r6_mdb_reuses_hole_without_stale_ports', tmp_path, saturate=False)
    with patch.object(scenarios.time, 'sleep'), pytest.raises(unittest.SkipTest, match='did not saturate'):
        case.test_r6_mdb_reuses_hole_without_stale_ports()


def test_r6_skips_missing_hardware_offload_evidence(tmp_path):
    case, _ = make_case('test_r6_mdb_reuses_hole_without_stale_ports', tmp_path, offload=False)
    with patch.object(scenarios.time, 'sleep'), pytest.raises(unittest.SkipTest, match='could not be established'):
        case.test_r6_mdb_reuses_hole_without_stale_ports()


def test_collision_groups_share_full_crc_and_distinct_multicast_macs():
    groups = scenarios.collision_groups()
    macs = [0x01005e000000 | (int(ipaddress.IPv4Address(group)) & 0x7fffff) for group in groups]
    assert len(set(macs)) == len(groups) == 5
    assert len({binascii.crc_hqx(mac.to_bytes(6, 'big'), 0) for mac in macs}) == 1
    assert all(ipaddress.IPv4Address(group).is_multicast for group in groups)


def test_nested_and_flat_mdb_flags_are_recognized():
    entry = dict(grp='239.1.2.3', port='port1', flags=['offload'])
    assert list(scenarios.mdb_entries([dict(mdb=[entry])])) == [entry]
    assert list(scenarios.mdb_entries([entry])) == [entry]
    assert scenarios.is_offloaded(entry)
    assert not scenarios.is_offloaded(dict(entry, flags=[]))


@pytest.mark.parametrize('driver', ['b53-srab-switch', 'bcm53xx', 'b53-switch', 'brcm-sf2'])
def test_switch_family_gate_accepts_b53_library_users(driver, tmp_path):
    case, _ = make_case(METHODS[0], tmp_path)
    case.cmd = Mock(return_value='../../../bus/platform/drivers/' + driver)
    with patch.object(scenarios.SwitchCase, 'setUp'):
        case.setUp()


def test_switch_family_gate_skips_other_hardware(tmp_path):
    case, _ = make_case(METHODS[0], tmp_path)
    case.cmd = Mock(return_value='../../../bus/mdio_bus/drivers/mv88e6085')
    with patch.object(scenarios.SwitchCase, 'setUp'), pytest.raises(unittest.SkipTest, match='b53/SF2'):
        case.setUp()


def test_r6_does_not_treat_pending_offload_as_bucket_saturation(tmp_path):
    case, hardware = make_case('test_r6_mdb_reuses_hole_without_stale_ports', tmp_path)
    fifth = scenarios.collision_groups()[4]
    original = hardware.command
    def pending(command):
        if command == case.mdb('add', fifth, 3):
            return 0, ''
        return original(command)
    case.attempt = pending
    with patch.object(scenarios.time, 'sleep'), pytest.raises(unittest.SkipTest, match='No completed hardware result'):
        case.test_r6_mdb_reuses_hole_without_stale_ports()
