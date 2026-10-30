"""Host isolation routing and partial setup recovery without hardware."""
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch, Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tests'))
import host_netns
from lab_support import HostLinks
from host import HOST


@pytest.fixture(autouse=True)
def registry():
    host_netns.INTERFACES.clear()
    host_netns.ADDRESSES.clear()
    yield
    host_netns.INTERFACES.clear()
    host_netns.ADDRESSES.clear()


def test_same_subnet_clients_have_explicit_separate_stacks():
    host_netns.INTERFACES.update(usb0='client0', usb1='client1')
    for name in ('usb0', 'usb1'):
        host_netns.address_added(name, '192.0.2.1/24')
        assert host_netns.command(['ping', '192.0.2.2'], interface=name)[:4] == [
            'ip', 'netns', 'exec', host_netns.INTERFACES[name]]
    with pytest.raises(RuntimeError, match='Ambiguous'):
        host_netns.command(['ping', '192.0.2.2'])
    assert host_netns.command(['uname', '-a']) == ['uname', '-a']


def test_legacy_address_resolution_and_vlan_upper():
    host_netns.INTERFACES.update(usb0='client0', usb1='client1')
    host_netns.address_added('usb1', 'fd42:4242:11::1/64')
    assert host_netns.command(['ping', 'fd42:4242:11::2'])[:4] == ['ip', 'netns', 'exec', 'client1']
    host_netns.register_upper('vlan10', 'usb0')
    assert host_netns.command(['ip', 'addr', 'add', '192.0.2.1/24', 'dev', 'vlan10'])[:4] == [
        'ip', 'netns', 'exec', 'client0']
    with pytest.raises(RuntimeError, match='No isolated'):
        host_netns.command(['ping', '198.51.100.1'])


def test_partial_move_failure_returns_prior_cable_and_removes_owned_namespaces(tmp_path):
    links = HostLinks(['usb0', 'usb1'], tmp_path)
    links.saved = {name: dict(mtu=1500, flags=['UP'], addr_info=[], saved_routes={})
                   for name in links.interfaces}
    calls = []
    def execute(*argv, **kwargs):
        calls.append(argv)
        if argv[:6] == ('ip', 'link', 'set', 'dev', 'usb1', 'netns'):
            raise subprocess.CalledProcessError(1, argv)
        return Mock(returncode=0)
    with patch('lab_support.run', side_effect=execute):
        with pytest.raises(subprocess.CalledProcessError):
            links.isolate()
        owned = dict(links.namespaces)
        assert host_netns.INTERFACES == {'usb0': owned['usb0']}
        assert links.restore() == []
    assert ('ip', 'link', 'set', 'dev', 'usb0', 'netns', str(links.origin_pid)) in calls
    assert {argv[3] for argv in calls if argv[:3] == ('ip', 'netns', 'del')} == set(owned.values())
    assert not host_netns.INTERFACES
    assert not links.namespaces


def test_multicast_membership_process_runs_in_client_and_is_reaped():
    host_netns.INTERFACES['usb0'] = 'client0'
    process = Mock()
    process.stdout.readline.return_value = b'READY\n'
    host = HOST.__new__(HOST)
    host.interfaces, host.groups = ['usb0'], []
    with patch('host.subprocess.Popen', return_value=process) as spawn, \
         patch('host.select.select', return_value=([process.stdout], [], [])):
        host.join('usb0', '192.0.2.1', '224.42.42.42')
        assert spawn.call_args.args[0][:4] == ['ip', 'netns', 'exec', 'client0']
        host.leave_all()
    process.communicate.assert_called_once_with(timeout=5)
    assert not host.groups


@pytest.mark.skipif(__import__('os').geteuid() != 0, reason='Requires root for temporary veth/netns smoke test')
def test_real_namespaces_ping_capture_and_membership(tmp_path):
    """Exercise real processes on disposable links, without a board or USB NIC."""
    import uuid
    from lab_support import run
    from traffic import Traffic

    token = uuid.uuid4().hex[:8]
    left, right = 'dsal' + token, 'dsar' + token
    links = HostLinks([left, right], tmp_path)
    host = None
    run('ip', 'link', 'add', left, 'type', 'veth', 'peer', 'name', right)
    try:
        links.snapshot()
        links.isolate()
        host = HOST()
        for interface, address in ((left, '192.0.2.1/24'), (right, '192.0.2.2/24')):
            host.addInterface(interface)
            host.up(interface)
            host.addAddress(interface, address)
        assert host.ping('192.0.2.2', interface=left)
        # Both subnets match: an unspecified source must never use a local route.
        with pytest.raises(RuntimeError, match='Ambiguous'):
            host.ping('192.0.2.2')
        host.join(right, '192.0.2.2', '224.42.42.42')
        traffic = Traffic([left, right], tmp_path / 'captures')
        for interface in (left, right):
            traffic.addInterface(interface)
        traffic.addPacketStream(left)
        traffic.run()
        assert traffic.getStats(left) == {'rx_pkts': 0, 'tx_pkts': 5}
        assert traffic.getStats(right) == {'rx_pkts': 5, 'tx_pkts': 0}
    finally:
        try:
            if host is not None:
                host.leave_all()
                host.sock.close()
        finally:
            errors = links.restore()
            run('ip', 'link', 'del', left, check=False)
            assert not errors, errors


def recovery_fixture(tmp_path):
    old = tmp_path / 'board' / 'old'
    old.mkdir(parents=True)
    namespace = 'dsatest-0123456789-0'
    import json
    (old / 'host-namespaces.json').write_text(json.dumps({'usb0': namespace}))
    state = dict(mtu=1500, flags=['UP'], addr_info=[], saved_routes={})
    (old / 'host-before.json').write_text(json.dumps({'usb0': state}))
    output = tmp_path / 'board' / 'new'
    output.mkdir()
    return HostLinks(['usb0'], output), namespace


def test_recovers_recorded_idle_namespace_and_original_settings(tmp_path):
    links, namespace = recovery_fixture(tmp_path)
    calls = []
    def execute(*argv, **kwargs):
        calls.append(argv)
        if argv == ('ip', '-j', 'address', 'show', 'dev', 'usb0'):
            return Mock(returncode=1)
        if argv == ('ip', 'netns', 'list'):
            return Mock(stdout=namespace + '\n')
        if argv == ('ip', 'netns', 'pids', namespace):
            return Mock(stdout='')
        return Mock(returncode=0)
    with patch('lab_support.run', side_effect=execute):
        assert links.recover() == {'usb0': namespace}
    assert ('ip', 'link', 'set', 'dev', 'usb0', 'netns', str(links.origin_pid)) in calls
    assert ('ip', 'link', 'set', 'dev', 'usb0', 'mtu', '1500') in calls
    assert ('ip', 'netns', 'del', namespace) in calls
    assert not host_netns.INTERFACES
    assert (links.output / 'host-namespace-recovery.json').exists()


def test_recovery_refuses_namespace_with_live_processes(tmp_path):
    links, namespace = recovery_fixture(tmp_path)
    def execute(*argv, **kwargs):
        if argv == ('ip', '-j', 'address', 'show', 'dev', 'usb0'):
            return Mock(returncode=1)
        if argv == ('ip', 'netns', 'list'):
            return Mock(stdout=namespace + '\n')
        if argv == ('ip', 'netns', 'pids', namespace):
            return Mock(stdout='1234\n')
        return Mock(returncode=0)
    with patch('lab_support.run', side_effect=execute) as run:
        with pytest.raises(RuntimeError, match='live processes: 1234'):
            links.recover()
        assert not any(call.args[:3] == ('ip', 'link', 'set') for call in run.call_args_list)
    assert not host_netns.INTERFACES


def test_recovery_does_not_adopt_unrecorded_namespace(tmp_path):
    links = HostLinks(['usb0'], tmp_path / 'board' / 'new')
    with patch('lab_support.run', side_effect=[Mock(returncode=1), Mock(stdout='unrelated\n')]):
        with pytest.raises(RuntimeError, match='no recorded recoverable namespace: usb0'):
            links.recover()


def test_visible_interface_needs_no_recovery(tmp_path):
    links = HostLinks(['usb0'], tmp_path)
    with patch('lab_support.run', return_value=Mock(returncode=0)) as run:
        assert links.recover() == {}
        assert run.call_count == 1
