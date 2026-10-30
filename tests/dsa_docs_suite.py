# SPDX-License-Identifier: GPL-2.0
"""Home/office recipes from Linux Documentation/networking/dsa.

Logical ports and conduit names come from discovery, never the switch model.
Tag-protocol-none recipes use conduit VLANs; native taggers use user ports.
"""
import json
import re

from linux_switch_suite import LinuxFdb
from switch_case import SwitchCase


class DsaDocumentation(SwitchCase):
    def tagging(self):
        protocol = self.cmd('cat /sys/class/net/%s/dsa/tagging' % self.config.SUT_MASTER).strip()
        self.assertRegex(protocol, r'^[a-zA-Z0-9_-]+$')
        (self.folder / 'documentation-layout.json').write_text(json.dumps({
            'tag_protocol': protocol, 'conduit': self.config.SUT_MASTER,
            'ports': self.ports, 'source': 'Documentation/networking/dsa/configuration.rst',
            'recipe': 'conduit VLANs' if protocol == 'none' else 'native DSA tagging'
        }, indent=2) + '\n')
        return protocol

    def none_layout(self, mode):
        """VLAN CPU isolation, including b53.rst's VID-1 removal rule."""
        members = (0, 1, 3) if mode == 'gateway' else (0, 1, 2)
        self.bridge(1, members=members)
        if mode == 'single':
            uppers = []
            for index, vid in enumerate((1, 2, 3)):
                uppers.append(self.new_link(self.config.SUT_MASTER, 'vlan', 'id %d' % vid))
                self.vlan(index, vid, 'pvid untagged')
                if vid != 1:
                    self.cmd('bridge vlan del dev %s vid 1' % self.ports[index])
            return uppers
        lan_cpu = self.new_link(self.config.SUT_MASTER, 'vlan', 'id 1')
        self.cmd('ip link set dev %s master %s' % (lan_cpu, self.br))
        if mode == 'bridge':
            return self.br
        wan_cpu = self.new_link(self.config.SUT_MASTER, 'vlan', 'id 2')
        self.vlan(3, 2, 'pvid untagged')
        self.cmd('bridge vlan del dev %s vid 1' % self.ports[3])
        return self.br, wan_cpu

    def test_single_port_showcase(self):
        """Three independent interfaces with the documentation's /30 addresses."""
        lowers = self.none_layout('single') if self.tagging() == 'none' else self.ports[:3]
        for index, lower in enumerate(lowers):
            with self.subTest(port=index):
                self.local_ping(lower, host_index=index,
                    addresses=('192.0.2.%d' % (1 + 4 * index),
                               '192.0.2.%d' % (2 + 4 * index), 30))
        self.probe(receivers=())

    def test_bridge_showcase(self):
        """Three LAN sockets share the documented 192.0.2.129/25 bridge."""
        lower = self.none_layout('bridge') if self.tagging() == 'none' else self.bridge()
        for index in range(3):
            with self.subTest(port=index):
                self.local_ping(lower, host_index=index, assign_board=index == 0,
                    addresses=('192.0.2.129', '192.0.2.%d' % (130 + index), 25))
        self.probe(receivers=(1, 2))
        self.probe(source=3, receivers=())

    def test_gateway_showcase(self):
        """Two LAN sockets and a separate WAN interface, with both CPU IPs."""
        if self.tagging() == 'none':
            lan, wan = self.none_layout('gateway')
        else:
            lan, wan = self.bridge(members=(0, 1)), self.ports[3]
        for index in (0, 1):
            self.local_ping(lan, host_index=index, assign_board=index == 0,
                addresses=('192.0.2.129', '192.0.2.%d' % (130 + index), 25))
        self.local_ping(wan, host_index=3, addresses=('192.0.2.1', '192.0.2.2', 30))
        self.probe(receivers=(1,))
        self.probe(source=3, receivers=())

    def test_user_up_automatically_raises_conduit(self):
        """Documented kernel >=5.12 user/conduit administrative behavior."""
        self.addCleanup(self.cmd, 'ip link set dev %s up' % self.config.SUT_MASTER)
        self.cmd('ip link set dev %s down' % self.config.SUT_MASTER)
        self.cmd('ip link set dev %s up' % self.ports[0])
        info = self.read_json('ip -j link show dev ' + self.config.SUT_MASTER)[0]
        self.assertIn('UP', info['flags'])
        for port in self.ports:
            self.sut.up(port)
        self.wait_links()
        lower = self.none_layout('single')[0] if self.tagging() == 'none' else self.ports[0]
        self.local_ping(lower)

    def test_conduit_down_lowers_users(self):
        self.addCleanup(self.cmd, 'ip link set dev %s up' % self.config.SUT_MASTER)
        self.cmd('ip link set dev %s down' % self.config.SUT_MASTER)
        for port in self.ports:
            with self.subTest(port=port):
                self.assertNotIn('UP', self.read_json('ip -j link show dev ' + port)[0]['flags'])
        for port in self.ports:
            self.sut.up(port)
        self.wait_links()

    def test_read_tagging_protocol(self):
        """dsa.rst: read the active protocol through the documented sysfs API."""
        self.tagging()

    # Reuse the same model-independent assertions instead of diverging copies.
    entry = LinuxFdb.entry
    test_master_static_fdb = LinuxFdb.test_static_forwarding_and_delete
    test_master_local_fdb = LinuxFdb.test_local_mac_not_forwarded

    def test_self_static_is_not_a_hardware_forwarding_entry(self):
        """Modern DSA: the obsolete 'self static' recipe must not steer traffic."""
        self.bridge()
        mac = '02:00:00:00:dd:01'
        self.probe(destination=mac)
        code, output = self.attempt('bridge fdb add dev %s %s self static' % (self.ports[1], mac), 15)
        if code == 0:
            self.addCleanup(self.cmd, 'bridge fdb del dev %s %s self static' % (self.ports[1], mac))
        else:
            # ndo_dflt_fdb_add rejects the static/NUD_REACHABLE state with
            # EINVAL; drivers may instead return EOPNOTSUPP. Both are expected
            # for this exact obsolete command, not arbitrary setup operations.
            # printk can interrupt any byte of the userspace diagnostic. Keep
            # the raw attempt artifact, but reconstruct its text for matching.
            clean = re.sub(r'\[\s*\d+\.\d+\][^\r\n]*(?:\r\n|\r|\n|$)', '', output)
            error = ' '.join(clean.lower().split())
            self.assertIn(error, {'rtnetlink answers: invalid argument',
                                  'rtnetlink answers: operation not supported'}, output)
        self.probe(destination=mac)

    def test_implicit_local_fdb_is_device_local(self):
        """Implicit self/permanent adds a device address, not a bridge FDB entry."""
        self.bridge()
        mac = '02:00:00:00:dd:02'
        add = 'bridge fdb add dev %s %s' % (self.ports[1], mac)
        delete = 'bridge fdb del dev %s %s' % (self.ports[1], mac)
        self.cmd(add)
        installed = True
        def remove():
            nonlocal installed
            if installed:
                self.cmd(delete)
                installed = False
        self.addCleanup(remove)
        # DSA's FDB dump may expose only hardware entries, not dev_uc. An
        # exclusive duplicate add verifies the device-local address exists.
        code, output = self.attempt(add, 15)
        self.assertNotEqual(code, 0, 'Duplicate device-local address was accepted')
        self.assertIn('file exists', output.lower())
        entries = self.read_json('bridge -j fdb show dev ' + self.ports[1])
        self.assertFalse(any(entry.get('mac') == mac and
            (entry.get('master') == self.br or 'master' in entry.get('flags', []))
            for entry in entries), 'Self-local address unexpectedly entered the bridge FDB')
        remove()
        self.cmd(add)  # Deletion must allow exclusive reinstallation.
        installed = True
        # No packet flooding assertion: receive-filter offload differs by
        # capability. test_master_local_fdb covers bridge-local termination.
