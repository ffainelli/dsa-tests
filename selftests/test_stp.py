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
