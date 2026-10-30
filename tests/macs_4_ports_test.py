#!/usr/bin/env python
"""Test lots of MAC addresses on a bridge of four ports"""

import time
import unittest as unittest2

import params
import sut
import traffic

SUT = None
TRAFFIC = None
CONFIG = None

MAC_STEP = 7


class macs_4_ports_test(unittest2.TestCase):
    '''Class containing the test cases'''

    def setUp(self):
        """Setup ready to perform the test"""
        self.sut = SUT
        self.traffic = TRAFFIC
        self.config = CONFIG
        self.maxDiff = None

    def _check_interface_macs(self, macs, own_mac, bridge_mac):
        """Require the learned source MAC on this port."""
        return own_mac in macs

    def _check_additional_macs(self, macs, own_mac, bridge_mac,
                               base, number, step):
        """Verify own_mac is in macs, and the additional macs are present, as
           well as the well know multicast addresses, the bridge MAC
           and the expected additional MAC addresses."""
        additional = []

        int_base = int.from_bytes(traffic.mac_bytes(base), 'big')
        for lower in range(number):
            additional.append(traffic.mac_text(int_base + lower * step))

        for mac in macs:
            if mac == own_mac:
                continue
            if mac == bridge_mac:
                continue
            if mac == '33:33:00:00:00:01':
                continue
            if mac == '01:00:5e:00:00:01':
                continue
            if mac in additional:
                continue

        for mac in additional:
            if mac not in macs:
                self.fail(
                    'MAC {0} missing. Have {1} MAC addresses: {2}'.format(
                        mac, len(macs), sorted(macs)))
                return False
        return True

    def _check_dmesg_contains(self, string):
        """Check that the output of dmesg contains string"""
        dmsg = self.sut.getDmsg()
        for line in dmsg.splitlines():
            if string in line:
                return True
        return False

    def test_01_create_bridge(self):
        """Create the bridge"""
        # Ensure all the interfaces are up
        self.sut.up(self.config.SUT_MASTER)
        self.sut.up(self.config.SUT_LAN0)
        self.sut.up(self.config.SUT_LAN1)
        self.sut.up(self.config.SUT_LAN2)

        self.sut.addBridge('br1')
        self.sut.up('br1')
        self.sut.addBridgeInterface('br1', self.config.SUT_LAN0)
        self.sut.addBridgeInterface('br1', self.config.SUT_LAN1)
        self.sut.addBridgeInterface('br1', self.config.SUT_LAN2)

        # Wait the forwarding delay of the bridge
        time.sleep(10)

        self.traffic.addInterface(self.config.HOST_LAN0)
        self.traffic.addInterface(self.config.HOST_LAN1)
        self.traffic.addInterface(self.config.HOST_LAN2)

    def test_02_learn(self):
        """Send learning packets, so the bridge knows which MAC address
           is where"""
        self.traffic.learning()

    def test_03_interface_macs(self):
        """Test the MAC addresses associated with the interfaces has been
           learnt, and on the correct ports"""
        macs_lan0 = self.sut.getFdb(self.config.SUT_LAN0)
        macs_lan1 = self.sut.getFdb(self.config.SUT_LAN1)
        macs_lan2 = self.sut.getFdb(self.config.SUT_LAN2)
        mac_bridge = self.sut.getMacAddress(self.config.SUT_MASTER)

        mac_lan0 = self.traffic.getInterfaceMacAddress(self.config.HOST_LAN0)
        mac_lan1 = self.traffic.getInterfaceMacAddress(self.config.HOST_LAN1)
        mac_lan2 = self.traffic.getInterfaceMacAddress(self.config.HOST_LAN2)

        self.assertTrue(self._check_interface_macs(macs_lan0, mac_lan0,
                                                   mac_bridge),
                        '{0} not in {1}'.format(
                            mac_lan0, macs_lan0))
        self.assertTrue(self._check_interface_macs(macs_lan1, mac_lan1,
                                                   mac_bridge),
                        '{0} not in {1}'.format(
                            mac_lan1, macs_lan1))
        self.assertTrue(self._check_interface_macs(macs_lan2, mac_lan2,
                                                   mac_bridge),
                        '{0} not in {1}'.format(
                            mac_lan2, macs_lan2))

        # Refresh the learning
        self.traffic.learning()

    def test_04_384_macs(self):
        """Add 128 MAC addresses to each interface. Over three interfaces, this
           is 384 MAC addresses"""
        src_mac = 0x001120300000
        self.traffic.addUDPMacIncStream(self.config.HOST_LAN0,
                                        self.config.HOST_LAN1,
                                        src_mac, 128, 50, 128, MAC_STEP)
        src_mac = 0x001220300000
        self.traffic.addUDPMacIncStream(self.config.HOST_LAN1,
                                        self.config.HOST_LAN2,
                                        src_mac, 128, 50, 128, MAC_STEP)
        src_mac = 0x001320300000
        self.traffic.addUDPMacIncStream(self.config.HOST_LAN2,
                                        self.config.HOST_LAN0,
                                        src_mac, 128, 50, 128, MAC_STEP)
        self.traffic.run()

        macs_lan0 = self.sut.getFdb(self.config.SUT_LAN0)
        macs_lan1 = self.sut.getFdb(self.config.SUT_LAN1)
        macs_lan2 = self.sut.getFdb(self.config.SUT_LAN2)
        mac_bridge = self.sut.getMacAddress(self.config.SUT_MASTER)

        mac_lan0 = self.traffic.getInterfaceMacAddress(self.config.HOST_LAN0)
        mac_lan1 = self.traffic.getInterfaceMacAddress(self.config.HOST_LAN1)
        mac_lan2 = self.traffic.getInterfaceMacAddress(self.config.HOST_LAN2)

        self.assertTrue(self._check_additional_macs(macs_lan0, mac_lan0,
                                                    mac_bridge,
                                                    '00:11:20:30:00:00',
                                                    128, MAC_STEP),
                        '{0} not in {1}'.format(
                            mac_lan0, macs_lan0))
        self.assertTrue(self._check_additional_macs(macs_lan1, mac_lan1,
                                                    mac_bridge,
                                                    '00:12:20:30:00:00',
                                                    128, MAC_STEP),
                        '{0} not in {1}'.format(
                            mac_lan1, macs_lan1))
        self.assertTrue(self._check_additional_macs(macs_lan2, mac_lan2,
                                                    mac_bridge,
                                                    '00:13:20:30:00:00',
                                                    128, MAC_STEP),
                        '{0} not in {1}'.format(
                            mac_lan2, macs_lan2))

        # Refresh the learning
        self.traffic.learning()

    def test_05_1020_macs(self):
        """Add 340 MAC addresses to each interface. Over three interfaces, this
           is 1020 MAC addresses total"""
        src_mac = 0x001120300000
        self.traffic.addUDPMacIncStream(self.config.HOST_LAN0,
                                        self.config.HOST_LAN1,
                                        src_mac, 340, 100, 340, MAC_STEP)
        src_mac = 0x001220300000
        self.traffic.addUDPMacIncStream(self.config.HOST_LAN1,
                                        self.config.HOST_LAN2,
                                        src_mac, 340, 100, 340, MAC_STEP)
        src_mac = 0x001320300000
        self.traffic.addUDPMacIncStream(self.config.HOST_LAN2,
                                        self.config.HOST_LAN0,
                                        src_mac, 340, 100, 340, MAC_STEP)
        self.traffic.run()

        macs_lan0 = self.sut.getFdb(self.config.SUT_LAN0)
        macs_lan1 = self.sut.getFdb(self.config.SUT_LAN1)
        macs_lan2 = self.sut.getFdb(self.config.SUT_LAN2)
        mac_bridge = self.sut.getMacAddress(self.config.SUT_MASTER)

        mac_lan0 = self.traffic.getInterfaceMacAddress(self.config.HOST_LAN0)
        mac_lan1 = self.traffic.getInterfaceMacAddress(self.config.HOST_LAN1)
        mac_lan2 = self.traffic.getInterfaceMacAddress(self.config.HOST_LAN2)

        self.assertTrue(self._check_additional_macs(macs_lan0, mac_lan0,
                                                    mac_bridge,
                                                    '00:11:20:30:00:00',
                                                    340, MAC_STEP),
                        '{0} not in {1}'.format(
                            mac_lan0, macs_lan0))
        self.assertTrue(self._check_additional_macs(macs_lan1, mac_lan1,
                                                    mac_bridge,
                                                    '00:12:20:30:00:00',
                                                    340, MAC_STEP),
                        '{0} not in {1}'.format(
                            mac_lan1, macs_lan1))
        self.assertTrue(self._check_additional_macs(macs_lan2, mac_lan2,
                                                    mac_bridge,
                                                    '00:13:20:30:00:00',
                                                    340, MAC_STEP),
                        '{0} not in {1}'.format(
                            mac_lan2, macs_lan2))

        # Refresh the learning
        self.traffic.learning()

    def test_06_3072_macs(self):
        """Add 1024 MAC addresses to each interface. Over three interfaces,
           this is 3072 MAC addresses."""

        src_mac = 0x001120300000
        self.traffic.addUDPMacIncStream(self.config.HOST_LAN0,
                                        self.config.HOST_LAN1,
                                        src_mac, 1024, 100, 1024, MAC_STEP)
        src_mac = 0x001220300000
        self.traffic.addUDPMacIncStream(self.config.HOST_LAN1,
                                        self.config.HOST_LAN2,
                                        src_mac, 1024, 100, 1024, MAC_STEP)
        src_mac = 0x001320300000
        self.traffic.addUDPMacIncStream(self.config.HOST_LAN2,
                                        self.config.HOST_LAN0,
                                        src_mac, 1024, 100, 1024, MAC_STEP)
        self.traffic.run()

        macs_lan0 = self.sut.getFdb(self.config.SUT_LAN0)
        macs_lan1 = self.sut.getFdb(self.config.SUT_LAN1)
        macs_lan2 = self.sut.getFdb(self.config.SUT_LAN2)
        mac_bridge = self.sut.getMacAddress(self.config.SUT_MASTER)

        mac_lan0 = self.traffic.getInterfaceMacAddress(self.config.HOST_LAN0)
        mac_lan1 = self.traffic.getInterfaceMacAddress(self.config.HOST_LAN1)
        mac_lan2 = self.traffic.getInterfaceMacAddress(self.config.HOST_LAN2)

        self.assertTrue(self._check_additional_macs(macs_lan0, mac_lan0,
                                                    mac_bridge,
                                                    '00:11:20:30:00:00',
                                                    1024, MAC_STEP),
                        '{0} not in {1}'.format(
                            mac_lan0, macs_lan0))
        self.assertTrue(self._check_additional_macs(macs_lan1, mac_lan1,
                                                    mac_bridge,
                                                    '00:12:20:30:00:00',
                                                    1024, MAC_STEP),
                        '{0} not in {1}'.format(
                            mac_lan1, macs_lan1))
        self.assertTrue(self._check_additional_macs(macs_lan2, mac_lan2,
                                                    mac_bridge,
                                                    '00:13:20:30:00:00',
                                                    1024, MAC_STEP),
                        '{0} not in {1}'.format(
                            mac_lan2, macs_lan2))

        # Refresh the learning
        self.traffic.learning()

    def test_07_atu_full_violation(self):
        """Add 5 MAC addresses which all hash to the same value in the ATU.
           This is one more than it can contain, so should trigger a
           full violation"""

        # 6352
        self.sut.addFdb(self.config.SUT_LAN0, "00:11:20:30:80:00")
        self.sut.addFdb(self.config.SUT_LAN0, "00:11:20:30:a9:66")
        self.sut.addFdb(self.config.SUT_LAN0, "00:11:20:30:d5:4c")
        self.sut.addFdb(self.config.SUT_LAN0, "00:11:20:30:fc:2a")
        self.sut.addFdb(self.config.SUT_LAN0, "00:11:20:31:3c:17")

        # 6390
        self.sut.addFdb(self.config.SUT_LAN0, '00:11:20:30:40:00')
        self.sut.addFdb(self.config.SUT_LAN0, '00:11:20:30:40:c0')
        self.sut.addFdb(self.config.SUT_LAN0, '00:11:20:30:43:c0')
        self.sut.addFdb(self.config.SUT_LAN0, '00:11:20:30:69:66')
        self.sut.addFdb(self.config.SUT_LAN0, '00:11:20:30:6a:a6')

        self.assertTrue(self._check_dmesg_contains('ATU full violation'))
        self.sut.flushFdb()

    def test_08_atu_member_violation(self):
        """Add a static FBD entry lan0. Send a packet with the same source
           address from lan2. This should trigger a member violation"""

        self.sut.addFdb(self.config.SUT_LAN0, '00:11:20:30:80:00')

        src_mac = 0x001120308000
        self.traffic.addUDPMacIncStream(self.config.HOST_LAN2,
                                        self.config.HOST_LAN0,
                                        src_mac, 1, 50, 1, 0)
        self.traffic.run()

        self.assertTrue(self._check_dmesg_contains('ATU member violation'))

        # Flush the Fdb we have added
        self.sut.flushFdb()

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

        # Flush the Fdb we have added
        self.sut.flushFdb()


if __name__ == '__main__':
    ARGS = params.params()
    CONFIG = params.readConfig(ARGS.config)
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

    unittest2.main(buffer=False, testRunner=TESTRUNNER, exit=False)
