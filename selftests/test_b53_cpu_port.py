"""Offline checks for the b53 hardware CPU-port preflight."""
from pathlib import Path
import sys
import subprocess
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tests'))
from lab_support import check_b53_cpu_port


@pytest.mark.parametrize('driver', ['b53-srab-switch', 'b53-switch', 'bcm53xx', 'brcm-sf2'])
def test_imp_port(driver):
    sut = Mock()
    sut.checked.side_effect = ['/sys/bus/platform/drivers/' + driver, '   0   0   0   8\n']
    assert check_b53_cpu_port(sut, 'lan1') == 8


@pytest.mark.parametrize('registers', ['', '0 0 0 5\n', '0 0 0 8\n0 0 0 5\n'])
def test_missing_or_wrong_cpu_port(registers):
    sut = Mock()
    sut.checked.side_effect = ['b53-switch', registers]
    with pytest.raises(RuntimeError, match='CPU port 8'):
        check_b53_cpu_port(sut, 'lan1')


def test_other_switches_are_unchanged():
    sut = Mock()
    sut.checked.return_value = '/sys/bus/mdio_bus/drivers/mv88e6085\n'
    assert check_b53_cpu_port(sut, 'ethernet1') is None
    assert sut.checked.call_count == 1


@pytest.mark.parametrize('status', [None, 'okay', 'ok', 'disabled', 'reserved', 'fail'])
def test_device_tree_availability(tmp_path, status):
    node = tmp_path / 'of_node'
    for number in (5, 7, 8):
        path = node / 'ports' / ('port@%d' % number)
        path.mkdir(parents=True)
        (path / 'ethernet').write_bytes(b'\x00\x00\x00\x01')
        (path / 'reg').write_bytes(number.to_bytes(4, 'big'))
        value = status if number == 8 else 'disabled'
        if value is not None:
            (path / 'status').write_bytes(value.encode() + b'\x00')

    def checked(command):
        if command.startswith('readlink '):
            return 'b53-switch'
        command = command.replace('/sys/class/net/lan1/device/of_node', str(node))
        return subprocess.run(['sh', '-c', command], check=True,
                              capture_output=True, text=True).stdout

    sut = Mock()
    sut.checked.side_effect = checked
    if status in (None, 'okay', 'ok'):
        assert check_b53_cpu_port(sut, 'lan1') == 8
    else:
        with pytest.raises(RuntimeError, match='CPU port 8'):
            check_b53_cpu_port(sut, 'lan1')
