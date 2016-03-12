#!/usr/bin/env python
"""Ping individual interfaces of SUT. Assumes all four port are on the
same switch"""

import time
import unittest2
import xmlrunner

import host
import params
import sut


SUT = None
HOST = None
CONFIG = None

CLASS_TX_RX_8 = {'tx_packets': (8, 12),
                 'rx_packets': (8, 12)}


class ping_individual_test(unittest2.TestCase):
    '''Class containing the test cases'''

    def setUp(self):
        """Setup ready to perform the test"""
        self.sut = SUT
        self.host = HOST
        self.config = CONFIG
        self.maxDiff = None

    def test_00_setup_sut(self):
        """Setup IP addresses on the SUT interfaces"""
        # Ensure all the interfaces are up
        self.sut.up(self.config.SUT_MASTER)
        self.sut.up(self.config.SUT_LAN0)
        self.sut.up(self.config.SUT_LAN1)
        self.sut.up(self.config.SUT_LAN2)
        self.sut.up(self.config.SUT_LAN3)

        self.sut.addAddress(self.config.SUT_LAN0, '192.168.10.2/24')
        self.sut.addAddress(self.config.SUT_LAN1, '192.168.11.2/24')
        self.sut.addAddress(self.config.SUT_LAN2, '192.168.12.2/24')
        self.sut.addAddress(self.config.SUT_LAN3, '192.168.13.2/24')

        # Allow time for the interfaces to come up
        time.sleep(5)

    def test_01_setup_host(self):
        """Setup IP addresses on the host device"""
        self.host.addInterface(self.config.HOST_LAN0)
        self.host.addInterface(self.config.HOST_LAN1)
        self.host.addInterface(self.config.HOST_LAN2)
        self.host.addInterface(self.config.HOST_LAN3)
        self.host.cleanSystem()

        self.host.up(self.config.HOST_LAN0)
        self.host.up(self.config.HOST_LAN1)
        self.host.up(self.config.HOST_LAN2)
        self.host.up(self.config.HOST_LAN3)

        self.host.addAddress(self.config.HOST_LAN0, '192.168.10.1/24')
        self.host.addAddress(self.config.HOST_LAN1, '192.168.11.1/24')
        self.host.addAddress(self.config.HOST_LAN2, '192.168.12.1/24')
        self.host.addAddress(self.config.HOST_LAN3, '192.168.13.1/24')

    def test_02_ping(self):
        """Ping the SUT. We expect replies for all interfaces"""

        class_stats_eth1 = self.sut.getClassStats(self.config.SUT_MASTER)

        self.assertTrue(self.host.ping('192.168.10.2'))
        self.assertTrue(self.host.ping('192.168.11.2'))
        self.assertTrue(self.host.ping('192.168.12.2'))
        self.assertTrue(self.host.ping('192.168.13.2'))

        self.sut.checkClassStatsRange(self.config.SUT_MASTER,
                                      class_stats_eth1,
                                      CLASS_TX_RX_8, self)

    def test_03_ping_down(self):
        """Down the interfaces on the SUT and then ping the SUT.
           We don't expect replies for all interfaces"""
        self.sut.down(self.config.SUT_LAN0)
        self.sut.down(self.config.SUT_LAN1)
        self.sut.down(self.config.SUT_LAN2)
        self.sut.down(self.config.SUT_LAN3)

        self.assertFalse(self.host.ping('192.168.10.2'))
        self.assertFalse(self.host.ping('192.168.11.2'))
        self.assertFalse(self.host.ping('192.168.12.2'))
        self.assertFalse(self.host.ping('192.168.13.2'))

if __name__ == '__main__':
    args = params.params()
    CONFIG = params.readConfig(args.config)
    SUT = sut.SUT(hostname=CONFIG.hostname, key=CONFIG.key)
    SUT.cleanSystem()
    HOST = host.HOST()

    if args.xml:
        testRunner = xmlrunner.XMLTestRunner(output='test-reports',
                                             verbosity=args.verbose)
    else:
        testRunner = unittest2.TextTestRunner(failfast=args.failfast,
                                              verbosity=args.verbose)

    unittest2.main(buffer=False, testRunner=testRunner, exit=False)
    HOST.cleanSystem()
