# SPDX-License-Identifier: GPL-2.0
"""Ports of Linux net/forwarding and drivers/net/dsa switch scenarios.

Adapted assertions use host mz/tcpdump and board UART, not the upstream
single-kernel NETIFS/VRF harness. See docs/test-catalog.md for exact scope.
"""
import time

from switch_case import SwitchCase
from traffic import mac_text


class LinuxBridge(SwitchCase):
    def test_learning_enabled(self):
        self.bridge()
        mac = 0x02000000aa01
        self.probe(source_mac=mac)
        self.assertIn(mac_text(mac), self.sut.getFdb(self.ports[0]))

    def test_learning_disabled(self):
        self.bridge()
        self.cmd('bridge link set dev %s learning off' % self.ports[0])
        mac = 0x02000000aa02
        self.probe(source_mac=mac)
        self.assertNotIn(mac_text(mac), self.sut.getFdb(self.ports[0]))

    def test_unknown_unicast_flood_toggle(self):
        self.bridge()
        self.probe(destination=0x02000000bb01)
        self.cmd('bridge link set dev %s flood off' % self.ports[1])
        self.probe(destination=0x02000000bb01, receivers=(2,))
        self.cmd('bridge link set dev %s flood on' % self.ports[1])
        self.probe(destination=0x02000000bb01)

    def test_broadcast_flood_toggle(self):
        self.bridge()
        self.probe()
        self.cmd('bridge link set dev %s bcast_flood off' % self.ports[1], optional=True)
        self.probe(receivers=(2,))
        self.cmd('bridge link set dev %s bcast_flood on' % self.ports[1])
        self.probe()

    def test_multicast_flood_toggle(self):
        self.bridge(snooping=1)
        self.probe(destination=0x01005e010203, destination_ip='239.1.2.3')
        self.cmd('bridge link set dev %s mcast_flood off' % self.ports[1])
        self.probe(destination=0x01005e010203, destination_ip='239.1.2.3', receivers=(2,))

    def test_port_isolation(self):
        self.bridge()
        for i in (0, 1):
            self.cmd('bridge link set dev %s isolated on' % self.ports[i], optional=True)
        for version in (4, 6):
            self.probe(receivers=(2,), version=version)
            self.probe(source=2, receivers=(0, 1), version=version)
            self.probe(receivers=(2,), destination=0x02000000bb02, version=version)

    def test_hairpin(self):
        self.bridge()
        self.probe()
        self.cmd('bridge link set dev %s hairpin on' % self.ports[0], optional=True)
        self.probe(receivers=(0, 1, 2))
        self.cmd('bridge link set dev %s hairpin off' % self.ports[0])
        self.probe()

    def test_link_down_up(self):
        self.bridge()
        self.probe()
        self.sut.down(self.ports[1])
        self.probe(receivers=(2,))
        self.sut.up(self.ports[1]); self.wait_links()
        self.probe()

    def test_bridge_leave_rejoin(self):
        self.bridge()
        self.probe()
        self.sut.deleteBridgeInterface(self.br, self.ports[1])
        self.probe(receivers=(2,))
        self.sut.addBridgeInterface(self.br, self.ports[1])
        self.probe()

    def test_unaware_pvid_change(self):
        self.bridge()
        self.probe()
        self.cmd('ip link set dev %s type bridge vlan_default_pvid 100' % self.br)
        self.probe()
        self.probe(tags=((0x8100, 200),), out_tags=((0x8100, 200),))

    def test_blocked_port(self):
        self.bridge()
        # Explicit userspace STP ownership prevents automatic forwarding reset.
        self.cmd('ip link set dev %s type bridge stp_mode user' % self.br, optional=True)
        self.cmd('ip link set dev %s type bridge stp_state 1' % self.br)
        for port in self.ports[:3]:
            self.cmd('bridge link set dev %s state 3' % port)
        self.cmd('bridge link set dev %s state 4' % self.ports[1])
        self.assertIn('state blocking', self.cmd('bridge link show dev ' + self.ports[1]))
        self.probe(receivers=(2,))
        self.cmd('bridge link set dev %s state 3' % self.ports[1])
        self.probe()

    def test_reserved_link_local_not_forwarded(self):
        """STP-disabled bridges forward the bridge-group address, but not its peers."""
        self.bridge()
        for suffix in range(16):
            with self.subTest(suffix=suffix):
                self.probe(destination=0x0180c2000000 + suffix,
                           receivers=(1, 2) if suffix == 0 else (), ethertype=0x88b5)


class LinuxFdb(SwitchCase):
    def entry(self, mac, index, flags='static', vid=None):
        self.cmd('bridge fdb replace %s dev %s master %s %s' %
            (mac, self.ports[index], flags, '' if vid is None else 'vlan %d' % vid))

    def test_static_forwarding_and_delete(self):
        self.bridge()
        mac = '02:00:00:00:bb:03'
        self.probe(destination=mac)
        self.entry(mac, 1)
        self.probe(destination=mac, receivers=(1,))
        self.cmd('bridge fdb del %s dev %s master' % (mac, self.ports[1]))
        self.probe(destination=mac)

    def test_dynamic_roaming(self):
        self.bridge()
        mac = 0x02000000bb04
        self.probe(source_mac=mac)
        self.probe(source=1, destination=mac, receivers=(0,))
        self.probe(source=2, source_mac=mac, receivers=(0, 1))
        self.probe(source=1, destination=mac, receivers=(2,))

    def test_sticky_does_not_roam(self):
        self.bridge()
        mac = '02:00:00:00:bb:05'
        self.cmd('bridge fdb add %s dev %s master static sticky' % (mac, self.ports[1]), optional=True)
        self.probe(source=2, source_mac=mac, receivers=(0, 1))
        self.assertIn(mac, self.sut.getFdb(self.ports[1]))
        self.probe(destination=mac, receivers=(1,))

    def test_extern_learn_roaming(self):
        self.bridge()
        mac = '02:00:00:00:bb:06'
        self.entry(mac, 1, 'extern_learn')
        self.probe(destination=mac, receivers=(1,))
        self.probe(source=2, source_mac=mac, receivers=(0, 1))
        self.probe(destination=mac, receivers=(2,))

    def short_ageing(self):
        # iproute2 passes USER_HZ ticks, not milliseconds. 3000 = 30 s,
        # above mv88e6171's 15 s minimum and aligned with its timer quantum.
        output = self.cmd('ip link set dev %s type bridge ageing_time 3000' % self.br)
        self.assertNotRegex(output, r'failed \(err=-\d+\) to set attribute',
                            'Bridge ageing offload was rejected')
        info = self.read_json('ip -d -j link show dev ' + self.br)[0]
        self.assertEqual(info['linkinfo']['info_data']['ageing_time'], 3000)

    def test_dynamic_ageing(self):
        self.bridge()
        self.short_ageing()
        mac = 0x02000000bb07
        self.probe(source_mac=mac)
        self.assertIn(mac_text(mac), self.sut.getFdb(self.ports[0]))
        # Allow hardware timer quantization and a full ageing scan margin.
        deadline = time.monotonic() + 90
        while mac_text(mac) in self.sut.getFdb(self.ports[0]):
            if time.monotonic() >= deadline:
                self.fail('Dynamic FDB entry did not age out')
            time.sleep(1)

    def test_extern_learn_does_not_age(self):
        self.bridge()
        self.short_ageing()
        mac = '02:00:00:00:bb:08'
        self.entry(mac, 1, 'extern_learn')
        time.sleep(35)
        self.assertIn(mac, self.sut.getFdb(self.ports[1]))
        self.probe(destination=mac, receivers=(1,))

    def test_same_mac_different_vlans(self):
        self.bridge(1, default_pvid=0)
        for i in range(3):
            for vid in (100, 200):
                self.vlan(i, vid)
        mac = '02:00:00:00:bb:09'
        self.entry(mac, 1, vid=100); self.entry(mac, 2, vid=200)
        self.probe(destination=mac, tags=((0x8100, 100),), receivers=(1,))
        self.probe(destination=mac, tags=((0x8100, 200),), receivers=(2,))

    def test_local_mac_not_forwarded(self):
        self.bridge()
        mac = '02:00:00:00:bb:10'
        self.entry(mac, 1, 'local')
        self.probe(destination=mac, receivers=())

    def test_dsa_fdb_concurrent_add_delete_dump(self):
        self.bridge()
        port = self.ports[0]
        # Bounded counterpart of DSA test_bridge_fdb_stress.sh. The worker's
        # exit status is checked; panic monitoring stays active on the UART.
        status = '/tmp/dsa-fdb-' + self.token
        self.addCleanup(self.cmd, 'rm -f %s %s.log' % (status, status))
        worker = ('(for i in $(seq 1 100); do '
            'bridge fdb add 02:00:00:00:bb:11 dev %s master static || exit 1; '
            'bridge fdb del 02:00:00:00:bb:11 dev %s master static || exit 1; '
            'done) >%s.log 2>&1; echo $? >%s') % (port, port, status, status)
        pid = self.sut.start_ssh(worker)
        try:
            for _ in range(30):
                self.cmd('bridge fdb show dev ' + port)
            self.cmd('for i in $(seq 1 30); do test -f %s && break; sleep 1; done; '
                     'test -f %s && test "$(cat %s)" = 0' % (status, status, status))
        finally:
            # The shell remains our child while running. Remove the tracked
            # worker only after waiting for it, including the failure path.
            self.cmd('if test ! -f %s; then kill %d 2>/dev/null || :; fi' % (status, pid))
            self.sut.processes.discard(pid)
        self.assertNotIn('02:00:00:00:bb:11', self.sut.getFdb(port))


class LinuxMdb(SwitchCase):
    def prepare(self, vid=None):
        self.bridge(int(vid is not None), snooping=1)
        self.cmd('ip link set dev %s type bridge mcast_querier 1' % self.br)
        for i in (1, 2):
            self.cmd('bridge link set dev %s mcast_flood off' % self.ports[i])
        if vid is not None:
            for i in range(3):
                self.vlan(i, vid)
        time.sleep(10)  # upstream multicast-querier startup grace period

    def membership(self, version, vid=None, cycle=False):
        self.prepare(vid)
        group = '239.1.2.3' if version == 4 else 'ff0e::1234'
        mac = 0x01005e010203 if version == 4 else 0x333300001234
        tags = () if vid is None else ((0x8100, vid),)
        def probe(receivers):
            self.probe(destination=mac, destination_ip=group, version=version,
                       tags=tags, receivers=receivers)
        probe(())
        suffix = '' if vid is None else ' vid %d' % vid
        add = 'bridge mdb add dev %s port %s grp %s permanent%s' % (self.br, self.ports[1], group, suffix)
        delete = 'bridge mdb del dev %s port %s grp %s%s' % (self.br, self.ports[1], group, suffix)
        if cycle:
            self.sut.down(self.ports[1])
        self.cmd(add)
        self.assertIn(group, self.cmd('bridge mdb show dev ' + self.br))
        if cycle:
            self.sut.up(self.ports[1]); self.wait_links()
        probe((1,))
        if cycle:
            self.sut.down(self.ports[1])
        self.cmd(delete)
        if cycle:
            self.sut.up(self.ports[1]); self.wait_links()
        probe(())

    def test_igmp_mdb_add_delete(self):
        self.membership(4)

    def test_mld_mdb_add_delete(self):
        self.membership(6)

    def test_igmp_vlan_mdb(self):
        self.membership(4, 100)

    def test_mld_vlan_mdb(self):
        self.membership(6, 100)

    def test_mdb_port_down_ipv4(self):
        self.membership(4, cycle=True)

    def test_mdb_port_down_ipv6(self):
        self.membership(6, cycle=True)

    def test_mdb_vlan_scope(self):
        self.prepare(100)
        for i in range(3):
            self.vlan(i, 200)
        self.cmd('bridge mdb add dev %s port %s grp 239.1.2.3 permanent vid 100' % (self.br, self.ports[1]))
        self.probe(destination=0x01005e010203, destination_ip='239.1.2.3', tags=((0x8100, 100),), receivers=(1,))
        self.probe(destination=0x01005e010203, destination_ip='239.1.2.3', tags=((0x8100, 200),), receivers=())


class LinuxLockedPort(SwitchCase):
    def exercise(self, version=4, vid=None):
        self.bridge(int(vid is not None))
        tags = () if vid is None else ((0x8100, vid),)
        if vid is not None:
            for i in range(3):
                self.vlan(i, vid)
        self.cmd('bridge link set dev %s learning off' % self.ports[0])
        self.probe(version=version, tags=tags)
        self.cmd('bridge link set dev %s locked on' % self.ports[0], optional=True)
        self.probe(version=version, tags=tags, receivers=())
        mac = self.traffic.getInterfaceMacAddress(self.hosts[0])
        suffix = '' if vid is None else ' vlan %d' % vid
        self.cmd('bridge fdb replace %s dev %s master static%s' % (mac, self.ports[0], suffix))
        self.probe(version=version, tags=tags)
        self.cmd('bridge link set dev %s locked off' % self.ports[0])
        self.cmd('bridge fdb del %s dev %s master%s' % (mac, self.ports[0], suffix))
        self.probe(version=version, tags=tags)

    def test_locked_ipv4(self):
        self.exercise()

    def test_locked_ipv6(self):
        self.exercise(6)

    def test_locked_vlan(self):
        self.exercise(vid=100)

    def test_mab_authorization(self):
        self.bridge()
        self.cmd('bridge link set dev %s learning on locked on mab on' % self.ports[0], optional=True)
        mac = self.traffic.getInterfaceMacAddress(self.hosts[0])
        self.probe(receivers=())
        output = self.cmd('bridge fdb show dev ' + self.ports[0])
        self.assertTrue(any(mac in line and 'locked' in line for line in output.splitlines()),
                        'MAB did not create a locked FDB entry')
        self.cmd('bridge fdb replace %s dev %s master static' % (mac, self.ports[0]))
        self.probe()


class LinuxNoForwarding(SwitchCase):
    def exercise(self, mode):
        if mode == 'two_bridges':
            self.bridge(members=(0,))
            second = 'dsac' + self.token
            self.sut.addBridge(second); self.sut.up(second)
            self.sut.addBridgeInterface(second, self.ports[1])
        if mode == 'different_pvids':
            self.bridge(1, members=(0, 1), default_pvid=0)
            self.vlan(0, 1, 'pvid untagged'); self.vlan(1, 2, 'pvid untagged')
        # Linux no_forwarding.sh samples VID 0 and the upper VLAN boundary.
        for vid in (None, 0, 1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 4000, 4094):
            tags = () if vid is None else ((0x8100, vid),)
            for kind in ('ipv4_uc', 'ipv4_mc', 'ipv6_uc', 'ipv6_mc', 'nonip_uc', 'nonip_mc', 'broadcast'):
                version = 6 if 'ipv6' in kind else 4
                destination = (0x01005e000001 if kind == 'ipv4_mc' else
                    0x333300000001 if kind == 'ipv6_mc' else
                    0x010203040506 if kind == 'nonip_mc' else
                    0xffffffffffff if kind == 'broadcast' else
                    self.traffic.getInterfaceMacAddress(self.hosts[1]))
                with self.subTest(mode=mode, vid=vid, kind=kind):
                    self.probe(receivers=(), tags=tags, version=version, destination=destination,
                               ethertype=0x88b5 if kind.startswith('nonip') else 0,
                               destination_ip='224.0.0.1' if kind == 'ipv4_mc' else
                                              'ff02::1' if kind == 'ipv6_mc' else None)

    def test_standalone(self):
        self.exercise('standalone')

    def test_two_bridges(self):
        self.exercise('two_bridges')

    def test_different_pvids(self):
        self.exercise('different_pvids')


class LinuxTc(SwitchCase):
    def exercise(self, match, action='drop', protocol='ip', version=4, tags=()):
        self.bridge()
        self.probe(version=version, tags=tags)
        port = self.ports[0]
        self.cmd('command -v tc', optional=True)
        self.cmd('tc qdisc add dev %s clsact' % port, optional=True)
        self.addCleanup(self.cmd, 'tc qdisc del dev %s clsact' % port)
        self.cmd('tc filter add dev %s ingress protocol %s pref 1 flower skip_sw %s action %s' %
                 (port, protocol, match, action), optional=True)
        output = self.cmd('tc -s filter show dev %s ingress' % port)
        self.assertRegex(output, r'\bin_hw\b', 'skip_sw rule was not offloaded')
        expected = () if action == 'drop' else (1, 2)
        if action.startswith('mirred egress redirect'):
            expected = (3,)
        if action.startswith('mirred egress mirror'):
            expected = (1, 2, 3)
        self.probe(version=version, tags=tags, receivers=expected)
        self.cmd('tc filter del dev %s ingress pref 1' % port)
        self.probe(version=version, tags=tags)

    def test_flower_ipv4_drop(self):
        self.exercise('dst_ip 192.0.2.200')

    def test_flower_ipv6_drop(self):
        self.exercise('dst_ip 2001:db8::200', protocol='ipv6', version=6)

    def test_flower_udp_drop(self):
        self.exercise('ip_proto udp dst_port 17185')

    def test_flower_vlan_drop(self):
        self.exercise('vlan_id 100', protocol='802.1Q', tags=((0x8100, 100),))

    def test_flower_mac_drop(self):
        self.exercise('src_mac ' + self.traffic.getInterfaceMacAddress(self.hosts[0]), protocol='all')

    def test_mirror(self):
        self.exercise('dst_ip 192.0.2.200', 'mirred egress mirror dev ' + self.ports[3])

    def test_redirect(self):
        self.exercise('dst_ip 192.0.2.200', 'mirred egress redirect dev ' + self.ports[3])


class LinuxRouting(SwitchCase):
    def exercise(self, mode, version):
        ingress, egress = self.ports[:2]
        out_tags = ()
        if mode.startswith('bridge'):
            aware = mode == 'bridge_aware'
            ingress = self.bridge(int(aware), members=(0,))
            if aware:
                self.vlan(0, 100)
                self.cmd('bridge vlan add dev %s vid 100 self' % self.br)
                ingress = self.new_link(self.br, 'vlan', 'id 100')
        elif mode == 'vlan':
            ingress = self.new_link(ingress, 'vlan', 'id 100')
            egress = self.new_link(egress, 'vlan', 'id 200')
            out_tags = ((0x8100, 200),)
        tags = ((0x8100, 100),) if mode in ('vlan', 'bridge_aware') else ()
        key = 'net.ipv4.ip_forward' if version == 4 else 'net.ipv6.conf.all.forwarding'
        old = self.cmd('sysctl -n ' + key).strip()
        self.addCleanup(self.cmd, 'sysctl -w %s=%s' % (key, old))
        self.cmd('sysctl -w %s=1' % key)
        src, dst, target, prefix = (('192.168.58.1', '198.51.100.1', '198.51.100.2', 24)
            if version == 4 else ('fd42:4242::1', '2001:db8:2::1', '2001:db8:2::2', 64))
        for dev, address in ((ingress, src), (egress, dst)):
            self.cmd('ip addr add %s/%d dev %s' % (address, prefix, dev))
            self.addCleanup(self.cmd, 'ip addr del %s/%d dev %s' % (address, prefix, dev))
        mac = self.traffic.getInterfaceMacAddress(self.hosts[1])
        self.cmd('ip -%d neigh replace %s lladdr %s nud permanent dev %s' % (version, target, mac, egress))
        self.addCleanup(self.cmd, 'ip -%d neigh del %s dev %s' % (version, target, egress))
        time.sleep(2)
        destination = self.sut.getMacAddress(ingress)
        self.probe(destination=destination, destination_ip=target, version=version, tags=tags,
                   receivers=(1,), out_tags=out_tags)


def routing_test(mode, version):
    def test(self):
        self.exercise(mode, version)
    return test


for _mode in ('standalone', 'vlan', 'bridge_unaware', 'bridge_aware'):
    for _version in (4, 6):
        setattr(LinuxRouting, 'test_%s_ipv%d' % (_mode, _version), routing_test(_mode, _version))


class LinuxBridgeControl(SwitchCase):
    def test_vlan_range_dump(self):
        self.bridge(1, default_pvid=0)
        self.cmd('bridge vlan add dev %s vid 100-110' % self.ports[0])
        records = self.read_json('bridge -j vlan show dev ' + self.ports[0], optional=True)
        vids = set()
        for record in records:
            for vlan in record.get('vlans', []):
                start = vlan['vlan']
                vids.update(range(start, vlan.get('vlanEnd', start) + 1))
        self.assertEqual(vids, set(range(100, 111)))
        self.cmd('bridge vlan del dev %s vid 100-110' % self.ports[0])
        records = self.read_json('bridge -j vlan show dev ' + self.ports[0])
        self.assertFalse(any(r.get('vlans') for r in records))

    def test_backup_port(self):
        """Backup configuration persists across primary down/up.

        Hardware transit failover requires explicit driver offload support;
        acceptance of the software bridge option does not promise it.
        """
        self.bridge(members=(0, 1, 2))
        self.cmd('bridge link set dev %s backup_port %s' % (self.ports[1], self.ports[2]), optional=True)
        def check_backup():
            record = self.read_json('bridge -j -d link show dev ' + self.ports[1])[0]
            self.assertEqual(record['backup_port'], self.ports[2])
        check_backup()
        self.cmd('bridge fdb replace 02:00:00:00:bb:20 dev %s master static' % self.ports[1])
        self.probe(destination=0x02000000bb20, receivers=(1,))
        self.sut.down(self.ports[1])
        check_backup()
        self.sut.up(self.ports[1]); self.wait_links()
        self.probe(destination=0x02000000bb20, receivers=(1,))
        self.cmd('bridge link set dev %s nobackup_port' % self.ports[1])

    def test_fdb_flush_dynamic_preserves_static(self):
        self.bridge()
        learned = 0x02000000bb21
        self.probe(source_mac=learned)
        # Ensure there is a software dynamic entry to flush: transit hardware
        # learning alone need not notify the bridge master.
        self.cmd('bridge fdb replace %s dev %s master dynamic' %
                 (mac_text(learned), self.ports[0]))
        before = self.cmd('bridge fdb show dev ' + self.ports[0])
        self.assertTrue(any(mac_text(learned) in line and 'master ' in line
                            for line in before.splitlines()),
                        'No software dynamic FDB entry was installed before flush')
        self.cmd('bridge fdb replace 02:00:00:00:bb:22 dev %s master static' % self.ports[0])
        self.cmd('bridge fdb flush dev %s master dynamic' % self.ports[0], optional=True)
        output = self.cmd('bridge fdb show dev ' + self.ports[0])
        self.assertTrue(any('02:00:00:00:bb:22' in line and
                            'master ' in line and 'static' in line
                            for line in output.splitlines()),
                        'Software static FDB entry did not survive dynamic flush')
        # Check bridge-master records: self/hardware dumps may be asynchronous.
        self.assertFalse(any(mac_text(learned) in line and 'master ' in line for line in output.splitlines()))
        # A master flush need not flush autonomous hardware self entries.
        # Check preserved static forwarding independently of dynamic state.
        self.probe(source=1, destination=0x02000000bb22, receivers=(0,))


class LinuxStpMode(SwitchCase):
    def prepare(self):
        self.bridge(members=(0, 1))
        self.cmd('ip link set dev %s type bridge stp_mode auto' % self.br, optional=True)
        if 'stp_mode' not in self.info():
            self.skipTest('Kernel/iproute2 does not expose bridge stp_mode')

    def info(self):
        return self.read_json('ip -d -j link show dev ' + self.br)[0]['linkinfo']['info_data']

    def mode(self, mode, state=None):
        self.cmd('ip link set dev %s type bridge stp_mode %s%s' %
                 (self.br, mode, '' if state is None else ' stp_state %d' % state))

    def test_default_auto(self):
        self.bridge(members=(0, 1))
        info = self.info()
        if 'stp_mode' not in info:
            self.skipTest('Kernel/iproute2 does not expose bridge stp_mode')
        self.assertEqual(info['stp_mode'], 'auto')

    def test_set_modes(self):
        self.prepare()
        for mode in ('user', 'kernel', 'auto'):
            self.mode(mode)
            self.assertEqual(self.info()['stp_mode'], mode)

    def test_reject_change_while_active(self):
        self.prepare(); self.mode('user', 1)
        code, output = self.sut.uart.command('ip link set dev %s type bridge stp_mode kernel' % self.br, 10)
        self.assertNotEqual(code, 0, output)
        self.assertEqual(self.info()['stp_mode'], 'user')

    def test_idempotent_mode_while_active(self):
        self.prepare(); self.mode('user', 1); self.mode('user')
        self.assertEqual(self.info()['stp_state'], 2)

    def test_user_mode(self):
        self.prepare(); self.mode('user', 1)
        self.assertEqual(self.info()['stp_state'], 2)

    def test_kernel_mode(self):
        self.prepare(); self.mode('kernel', 1)
        self.assertEqual(self.info()['stp_state'], 1)

    def test_atomic_mode_and_state(self):
        self.prepare(); self.mode('user', 1)
        self.assertEqual(self.info()['stp_mode'], 'user')
        self.assertEqual(self.info()['stp_state'], 2)

    def test_mode_persistence(self):
        self.prepare(); self.mode('user', 1)
        self.cmd('ip link set dev %s type bridge stp_state 0' % self.br)
        self.assertEqual(self.info()['stp_mode'], 'user')
        self.cmd('ip link set dev %s type bridge stp_state 1' % self.br)
        self.assertEqual(self.info()['stp_state'], 2)


class LinuxVlanBinding(SwitchCase):
    def exercise(self, initial, toggle=False, upper_down=False, lower_down=False):
        self.bridge(1, members=(0, 1))
        self.vlan(0, 100)
        self.cmd('bridge vlan add dev %s vid 100 self' % self.br)
        # Probe parser/kernel capability before creating the owned interface.
        name = 'dsav' + self.token
        self.cmd('ip link add link %s name %s type vlan id 100 bridge_binding %s' %
                 (self.br, name, 'on' if initial else 'off'), optional=True)
        self.addCleanup(self.cmd, 'ip link del dev ' + name)
        self.cmd('ip link set dev %s up' % name)
        self.sut.down(self.ports[0])
        if lower_down:
            self.sut.down(self.br)
        if upper_down:
            self.cmd('ip link set dev %s down' % name)
        expected = initial
        if toggle:
            expected = not initial
            self.cmd('ip link set dev %s type vlan bridge_binding %s' % (name, 'on' if expected else 'off'))
        if lower_down:
            self.sut.up(self.br)
        if upper_down:
            self.cmd('ip link set dev %s up' % name)
        deadline = time.monotonic() + 5
        while True:
            state = self.read_json('ip -j link show dev ' + name)[0]
            if ('LOWER_UP' in state['flags']) == (not expected):
                break
            if time.monotonic() >= deadline:
                self.fail('VLAN bridge_binding carrier does not match VLAN member state: ' + str(state))
            time.sleep(.2)
        self.sut.up(self.ports[0]); self.wait_links()
        self.assertIn('LOWER_UP', self.read_json('ip -j link show dev ' + name)[0]['flags'])


def binding_test(initial, toggle, upper_down, lower_down):
    def test(self):
        self.exercise(initial, toggle, upper_down, lower_down)
    return test


for _initial in (False, True):
    setattr(LinuxVlanBinding, 'test_binding_' + ('on' if _initial else 'off'),
            binding_test(_initial, False, False, False))
    for _when in ('up', 'upper_down', 'lower_down'):
        setattr(LinuxVlanBinding, 'test_toggle_%s_when_%s' % ('off' if _initial else 'on', _when),
                binding_test(_initial, True, _when == 'upper_down', _when == 'lower_down'))


class LinuxMtu(SwitchCase):
    def test_invalid_bounds(self):
        for port in self.ports:
            with self.subTest(port=port):
                info = self.read_json('ip -d -j link show dev ' + port)[0]
                self.assertIn('min_mtu', info)
                self.assertIn('max_mtu', info)
                for mtu in (info['min_mtu'] - 1, info['max_mtu'] + 1):
                    code, output = self.sut.uart.command('ip link set dev %s mtu %d' % (port, mtu), 10)
                    self.assertNotEqual(code, 0, output)
                    after = self.read_json('ip -j link show dev ' + port)[0]
                    self.assertEqual(after['mtu'], info['mtu'])

    def jumbo(self, index):
        import json
        from lab_support import run
        port, host = self.ports[index], self.hosts[index]
        old_board = self.read_json('ip -j link show dev ' + port)[0]['mtu']
        host_info = json.loads(run('ip', '-j', '-d', 'link', 'show', 'dev', host).stdout)[0]
        old_host = host_info['mtu']
        if host_info.get('max_mtu', 2000) < 2000:
            self.skipTest('Host interface %s max_mtu=%s cannot carry MTU 2000' %
                          (host, host_info['max_mtu']))
        self.cmd('ip link set dev %s mtu 2000' % port, optional=True)
        self.addCleanup(self.cmd, 'ip link set dev %s mtu %d' % (port, old_board))
        run('ip', 'link', 'set', 'dev', host, 'mtu', '2000')
        self.addCleanup(run, 'ip', 'link', 'set', 'dev', host, 'mtu', str(old_host))
        self.local_ping(port, host_index=index, size=1972)


def jumbo_test(index):
    def test(self):
        self.jumbo(index)
    return test


for _index in range(4):
    setattr(LinuxMtu, 'test_jumbo_port%d' % _index, jumbo_test(_index))
