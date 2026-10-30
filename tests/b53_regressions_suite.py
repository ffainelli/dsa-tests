# SPDX-License-Identifier: GPL-2.0
"""Hardware regressions for stale MDB bins and shared mirror resources."""
import binascii
import ipaddress
import time

from switch_case import SwitchCase
from traffic import mac_text


def collision_groups(count=5):
    """Equal full CRC-CCITT of same-length multicast MACs, not just low 10 bits.

    A constant VID and CRC seed preserve these collisions. The live fixture
    also verifies bucket saturation: bit order/hash variants must never turn
    a non-colliding address set into a passing regression test.
    """
    groups = []
    target = None
    for suffix in range(1, 1 << 23):
        mac = 0x01005e000000 | suffix
        crc = binascii.crc_hqx(mac.to_bytes(6, 'big'), 0)
        if target is None:
            target = crc
        if crc == target:
            groups.append(str(ipaddress.IPv4Address(0xef000000 | suffix)))
            if len(groups) == count:
                return groups
    raise RuntimeError('Could not construct multicast CRC collisions')


def mdb_entries(value):
    """Flatten iproute2 MDB JSON while retaining offload flags."""
    if isinstance(value, list):
        for row in value:
            yield from mdb_entries(row)
    elif isinstance(value, dict):
        if 'grp' in value and 'port' in value:
            yield value
        for child in value.values():
            if isinstance(child, (list, dict)):
                yield from mdb_entries(child)


def is_offloaded(entry):
    return bool(entry.get('offload')) or 'offload' in entry.get('flags', [])


class B53Regressions(SwitchCase):
    def setUp(self):
        super().setUp()
        driver = self.cmd('readlink /sys/class/net/%s/device/driver' % self.ports[0])
        driver = driver.strip().rsplit('/', 1)[-1]
        if driver not in ('b53-srab-switch', 'bcm53xx', 'b53-switch', 'brcm-sf2'):
            self.skipTest('b53/SF2 switch required; found driver ' + driver)

    def mdb(self, operation, group, port):
        return 'bridge mdb %s dev %s port %s grp %s permanent' % (
            operation, self.br, self.ports[port], group)

    def mdb_state(self, group, port):
        rows = self.read_json('bridge -j -d mdb show dev ' + self.br)
        for row in mdb_entries(rows):
            if row['grp'] != group or row['port'] != self.ports[port]:
                continue
            if is_offloaded(row):
                return 'offloaded'
            if 'offload_failed' in row.get('flags', []):
                return 'failed'
        return 'pending'

    def offloaded(self, group, port):
        return self.mdb_state(group, port) == 'offloaded'

    def wait_offloaded(self, group, port):
        for _ in range(20):
            if self.offloaded(group, port):
                return True
            time.sleep(.1)
        return False

    def test_r6_mdb_reuses_hole_without_stale_ports(self):
        self.bridge(members=(0, 1, 2, 3), snooping=1)
        self.cmd('ip link set dev %s type bridge mcast_querier 1' % self.br)
        for port in self.ports:
            self.cmd('bridge link set dev %s learning off mcast_flood off mcast_router 0' % port)
        # Wait out the bridge's startup-querier grace period before data probes.
        time.sleep(10)
        groups = collision_groups()
        (self.folder / 'collision-groups.txt').write_text('\n'.join(groups) + '\n')
        for group in groups[:4]:
            self.cmd(self.mdb('add', group, 3), optional=True)
            if not self.wait_offloaded(group, 3):
                self.skipTest('Four colliding hardware MDB entries could not be established')
        # Confirm a real four-bin collision, rather than guessing the hash layout.
        status, output = self.attempt(self.mdb('add', groups[4], 3))
        if status:
            self.assertRegex(output.lower(), r'no space|enospc', output)
        else:
            for _ in range(20):
                state = self.mdb_state(groups[4], 3)
                if state != 'pending':
                    break
                time.sleep(.1)
            if state == 'offloaded':
                self.skipTest('Collision fixture did not saturate a four-bin hardware bucket')
            if state != 'failed':
                self.skipTest('No completed hardware result for the collision fixture')
            self.cmd(self.mdb('del', groups[4], 3))
        self.cmd(self.mdb('del', groups[0], 3))
        self.cmd(self.mdb('add', groups[4], 1))
        self.assertTrue(self.wait_offloaded(groups[4], 1), 'Replacement MDB entry was not offloaded')
        mac = 0x01005e000000 | (int(ipaddress.IPv4Address(groups[4])) & 0x7fffff)
        self.probe(destination=mac, destination_ip=groups[4], receivers=(1,))
        # Existing entries in the same bucket must survive the replacement.
        mac = 0x01005e000000 | (int(ipaddress.IPv4Address(groups[3])) & 0x7fffff)
        self.probe(destination=mac, destination_ip=groups[3], receivers=(3,))

    def mirror_setup(self):
        self.bridge(members=(0, 1))
        self.destination = 0x02000000b601
        self.cmd('bridge fdb replace %s dev %s master static' %
                 (mac_text(self.destination), self.ports[1]))
        self.cmd('command -v tc', optional=True)
        for port in self.ports[:2]:
            self.cmd('tc qdisc add dev %s clsact' % port, optional=True)
            self.addCleanup(self.cmd, 'tc qdisc del dev %s clsact' % port)
        self.mirror_probe(())

    def mirror_command(self, operation, port, direction, pref, target=2):
        base = 'tc filter %s dev %s %s protocol all pref %d' % (
            operation, self.ports[port], direction, pref)
        if operation == 'add':
            base += ' matchall skip_sw action mirred egress mirror dev ' + self.ports[target]
        return base

    def add_mirror(self, port, direction, pref, target=2, optional=False):
        self.cmd(self.mirror_command('add', port, direction, pref, target), optional=optional)
        self.assert_mirror(port, direction, pref)

    def assert_mirror(self, port, direction, pref):
        output = self.cmd('tc -s filter show dev %s %s pref %d' %
                          (self.ports[port], direction, pref))
        self.assertRegex(output, r'\bin_hw\b', 'skip_sw mirror was not offloaded')

    def del_mirror(self, port, direction, pref):
        self.cmd(self.mirror_command('del', port, direction, pref))

    def mirror_probe(self, captures):
        self.probe(destination=self.destination, receivers=(1,) + tuple(captures))

    def conflicting_destination(self, first_direction, second_direction):
        self.mirror_setup()
        first = 0 if first_direction == 'ingress' else 1
        second = 0 if second_direction == 'ingress' else 1
        self.add_mirror(first, first_direction, 10, optional=True)
        self.mirror_probe((2,))
        status, output = self.attempt(self.mirror_command('add', second, second_direction, 20, 3))
        self.assertNotEqual(status, 0, 'A second global capture destination was accepted')
        self.assertRegex(output.lower(), r'mirror.*destination|capture.*port|not supported|no space')
        self.assert_mirror(first, first_direction, 10)
        self.mirror_probe((2,))
        self.del_mirror(first, first_direction, 10)
        self.mirror_probe(())
        # The destination must become available after the last rule is removed.
        self.add_mirror(second, second_direction, 20, 3)
        self.mirror_probe((3,))
        self.del_mirror(second, second_direction, 20)
        self.mirror_probe(())

    def test_r7_ingress_conflicting_destination(self):
        self.conflicting_destination('ingress', 'ingress')

    def test_r7_egress_conflicting_destination(self):
        self.conflicting_destination('egress', 'egress')

    def test_r7_cross_direction_conflicting_destination(self):
        self.conflicting_destination('ingress', 'egress')

    def shared_membership(self, direction):
        self.mirror_setup()
        source = 0 if direction == 'ingress' else 1
        self.add_mirror(source, direction, 10, optional=True)
        self.add_mirror(source, direction, 20)
        self.mirror_probe((2,))
        self.del_mirror(source, direction, 10)
        self.assert_mirror(source, direction, 20)
        self.mirror_probe((2,))
        self.del_mirror(source, direction, 20)
        self.mirror_probe(())
        self.add_mirror(source, direction, 30, 3)
        self.mirror_probe((3,))
        self.del_mirror(source, direction, 30)
        self.mirror_probe(())

    def test_r8_ingress_shared_membership(self):
        self.shared_membership('ingress')

    def test_r8_egress_shared_membership(self):
        self.shared_membership('egress')

    def test_r8_direction_memberships_are_independent(self):
        self.mirror_setup()
        self.add_mirror(0, 'ingress', 10, optional=True)
        self.add_mirror(0, 'egress', 20)
        self.del_mirror(0, 'egress', 20)
        self.mirror_probe((2,))
        self.del_mirror(0, 'ingress', 10)
        self.mirror_probe(())
