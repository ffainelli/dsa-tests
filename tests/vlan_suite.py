# SPDX-License-Identifier: GPL-2.0
"""802.1Q and CPU termination: dsatest PR27 and Linux local_termination."""
from switch_case import SwitchCase


class VlanTermination(SwitchCase):
    pass


def termination(mode, version=4, vid=100, index=0):
    def test(self):
        lower = self.ports[index]
        if mode != 'standalone':
            filtering = int(mode.endswith('aware') and not mode.endswith('unaware'))
            self.bridge(filtering, members=(index,))
            if mode.startswith('bridge_'):
                lower = self.br
                if filtering:
                    self.vlan(index, vid)
                    self.cmd('bridge vlan add dev %s vid %d self' % (self.br, vid))
        upper = self.new_link(lower, 'vlan', 'id %d' % vid)
        self.local_ping(upper, index, vid, version)
    return test


# PR27's exact VID 0..9 range, independently reported on all four cables.
for _port in range(4):
    for _vid in range(10):
        setattr(VlanTermination, 'test_pr27_port%d_vid%d' % (_port, _vid),
                termination('standalone', vid=_vid, index=_port))

# IPv4/IPv6 on standalone, port uppers and bridge uppers, aware/unaware.
for _mode in ('standalone', 'port_unaware', 'port_aware', 'bridge_unaware', 'bridge_aware'):
    for _version in (4, 6):
        setattr(VlanTermination, 'test_%s_ipv%d' % (_mode, _version), termination(_mode, _version))


def simultaneous_uppers(index):
    def test(self):
        pairs = [(self.new_link(self.ports[index], 'vlan', 'id %d' % vid),
                  self.new_link(self.hosts[index], 'vlan', 'id %d' % vid, host=True))
                 for vid in range(10)]
        for vid, (board, host) in enumerate(pairs):
            with self.subTest(vid=vid):
                self.local_ping(board, index, vid, host_device=host, subnet=10 + vid)
    return test


# Preserve PR27's simultaneous presence of all ten uppers as well as the
# independently reported per-VID cases above.
for _port in range(4):
    setattr(VlanTermination, 'test_pr27_simultaneous_uppers_port%d' % _port, simultaneous_uppers(_port))


class VlanAdmission(SwitchCase):
    def prepare(self):
        self.bridge(1, default_pvid=0)
        self.vlan(0, 100)
        self.vlan(1, 100)
        self.vlan(2, 200)

    def test_tagged_member(self):
        self.prepare()
        self.probe(tags=((0x8100, 100),), receivers=(1,), out_tags=((0x8100, 100),))

    def test_tagged_nonmember(self):
        self.prepare()
        self.probe(tags=((0x8100, 200),), receivers=())

    def test_unknown_vid(self):
        self.prepare()
        self.probe(tags=((0x8100, 300),), receivers=())

    def test_untagged_no_pvid(self):
        self.prepare()
        self.probe(receivers=())

    def test_priority_tag_no_pvid(self):
        self.prepare()
        self.probe(tags=((0x8100, 0xa000),), receivers=())

    def test_access_to_trunk(self):
        self.prepare()
        self.vlan(0, 100, 'pvid untagged')
        self.probe(receivers=(1,), out_tags=((0x8100, 100),))

    def test_trunk_to_access(self):
        self.prepare()
        self.vlan(1, 100, 'pvid untagged')
        self.probe(tags=((0x8100, 100),), receivers=(1,), out_tags=())

    def test_priority_tag_uses_pvid(self):
        self.prepare()
        self.vlan(0, 100, 'pvid untagged')
        self.probe(tags=((0x8100, 0xa000),), receivers=(1,), out_tags=((0x8100, 0xa064),))

    def test_pvid_change(self):
        self.prepare()
        self.vlan(0, 100, 'pvid untagged')
        self.probe(receivers=(1,))
        self.vlan(0, 200, 'pvid untagged')
        self.probe(receivers=(2,), out_tags=((0x8100, 200),))

    def test_delete_member(self):
        self.prepare()
        self.probe(tags=((0x8100, 100),), receivers=(1,))
        self.cmd('bridge vlan del dev %s vid 100' % self.ports[1])
        self.probe(tags=((0x8100, 100),), receivers=())
        self.vlan(1, 100)
        self.probe(tags=((0x8100, 100),), receivers=(1,))

    def test_delete_unrelated_vlan_preserves_pvid(self):
        self.prepare()
        self.vlan(0, 100, 'pvid untagged')
        self.vlan(0, 300)
        self.cmd('bridge vlan del dev %s vid 300' % self.ports[0])
        self.probe(receivers=(1,), out_tags=((0x8100, 100),))

    def test_no_pvid_survives_link_cycle(self):
        self.prepare()
        self.sut.down(self.ports[0]); self.sut.up(self.ports[0])
        self.wait_links()
        self.probe(receivers=())
        self.probe(tags=((0x8100, 0),), receivers=())

    def test_no_pvid_survives_bridge_rejoin(self):
        self.prepare()
        self.sut.deleteBridgeInterface(self.br, self.ports[0])
        self.sut.addBridgeInterface(self.br, self.ports[0])
        self.probe(receivers=())
        self.probe(tags=((0x8100, 0),), receivers=())

    def test_unaware_preserves_vlan(self):
        self.bridge(0)
        for vid in (1, 100, 4094):
            with self.subTest(vid=vid):
                self.probe(tags=((0x8100, vid),), out_tags=((0x8100, vid),))

    def test_filtering_transition(self):
        self.bridge(0)
        self.probe(tags=((0x8100, 100),))
        self.cmd('ip link set dev %s type bridge vlan_filtering 1' % self.br)
        self.probe(tags=((0x8100, 100),), receivers=())
        for i in range(3):
            self.vlan(i, 100)
        self.probe(tags=((0x8100, 100),))
        self.cmd('ip link set dev %s type bridge vlan_filtering 0' % self.br)
        self.probe(tags=((0x8100, 200),))

    def test_other_tpid_uses_pvid(self):
        self.bridge(1)
        tags = ((0x88a8, 3), (0x8100, 5))
        self.probe(tags=tags, out_tags=tags)
        self.cmd('bridge vlan del dev %s vid 1' % self.ports[0])
        self.probe(tags=tags, receivers=())

    def test_high_vid(self):
        self.bridge(1, default_pvid=0)
        for i in range(3):
            self.vlan(i, 4094)
        self.probe(tags=((0x8100, 4094),), out_tags=((0x8100, 4094),))


class PingSizes(SwitchCase):
    pass


def ping_size(index, size, bridged):
    def test(self):
        lower = self.ports[index]
        if bridged:
            lower = self.bridge(members=tuple(range(4)))
        self.local_ping(lower, host_index=index, size=size)
    return test


for _bridged in (False, True):
    for _port in range(4):
        for _size in (1, 32, 1472):
            setattr(PingSizes, 'test_pr17_%s_port%d_size%d' %
                    ('bridge' if _bridged else 'standalone', _port, _size),
                    ping_size(_port, _size, _bridged))


class VlanLocalAdmission(SwitchCase):
    def test_wrong_vid_not_terminated(self):
        upper = self.new_link(self.ports[0], 'vlan', 'id 100')
        self.local_ping(upper, vid=200, allowed=False)

    def test_cpu_bridge_self_membership(self):
        self.bridge(1, members=(0,), default_pvid=0)
        self.vlan(0, 100)
        upper = self.new_link(self.br, 'vlan', 'id 100')
        # Creating a VLAN upper may itself install the bridge self VID; remove
        # it explicitly to exercise CPU admission, then restore for reception.
        self.cmd('bridge vlan del dev %s vid 100 self' % self.br)
        self.local_ping(upper, vid=100, allowed=False)

    def test_cpu_bridge_self_member_admitted(self):
        self.bridge(1, members=(0,), default_pvid=0)
        self.vlan(0, 100)
        upper = self.new_link(self.br, 'vlan', 'id 100')
        self.cmd('bridge vlan add dev %s vid 100 self' % self.br)
        self.local_ping(upper, vid=100)
