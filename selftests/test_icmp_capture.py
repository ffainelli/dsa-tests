"""Regression for priority-tagged gateway replies on B53 targets."""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tests'))
from switch_case import icmp_echo_reply
from traffic import tag_frame


def echo(version, reply=True, options=False):
    ethernet = bytes(12) + (b'\x08\x00' if version == 4 else b'\x86\xdd')
    if version == 4:
        header = bytearray(24 if options else 20)
        header[0], header[9] = (0x46 if options else 0x45), 1
        kind = 0 if reply else 8
    else:
        header = bytearray(40)
        header[0], header[6] = 0x60, 58
        kind = 129 if reply else 128
    return ethernet + header + bytes([kind, 0]) + bytes(6)


@pytest.mark.parametrize('version', [4, 6])
@pytest.mark.parametrize('tags', [(), ((0x8100, 0),), ((0x8100, 10),),
                                  ((0x88a8, 100), (0x8100, 0xa00a))])
def test_echo_reply_after_vlan_headers(version, tags):
    assert icmp_echo_reply(tag_frame(echo(version), tags), version)
    assert not icmp_echo_reply(tag_frame(echo(version, reply=False), tags), version)
    assert not icmp_echo_reply(tag_frame(echo(version), tags), 6 if version == 4 else 4)


def test_ipv4_options_and_truncated_frames():
    frame = tag_frame(echo(4, options=True), ((0x8100, 0),))
    assert icmp_echo_reply(frame, 4)
    for length in range(len(frame)):
        assert not icmp_echo_reply(frame[:length], 4)
    for length in range(len(echo(6))):
        assert not icmp_echo_reply(echo(6)[:length], 6)
    invalid = bytearray(echo(4))
    invalid[14] = 0x40
    assert not icmp_echo_reply(invalid, 4)
