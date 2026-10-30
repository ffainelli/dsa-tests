#!/usr/bin/env python
"""Test PTP support."""

import datetime
import os
import time
import unittest as unittest2

import host
import params
import sut


SUT_MASTER = None
CONFIG_MASTER = None
MASTER_INTERFACE = 'lan0'
SUT_SLAVE = None
CONFIG_SLAVE = None
HOST = None
PTP = None

class Ptp_master_slave_bridge_test(unittest2.TestCase):
    '''Class containing the test cases'''

    def setUp(self):
        """Setup ready to perform the test"""
        self.sut_master = SUT_MASTER
        self.config_master = CONFIG_MASTER
        self.config_master_interface = MASTER_INTERFACE
        self.sut_slave = SUT_SLAVE
        self.config_slave = CONFIG_SLAVE
        self.host = HOST
        self.test_dir = os.path.dirname(os.path.realpath(__file__))
        self.maxDiff = None

    def _put_master_config(self, config):
        """Copy the requested configuration file on the master"""
        filename = self.test_dir + \
                   '/ptp_master_slave_test/master-{0}-{1}.conf'.format(
                       config, self.config_master_interface)
        self._put_master_config(filename, '/etc/ptp4l.conf')

    def _put_slave_config(self, config):
        """Copy the requested configuration file on the slave"""
        filename = self.test_dir + \
                   '/ptp_master_slave_test/slave-{0}.conf'.format(
                       config)
        self.sut_slave.sftpPut(filename, '/etc/ptp4l.conf')

    def test_00_setup_master_l2(self):
        """Setup the interfaces on the PTP master"""

        # Ensure all the interfaces are up
        self.sut_master.up(self.config_master.SUT_MASTER)
        self.sut_master.up(self.config_master.SUT_LAN0)
        self.sut_master.addAddress(self.config_master.SUT_LAN0,
                                   '192.168.10.1/24')

        self.sut_master.serviceStop('ptp4l')
        self.sut_master.phcSet(self.config_master.SUT_LAN0, 0)

        self._put_master_config('l2-ptp4l.conf')
        self.sut_master.serviceStart('ptp4l')

    def test_01_setup_slave_l2(self):
        """Setup the interfaces on the PTP slave, add it to a bridge"""

        # Ensure all the interfaces are up
        self.sut_slave.up(self.config_slave.SUT_MASTER)
        self.sut_slave.up(self.config_slave.SUT_LAN0)

        self.sut_slave.addBridge('br1')
        self.sut_slave.up('br1')
        self.sut_slave.addBridgeInterface('br1', self.config_slave.SUT_LAN0)
        self.sut_slave.addAddress('br1', '192.168.10.2/24')

        self.sut_slave.serviceStop('ptp4l')

        beginning = datetime.datetime(2018, 1, 1)
        seconds = time.mktime(beginning.timetuple())
        self.sut_slave.phcSet(self.config_slave.SUT_LAN0, seconds)
        phc_seconds, _ = self.sut_slave.phcGet(self.config_slave.SUT_LAN0)
        self.assertAlmostEqual(seconds, phc_seconds, places=0,
                               msg='{0} != {1}'.format(
                                   (beginning),
                                   datetime.datetime.utcfromtimestamp(
                                       phc_seconds)))

        self._put_slave_config('l2-ptp4l.conf')
        self.sut_slave.serviceStart('ptp4l')

    def test_02_check_slave_l2(self):
        """Check that the slave has a similar time to the master"""
        time.sleep(20)

        master_seconds, _ = self.sut_master.phcGet(self.config_master.SUT_LAN0)
        slave_seconds, _ = self.sut_slave.phcGet(self.config_slave.SUT_LAN0)

        self.assertAlmostEqual(master_seconds, slave_seconds, places=0,
                               msg='{0} != {1}'.format(
                                   datetime.datetime.utcfromtimestamp(
                                       master_seconds),
                                   datetime.datetime.utcfromtimestamp(
                                       slave_seconds)))

    @unittest2.skipIf(PTP == 'l2', "IPv6 PTP not supported")
    def test_03_setup_master_ipv6(self):
        """Setup the interfaces on the PTP master, for L2 test"""

        self.sut_master.serviceStop('ptp4l')
        self.sut_master.phcSet(self.config_master.SUT_LAN0, 0)

        self._put_master_config('ipv6-ptp4l.conf')
        self.sut_master.serviceStart('ptp4l')

    @unittest2.skipIf(PTP == 'l2', "IPv6 PTP not supported")
    def test_04_setup_slave_ipv6(self):
        """Setup the interfaces on the PTP slave, for L2 test"""

        self.sut_slave.serviceStop('ptp4l')

        beginning = datetime.datetime(2018, 1, 1)
        seconds = time.mktime(beginning.timetuple())
        self.sut_slave.phcSet(self.config_slave.SUT_LAN0, seconds)
        phc_seconds, _ = self.sut_slave.phcGet(self.config_slave.SUT_LAN0)
        self.assertAlmostEqual(seconds, phc_seconds, places=0,
                               msg='{0} != {1}'.format(
                                   beginning,
                                   datetime.datetime.utcfromtimestamp(
                                       phc_seconds)))

        self._put_slave_config('ipv6-ptp4l')
        self.sut_slave.serviceStart('ptp4l')

    @unittest2.skipIf(PTP == 'l2', "IPv6 PTP not supported")
    def test_05_check_slave_ipv6(self):
        """Check that the slave has a similar time to the master"""
        time.sleep(30)

        master_seconds, _ = self.sut_master.phcGet(self.config_master.SUT_LAN0)
        slave_seconds, _ = self.sut_slave.phcGet(self.config_slave.SUT_LAN0)

        self.assertAlmostEqual(master_seconds, slave_seconds, places=0,
                               msg='{0} != {1}'.format(
                                   datetime.datetime.utcfromtimestamp(
                                       master_seconds),
                                   datetime.datetime.utcfromtimestamp(
                                       slave_seconds)))

    def test_99_cleanup(self):
        """Stop the daemons"""
        self.sut_slave.serviceStop('ptp4l')
        self.sut_master.serviceStop('ptp4l')


if __name__ == '__main__':
    ARGS = params.params()
    PTP = ARGS.ptp

    CONFIG_SLAVE = params.readConfig(ARGS.config)
    SUT_SLAVE = sut.SUT(hostname=CONFIG_SLAVE.hostname,
                        key=CONFIG_SLAVE.key,
                        mgmt=CONFIG_SLAVE.SUT_MGMT)
    SUT_SLAVE.cleanSystem()

    CONFIG_MASTER = params.readConfig(ARGS.config_master)
    MASTER_INTERFACE = ARGS.config_master_interface
    SUT_MASTER = sut.SUT(hostname=CONFIG_MASTER.hostname,
                         key=CONFIG_MASTER.key,
                         mgmt=CONFIG_MASTER.SUT_MGMT)

    SUT_MASTER.cleanSystem()
    HOST = host.HOST()

    if ARGS.xml:
        import xmlrunner
        TEST_RUNNER = xmlrunner.XMLTestRunner(output='test-reports',
                                              verbosity=ARGS.verbose)
    else:
        TEST_RUNNER = unittest2.TextTestRunner(failfast=ARGS.failfast,
                                               verbosity=ARGS.verbose)

    unittest2.main(buffer=False, testRunner=TEST_RUNNER, exit=False)
    HOST.cleanSystem()
