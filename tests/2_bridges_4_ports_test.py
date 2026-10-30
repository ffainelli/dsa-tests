#!/usr/bin/env python
"""Test the operation of two bridges on the SUT"""

import time
import unittest as unittest2

import params
import sut
import traffic


zero_stats = {
    'rx_pkts': 0,
    'tx_pkts': 0,
    }

tx_10_stats = {
    'rx_pkts': 0,
    'tx_pkts': 10,
    }

tx_30_stats = {
    'rx_pkts': 0,
    'tx_pkts': 30,
    }

rx_10_stats = {
    'rx_pkts': 10,
    'tx_pkts': 0,
    }

rx_20_stats = {
    'rx_pkts': 20,
    'tx_pkts': 0,
    }

rx_30_stats = {
    'rx_pkts': 30,
    'tx_pkts': 0,
    }

ethtool_zero = {'rx_packets': (0, 4),
                'in_unicast': (0, 4),
                'tx_packets': (0, 4),
                'out_unicast': (0, 4)}

ethtool_rx_30 = {'in_unicast': (30, 34),
                 'out_unicast': (0, 4)}

ethtool_tx_10 = {'in_unicast': (0, 4),
                 'out_unicast': (10, 14)}

ethtool_tx_30 = {'in_unicast': (30, 34),
                 'out_unicast': (30, 34)}

SUT = None
TRAFFIC = None
CONFIG = None


class two_bridges_4_ports_test(unittest2.TestCase):
    '''Class containing the test cases'''

    def setUp(self):
        """Setup ready to perform the test"""
        self.sut = SUT
        self.traffic = TRAFFIC
        self.config = CONFIG
        self.maxDiff = None

    def test_01_create_bridge(self):
        """Create the bridge"""
        # Ensure all the interfaces are up
        self.sut.up(self.config.SUT_MASTER)
        self.sut.up(self.config.SUT_LAN0)
        self.sut.up(self.config.SUT_LAN1)
        self.sut.up(self.config.SUT_LAN2)
        self.sut.up(self.config.SUT_LAN3)

        self.sut.addBridge('br1')
        self.sut.up('br1')
        self.sut.addBridgeInterface('br1', self.config.SUT_LAN0)
        self.sut.addBridgeInterface('br1', self.config.SUT_LAN1)

        self.sut.addBridge('br2')
        self.sut.up('br2')
        self.sut.addBridgeInterface('br2', self.config.SUT_LAN2)
        self.sut.addBridgeInterface('br2', self.config.SUT_LAN3)

        # Wait the forwarding delay of the bridge
        time.sleep(10)

        self.traffic.addInterface(self.config.HOST_LAN0)
        self.traffic.addInterface(self.config.HOST_LAN1)
        self.traffic.addInterface(self.config.HOST_LAN2)
        self.traffic.addInterface(self.config.HOST_LAN3)

    def test_02_learn(self):
        """Send learning packets, so the bridge knows which MAC address
           is where"""
        self.traffic.learning()

    def test_03_bridged_unicast_lan0(self):
        """Send traffic between bridged ports, and ensure they come out the
           expected ports. lan0 is the source"""
        ethtool_stats_lan0 = self.sut.getEthtoolStats(self.config.SUT_LAN0)
        ethtool_stats_lan1 = self.sut.getEthtoolStats(self.config.SUT_LAN1)
        ethtool_stats_lan2 = self.sut.getEthtoolStats(self.config.SUT_LAN2)
        ethtool_stats_lan3 = self.sut.getEthtoolStats(self.config.SUT_LAN3)

        self.traffic.addUDPStream(self.config.HOST_LAN0,
                                  self.config.HOST_LAN1, 10, 10)
        self.traffic.addUDPStream(self.config.HOST_LAN0,
                                  self.config.HOST_LAN2, 10, 10)
        self.traffic.addUDPStream(self.config.HOST_LAN0,
                                  self.config.HOST_LAN3, 10, 10)
        self.traffic.run()

        stats_lan0 = self.traffic.getStats(self.config.HOST_LAN0)
        stats_lan1 = self.traffic.getStats(self.config.HOST_LAN1)
        stats_lan2 = self.traffic.getStats(self.config.HOST_LAN2)
        stats_lan3 = self.traffic.getStats(self.config.HOST_LAN3)

        self.assertEqual(stats_lan0, tx_30_stats)
        # Not obviouus: The destination MAC for LAN2 and LAN3 are not
        # known, so the packets are flooded out LAN 1 as
        # well. However, it depends on the kernel configuration. With
        # just 802.1d, the ATU/FID is shared, so it knows the MAC
        # address is on a different port and so drops the packet. With
        # 802.1d an 802.1q, they are separated, and so it floods.  So
        # allow either to be a pass result.
        self.assertTrue(stats_lan1 == rx_10_stats or
                        stats_lan1 == rx_20_stats or
                        stats_lan1 == rx_30_stats, stats_lan1)
        self.assertEqual(stats_lan2, zero_stats)
        self.assertEqual(stats_lan3, zero_stats)

        self.sut.checkEthtoolStatsRange(self.config.SUT_LAN0,
                                        ethtool_stats_lan0,
                                        ethtool_rx_30, self)
        self.sut.checkEthtoolStatsRange(self.config.SUT_LAN2,
                                        ethtool_stats_lan2,
                                        ethtool_zero, self)
        self.sut.checkEthtoolStatsRange(self.config.SUT_LAN3,
                                        ethtool_stats_lan3,
                                        ethtool_zero, self)

    def test_04_bridged_unicast_lan1(self):
        """Send traffic between bridged ports, and ensure they come out the
           expected ports. lan1 is the source"""
        self.traffic.addUDPStream(self.config.HOST_LAN1,
                                  self.config.HOST_LAN0, 10, 10)
        self.traffic.addUDPStream(self.config.HOST_LAN1,
                                  self.config.HOST_LAN2, 10, 10)
        self.traffic.addUDPStream(self.config.HOST_LAN1,
                                  self.config.HOST_LAN3, 10, 10)
        self.traffic.run()

        stats_lan0 = self.traffic.getStats(self.config.HOST_LAN0)
        stats_lan1 = self.traffic.getStats(self.config.HOST_LAN1)
        stats_lan2 = self.traffic.getStats(self.config.HOST_LAN2)
        stats_lan3 = self.traffic.getStats(self.config.HOST_LAN3)

        self.assertTrue(stats_lan0 == rx_10_stats or
                        stats_lan0 == rx_20_stats or
                        stats_lan0 == rx_30_stats, stats_lan0)
        self.assertEqual(stats_lan1, tx_30_stats)
        self.assertEqual(stats_lan2, zero_stats)
        self.assertEqual(stats_lan3, zero_stats)

    def test_05_bridged_unicast_lan2(self):
        """Send traffic between bridged ports, and ensure they come out the
           expected ports. lan2 is the source"""
        self.traffic.addUDPStream(self.config.HOST_LAN2,
                                  self.config.HOST_LAN0, 10, 10)
        self.traffic.addUDPStream(self.config.HOST_LAN2,
                                  self.config.HOST_LAN1, 10, 10)
        self.traffic.addUDPStream(self.config.HOST_LAN2,
                                  self.config.HOST_LAN3, 10, 10)
        self.traffic.run()

        stats_lan0 = self.traffic.getStats(self.config.HOST_LAN0)
        stats_lan1 = self.traffic.getStats(self.config.HOST_LAN1)
        stats_lan2 = self.traffic.getStats(self.config.HOST_LAN2)
        stats_lan3 = self.traffic.getStats(self.config.HOST_LAN3)

        self.assertEqual(stats_lan0, zero_stats)
        self.assertEqual(stats_lan1, zero_stats)
        self.assertEqual(stats_lan2, tx_30_stats)
        self.assertTrue(stats_lan3 == rx_10_stats or
                        stats_lan3 == rx_30_stats, stats_lan3)


    def test_06_bridged_unicast_lan3(self):
        """Send traffic between bridged ports, and ensure they come out the
           expected ports. lan3 is the source"""
        self.traffic.addUDPStream(self.config.HOST_LAN3,
                                  self.config.HOST_LAN0, 10, 10)
        self.traffic.addUDPStream(self.config.HOST_LAN3,
                                  self.config.HOST_LAN1, 10, 10)
        self.traffic.addUDPStream(self.config.HOST_LAN3,
                                  self.config.HOST_LAN2, 10, 10)
        self.traffic.run()

        stats_lan0 = self.traffic.getStats(self.config.HOST_LAN0)
        stats_lan1 = self.traffic.getStats(self.config.HOST_LAN1)
        stats_lan2 = self.traffic.getStats(self.config.HOST_LAN2)
        stats_lan3 = self.traffic.getStats(self.config.HOST_LAN3)

        self.assertEqual(stats_lan0, zero_stats)
        self.assertEqual(stats_lan1, zero_stats)
        self.assertTrue(stats_lan2 == rx_10_stats or
                        stats_lan2 == rx_30_stats, stats_lan2)
        self.assertEqual(stats_lan3, tx_30_stats)

    def test_07_bridged_broadcast_lan0(self):
        """Send traffic between bridged ports, and ensure they come out the
           expected ports. lan0 is the source of broadcast packets"""
        self.traffic.addUDPBroadcastStream(self.config.HOST_LAN0, 10, 10)
        self.traffic.run()

        stats_lan0 = self.traffic.getStats(self.config.HOST_LAN0)
        stats_lan1 = self.traffic.getStats(self.config.HOST_LAN1)
        stats_lan2 = self.traffic.getStats(self.config.HOST_LAN2)
        stats_lan3 = self.traffic.getStats(self.config.HOST_LAN3)

        self.assertEqual(stats_lan0, tx_10_stats)
        self.assertEqual(stats_lan1, rx_10_stats)
        self.assertEqual(stats_lan2, zero_stats)
        self.assertEqual(stats_lan3, zero_stats)

    def test_08_bridged_broadcast_lan1(self):
        """Send traffic between bridged ports, and ensure they come out the
           expected ports. lan1 is the source of broadcast packets"""
        self.traffic.addUDPBroadcastStream(self.config.HOST_LAN1, 10, 10)
        self.traffic.run()

        stats_lan0 = self.traffic.getStats(self.config.HOST_LAN0)
        stats_lan1 = self.traffic.getStats(self.config.HOST_LAN1)
        stats_lan2 = self.traffic.getStats(self.config.HOST_LAN2)
        stats_lan3 = self.traffic.getStats(self.config.HOST_LAN3)

        self.assertEqual(stats_lan0, rx_10_stats)
        self.assertEqual(stats_lan1, tx_10_stats)
        self.assertEqual(stats_lan2, zero_stats)
        self.assertEqual(stats_lan3, zero_stats)

    def test_09_bridged_broadcast_lan2(self):
        """Send traffic between bridged ports, and ensure they come out the
           expected ports. lan2 is the source of broadcast packets"""
        self.traffic.addUDPBroadcastStream(self.config.HOST_LAN2, 10, 10)
        self.traffic.run()

        stats_lan0 = self.traffic.getStats(self.config.HOST_LAN0)
        stats_lan1 = self.traffic.getStats(self.config.HOST_LAN1)
        stats_lan2 = self.traffic.getStats(self.config.HOST_LAN2)
        stats_lan3 = self.traffic.getStats(self.config.HOST_LAN3)

        self.assertEqual(stats_lan0, zero_stats)
        self.assertEqual(stats_lan1, zero_stats)
        self.assertEqual(stats_lan2, tx_10_stats)
        self.assertEqual(stats_lan3, rx_10_stats)

    def test_10_bridged_broadcast_lan3(self):
        """Send traffic between bridged ports, and ensure they come out the
           expected ports. lan3 is the source of broadcast packets"""
        self.traffic.addUDPBroadcastStream(self.config.HOST_LAN3, 10, 10)
        self.traffic.run()

        stats_lan0 = self.traffic.getStats(self.config.HOST_LAN0)
        stats_lan1 = self.traffic.getStats(self.config.HOST_LAN1)
        stats_lan2 = self.traffic.getStats(self.config.HOST_LAN2)
        stats_lan3 = self.traffic.getStats(self.config.HOST_LAN3)

        self.assertEqual(stats_lan0, zero_stats)
        self.assertEqual(stats_lan1, zero_stats)
        self.assertEqual(stats_lan2, rx_10_stats)
        self.assertEqual(stats_lan3, tx_10_stats)

    def test_11_bridged_unicast_lan0_ipv6(self):
        """Send traffic between bridged ports, and ensure they come out the
           expected ports. lan0 is the source"""
        ethtool_stats_lan0 = self.sut.getEthtoolStats(self.config.SUT_LAN0)
        ethtool_stats_lan1 = self.sut.getEthtoolStats(self.config.SUT_LAN1)
        ethtool_stats_lan2 = self.sut.getEthtoolStats(self.config.SUT_LAN2)
        ethtool_stats_lan3 = self.sut.getEthtoolStats(self.config.SUT_LAN3)

        self.traffic.addUDPv6Stream(self.config.HOST_LAN0,
                                    self.config.HOST_LAN1, 10, 10)
        self.traffic.addUDPv6Stream(self.config.HOST_LAN0,
                                    self.config.HOST_LAN2, 10, 10)
        self.traffic.addUDPv6Stream(self.config.HOST_LAN0,
                                    self.config.HOST_LAN3, 10, 10)
        self.traffic.run()

        stats_lan0 = self.traffic.getStats(self.config.HOST_LAN0)
        stats_lan1 = self.traffic.getStats(self.config.HOST_LAN1)
        stats_lan2 = self.traffic.getStats(self.config.HOST_LAN2)
        stats_lan3 = self.traffic.getStats(self.config.HOST_LAN3)

        self.assertEqual(stats_lan0, tx_30_stats)
        # Not obviouus: The destination MAC for LAN2 and LAN3 are not
        # known, so the packets are flooded out LAN 1 as
        # well. However, it depends on the kernel configuration. With
        # just 802.1d, the ATU/FID is shared, so it knows the MAC
        # address is on a different port and so drops the packet. With
        # 802.1d an 802.1q, they are separated, and so it floods.  So
        # allow either to be a pass result.
        self.assertTrue(stats_lan1 == rx_10_stats or
                        stats_lan1 == rx_20_stats or
                        stats_lan1 == rx_30_stats, stats_lan1)
        self.assertEqual(stats_lan2, zero_stats)
        self.assertEqual(stats_lan3, zero_stats)

        self.sut.checkEthtoolStatsRange(self.config.SUT_LAN0,
                                        ethtool_stats_lan0,
                                        ethtool_rx_30, self)
        self.sut.checkEthtoolStatsRange(self.config.SUT_LAN2,
                                        ethtool_stats_lan2,
                                        ethtool_zero, self)
        self.sut.checkEthtoolStatsRange(self.config.SUT_LAN3,
                                        ethtool_stats_lan3,
                                        ethtool_zero, self)

    def test_12_bridged_unicast_lan1_ipv6(self):
        """Send traffic between bridged ports, and ensure they come out the
           expected ports. lan1 is the source"""
        self.traffic.addUDPv6Stream(self.config.HOST_LAN1,
                                    self.config.HOST_LAN0, 10, 10)
        self.traffic.addUDPv6Stream(self.config.HOST_LAN1,
                                    self.config.HOST_LAN2, 10, 10)
        self.traffic.addUDPv6Stream(self.config.HOST_LAN1,
                                    self.config.HOST_LAN3, 10, 10)
        self.traffic.run()

        stats_lan0 = self.traffic.getStats(self.config.HOST_LAN0)
        stats_lan1 = self.traffic.getStats(self.config.HOST_LAN1)
        stats_lan2 = self.traffic.getStats(self.config.HOST_LAN2)
        stats_lan3 = self.traffic.getStats(self.config.HOST_LAN3)

        self.assertTrue(stats_lan0 == rx_10_stats or
                        stats_lan0 == rx_20_stats or
                        stats_lan0 == rx_30_stats, stats_lan0)
        self.assertEqual(stats_lan1, tx_30_stats)
        self.assertEqual(stats_lan2, zero_stats)
        self.assertEqual(stats_lan3, zero_stats)

    def test_13_bridged_unicast_lan2_ipv6(self):
        """Send traffic between bridged ports, and ensure they come out the
           expected ports. lan2 is the source"""
        self.traffic.addUDPv6Stream(self.config.HOST_LAN2,
                                    self.config.HOST_LAN0, 10, 10)
        self.traffic.addUDPv6Stream(self.config.HOST_LAN2,
                                    self.config.HOST_LAN1, 10, 10)
        self.traffic.addUDPv6Stream(self.config.HOST_LAN2,
                                    self.config.HOST_LAN3, 10, 10)
        self.traffic.run()

        stats_lan0 = self.traffic.getStats(self.config.HOST_LAN0)
        stats_lan1 = self.traffic.getStats(self.config.HOST_LAN1)
        stats_lan2 = self.traffic.getStats(self.config.HOST_LAN2)
        stats_lan3 = self.traffic.getStats(self.config.HOST_LAN3)

        self.assertEqual(stats_lan0, zero_stats)
        self.assertEqual(stats_lan1, zero_stats)
        self.assertEqual(stats_lan2, tx_30_stats)
        self.assertTrue(stats_lan3 == rx_10_stats or
                        stats_lan3 == rx_30_stats, stats_lan3)


    def test_14_bridged_unicast_lan3_ipv6(self):
        """Send traffic between bridged ports, and ensure they come out the
           expected ports. lan3 is the source"""
        self.traffic.addUDPv6Stream(self.config.HOST_LAN3,
                                    self.config.HOST_LAN0, 10, 10)
        self.traffic.addUDPv6Stream(self.config.HOST_LAN3,
                                    self.config.HOST_LAN1, 10, 10)
        self.traffic.addUDPv6Stream(self.config.HOST_LAN3,
                                    self.config.HOST_LAN2, 10, 10)
        self.traffic.run()

        stats_lan0 = self.traffic.getStats(self.config.HOST_LAN0)
        stats_lan1 = self.traffic.getStats(self.config.HOST_LAN1)
        stats_lan2 = self.traffic.getStats(self.config.HOST_LAN2)
        stats_lan3 = self.traffic.getStats(self.config.HOST_LAN3)

        self.assertEqual(stats_lan0, zero_stats)
        self.assertEqual(stats_lan1, zero_stats)
        self.assertTrue(stats_lan2 == rx_10_stats or
                        stats_lan2 == rx_30_stats, stats_lan2)
        self.assertEqual(stats_lan3, tx_30_stats)

    def test_99_delete_bridge(self):
        """Destroy the bridge"""
        self.sut.deleteBridgeInterface('br1', self.config.SUT_LAN0)
        self.sut.deleteBridgeInterface('br1', self.config.SUT_LAN1)
        self.sut.deleteBridgeInterface('br2', self.config.SUT_LAN2)
        self.sut.deleteBridgeInterface('br2', self.config.SUT_LAN3)
        self.sut.deleteBridge('br1')
        self.sut.deleteBridge('br2')

        # Ensure all the interfaces are down
        self.sut.down(self.config.SUT_LAN0)
        self.sut.down(self.config.SUT_LAN1)
        self.sut.down(self.config.SUT_LAN2)
        self.sut.down(self.config.SUT_LAN3)


if __name__ == '__main__':
    args = params.params()
    CONFIG = params.readConfig(args.config)
    SUT = sut.SUT(hostname=CONFIG.hostname, key=CONFIG.key,
                  mgmt=CONFIG.SUT_MGMT)
    SUT.cleanSystem()
    TRAFFIC = traffic.Traffic()

    if args.xml:
        import xmlrunner
        testRunner = xmlrunner.XMLTestRunner(output='test-reports',
                                             verbosity=args.verbose)
    else:
        testRunner = unittest2.TextTestRunner(failfast=args.failfast,
                                              verbosity=args.verbose)

    unittest2.main(buffer=False, testRunner=testRunner, exit=False)
