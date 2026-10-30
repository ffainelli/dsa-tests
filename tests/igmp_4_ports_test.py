#!/usr/bin/env python
"""Test the operation of IGMP on a bridge on the SUT"""

import time
import unittest as unittest2

import host
import params
import sut
import traffic


SUT = None
HOST = host
TRAFFIC = None
CONFIG = None
VLAN_FILTERING = False
CHANNEL = None

ethtool_rx_0_tx_0 = {'in_multicasts': (0, 20),
                     'out_multicasts': (0, 20)}

ethtool_rx_500_tx_0 = {'in_multicasts': (500, 520),
                       'out_multicasts': (0, 20)}

ethtool_rx_0_tx_500 = {'in_multicasts': (0, 20),
                       'out_multicasts': (500, 520)}


class igmp_4_port_test(unittest2.TestCase):
    '''Class containing the test cases'''

    def setUp(self):
        """Setup ready to perform the test"""
        self.sut = SUT
        self.host = HOST
        self.traffic = TRAFFIC
        self.config = CONFIG
        self.maxDiff = None
        self.vlan_filtering = VLAN_FILTERING

    def test_01_setup_sut(self):
        """Create the bridge"""
        # Ensure all the interfaces are up
        self.sut.up(self.config.SUT_MASTER)
        self.sut.up(self.config.SUT_LAN0)
        self.sut.up(self.config.SUT_LAN1)
        self.sut.up(self.config.SUT_LAN2)
        self.sut.up(self.config.SUT_LAN3)

        self.sut.addBridge('br1')
        self.sut.addBridgeIgmpQuerier('br1')

        if self.vlan_filtering:
            self.sut.bridgeEnableVlanFiltering('br1')

        self.sut.up('br1')
        self.sut.addAddress('br1', '192.168.58.42/24')
        self.sut.addBridgeInterface('br1', self.config.SUT_LAN0)
        self.sut.addBridgeInterface('br1', self.config.SUT_LAN1)
        self.sut.addBridgeInterface('br1', self.config.SUT_LAN2)

        # Wait the forwarding delay of the bridge
        time.sleep(10)

    def test_02_setup_host(self):
        """Setup on the host, perform learning"""
        self.host.addInterface(self.config.HOST_LAN0)
        self.host.addInterface(self.config.HOST_LAN1)
        self.host.addInterface(self.config.HOST_LAN2)
        self.host.addInterface(self.config.HOST_LAN3)
        self.host.cleanSystem()

        self.host.up(self.config.HOST_LAN0)
        self.host.up(self.config.HOST_LAN1)
        self.host.up(self.config.HOST_LAN2)
        self.host.up(self.config.HOST_LAN3)

        self.host.addAddress(self.config.HOST_LAN0, '192.168.58.1/24')
        self.host.addAddress(self.config.HOST_LAN1, '192.168.58.2/24')
        self.host.addAddress(self.config.HOST_LAN2, '192.168.58.3/24')

        # Send learning packets, so the bridge knows which MAC address
        # is where
        self.traffic.addInterface(self.config.HOST_LAN0)
        self.traffic.addInterface(self.config.HOST_LAN1)
        self.traffic.addInterface(self.config.HOST_LAN2)
        self.traffic.addInterface(self.config.HOST_LAN3)
        self.traffic.learning()

    def test_03_join(self):
        """Join a group on LAN1 and LAN2. LAN0 is does not join, so snooping
           should mean it does not receive multicast frames for this group"""

        self.host.join(self.config.HOST_LAN1, '192.168.58.2', '224.42.42.42')
        self.host.join(self.config.HOST_LAN2, '192.168.58.3', '224.42.42.42')

    def test_04_sleep(self):
        """Sleep for a while to allow IGMP to be exchanged"""
        time.sleep(10)

    def test_05_multicast_lan1(self):
        """Send some multicast packets out LAN1. We expect to receive them on
           LAN2, but not LAN0. LAN3 is not part of the bridge, it
           should not receive them either."""

        ethtool_stats_lan0 = self.sut.getEthtoolStats(self.config.SUT_LAN0)
        ethtool_stats_lan1 = self.sut.getEthtoolStats(self.config.SUT_LAN1)
        ethtool_stats_lan2 = self.sut.getEthtoolStats(self.config.SUT_LAN2)
        ethtool_stats_lan3 = self.sut.getEthtoolStats(self.config.SUT_LAN3)

        self.traffic.addUDPMulticastStream(self.config.HOST_LAN1,
                                           '224.42.42.42', 500, 500)
        self.traffic.run()

        self.sut.checkEthtoolStatsRange(self.config.SUT_LAN0,
                                        ethtool_stats_lan0,
                                        ethtool_rx_0_tx_0, self)

        self.sut.checkEthtoolStatsRange(self.config.SUT_LAN1,
                                        ethtool_stats_lan1,
                                        ethtool_rx_500_tx_0, self)

        self.sut.checkEthtoolStatsRange(self.config.SUT_LAN2,
                                        ethtool_stats_lan2,
                                        ethtool_rx_0_tx_500, self)

        self.sut.checkEthtoolStatsRange(self.config.SUT_LAN3,
                                        ethtool_stats_lan3,
                                        ethtool_rx_0_tx_0, self)

    def test_06_multicast_lan2(self):
        """Send some multicast packets out LAN2. We expect to receive them on
           LAN1, but not LAN0.  LAN3 is not part of the bridge, it
           should not receive them either."""

        ethtool_stats_lan0 = self.sut.getEthtoolStats(self.config.SUT_LAN0)
        ethtool_stats_lan1 = self.sut.getEthtoolStats(self.config.SUT_LAN1)
        ethtool_stats_lan2 = self.sut.getEthtoolStats(self.config.SUT_LAN2)
        ethtool_stats_lan3 = self.sut.getEthtoolStats(self.config.SUT_LAN3)

        self.traffic.addUDPMulticastStream(self.config.HOST_LAN2,
                                           '224.42.42.42', 500, 500)
        self.traffic.run()

        self.sut.checkEthtoolStatsRange(self.config.SUT_LAN0,
                                        ethtool_stats_lan0,
                                        ethtool_rx_0_tx_0, self)

        self.sut.checkEthtoolStatsRange(self.config.SUT_LAN1,
                                        ethtool_stats_lan1,
                                        ethtool_rx_0_tx_500, self)

        self.sut.checkEthtoolStatsRange(self.config.SUT_LAN2,
                                        ethtool_stats_lan2,
                                        ethtool_rx_500_tx_0, self)

        self.sut.checkEthtoolStatsRange(self.config.SUT_LAN3,
                                        ethtool_stats_lan3,
                                        ethtool_rx_0_tx_0, self)

    def test_07_multicast_lan0(self):
        """Send some multicast packets out LAN0. We expect to receive them on
           LAN1 and LAN2. LAN3 is not part of the bridge, it should
           not receive them either."""

        ethtool_stats_lan0 = self.sut.getEthtoolStats(self.config.SUT_LAN0)
        ethtool_stats_lan1 = self.sut.getEthtoolStats(self.config.SUT_LAN1)
        ethtool_stats_lan2 = self.sut.getEthtoolStats(self.config.SUT_LAN2)
        ethtool_stats_lan3 = self.sut.getEthtoolStats(self.config.SUT_LAN3)

        self.traffic.addUDPMulticastStream(self.config.HOST_LAN0,
                                           '224.42.42.42', 500, 500)
        self.traffic.run()

        self.sut.checkEthtoolStatsRange(self.config.SUT_LAN0,
                                        ethtool_stats_lan0,
                                        ethtool_rx_500_tx_0, self)

        self.sut.checkEthtoolStatsRange(self.config.SUT_LAN1,
                                        ethtool_stats_lan1,
                                        ethtool_rx_0_tx_500, self)

        self.sut.checkEthtoolStatsRange(self.config.SUT_LAN2,
                                        ethtool_stats_lan2,
                                        ethtool_rx_0_tx_500, self)

        self.sut.checkEthtoolStatsRange(self.config.SUT_LAN3,
                                        ethtool_stats_lan3,
                                        ethtool_rx_0_tx_0, self)

    def test_08_host_join(self):
        """Have the sut join the group on the bridge interface"""
        global CHANNEL
        CHANNEL = self.sut.start_ssh('join-mcast-group')
        time.sleep(60)

    def test_09_multicast_lan1(self):

        """Send some multicast packets out LAN1. We expect to receive them on
           LAN2, but not LAN0. LAN3 is not part of the bridge, it
           should not receive them either. The SUT should get the frames on
           br1"""

        ethtool_stats_lan0 = self.sut.getEthtoolStats(self.config.SUT_LAN0)
        ethtool_stats_lan1 = self.sut.getEthtoolStats(self.config.SUT_LAN1)
        ethtool_stats_lan2 = self.sut.getEthtoolStats(self.config.SUT_LAN2)
        ethtool_stats_lan3 = self.sut.getEthtoolStats(self.config.SUT_LAN3)
        class_stats_br1 = self.sut.getClassStats('br1')

        self.traffic.addUDPMulticastStream(self.config.HOST_LAN1,
                                           '224.42.42.42', 500, 500)
        self.traffic.run()

        self.sut.checkEthtoolStatsRange(self.config.SUT_LAN0,
                                        ethtool_stats_lan0,
                                        ethtool_rx_0_tx_0, self)

        self.sut.checkEthtoolStatsRange(self.config.SUT_LAN1,
                                        ethtool_stats_lan1,
                                        ethtool_rx_500_tx_0, self)

        self.sut.checkEthtoolStatsRange(self.config.SUT_LAN2,
                                        ethtool_stats_lan2,
                                        ethtool_rx_0_tx_500, self)

        self.sut.checkEthtoolStatsRange(self.config.SUT_LAN3,
                                        ethtool_stats_lan3,
                                        ethtool_rx_0_tx_0, self)
        print(class_stats_br1)
        print(self.sut.getClassStats('br1'))

    def test_10_host_leave(self):
        """Have the sut leave the multicast group, by killing the command
           we started"""
        global CHANNEL
        self.sut.stop_ssh(CHANNEL)
        self.sut.ssh('killall join-mcast-group')

    def test_99_delete_bridge(self):
        """Destroy the bridge"""
        self.sut.deleteBridgeInterface('br1', self.config.SUT_LAN0)
        self.sut.deleteBridgeInterface('br1', self.config.SUT_LAN1)
        self.sut.deleteBridgeInterface('br1', self.config.SUT_LAN2)
        self.sut.deleteBridge('br1')

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
    HOST = host.HOST()
    TRAFFIC = traffic.Traffic()

    if args.xml:
        import xmlrunner
        testRunner = xmlrunner.XMLTestRunner(output='test-reports',
                                             verbosity=args.verbose)
    else:
        testRunner = unittest2.TextTestRunner(failfast=args.failfast,
                                              verbosity=args.verbose)
    if args.vlanfiltering:
        VLAN_FILTERING = True

    unittest2.main(buffer=False, testRunner=testRunner, exit=False)
    HOST.cleanSystem()
