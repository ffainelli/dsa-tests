"""STP wire parsing and negative checks for the hardware scenario assertions."""
from pathlib import Path
import struct
import sys
import unittest
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tests'))
import stp_suite as stp


def bpdu():
    root = bytes.fromhex('8000020000000001')
    body = b'\x00\x00\x00\x00\x00' + root + struct.pack('!I', 0) + root
    body += struct.pack('!HHHHH', 0x8001, 0, 20 * 256, 256, 8 * 256)
    llc = b'\x42\x42\x03' + body
    return bytes.fromhex('0180c2000000020000000001') + struct.pack('!H', len(llc)) + llc


def test_configuration_bpdu_decoding_and_padding():
    expected = dict(root='8000020000000001', bridge='8000020000000001', cost=0, port=0x8001)
    assert stp.config_bpdu(bpdu()) == expected
    assert stp.config_bpdu(bpdu().ljust(60, b'\0')) == expected


@pytest.mark.parametrize('offset,value', [(0, 0xff), (12, 0x08), (14, 0), (17, 1), (19, 2), (20, 0x80)])
def test_non_configuration_frames_are_excluded(offset, value):
    frame = bytearray(bpdu())
    frame[offset] = value
    assert stp.config_bpdu(frame) is None


def test_truncated_bpdu_is_excluded():
    for length in range(52):
        assert stp.config_bpdu(bpdu()[:length]) is None


def case(method):
    obj = stp.KernelStp(method)
    obj.br = 'testbridge'
    obj.ports = ['port%d' % i for i in range(4)]
    obj.bridge = Mock()
    obj.attempt = Mock(return_value=(0, ''))
    obj.cmd = Mock(return_value='1')
    obj.sut = Mock()
    return obj


def test_legacy_helper_handoff_skips_instead_of_claiming_kernel_coverage():
    obj = case('test_disable_stp_restores_forwarding')
    obj.attempt.return_value = (1, 'unknown option stp_mode')
    obj.cmd.return_value = '2'
    with pytest.raises(unittest.SkipTest, match='userspace STP'):
        obj.prepare()


def test_legacy_kernel_stp_remains_supported():
    obj = case('test_disable_stp_restores_forwarding')
    obj.attempt.return_value = (1, 'unknown option stp_mode')
    obj.prepare()


def test_unexpected_mode_selection_error_fails():
    obj = case('test_disable_stp_restores_forwarding')
    obj.attempt.return_value = (1, 'Permission denied')
    with pytest.raises(AssertionError, match='Cannot select kernel STP'):
        obj.prepare()


@pytest.mark.parametrize('learned,state,passes', [(False, '1', True), (True, '1', False), (False, '2', False)])
def test_listening_assertions_reject_early_learning_or_missed_window(learned, state, passes):
    obj = case('test_listening_does_not_learn_or_forward')
    obj.prepare = Mock()
    obj.state = Mock(side_effect=['1', state])
    obj.probe = Mock()
    obj.forwarding = Mock()
    obj.sut.getFdb.return_value = '02:00:00:57:a0:01' if learned else ''
    if passes:
        obj.test_listening_does_not_learn_or_forward()
    else:
        with pytest.raises(AssertionError):
            obj.test_listening_does_not_learn_or_forward()
        obj.forwarding.assert_not_called()


@pytest.mark.parametrize('learned,state,passes', [(True, '2', True), (False, '2', False), (True, '3', False)])
def test_learning_assertions_require_learning_and_correct_window(learned, state, passes):
    obj = case('test_learning_learns_without_forwarding')
    obj.prepare = Mock()
    obj.wait_state = Mock()
    obj.state = Mock(return_value=state)
    obj.probe = Mock()
    obj.forwarding = Mock()
    obj.sut.getFdb.return_value = '02:00:00:57:a0:02' if learned else ''
    if passes:
        obj.test_learning_learns_without_forwarding()
    else:
        with pytest.raises(AssertionError):
            obj.test_learning_learns_without_forwarding()
        obj.forwarding.assert_not_called()


def test_generated_bpdu_has_exact_llc_length_and_network_order_timers():
    frame = stp.configuration_frame(priority=0x2000, address=0x0200000000aa,
                                    port=0x8012, cost=123, message_age=1)
    assert len(frame) == 52
    assert frame[12:17] == bytes.fromhex('0026424203')
    assert frame[6:12] == bytes.fromhex('0200000000aa')
    assert stp.config_bpdu(frame) == dict(root='20000200000000aa', bridge='20000200000000aa',
                                         cost=123, port=0x8012)
    assert struct.unpack('!HHHH', frame[44:52]) == (256, 12 * 256, 256, 8 * 256)


def test_8023_markers_do_not_change_bpdu_or_make_short_body_parseable(tmp_path):
    from unittest.mock import patch
    import traffic as backend
    valid = stp.configuration_frame()
    short = valid[:12] + struct.pack('!H', 10) + valid[14:24]
    marker = b'DSATEST:' + b'm' * 16
    with patch('traffic.shutil.which', return_value='/bin/tool'):
        traffic = backend.Traffic(['one'], tmp_path, mz='mz')
    traffic.addInterface('one')
    for frame in (valid, short):
        traffic.addEthernetStream('one', frame)
    with patch('traffic.subprocess.run') as send:
        traffic._send(list(enumerate(traffic.streams)), marker, tmp_path)
    frames = [bytes.fromhex(call.args[0][-1].replace(':', '')) for call in send.call_args_list]
    for body, wire in zip((valid, short), frames):
        length = int.from_bytes(wire[12:14], 'big')
        assert wire[:14 + length] == body
        assert wire[14 + length:].startswith(marker)
        assert backend.signed_frame(wire, marker)
        assert not backend.signed_frame(wire, b'old-run')
        assert len(wire) >= 60
    assert stp.config_bpdu(frames[0]) == stp.config_bpdu(valid)
    # A decoder honoring 802.3 length sees the original truncated body.
    assert stp.config_bpdu(frames[1][:24]) is None
    assert not backend.signed_frame(valid.ljust(60, b'\0'), marker)


@pytest.mark.parametrize('frame', [b'', bytes(13), bytes(14), bytes.fromhex('00' * 12 + '0800')])
def test_ethernet_stream_rejects_missing_header_or_wrong_length(frame, tmp_path):
    from unittest.mock import patch
    from traffic import Traffic
    with patch('traffic.shutil.which', return_value='/bin/tool'):
        traffic = Traffic(['one'], tmp_path, mz='mz')
    traffic.addInterface('one')
    # A 14-byte zero-length frame is a valid 802.3 frame; force a mismatch.
    if len(frame) == 14 and frame[12:] == b'\0\0':
        frame += b'extra'
    with pytest.raises(ValueError):
        traffic.addEthernetStream('one', frame)
    assert not traffic.streams


def test_real_tcpdump_decodes_generated_bpdu(tmp_path):
    import shutil
    import subprocess
    if not shutil.which('tcpdump'):
        pytest.skip('tcpdump not installed')
    frame = stp.configuration_frame().ljust(60, b'\0')
    path = tmp_path / 'bpdu.pcap'
    header = bytes.fromhex('d4c3b2a1') + struct.pack('<HHIIII', 2, 4, 0, 0, 65535, 1)
    path.write_bytes(header + struct.pack('<IIII', 0, 0, len(frame), len(frame)) + frame)
    result = subprocess.run(['tcpdump', '-n', '-e', '-vv', '-r', str(path)],
                            capture_output=True, text=True, check=True)
    assert '802.1d' in result.stdout
    assert 'Config' in result.stdout
    assert '1000.02:00:00:57:b0:01' in result.stdout
    assert 'max-age 12.00s' in result.stdout


@pytest.mark.parametrize('field,actual', [('root_id', '8000.020000000001'), ('root_port', '2'),
                                         ('root_path_cost', '200')])
def test_root_assertions_detect_wrong_identity_port_or_cost(field, actual):
    obj = case('test_injected_superior_bpdu_on_each_user_port')
    values = dict(root_id='1000.02000057b001', root_port='1', root_path_cost='100')
    values[field] = actual
    obj.bridge_value = lambda name: values[name]
    obj.cmd.return_value = '0x1'
    with pytest.raises(AssertionError):
        obj.assert_root('100002000057b001', 0, 100)


def test_injected_frames_must_not_leak_to_other_clients(tmp_path):
    from unittest.mock import patch
    obj = case('test_injected_inferior_bpdu_does_not_replace_root')
    obj.hosts = ['one', 'two']
    obj.traffic = Mock(output=tmp_path, sequence=1)
    frame = stp.configuration_frame()
    with patch('stp_suite.pcap_frames', side_effect=[[], [frame.ljust(60, b'\0')]]):
        with pytest.raises(AssertionError, match='leaked to client two'):
            obj.inject(0, frame)
    obj.traffic.addEthernetStream.assert_called_once_with('one', frame)
    obj.traffic.run.assert_called_once()
