#!/usr/bin/env python
"""Test the operation of a bridge on the SUT"""

import time
import unittest as unittest2

import params
import sut
import traffic


ZERO_STATS = {
    'rx_pkts': 0,
    'tx_pkts': 0,
    }

TX_50_STATS = {
    'rx_pkts': 0,
    'tx_pkts': 50,
    }

TX_100_STATS = {
    'rx_pkts': 0,
    'tx_pkts': 100,
    }

TX_400_STATS = {
    'rx_pkts': 0,
    'tx_pkts': 400,
    }

RX_100_STATS = {
    'rx_pkts': 100,
    'tx_pkts': 0,
    }

CLASS_TX_RX_0 = {'tx_packets': (0, 20),
                 'rx_packets': (0, 20)}

ETHTOOL_ZERO = {'rx_packets': (0, 20),
                'in_unicast': (0, 20),
                'tx_packets': (0, 20),
                'out_unicast': (0, 20)}

ETHTOOL_RX_400 = {'in_unicast': (400, 410),
                  'out_unicast': (0, 20)}

ETHTOOL_RX_0_BROADCAST_50 = {'in_broadcasts': (50, 60),
                             'out_unicast': (0, 20)}

ETHTOOL_RX_50_BROADCAST_50 = {'in_broadcasts': (50, 60),
                              'out_unicast': (0, 20)}

ETHTOOL_RX_50 = {'in_broadcasts': (0, 20),
                 'in_unicast': (50, 60),
                 'out_broadcasts': (0, 20),
                 'out_unicast': (0, 20)}

ETHTOOL_TX_100 = {'in_unicast': (0, 20),
                  'out_unicast': (100, 110)}

SUT = None
TRAFFIC = None
CONFIG = None
VLAN_FILTERING = False


class Bridge_test(unittest2.TestCase):
    '''Class containing the test cases'''

    def setUp(self):
        """Setup ready to perform the test"""
        self.sut = SUT
        self.traffic = TRAFFIC
        self.config = CONFIG
        self.maxDiff = None
        self.vlan_filtering = VLAN_FILTERING
        self.hostname = CONFIG.hostname

    def test_01_create_bridge(self):
        """Create the bridge"""
        # Ensure all the interfaces are up
        self.sut.up(self.config.SUT_MASTER)
        self.sut.up(self.config.SUT_LAN0)
        self.sut.up(self.config.SUT_LAN1)
        self.sut.up(self.config.SUT_LAN2)
        self.sut.up(self.config.SUT_LAN3)
        self.sut.up(self.config.SUT_LAN4)
        self.sut.up(self.config.SUT_LAN5)
        self.sut.up(self.config.SUT_LAN6)
        self.sut.up(self.config.SUT_OPTICAL3)

        self.sut.addBridge('br1')

        if self.vlan_filtering:
            self.sut.bridgeEnableVlanFiltering('br1')

        self.sut.up('br1')
        self.sut.addBridgeInterface('br1', self.config.SUT_LAN1)
        self.sut.addBridgeInterface('br1', self.config.SUT_LAN2)
        self.sut.addBridgeInterface('br1', self.config.SUT_LAN3)
        self.sut.addBridgeInterface('br1', self.config.SUT_LAN4)
        self.sut.addBridgeInterface('br1', self.config.SUT_LAN6)

        # Wait the forwarding delay of the bridge
        time.sleep(10)

        self.traffic.addInterface(self.config.HOST_LAN0)
        self.traffic.addInterface(self.config.HOST_LAN1)
        self.traffic.addInterface(self.config.HOST_LAN2)
        self.traffic.addInterface(self.config.HOST_LAN3)
        self.traffic.addInterface(self.config.HOST_LAN4)
        self.traffic.addInterface(self.config.HOST_LAN5)
        self.traffic.addInterface(self.config.HOST_LAN6)
        self.traffic.addInterface(self.config.HOST_OPTICAL3)

    def test_01_learn(self):
        """Send learning packets, so the bridge knows which MAC address
           is where"""
        self.traffic.learning()

    def test_02_not_bridged(self):
        """lan0, lan5, and optical3 are not a member of a bridge. Send
           traffic on these ports, and make sure there is no traffic
           sent out other ports"""

        ethtool_stats_lan0 = self.sut.getEthtoolStats(self.config.SUT_LAN0)
        ethtool_stats_lan1 = self.sut.getEthtoolStats(self.config.SUT_LAN1)
        ethtool_stats_lan2 = self.sut.getEthtoolStats(self.config.SUT_LAN2)
        ethtool_stats_lan3 = self.sut.getEthtoolStats(self.config.SUT_LAN3)
        ethtool_stats_lan4 = self.sut.getEthtoolStats(self.config.SUT_LAN4)
        ethtool_stats_lan5 = self.sut.getEthtoolStats(self.config.SUT_LAN5)
        ethtool_stats_lan6 = self.sut.getEthtoolStats(self.config.SUT_LAN6)
        ethtool_stats_optical3 = self.sut.getEthtoolStats(
            self.config.SUT_OPTICAL3)

        self.traffic.addUDPStream(self.config.HOST_LAN5,
                                  self.config.HOST_LAN0, 50, 50)
        self.traffic.addUDPStream(self.config.HOST_OPTICAL3,
                                  self.config.HOST_LAN0, 50, 50)
        self.traffic.addUDPBroadcastStream(self.config.HOST_LAN0, 50, 50)
        self.traffic.addUDPBroadcastStream(self.config.HOST_LAN5, 50, 50)
        self.traffic.addUDPBroadcastStream(self.config.HOST_OPTICAL3, 50, 50)
        self.traffic.run()

        stats_lan0 = self.traffic.getStats(self.config.HOST_LAN0)
        stats_lan1 = self.traffic.getStats(self.config.HOST_LAN1)
        stats_lan2 = self.traffic.getStats(self.config.HOST_LAN2)
        stats_lan3 = self.traffic.getStats(self.config.HOST_LAN3)
        stats_lan4 = self.traffic.getStats(self.config.HOST_LAN4)
        stats_lan5 = self.traffic.getStats(self.config.HOST_LAN5)
        stats_lan6 = self.traffic.getStats(self.config.HOST_LAN6)
        stats_optical3 = self.traffic.getStats(self.config.HOST_OPTICAL3)

        self.assertEqual(stats_lan0, TX_50_STATS)
        self.assertEqual(stats_lan1, ZERO_STATS)
        self.assertEqual(stats_lan2, ZERO_STATS)
        self.assertEqual(stats_lan3, ZERO_STATS)
        self.assertEqual(stats_lan4, ZERO_STATS)
        self.assertEqual(stats_lan5, TX_100_STATS)
        if 'zii-devel-b' not in self.hostname:
            # The third switch does not have the hardware needed to properly
            # support cross chip bridges. Packets leak.
            self.assertEqual(stats_lan6, ZERO_STATS)
            self.assertEqual(stats_optical3, TX_100_STATS)

        self.sut.checkEthtoolStatsRange(self.config.SUT_LAN0,
                                        ethtool_stats_lan0,
                                        ETHTOOL_RX_0_BROADCAST_50, self)
        self.sut.checkEthtoolStatsRange(self.config.SUT_LAN1,
                                        ethtool_stats_lan1,
                                        ETHTOOL_ZERO, self)
        self.sut.checkEthtoolStatsRange(self.config.SUT_LAN2,
                                        ethtool_stats_lan2,
                                        ETHTOOL_ZERO, self)
        self.sut.checkEthtoolStatsRange(self.config.SUT_LAN3,
                                        ethtool_stats_lan3,
                                        ETHTOOL_ZERO, self)
        self.sut.checkEthtoolStatsRange(self.config.SUT_LAN4,
                                        ethtool_stats_lan4,
                                        ETHTOOL_ZERO, self)
        self.sut.checkEthtoolStatsRange(self.config.SUT_LAN5,
                                        ethtool_stats_lan5,
                                        ETHTOOL_RX_50_BROADCAST_50, self)
        if 'zii-devel-b' not in self.hostname:
            # The third switch does not have the hardware needed to properly
            # support cross chip bridges. Packets leak.
            self.sut.checkEthtoolStatsRange(self.config.SUT_LAN6,
                                            ethtool_stats_lan6,
                                            ETHTOOL_ZERO, self)
            self.sut.checkEthtoolStatsRange(self.config.SUT_OPTICAL3,
                                            ethtool_stats_optical3,
                                            ETHTOOL_RX_50_BROADCAST_50, self)

    def test_03_bridged_unicast_lan1(self):
        """Send traffic between bridged ports, and ensure they come out the
           expected ports. lan1 is the source"""

        class_stats_master = self.sut.getClassStats(self.config.SUT_MASTER)
        ethtool_stats_lan0 = self.sut.getEthtoolStats(self.config.SUT_LAN0)
        ethtool_stats_lan1 = self.sut.getEthtoolStats(self.config.SUT_LAN1)
        ethtool_stats_lan2 = self.sut.getEthtoolStats(self.config.SUT_LAN2)
        ethtool_stats_lan3 = self.sut.getEthtoolStats(self.config.SUT_LAN3)
        ethtool_stats_lan4 = self.sut.getEthtoolStats(self.config.SUT_LAN4)
        ethtool_stats_lan5 = self.sut.getEthtoolStats(self.config.SUT_LAN5)
        ethtool_stats_lan6 = self.sut.getEthtoolStats(self.config.SUT_LAN6)
        ethtool_stats_optical3 = self.sut.getEthtoolStats(
            self.config.SUT_OPTICAL3)

        self.traffic.addUDPStream(self.config.HOST_LAN1,
                                  self.config.HOST_LAN2, 100, 100)
        self.traffic.addUDPStream(self.config.HOST_LAN1,
                                  self.config.HOST_LAN3, 100, 100)
        self.traffic.addUDPStream(self.config.HOST_LAN1,
                                  self.config.HOST_LAN4, 100, 100)
        self.traffic.addUDPStream(self.config.HOST_LAN1,
                                  self.config.HOST_LAN6, 100, 100)
        self.traffic.run()

        stats_lan0 = self.traffic.getStats(self.config.HOST_LAN0)
        stats_lan1 = self.traffic.getStats(self.config.HOST_LAN1)
        stats_lan2 = self.traffic.getStats(self.config.HOST_LAN2)
        stats_lan3 = self.traffic.getStats(self.config.HOST_LAN3)
        stats_lan4 = self.traffic.getStats(self.config.HOST_LAN4)
        stats_lan5 = self.traffic.getStats(self.config.HOST_LAN5)
        stats_lan6 = self.traffic.getStats(self.config.HOST_LAN6)
        stats_optical3 = self.traffic.getStats(self.config.HOST_OPTICAL3)

        self.assertEqual(stats_lan0, ZERO_STATS)
        self.assertEqual(stats_lan1, TX_400_STATS)

        self.assertEqual(stats_lan2, RX_100_STATS)
        self.assertEqual(stats_lan3, RX_100_STATS)
        self.assertEqual(stats_lan4, RX_100_STATS)
        self.assertEqual(stats_lan5, ZERO_STATS)
        self.assertEqual(stats_lan6, RX_100_STATS)
        self.assertEqual(stats_optical3, ZERO_STATS)

        # All frames should be hardware bridge
        self.sut.checkClassStatsRange(self.config.SUT_MASTER,
                                      class_stats_master,
                                      CLASS_TX_RX_0, self)
        self.sut.checkEthtoolStatsRange(self.config.SUT_LAN0,
                                        ethtool_stats_lan0,
                                        ETHTOOL_ZERO, self)
        self.sut.checkEthtoolStatsRange(self.config.SUT_LAN1,
                                        ethtool_stats_lan1,
                                        ETHTOOL_RX_400, self)
        self.sut.checkEthtoolStatsRange(self.config.SUT_LAN2,
                                        ethtool_stats_lan2,
                                        ETHTOOL_TX_100, self)
        self.sut.checkEthtoolStatsRange(self.config.SUT_LAN3,
                                        ethtool_stats_lan3,
                                        ETHTOOL_TX_100, self)
        self.sut.checkEthtoolStatsRange(self.config.SUT_LAN4,
                                        ethtool_stats_lan4,
                                        ETHTOOL_TX_100, self)
        self.sut.checkEthtoolStatsRange(self.config.SUT_LAN5,
                                        ethtool_stats_lan5,
                                        ETHTOOL_ZERO, self)
        self.sut.checkEthtoolStatsRange(self.config.SUT_LAN6,
                                        ethtool_stats_lan6,
                                        ETHTOOL_TX_100, self)
        self.sut.checkEthtoolStatsRange(self.config.SUT_OPTICAL3,
                                        ethtool_stats_optical3,
                                        ETHTOOL_ZERO, self)

    def test_04_bridged_unicast_lan2(self):
        """Send traffic between bridged ports, and ensure they come out the
           expected ports. lan2 is the source"""

        class_stats_master = self.sut.getClassStats(self.config.SUT_MASTER)

        self.traffic.addUDPStream(self.config.HOST_LAN2,
                                  self.config.HOST_LAN1, 100, 100)
        self.traffic.addUDPStream(self.config.HOST_LAN2,
                                  self.config.HOST_LAN3, 100, 100)
        self.traffic.addUDPStream(self.config.HOST_LAN2,
                                  self.config.HOST_LAN4, 100, 100)
        self.traffic.addUDPStream(self.config.HOST_LAN2,
                                  self.config.HOST_LAN6, 100, 100)
        self.traffic.run()

        stats_lan0 = self.traffic.getStats(self.config.HOST_LAN0)
        stats_lan1 = self.traffic.getStats(self.config.HOST_LAN1)
        stats_lan2 = self.traffic.getStats(self.config.HOST_LAN2)
        stats_lan3 = self.traffic.getStats(self.config.HOST_LAN3)
        stats_lan4 = self.traffic.getStats(self.config.HOST_LAN4)
        stats_lan5 = self.traffic.getStats(self.config.HOST_LAN5)
        stats_lan6 = self.traffic.getStats(self.config.HOST_LAN6)
        stats_optical3 = self.traffic.getStats(self.config.HOST_OPTICAL3)

        self.assertEqual(stats_lan0, ZERO_STATS)
        self.assertEqual(stats_lan1, RX_100_STATS)
        self.assertEqual(stats_lan2, TX_400_STATS)
        self.assertEqual(stats_lan3, RX_100_STATS)
        self.assertEqual(stats_lan4, RX_100_STATS)
        self.assertEqual(stats_lan5, ZERO_STATS)
        self.assertEqual(stats_lan6, RX_100_STATS)
        self.assertEqual(stats_optical3, ZERO_STATS)

        # All frames should be hardware bridge
        self.sut.checkClassStatsRange(self.config.SUT_MASTER,
                                      class_stats_master,
                                      CLASS_TX_RX_0, self)

    def test_05_bridged_unicast_lan3(self):
        """Send traffic between bridged ports, and ensure they come out the
           expected ports. lan3 is the source"""

        class_stats_master = self.sut.getClassStats(self.config.SUT_MASTER)

        self.traffic.addUDPStream(self.config.HOST_LAN3,
                                  self.config.HOST_LAN1, 100, 100)
        self.traffic.addUDPStream(self.config.HOST_LAN3,
                                  self.config.HOST_LAN2, 100, 100)
        self.traffic.addUDPStream(self.config.HOST_LAN3,
                                  self.config.HOST_LAN4, 100, 100)
        self.traffic.addUDPStream(self.config.HOST_LAN3,
                                  self.config.HOST_LAN6, 100, 100)
        self.traffic.run()

        stats_lan0 = self.traffic.getStats(self.config.HOST_LAN0)
        stats_lan1 = self.traffic.getStats(self.config.HOST_LAN1)
        stats_lan2 = self.traffic.getStats(self.config.HOST_LAN2)
        stats_lan3 = self.traffic.getStats(self.config.HOST_LAN3)
        stats_lan4 = self.traffic.getStats(self.config.HOST_LAN4)
        stats_lan5 = self.traffic.getStats(self.config.HOST_LAN5)
        stats_lan6 = self.traffic.getStats(self.config.HOST_LAN6)
        stats_optical3 = self.traffic.getStats(self.config.HOST_OPTICAL3)

        self.assertEqual(stats_lan0, ZERO_STATS)
        self.assertEqual(stats_lan1, RX_100_STATS)
        self.assertEqual(stats_lan2, RX_100_STATS)
        self.assertEqual(stats_lan3, TX_400_STATS)
        self.assertEqual(stats_lan4, RX_100_STATS)
        self.assertEqual(stats_lan5, ZERO_STATS)
        self.assertEqual(stats_lan6, RX_100_STATS)
        self.assertEqual(stats_optical3, ZERO_STATS)

        # All frames should be hardware bridge
        self.sut.checkClassStatsRange(self.config.SUT_MASTER,
                                      class_stats_master,
                                      CLASS_TX_RX_0, self)

    def test_06_bridged_unicast_lan4(self):
        """Send traffic between bridged ports, and ensure they come out the
           expected ports. lan4 is the source"""

        class_stats_master = self.sut.getClassStats(self.config.SUT_MASTER)

        self.traffic.addUDPStream(self.config.HOST_LAN4,
                                  self.config.HOST_LAN1, 100, 100)
        self.traffic.addUDPStream(self.config.HOST_LAN4,
                                  self.config.HOST_LAN2, 100, 100)
        self.traffic.addUDPStream(self.config.HOST_LAN4,
                                  self.config.HOST_LAN3, 100, 100)
        self.traffic.addUDPStream(self.config.HOST_LAN4,
                                  self.config.HOST_LAN6, 100, 100)
        self.traffic.run()

        stats_lan0 = self.traffic.getStats(self.config.HOST_LAN0)
        stats_lan1 = self.traffic.getStats(self.config.HOST_LAN1)
        stats_lan2 = self.traffic.getStats(self.config.HOST_LAN2)
        stats_lan3 = self.traffic.getStats(self.config.HOST_LAN3)
        stats_lan4 = self.traffic.getStats(self.config.HOST_LAN4)
        stats_lan5 = self.traffic.getStats(self.config.HOST_LAN5)
        stats_lan6 = self.traffic.getStats(self.config.HOST_LAN6)
        stats_optical3 = self.traffic.getStats(self.config.HOST_OPTICAL3)

        self.assertEqual(stats_lan0, ZERO_STATS)
        self.assertEqual(stats_lan1, RX_100_STATS)
        self.assertEqual(stats_lan2, RX_100_STATS)
        self.assertEqual(stats_lan3, RX_100_STATS)
        self.assertEqual(stats_lan4, TX_400_STATS)
        self.assertEqual(stats_lan5, ZERO_STATS)
        self.assertEqual(stats_lan6, RX_100_STATS)
        self.assertEqual(stats_optical3, ZERO_STATS)

        # All frames should be hardware bridge
        self.sut.checkClassStatsRange(self.config.SUT_MASTER,
                                      class_stats_master,
                                      CLASS_TX_RX_0, self)

    def test_07_bridged_unicast_lan6(self):
        """Send traffic between bridged ports, and ensure they come out the
           expected ports. lan6 is the source"""

        self.traffic.addUDPStream(self.config.HOST_LAN6,
                                  self.config.HOST_LAN1, 100, 100)
        self.traffic.addUDPStream(self.config.HOST_LAN6,
                                  self.config.HOST_LAN2, 100, 100)
        self.traffic.addUDPStream(self.config.HOST_LAN6,
                                  self.config.HOST_LAN3, 100, 100)
        self.traffic.addUDPStream(self.config.HOST_LAN6,
                                  self.config.HOST_LAN4, 100, 100)
        self.traffic.run()

        stats_lan0 = self.traffic.getStats(self.config.HOST_LAN0)
        stats_lan1 = self.traffic.getStats(self.config.HOST_LAN1)
        stats_lan2 = self.traffic.getStats(self.config.HOST_LAN2)
        stats_lan3 = self.traffic.getStats(self.config.HOST_LAN3)
        stats_lan4 = self.traffic.getStats(self.config.HOST_LAN4)
        stats_lan5 = self.traffic.getStats(self.config.HOST_LAN5)
        stats_lan6 = self.traffic.getStats(self.config.HOST_LAN6)
        stats_optical3 = self.traffic.getStats(self.config.HOST_OPTICAL3)

        self.assertEqual(stats_lan0, ZERO_STATS)
        self.assertEqual(stats_lan1, RX_100_STATS)
        self.assertEqual(stats_lan2, RX_100_STATS)
        self.assertEqual(stats_lan3, RX_100_STATS)
        self.assertEqual(stats_lan4, RX_100_STATS)
        self.assertEqual(stats_lan5, ZERO_STATS)
        self.assertEqual(stats_lan6, TX_400_STATS)
        self.assertEqual(stats_optical3, ZERO_STATS)

    def test_08_bridged_unicast_optical3(self):
        """Add optical3 to the bridge, and test hardware bridging between
           ports on switch 2."""

        self.sut.addBridgeInterface('br1', self.config.SUT_OPTICAL3)

        self.traffic.addUDPStream(self.config.HOST_OPTICAL3,
                                  self.config.HOST_LAN1, 100, 100)
        self.traffic.addUDPStream(self.config.HOST_OPTICAL3,
                                  self.config.HOST_LAN2, 100, 100)
        self.traffic.addUDPStream(self.config.HOST_OPTICAL3,
                                  self.config.HOST_LAN3, 100, 100)
        self.traffic.addUDPStream(self.config.HOST_OPTICAL3,
                                  self.config.HOST_LAN6, 100, 100)
        self.traffic.run()

        stats_lan0 = self.traffic.getStats(self.config.HOST_LAN0)
        stats_lan1 = self.traffic.getStats(self.config.HOST_LAN1)
        stats_lan2 = self.traffic.getStats(self.config.HOST_LAN2)
        stats_lan3 = self.traffic.getStats(self.config.HOST_LAN3)
        stats_lan4 = self.traffic.getStats(self.config.HOST_LAN4)
        stats_lan5 = self.traffic.getStats(self.config.HOST_LAN5)
        stats_lan6 = self.traffic.getStats(self.config.HOST_LAN6)
        stats_optical3 = self.traffic.getStats(self.config.HOST_OPTICAL3)

        self.assertEqual(stats_lan0, ZERO_STATS)
        self.assertEqual(stats_lan1, RX_100_STATS)
        self.assertEqual(stats_lan2, RX_100_STATS)
        self.assertEqual(stats_lan3, RX_100_STATS)
        self.assertEqual(stats_lan4, ZERO_STATS)
        self.assertEqual(stats_lan5, ZERO_STATS)
        self.assertEqual(stats_lan6, RX_100_STATS)
        self.assertEqual(stats_optical3, TX_400_STATS)

        self.sut.deleteBridgeInterface('br1', self.config.SUT_OPTICAL3)

    def test_09_bridged_broadcast_lan0(self):
        """Send traffic between bridged ports, and ensure they come out the
           expected ports. lan0 is the source of broadcast packets"""

        self.traffic.addUDPBroadcastStream(self.config.HOST_LAN0, 100, 100)
        self.traffic.run()

        stats_lan0 = self.traffic.getStats(self.config.HOST_LAN0)
        stats_lan1 = self.traffic.getStats(self.config.HOST_LAN1)
        stats_lan2 = self.traffic.getStats(self.config.HOST_LAN2)
        stats_lan3 = self.traffic.getStats(self.config.HOST_LAN3)
        stats_lan4 = self.traffic.getStats(self.config.HOST_LAN4)
        stats_lan5 = self.traffic.getStats(self.config.HOST_LAN5)
        stats_lan6 = self.traffic.getStats(self.config.HOST_LAN6)
        stats_optical3 = self.traffic.getStats(self.config.HOST_OPTICAL3)

        self.assertEqual(stats_lan0, TX_100_STATS)
        self.assertEqual(stats_lan1, ZERO_STATS)
        self.assertEqual(stats_lan2, ZERO_STATS)
        self.assertEqual(stats_lan3, ZERO_STATS)
        self.assertEqual(stats_lan4, ZERO_STATS)
        self.assertEqual(stats_lan5, ZERO_STATS)
        if 'zii-devel-b' not in self.hostname:
            # The third switch does not have the hardware needed to properly
            # support cross chip bridges. Packets leak.
            self.assertEqual(stats_lan6, ZERO_STATS)
            self.assertEqual(stats_optical3, ZERO_STATS)

    def test_10_bridged_broadcast_lan1(self):
        """Send traffic between bridged ports, and ensure they come out the
           expected ports. lan1 is the source of broadcast packets"""
        self.traffic.addUDPBroadcastStream(self.config.HOST_LAN1, 100, 100)
        self.traffic.run()

        stats_lan0 = self.traffic.getStats(self.config.HOST_LAN0)
        stats_lan1 = self.traffic.getStats(self.config.HOST_LAN1)
        stats_lan2 = self.traffic.getStats(self.config.HOST_LAN2)
        stats_lan3 = self.traffic.getStats(self.config.HOST_LAN3)
        stats_lan4 = self.traffic.getStats(self.config.HOST_LAN4)
        stats_lan5 = self.traffic.getStats(self.config.HOST_LAN5)
        stats_lan6 = self.traffic.getStats(self.config.HOST_LAN6)
        stats_optical3 = self.traffic.getStats(self.config.HOST_OPTICAL3)

        self.assertEqual(stats_lan0, ZERO_STATS)
        self.assertEqual(stats_lan1, TX_100_STATS)
        self.assertEqual(stats_lan2, RX_100_STATS)
        self.assertEqual(stats_lan3, RX_100_STATS)
        self.assertEqual(stats_lan4, RX_100_STATS)
        self.assertEqual(stats_lan5, ZERO_STATS)
        self.assertEqual(stats_lan6, RX_100_STATS)
        if 'zii-devel-b' not in self.hostname:
            # The third switch does not have the hardware needed to properly
            # support cross chip bridges. Packets leak.
            self.assertEqual(stats_optical3, ZERO_STATS)

    def test_11_bridged_broadcast_lan2(self):
        """Send traffic between bridged ports, and ensure they come out the
           expected ports. lan2 is the source of broadcast packets"""
        self.traffic.addUDPBroadcastStream(self.config.HOST_LAN2, 100, 100)
        self.traffic.run()

        stats_lan0 = self.traffic.getStats(self.config.HOST_LAN0)
        stats_lan1 = self.traffic.getStats(self.config.HOST_LAN1)
        stats_lan2 = self.traffic.getStats(self.config.HOST_LAN2)
        stats_lan3 = self.traffic.getStats(self.config.HOST_LAN3)
        stats_lan4 = self.traffic.getStats(self.config.HOST_LAN4)
        stats_lan5 = self.traffic.getStats(self.config.HOST_LAN5)
        stats_lan6 = self.traffic.getStats(self.config.HOST_LAN6)
        stats_optical3 = self.traffic.getStats(self.config.HOST_OPTICAL3)

        self.assertEqual(stats_lan0, ZERO_STATS)
        self.assertEqual(stats_lan1, RX_100_STATS)
        self.assertEqual(stats_lan2, TX_100_STATS)
        self.assertEqual(stats_lan3, RX_100_STATS)
        self.assertEqual(stats_lan4, RX_100_STATS)
        self.assertEqual(stats_lan5, ZERO_STATS)
        self.assertEqual(stats_lan6, RX_100_STATS)
        if 'zii-devel-b' not in self.hostname:
            # The third switch does not have the hardware needed to properly
            # support cross chip bridges. Packets leak.
            self.assertEqual(stats_optical3, ZERO_STATS)

    def test_12_bridged_broadcast_lan3(self):
        """Send traffic between bridged ports, and ensure they come out the
           expected ports. lan3 is the source of broadcast packets"""
        self.traffic.addUDPBroadcastStream(self.config.HOST_LAN3, 100, 100)
        self.traffic.run()

        stats_lan0 = self.traffic.getStats(self.config.HOST_LAN0)
        stats_lan1 = self.traffic.getStats(self.config.HOST_LAN1)
        stats_lan2 = self.traffic.getStats(self.config.HOST_LAN2)
        stats_lan3 = self.traffic.getStats(self.config.HOST_LAN3)
        stats_lan4 = self.traffic.getStats(self.config.HOST_LAN4)
        stats_lan5 = self.traffic.getStats(self.config.HOST_LAN5)
        stats_lan6 = self.traffic.getStats(self.config.HOST_LAN6)
        stats_optical3 = self.traffic.getStats(self.config.HOST_OPTICAL3)

        self.assertEqual(stats_lan0, ZERO_STATS)
        self.assertEqual(stats_lan1, RX_100_STATS)
        self.assertEqual(stats_lan2, RX_100_STATS)
        self.assertEqual(stats_lan3, TX_100_STATS)
        self.assertEqual(stats_lan4, RX_100_STATS)
        self.assertEqual(stats_lan5, ZERO_STATS)
        self.assertEqual(stats_lan6, RX_100_STATS)
        if 'zii-devel-b' not in self.hostname:
            # The third switch does not have the hardware needed to properly
            # support cross chip bridges. Packets leak.
            self.assertEqual(stats_optical3, ZERO_STATS)

    def test_13_bridged_broadcast_lan5(self):
        """Send traffic between bridged ports, and ensure they come out the
           expected ports. lan5 is the source of broadcast packets"""
        self.traffic.addUDPBroadcastStream(self.config.HOST_LAN5, 100, 100)
        self.traffic.run()

        stats_lan0 = self.traffic.getStats(self.config.HOST_LAN0)
        stats_lan1 = self.traffic.getStats(self.config.HOST_LAN1)
        stats_lan2 = self.traffic.getStats(self.config.HOST_LAN2)
        stats_lan3 = self.traffic.getStats(self.config.HOST_LAN3)
        stats_lan4 = self.traffic.getStats(self.config.HOST_LAN4)
        stats_lan5 = self.traffic.getStats(self.config.HOST_LAN5)
        stats_lan6 = self.traffic.getStats(self.config.HOST_LAN6)
        stats_optical3 = self.traffic.getStats(self.config.HOST_OPTICAL3)

        self.assertEqual(stats_lan0, ZERO_STATS)
        self.assertEqual(stats_lan1, ZERO_STATS)
        self.assertEqual(stats_lan2, ZERO_STATS)
        self.assertEqual(stats_lan3, ZERO_STATS)
        self.assertEqual(stats_lan4, ZERO_STATS)
        self.assertEqual(stats_lan5, TX_100_STATS)
        if 'zii-devel-b' not in self.hostname:
            # The third switch does not have the hardware needed to properly
            # support cross chip bridges. Packets leak.
            self.assertEqual(stats_lan6, ZERO_STATS)
            self.assertEqual(stats_optical3, ZERO_STATS)

    def test_14_not_bridged_ipv6(self):
        """lan0, lan5, and optical3 are not a member of a bridge. Send
           traffic on these ports, and make sure there is no traffic
           sent out other ports"""

        ethtool_stats_lan0 = self.sut.getEthtoolStats(self.config.SUT_LAN0)
        ethtool_stats_lan1 = self.sut.getEthtoolStats(self.config.SUT_LAN1)
        ethtool_stats_lan2 = self.sut.getEthtoolStats(self.config.SUT_LAN2)
        ethtool_stats_lan3 = self.sut.getEthtoolStats(self.config.SUT_LAN3)
        ethtool_stats_lan4 = self.sut.getEthtoolStats(self.config.SUT_LAN4)
        ethtool_stats_lan5 = self.sut.getEthtoolStats(self.config.SUT_LAN5)
        ethtool_stats_lan6 = self.sut.getEthtoolStats(self.config.SUT_LAN6)
        ethtool_stats_optical3 = self.sut.getEthtoolStats(
            self.config.SUT_OPTICAL3)

        self.traffic.addUDPv6Stream(self.config.HOST_LAN5,
                                    self.config.HOST_LAN0, 50, 50)
        self.traffic.addUDPv6Stream(self.config.HOST_OPTICAL3,
                                    self.config.HOST_LAN0, 50, 50)
        self.traffic.run()

        stats_lan0 = self.traffic.getStats(self.config.HOST_LAN0)
        stats_lan1 = self.traffic.getStats(self.config.HOST_LAN1)
        stats_lan2 = self.traffic.getStats(self.config.HOST_LAN2)
        stats_lan3 = self.traffic.getStats(self.config.HOST_LAN3)
        stats_lan4 = self.traffic.getStats(self.config.HOST_LAN4)
        stats_lan5 = self.traffic.getStats(self.config.HOST_LAN5)
        stats_lan6 = self.traffic.getStats(self.config.HOST_LAN6)
        stats_optical3 = self.traffic.getStats(self.config.HOST_OPTICAL3)

        self.assertEqual(stats_lan0, ZERO_STATS)
        self.assertEqual(stats_lan1, ZERO_STATS)
        self.assertEqual(stats_lan2, ZERO_STATS)
        self.assertEqual(stats_lan3, ZERO_STATS)
        self.assertEqual(stats_lan4, ZERO_STATS)
        self.assertEqual(stats_lan5, TX_50_STATS)
        if 'zii-devel-b' not in self.hostname:
            # The third switch does not have the hardware needed to properly
            # support cross chip bridges. Packets leak.
            self.assertEqual(stats_lan6, ZERO_STATS)
            self.assertEqual(stats_optical3, TX_50_STATS)

        self.sut.checkEthtoolStatsRange(self.config.SUT_LAN0,
                                        ethtool_stats_lan0,
                                        ETHTOOL_ZERO, self)
        self.sut.checkEthtoolStatsRange(self.config.SUT_LAN1,
                                        ethtool_stats_lan1,
                                        ETHTOOL_ZERO, self)
        self.sut.checkEthtoolStatsRange(self.config.SUT_LAN2,
                                        ethtool_stats_lan2,
                                        ETHTOOL_ZERO, self)
        self.sut.checkEthtoolStatsRange(self.config.SUT_LAN3,
                                        ethtool_stats_lan3,
                                        ETHTOOL_ZERO, self)
        self.sut.checkEthtoolStatsRange(self.config.SUT_LAN4,
                                        ethtool_stats_lan4,
                                        ETHTOOL_ZERO, self)
        self.sut.checkEthtoolStatsRange(self.config.SUT_LAN5,
                                        ethtool_stats_lan5,
                                        ETHTOOL_RX_50, self)
        if 'zii-devel-b' not in self.hostname:
            # The third switch does not have the hardware needed to properly
            # support cross chip bridges. Packets leak.
            self.sut.checkEthtoolStatsRange(self.config.SUT_LAN6,
                                            ethtool_stats_lan6,
                                            ETHTOOL_ZERO, self)
            self.sut.checkEthtoolStatsRange(self.config.SUT_OPTICAL3,
                                            ethtool_stats_optical3,
                                            ETHTOOL_RX_50, self)

    def test_15_bridged_unicast_lan1_ipv6(self):
        """Send traffic between bridged ports, and ensure they come out the
           expected ports. lan1 is the source"""

        class_stats_master = self.sut.getClassStats(self.config.SUT_MASTER)
        ethtool_stats_lan0 = self.sut.getEthtoolStats(self.config.SUT_LAN0)
        ethtool_stats_lan1 = self.sut.getEthtoolStats(self.config.SUT_LAN1)
        ethtool_stats_lan2 = self.sut.getEthtoolStats(self.config.SUT_LAN2)
        ethtool_stats_lan3 = self.sut.getEthtoolStats(self.config.SUT_LAN3)
        ethtool_stats_lan4 = self.sut.getEthtoolStats(self.config.SUT_LAN4)
        ethtool_stats_lan5 = self.sut.getEthtoolStats(self.config.SUT_LAN5)
        ethtool_stats_lan6 = self.sut.getEthtoolStats(self.config.SUT_LAN6)
        ethtool_stats_optical3 = self.sut.getEthtoolStats(
            self.config.SUT_OPTICAL3)

        self.traffic.addUDPv6Stream(self.config.HOST_LAN1,
                                    self.config.HOST_LAN2, 100, 100)
        self.traffic.addUDPv6Stream(self.config.HOST_LAN1,
                                    self.config.HOST_LAN3, 100, 100)
        self.traffic.addUDPv6Stream(self.config.HOST_LAN1,
                                    self.config.HOST_LAN4, 100, 100)
        self.traffic.addUDPv6Stream(self.config.HOST_LAN1,
                                    self.config.HOST_LAN6, 100, 100)
        self.traffic.run()

        stats_lan0 = self.traffic.getStats(self.config.HOST_LAN0)
        stats_lan1 = self.traffic.getStats(self.config.HOST_LAN1)
        stats_lan2 = self.traffic.getStats(self.config.HOST_LAN2)
        stats_lan3 = self.traffic.getStats(self.config.HOST_LAN3)
        stats_lan4 = self.traffic.getStats(self.config.HOST_LAN4)
        stats_lan5 = self.traffic.getStats(self.config.HOST_LAN5)
        stats_lan6 = self.traffic.getStats(self.config.HOST_LAN6)
        stats_optical3 = self.traffic.getStats(self.config.HOST_OPTICAL3)

        self.assertEqual(stats_lan0, ZERO_STATS)
        self.assertEqual(stats_lan1, TX_400_STATS)

        self.assertEqual(stats_lan2, RX_100_STATS)
        self.assertEqual(stats_lan3, RX_100_STATS)
        self.assertEqual(stats_lan4, RX_100_STATS)
        self.assertEqual(stats_lan5, ZERO_STATS)
        self.assertEqual(stats_lan6, RX_100_STATS)
        self.assertEqual(stats_optical3, ZERO_STATS)

        # All frames should be hardware bridge
        self.sut.checkClassStatsRange(self.config.SUT_MASTER,
                                      class_stats_master,
                                      CLASS_TX_RX_0, self)
        self.sut.checkEthtoolStatsRange(self.config.SUT_LAN0,
                                        ethtool_stats_lan0,
                                        ETHTOOL_ZERO, self)
        self.sut.checkEthtoolStatsRange(self.config.SUT_LAN1,
                                        ethtool_stats_lan1,
                                        ETHTOOL_RX_400, self)
        self.sut.checkEthtoolStatsRange(self.config.SUT_LAN2,
                                        ethtool_stats_lan2,
                                        ETHTOOL_TX_100, self)
        self.sut.checkEthtoolStatsRange(self.config.SUT_LAN3,
                                        ethtool_stats_lan3,
                                        ETHTOOL_TX_100, self)
        self.sut.checkEthtoolStatsRange(self.config.SUT_LAN4,
                                        ethtool_stats_lan4,
                                        ETHTOOL_TX_100, self)
        self.sut.checkEthtoolStatsRange(self.config.SUT_LAN5,
                                        ethtool_stats_lan5,
                                        ETHTOOL_ZERO, self)
        self.sut.checkEthtoolStatsRange(self.config.SUT_LAN6,
                                        ethtool_stats_lan6,
                                        ETHTOOL_TX_100, self)
        self.sut.checkEthtoolStatsRange(self.config.SUT_OPTICAL3,
                                        ethtool_stats_optical3,
                                        ETHTOOL_ZERO, self)

    def test_16_bridged_unicast_lan2_ipv6(self):
        """Send traffic between bridged ports, and ensure they come out the
           expected ports. lan2 is the source"""

        class_stats_master = self.sut.getClassStats(self.config.SUT_MASTER)

        self.traffic.addUDPv6Stream(self.config.HOST_LAN2,
                                    self.config.HOST_LAN1, 100, 100)
        self.traffic.addUDPv6Stream(self.config.HOST_LAN2,
                                    self.config.HOST_LAN3, 100, 100)
        self.traffic.addUDPv6Stream(self.config.HOST_LAN2,
                                    self.config.HOST_LAN4, 100, 100)
        self.traffic.addUDPv6Stream(self.config.HOST_LAN2,
                                    self.config.HOST_LAN6, 100, 100)
        self.traffic.run()

        stats_lan0 = self.traffic.getStats(self.config.HOST_LAN0)
        stats_lan1 = self.traffic.getStats(self.config.HOST_LAN1)
        stats_lan2 = self.traffic.getStats(self.config.HOST_LAN2)
        stats_lan3 = self.traffic.getStats(self.config.HOST_LAN3)
        stats_lan4 = self.traffic.getStats(self.config.HOST_LAN4)
        stats_lan5 = self.traffic.getStats(self.config.HOST_LAN5)
        stats_lan6 = self.traffic.getStats(self.config.HOST_LAN6)
        stats_optical3 = self.traffic.getStats(self.config.HOST_OPTICAL3)

        self.assertEqual(stats_lan0, ZERO_STATS)
        self.assertEqual(stats_lan1, RX_100_STATS)
        self.assertEqual(stats_lan2, TX_400_STATS)
        self.assertEqual(stats_lan3, RX_100_STATS)
        self.assertEqual(stats_lan4, RX_100_STATS)
        self.assertEqual(stats_lan5, ZERO_STATS)
        self.assertEqual(stats_lan6, RX_100_STATS)
        self.assertEqual(stats_optical3, ZERO_STATS)

        # All frames should be hardware bridge
        self.sut.checkClassStatsRange(self.config.SUT_MASTER,
                                      class_stats_master,
                                      CLASS_TX_RX_0, self)

    def test_17_bridged_unicast_lan3_ipv6(self):
        """Send traffic between bridged ports, and ensure they come out the
           expected ports. lan3 is the source"""

        class_stats_master = self.sut.getClassStats(self.config.SUT_MASTER)

        self.traffic.addUDPv6Stream(self.config.HOST_LAN3,
                                    self.config.HOST_LAN1, 100, 100)
        self.traffic.addUDPv6Stream(self.config.HOST_LAN3,
                                    self.config.HOST_LAN2, 100, 100)
        self.traffic.addUDPv6Stream(self.config.HOST_LAN3,
                                    self.config.HOST_LAN4, 100, 100)
        self.traffic.addUDPv6Stream(self.config.HOST_LAN3,
                                    self.config.HOST_LAN6, 100, 100)
        self.traffic.run()

        stats_lan0 = self.traffic.getStats(self.config.HOST_LAN0)
        stats_lan1 = self.traffic.getStats(self.config.HOST_LAN1)
        stats_lan2 = self.traffic.getStats(self.config.HOST_LAN2)
        stats_lan3 = self.traffic.getStats(self.config.HOST_LAN3)
        stats_lan4 = self.traffic.getStats(self.config.HOST_LAN4)
        stats_lan5 = self.traffic.getStats(self.config.HOST_LAN5)
        stats_lan6 = self.traffic.getStats(self.config.HOST_LAN6)
        stats_optical3 = self.traffic.getStats(self.config.HOST_OPTICAL3)

        self.assertEqual(stats_lan0, ZERO_STATS)
        self.assertEqual(stats_lan1, RX_100_STATS)
        self.assertEqual(stats_lan2, RX_100_STATS)
        self.assertEqual(stats_lan3, TX_400_STATS)
        self.assertEqual(stats_lan4, RX_100_STATS)
        self.assertEqual(stats_lan5, ZERO_STATS)
        self.assertEqual(stats_lan6, RX_100_STATS)
        self.assertEqual(stats_optical3, ZERO_STATS)

        # All frames should be hardware bridge
        self.sut.checkClassStatsRange(self.config.SUT_MASTER,
                                      class_stats_master,
                                      CLASS_TX_RX_0, self)

    def test_18_bridged_unicast_lan4_ipv6(self):
        """Send traffic between bridged ports, and ensure they come out the
           expected ports. lan4 is the source"""

        class_stats_master = self.sut.getClassStats(self.config.SUT_MASTER)

        self.traffic.addUDPv6Stream(self.config.HOST_LAN4,
                                    self.config.HOST_LAN1, 100, 100)
        self.traffic.addUDPv6Stream(self.config.HOST_LAN4,
                                    self.config.HOST_LAN2, 100, 100)
        self.traffic.addUDPv6Stream(self.config.HOST_LAN4,
                                    self.config.HOST_LAN3, 100, 100)
        self.traffic.addUDPv6Stream(self.config.HOST_LAN4,
                                    self.config.HOST_LAN6, 100, 100)
        self.traffic.run()

        stats_lan0 = self.traffic.getStats(self.config.HOST_LAN0)
        stats_lan1 = self.traffic.getStats(self.config.HOST_LAN1)
        stats_lan2 = self.traffic.getStats(self.config.HOST_LAN2)
        stats_lan3 = self.traffic.getStats(self.config.HOST_LAN3)
        stats_lan4 = self.traffic.getStats(self.config.HOST_LAN4)
        stats_lan5 = self.traffic.getStats(self.config.HOST_LAN5)
        stats_lan6 = self.traffic.getStats(self.config.HOST_LAN6)
        stats_optical3 = self.traffic.getStats(self.config.HOST_OPTICAL3)

        self.assertEqual(stats_lan0, ZERO_STATS)
        self.assertEqual(stats_lan1, RX_100_STATS)
        self.assertEqual(stats_lan2, RX_100_STATS)
        self.assertEqual(stats_lan3, RX_100_STATS)
        self.assertEqual(stats_lan4, TX_400_STATS)
        self.assertEqual(stats_lan5, ZERO_STATS)
        self.assertEqual(stats_lan6, RX_100_STATS)
        self.assertEqual(stats_optical3, ZERO_STATS)

        # All frames should be hardware bridge
        self.sut.checkClassStatsRange(self.config.SUT_MASTER,
                                      class_stats_master,
                                      CLASS_TX_RX_0, self)

    def test_19_bridged_unicast_lan6_ipv6(self):
        """Send traffic between bridged ports, and ensure they come out the
           expected ports. lan6 is the source"""

        self.traffic.addUDPv6Stream(self.config.HOST_LAN6,
                                    self.config.HOST_LAN1, 100, 100)
        self.traffic.addUDPv6Stream(self.config.HOST_LAN6,
                                    self.config.HOST_LAN2, 100, 100)
        self.traffic.addUDPv6Stream(self.config.HOST_LAN6,
                                    self.config.HOST_LAN3, 100, 100)
        self.traffic.addUDPv6Stream(self.config.HOST_LAN6,
                                    self.config.HOST_LAN4, 100, 100)
        self.traffic.run()

        stats_lan0 = self.traffic.getStats(self.config.HOST_LAN0)
        stats_lan1 = self.traffic.getStats(self.config.HOST_LAN1)
        stats_lan2 = self.traffic.getStats(self.config.HOST_LAN2)
        stats_lan3 = self.traffic.getStats(self.config.HOST_LAN3)
        stats_lan4 = self.traffic.getStats(self.config.HOST_LAN4)
        stats_lan5 = self.traffic.getStats(self.config.HOST_LAN5)
        stats_lan6 = self.traffic.getStats(self.config.HOST_LAN6)
        stats_optical3 = self.traffic.getStats(self.config.HOST_OPTICAL3)

        self.assertEqual(stats_lan0, ZERO_STATS)
        self.assertEqual(stats_lan1, RX_100_STATS)
        self.assertEqual(stats_lan2, RX_100_STATS)
        self.assertEqual(stats_lan3, RX_100_STATS)
        self.assertEqual(stats_lan4, RX_100_STATS)
        self.assertEqual(stats_lan5, ZERO_STATS)
        self.assertEqual(stats_lan6, TX_400_STATS)
        self.assertEqual(stats_optical3, ZERO_STATS)

    def test_20_bridged_unicast_optical3_ipv6(self):
        """Add optical3 to the bridge, and test hardware bridging between
           ports on switch 2."""

        self.sut.addBridgeInterface('br1', self.config.SUT_OPTICAL3)

        self.traffic.addUDPv6Stream(self.config.HOST_OPTICAL3,
                                    self.config.HOST_LAN1, 100, 100)
        self.traffic.addUDPv6Stream(self.config.HOST_OPTICAL3,
                                    self.config.HOST_LAN2, 100, 100)
        self.traffic.addUDPv6Stream(self.config.HOST_OPTICAL3,
                                    self.config.HOST_LAN3, 100, 100)
        self.traffic.addUDPv6Stream(self.config.HOST_OPTICAL3,
                                    self.config.HOST_LAN6, 100, 100)
        self.traffic.run()

        stats_lan0 = self.traffic.getStats(self.config.HOST_LAN0)
        stats_lan1 = self.traffic.getStats(self.config.HOST_LAN1)
        stats_lan2 = self.traffic.getStats(self.config.HOST_LAN2)
        stats_lan3 = self.traffic.getStats(self.config.HOST_LAN3)
        stats_lan4 = self.traffic.getStats(self.config.HOST_LAN4)
        stats_lan5 = self.traffic.getStats(self.config.HOST_LAN5)
        stats_lan6 = self.traffic.getStats(self.config.HOST_LAN6)
        stats_optical3 = self.traffic.getStats(self.config.HOST_OPTICAL3)

        self.assertEqual(stats_lan0, ZERO_STATS)
        self.assertEqual(stats_lan1, RX_100_STATS)
        self.assertEqual(stats_lan2, RX_100_STATS)
        self.assertEqual(stats_lan3, RX_100_STATS)
        self.assertEqual(stats_lan4, ZERO_STATS)
        self.assertEqual(stats_lan5, ZERO_STATS)
        self.assertEqual(stats_lan6, RX_100_STATS)
        self.assertEqual(stats_optical3, TX_400_STATS)

        self.sut.deleteBridgeInterface('br1', self.config.SUT_OPTICAL3)

    def test_21_bridged_broadcast_lan6_ipv6(self):
        """Send traffic between bridged ports, and ensure they come out the
           expected ports. lan6 is the source of broadcast packets"""
        self.traffic.addUDPBroadcastStream(self.config.HOST_LAN6, 100, 100)
        self.traffic.run()

        stats_lan0 = self.traffic.getStats(self.config.HOST_LAN0)
        stats_lan1 = self.traffic.getStats(self.config.HOST_LAN1)
        stats_lan2 = self.traffic.getStats(self.config.HOST_LAN2)
        stats_lan3 = self.traffic.getStats(self.config.HOST_LAN3)
        stats_lan4 = self.traffic.getStats(self.config.HOST_LAN4)
        stats_lan5 = self.traffic.getStats(self.config.HOST_LAN5)
        stats_lan6 = self.traffic.getStats(self.config.HOST_LAN6)
        stats_optical3 = self.traffic.getStats(self.config.HOST_OPTICAL3)

        self.assertEqual(stats_lan0, ZERO_STATS)
        self.assertEqual(stats_lan1, RX_100_STATS)
        self.assertEqual(stats_lan2, RX_100_STATS)
        self.assertEqual(stats_lan3, RX_100_STATS)
        self.assertEqual(stats_lan4, RX_100_STATS)
        self.assertEqual(stats_lan5, ZERO_STATS)
        self.assertEqual(stats_lan6, TX_100_STATS)
        self.assertEqual(stats_optical3, ZERO_STATS)

    def test_99_delete_bridge(self):
        """Destroy the bridge"""
        self.sut.deleteBridgeInterface('br1', self.config.SUT_LAN1)
        self.sut.deleteBridgeInterface('br1', self.config.SUT_LAN2)
        self.sut.deleteBridgeInterface('br1', self.config.SUT_LAN3)
        self.sut.deleteBridgeInterface('br1', self.config.SUT_LAN4)
        self.sut.deleteBridgeInterface('br1', self.config.SUT_LAN6)
        self.sut.deleteBridge('br1')

        # Ensure all the interfaces are down
        self.sut.down(self.config.SUT_LAN1)
        self.sut.down(self.config.SUT_LAN2)
        self.sut.down(self.config.SUT_LAN3)
        self.sut.down(self.config.SUT_LAN4)
        self.sut.down(self.config.SUT_LAN5)
        self.sut.down(self.config.SUT_LAN6)
        self.sut.down(self.config.SUT_OPTICAL3)


if __name__ == '__main__':
    ARGS = params.params()
    CONFIG = params.readConfig(ARGS.config, fourPorts=False)
    SUT = sut.SUT(hostname=CONFIG.hostname, key=CONFIG.key,
                  mgmt=CONFIG.SUT_MGMT)
    SUT.cleanSystem()
    TRAFFIC = traffic.Traffic()

    if ARGS.xml:
        import xmlrunner
        TESTRUNNER = xmlrunner.XMLTestRunner(output='test-reports',
                                             verbosity=ARGS.verbose)
    else:
        TESTRUNNER = unittest2.TextTestRunner(failfast=ARGS.failfast,
                                              verbosity=ARGS.verbose)
    if ARGS.vlanfiltering:
        VLAN_FILTERING = True

    unittest2.main(buffer=False, testRunner=TESTRUNNER, exit=False)
