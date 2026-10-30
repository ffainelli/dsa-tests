# SPDX-License-Identifier: GPL-2.0
"""User-facing home/small-office switch topologies on four direct cables."""
import json

from switch_case import SwitchCase


class HomeOffice(SwitchCase):
    def roles(self, labels):
        (self.folder / 'topology.json').write_text(json.dumps({label: {
            'board_port': self.ports[i], 'host_interface': self.hosts[i]}
            for i, label in enumerate(labels)}, indent=2) + '\n')

    def flat_lan(self):
        self.roles(('workstation', 'printer', 'nas', 'access_point'))
        self.bridge(members=(0, 1, 2, 3))

    def segmented_lan(self):
        self.roles(('staff_access', 'guest_access', 'iot_access', 'router_ap_trunk'))
        # PC, guest, IoT access ports; tagged uplink to router/AP/other switch.
        self.bridge(1, members=(0, 1, 2, 3), default_pvid=0)
        for index, vid in enumerate((10, 20, 30)):
            self.vlan(index, vid, 'pvid untagged')
            self.vlan(3, vid)

    def test_flat_lan_devices_communicate(self):
        """Wired clients share a LAN using IPv4 and IPv6, with learned unicast."""
        self.flat_lan()
        self.traffic.learning()
        for version in (4, 6):
            for source in range(4):
                for destination in range(4):
                    if source != destination:
                        with self.subTest(version=version, source=source, destination=destination):
                            self.probe(source=source, receivers=(destination,), version=version,
                                destination=self.traffic.getInterfaceMacAddress(self.hosts[destination]))
        self.probe(receivers=(1, 2, 3))

    def test_lan_does_not_bridge_to_wan(self):
        """A separate router-facing port cannot leak Ethernet traffic into LAN."""
        self.roles(('lan_client', 'lan_printer', 'lan_nas', 'simulated_wan'))
        self.bridge(members=(0, 1, 2))
        for version in (4, 6):
            self.probe(receivers=(1, 2), version=version)
            self.probe(source=3, receivers=(), version=version)
        self.local_ping(self.ports[3], host_index=3)

    def test_staff_guest_iot_access_and_trunk(self):
        """Access clients reach their own VLAN on a shared tagged uplink."""
        self.segmented_lan()
        for index, vid in enumerate((10, 20, 30)):
            for version in (4, 6):
                with self.subTest(vid=vid, version=version):
                    self.probe(source=index, receivers=(3,), version=version,
                               out_tags=((0x8100, vid),))
                    self.probe(source=3, receivers=(index,), version=version,
                               tags=((0x8100, vid),), out_tags=())

    def test_each_vlan_reaches_its_router_interface(self):
        """The switch CPU has a reachable gateway interface in each VLAN."""
        self.segmented_lan()
        for index, vid in enumerate((10, 20, 30)):
            upper = self.new_link(self.br, 'vlan', 'id %d' % vid)
            self.cmd('bridge vlan add dev %s vid %d self' % (self.br, vid))
            for version in (4, 6):
                with self.subTest(vid=vid, version=version):
                    self.local_ping(upper, host_index=index, subnet=vid, version=version)

    def test_wrong_vlan_and_untagged_uplink_are_rejected(self):
        """Guest/IoT clients cannot inject another VLAN onto the trunk."""
        self.segmented_lan()
        self.probe(source=3, receivers=())
        self.probe(source=3, tags=((0x8100, 99),), receivers=())
        for index, vid in enumerate((10, 20, 30)):
            with self.subTest(access_port=index):
                other = 20 if vid == 10 else 10
                self.probe(source=index, tags=((0x8100, other),), receivers=())

    def test_unplug_and_reconnect_a_client(self):
        """Other LAN clients keep working while one client disconnects."""
        self.flat_lan()
        self.probe(receivers=(1, 2, 3))
        self.sut.down(self.ports[2])
        self.probe(receivers=(1, 3))
        self.sut.up(self.ports[2]); self.wait_links()
        self.probe(receivers=(1, 2, 3))

    def test_move_a_device_to_another_socket(self):
        """A device keeps its MAC address when moved to another LAN socket."""
        self.flat_lan()
        device = 0x020000005501
        self.probe(source=1, source_mac=device, receivers=(0, 2, 3))
        self.probe(destination=device, receivers=(1,))
        self.probe(source=2, source_mac=device, receivers=(0, 1, 3))
        self.probe(destination=device, receivers=(2,))

    def test_reassign_a_guest_socket_to_staff(self):
        """Changing a socket's VLAN takes effect without rebooting the switch."""
        self.segmented_lan()
        self.probe(source=1, receivers=(3,), out_tags=((0x8100, 20),))
        self.cmd('bridge vlan del dev %s vid 20' % self.ports[1])
        self.vlan(1, 10, 'pvid untagged')
        self.probe(source=1, receivers=(0, 3), out_tags={0: (), 3: ((0x8100, 10),)})
        self.probe(source=3, tags=((0x8100, 20),), receivers=())
