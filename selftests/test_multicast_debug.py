"""Debug evidence survives forwarding failure and supports iproute2 MDB forms."""
import importlib.util
import json
from pathlib import Path
import sys
from unittest.mock import Mock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))
from multicast_debug import MulticastDebug, mdb_ports
from params import dotdict


def config():
    return dotdict({**{'HOST_LAN%d' % i: 'usb%d' % i for i in range(4)},
                    **{'SUT_LAN%d' % i: 'lan%d' % i for i in range(4)}})


def test_mdb_recognizes_both_nested_and_flat_json():
    entry = dict(port='lan2', grp='224.42.42.42', flags=['offload'])
    assert mdb_ports([entry], '224.42.42.42') == {'lan2'}
    assert mdb_ports([dict(mdb=[entry], router=[dict(port='lan0')])], '224.42.42.42') == {'lan2'}
    assert not mdb_ports([entry], '239.1.2.3')


def test_membership_timeout_is_recorded_without_skipping_data(tmp_path):
    debug = MulticastDebug(Mock(), config(), tmp_path)
    debug.board = Mock(return_value=dict(status=0, output='[]'))
    assert not debug.wait_memberships(timeout=0)
    rows = json.loads((debug.folder / 'state.json').read_text())
    assert rows[-1]['ready'] is False
    assert rows[-1]['expected'] == ['lan1', 'lan2']


def test_missing_membership_becomes_ready_when_both_ports_exist(tmp_path):
    debug = MulticastDebug(Mock(), config(), tmp_path)
    debug.board = Mock(return_value=dict(status=0, output=json.dumps([
        dict(mdb=[dict(port=port, grp='224.42.42.42') for port in ('lan1', 'lan2')])])) )
    assert debug.wait_memberships(timeout=0)


def test_after_snapshot_includes_counter_deltas(tmp_path):
    debug = MulticastDebug(Mock(), config(), tmp_path)
    debug.board = Mock(return_value=dict(status=0, output='[]'))
    debug.save(dict(phase='dynamic-before-counters', counters={'lan2': {'TxMulticastPkts': 17}}))
    debug.counters = Mock(return_value={'lan2': {'TxMulticastPkts': 517}})
    with patch('multicast_debug.subprocess.run', return_value=Mock(returncode=0, stdout='', stderr='')):
        record = debug.snapshot('dynamic-after', counters=True)
    assert record['counter_deltas']['lan2']['TxMulticastPkts'] == 500


def test_capture_close_reaps_every_process_even_when_one_fails(tmp_path):
    debug = MulticastDebug(Mock(), config(), tmp_path)
    processes = []
    for i in range(2):
        path = tmp_path / ('capture%d.log' % i)
        path.write_text('0 packets dropped by kernel\n')
        proc = Mock(returncode=1 if i == 0 else 0)
        proc.poll.return_value = None
        debug.captures.append((proc, Mock(), path))
        processes.append(proc)
    with pytest.raises(RuntimeError, match='Capture failed'):
        debug.close()
    for proc in processes:
        proc.send_signal.assert_called_once()
        proc.wait.assert_called_once()
    assert debug.captures == []


def load_case():
    spec = importlib.util.spec_from_file_location('debug_igmp_case', ROOT / 'tests/igmp_4_ports_test.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    case = module.igmp_4_port_test('test_05_multicast_lan1')
    case.config, case.sut, case.traffic = config(), Mock(), Mock()
    case.vlan_filtering = False
    return module, case


def test_failed_dynamic_reception_gets_post_counters_and_static_control():
    module, case = load_case()
    module.DEBUG = Mock()
    stats = {name: dict(rx_pkts=0, tx_pkts=500 if name == 'usb1' else 0)
             for name in ('usb0', 'usb1', 'usb2', 'usb3')}
    case.traffic.stats = stats
    case.traffic.getStats.side_effect = stats.__getitem__
    with pytest.raises(AssertionError):
        case.test_05_multicast_lan1()
    module.DEBUG.snapshot.assert_any_call('dynamic-after', counters=True)
    module.DEBUG.static_control.assert_called_once_with(filtering=False)


def test_packet_generator_error_still_records_after_state():
    module, case = load_case()
    module.DEBUG = Mock()
    case.traffic.run.side_effect = RuntimeError('generator failed')
    with pytest.raises(RuntimeError, match='generator failed'):
        case.test_05_multicast_lan1()
    module.DEBUG.snapshot.assert_any_call('dynamic-after', counters=True)
    module.DEBUG.static_control.assert_not_called()


def test_static_control_removes_its_entry_after_probe_failure(tmp_path):
    debug = MulticastDebug(Mock(), config(), tmp_path)
    debug.board = Mock(return_value=dict(status=0, output=''))
    debug.snapshot = Mock()
    probe = Mock()
    probe.run.side_effect = RuntimeError('probe failed')
    with patch('multicast_debug.Traffic', return_value=probe):
        debug.static_control(filtering=True)
    assert '239.42.42.43 permanent vid 1' in debug.board.call_args_list[0].args[0]
    assert debug.board.call_args_list[-1].args[0] == (
        'bridge mdb del dev br1 port lan2 grp 239.42.42.43 vid 1')
    debug.snapshot.assert_any_call('static-control-after', counters=True)
    assert any(record['phase'] == 'static-control-error' for record in debug.records)
