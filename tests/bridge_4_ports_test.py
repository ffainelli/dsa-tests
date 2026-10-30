"""Port-independent single-bridge forwarding, isolation and MAC movement."""
import time
import unittest

SUT = TRAFFIC = CONFIG = None
VLAN_FILTERING = False


class BridgeFourPorts(unittest.TestCase):
    def test_01_create_bridge(self):
        SUT.up(CONFIG.SUT_MASTER)
        SUT.addBridge('br1')
        if VLAN_FILTERING:
            SUT.bridgeEnableVlanFiltering('br1')
        SUT.up('br1')
        for index in range(4):
            SUT.up(CONFIG['SUT_LAN%d' % index])
            TRAFFIC.addInterface(CONFIG['HOST_LAN%d' % index])
            if index < 3:
                SUT.addBridgeInterface('br1', CONFIG['SUT_LAN%d' % index])
        time.sleep(5)
        TRAFFIC.learning()

    def test_02_unicast_ipv4_ipv6(self):
        for version in (4, 6):
            for source in range(4):
                for destination in range(4):
                    if source == destination:
                        continue
                    with self.subTest(version=version, source=source, destination=destination):
                        add = TRAFFIC.addUDPStream if version == 4 else TRAFFIC.addUDPv6Stream
                        add(CONFIG['HOST_LAN%d' % source], CONFIG['HOST_LAN%d' % destination], 20, 100)
                        TRAFFIC.run()
                        for port in range(4):
                            self.assertEqual(TRAFFIC.getStats(CONFIG['HOST_LAN%d' % port]), {
                                'tx_pkts': 20 if port == source else 0,
                                'rx_pkts': 20 if source < 3 and port < 3 and port != source
                                and (port == destination or destination == 3) else 0})

    def test_03_broadcast(self):
        for source in range(4):
            TRAFFIC.addUDPBroadcastStream(CONFIG['HOST_LAN%d' % source], 20, 100)
            TRAFFIC.run()
            for port in range(4):
                self.assertEqual(TRAFFIC.getStats(CONFIG['HOST_LAN%d' % port]), {
                    'tx_pkts': 20 if port == source else 0,
                    'rx_pkts': 20 if source < 3 and port < 3 and port != source else 0})

    def test_04_blocked_port(self):
        output = SUT.ssh('ip link set dev br1 type bridge stp_mode user')
        if SUT.exit_code and any(text in output.lower() for text in
                                 ('unknown command "stp_mode"', 'not supported')):
            self.skipTest('Explicit userspace STP control unavailable: ' + output)
        self.assertEqual(SUT.exit_code, 0, output)
        self.addCleanup(SUT.checked, 'ip link set dev br1 type bridge stp_mode auto')
        self.addCleanup(SUT.checked, 'ip link set dev br1 type bridge stp_state 0')
        SUT.checked('ip link set dev br1 type bridge stp_state 1')
        for index in range(3):
            SUT.checked('bridge link set dev %s state 3' % CONFIG['SUT_LAN%d' % index])
        SUT.checked('bridge link set dev %s state 4' % CONFIG.SUT_LAN2)
        self.assertIn('state blocking', SUT.checked('bridge link show dev ' + CONFIG.SUT_LAN2))
        try:
            TRAFFIC.addUDPBroadcastStream(CONFIG.HOST_LAN0, 20, 100)
            TRAFFIC.run()
            self.assertEqual(TRAFFIC.getStats(CONFIG.HOST_LAN1)['rx_pkts'], 20)
            self.assertEqual(TRAFFIC.getStats(CONFIG.HOST_LAN2)['rx_pkts'], 0)
            self.assertEqual(TRAFFIC.getStats(CONFIG.HOST_LAN3)['rx_pkts'], 0)
        finally:
            SUT.checked('bridge link set dev %s state 3' % CONFIG.SUT_LAN2)

    def test_05_mac_move(self):
        from traffic import mac_bytes
        moved_mac = int.from_bytes(mac_bytes(TRAFFIC.getInterfaceMacAddress(CONFIG.HOST_LAN1)), 'big')
        try:
            TRAFFIC.addUDPMacIncStream(CONFIG.HOST_LAN2, CONFIG.HOST_LAN0, moved_mac, 2, 100)
            TRAFFIC.run()
            TRAFFIC.addUDPStream(CONFIG.HOST_LAN0, CONFIG.HOST_LAN1, 20, 100)
            TRAFFIC.run()
            self.assertEqual(TRAFFIC.getStats(CONFIG.HOST_LAN2)['rx_pkts'], 20)
            self.assertEqual(TRAFFIC.getStats(CONFIG.HOST_LAN1)['rx_pkts'], 0)
        finally:
            TRAFFIC.learning()

    def test_99_delete_bridge(self):
        SUT.deleteBridge('br1')
