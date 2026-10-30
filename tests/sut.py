#!/usr/bin/env python
"""Model the System Under Test"""
import datetime
import pprint
import re

DEBUG = False
STATS_FILES = ['collisions', 'multicast', 'rx_compressed',
               'rx_crc_errors', 'rx_dropped', 'rx_errors', 'rx_fifo_errors',
               'rx_frame_errors', 'rx_length_errors', 'rx_missed_errors',
               'rx_over_errors', 'rx_packets', 'tx_aborted_errors',
               'tx_carrier_errors', 'tx_compressed',
               'tx_dropped', 'tx_errors', 'tx_fifo_errors',
               'tx_heartbeat_errors', 'tx_packets', 'tx_window_errors']


def dbg_print(args):
    """Print debug messages if they are enabled"""
    if DEBUG:
        print(args)


class SUT(object):
    """Class representing the System Under Test"""

    def __init__(self, hostname, key, mgmt):
        import paramiko
        self.mgmt = mgmt or "__no_management__"
        self.sshClient = paramiko.SSHClient()
        self.sshClient.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self.sshClient.connect(hostname, username='root', key_filename=key)
        self.sftpClient = self.sshClient.open_sftp()
        self.exit_code = None
        self.error = ""
        self.interfaces = self.getInterfaces()
        self.fdb = []

    def start_ssh(self, command):
        """Start a command running on the SUT, which is expected
           to be long running. Return a handle to it, so it can later
           be killed/close"""
        transport = self.sshClient.get_transport()
        channel = transport.open_session()
        channel.exec_command(command)

        return channel

    def stop_ssh(self, channel):
        """Stop a command running on the SUT that was started using
           start_ssh()."""
        channel.close()

    def ssh(self, command):
        """Execute a command on the SUT, using SSH"""
        self.exit_code = None
        pattern = re.compile('exit code ([a-z0-9]+)')
        _, stdout, stderr = self.sshClient.exec_command(
            command + '; echo exit code $? ; exit\n')
        results = stdout.read().decode()
        self.error = stderr.read().decode()
        dbg_print(results)
        match = pattern.search(results)
        if match:
            self.exit_code = int(match.group(1))
        return results

    def checkExitCode(self, code):
        """Check the exit code is the expected value"""
        if self.exit_code != code:
            raise NameError('Exit code mismatch {0} != {1}\n{2}'.format(
                self.exit_code, code, self.error))

    def getDmsg(self):
        """Return the recent kernel messages"""
        return self.ssh('dmesg')

    def getInterfaces(self):
        """Return a list of network interface names"""
        interfaces = []
        results = self.ssh('ip link show')
        pattern = re.compile('[0-9]+: ([a-z0-9_]+)')
        for line in results.splitlines():
            match = pattern.match(line)
            if match:
                interfaces.append(match.group(1))
                self.interfaces = interfaces
                self.checkExitCode(0)
        return interfaces

    def getBridges(self):
        """Return a list of bridge interface names"""
        bridges = []
        interfaces = self.getInterfaces()
        for interface in interfaces:
            if interface.startswith('br'):
                bridges.append(interface)
        return bridges

    def getBonds(self):
        """Return a list of bond interface names"""
        bonds = []
        interfaces = self.getInterfaces()
        for interface in interfaces:
            if interface.startswith('bond'):
                bonds.append(interface)
        return bonds

    def up(self, interface):
        """Set an interface up"""
        if interface not in self.interfaces:
            raise NameError('Up called for unknown interface')
        result = self.ssh('ip link set {0} up'.format(interface))
        if 'RTNETLINK answers' in result:
            raise NameError('Up failed')
        if 'Cannot find device' in result:
            raise NameError('Up failed, no such device')
        self.checkExitCode(0)

    def down(self, interface):
        """Set an interface down"""
        if interface not in self.interfaces:
            raise NameError('Down called for unknown interface')
        result = self.ssh('ip link set {0} down'.format(interface))
        if 'RTNETLINK answers' in result:
            raise NameError('Down failed')
        if 'Cannot find device' in result:
            raise NameError('Down failed, no such device')
        self.checkExitCode(0)

    def addAddress(self, interface, address):
        """Add an address to an interface"""
        if interface not in self.interfaces:
            raise NameError('addAddress called for unknown interface')
        self.ssh('ip addr add {0} dev {1}'.format(
            address, interface))
        self.checkExitCode(0)

    def delAddress(self, interface, address):
        """Delete an address from an interface"""
        if interface not in self.interfaces:
            raise NameError('addAddress called for unknown interface')
        self.ssh('ip addr del {0} dev {1}'.format(
            address, interface))
        self.checkExitCode(0)

    def setMacAddress(self, interface, address):
        """Set the MAC address on an interface"""
        if interface not in self.interfaces:
            raise NameError('setMacAddress called for unknown interface')
        self.ssh('ip link set address {0} dev {1}'.format(
            address, interface))
        self.checkExitCode(0)

    def getMacAddress(self, interface):
        """Get the MAC address on an interface"""
        if interface not in self.interfaces:
            raise NameError('getMacAddress called for unknown interface')
        pattern = re.compile(
            ' *link/ether ((?:[a-f0-9][a-f0-9]:){5}[a-f0-9][a-f0-9]).*')
        results = self.ssh('ip link show dev {0}'.format(interface))
        for line in results.splitlines():
            match = pattern.match(line)
            if match:
                return match.group(1)
        raise NameError('No MAC address for interface {0}'.format(
            interface))

    def flushAddresses(self, interface):
        """Remove all addresses from an interface"""
        if interface not in self.interfaces:
            raise NameError('flushAddresses called for unknown interface')
        self.ssh('ip addr flush dev {0}'.format(interface))
        self.checkExitCode(0)


    def deleteBridge(self, bridge):
        """Destroy the given bridge"""
        if bridge not in self.interfaces:
            raise NameError('deleteBridge called for unknown interface')
        if bridge not in self.getBridges():
            raise NameError('deleteBridge called for unknown bridge')
        self.down(bridge)
        self.ssh('brctl delbr {0}'.format(bridge))
        self.getInterfaces()
        self.checkExitCode(0)

    def addBridge(self, bridge):
        """Create the given bridge. Set the forwarding delay to 1 second, so
           we don't have to wait too long before the bridge starts
           forwaring packets"""
        if bridge in self.interfaces:
            raise NameError('addBridge called for known interface')
        if bridge in self.getBridges():
            raise NameError('addBridge called for known bridge')
        self.ssh('brctl addbr {0}'.format(bridge))
        self.getInterfaces()
        self.checkExitCode(0)
        self.ssh('brctl setfd {0} 2'.format(bridge))
        self.checkExitCode(0)

    def addBridgeIgmpQuerier(self, bridge):
        """Enable the bridge to perform IGMP queries"""
        if bridge not in self.getBridges():
            raise NameError('addBridgeIgmpQuerier called for unknown bridge')
        self.ssh('ip link set {0} type bridge mcast_querier 1'.format(bridge))
        self.ssh("ip link set {0} type bridge mcast_querier_interval 600".
                 format(bridge))

    def addBridgeInterface(self, bridge, interface):
        """Add an interface to a bridge"""
        if bridge not in self.interfaces:
            raise NameError('addBridgeInterface called for unknown bridge')
        if bridge not in self.getBridges():
            raise NameError('addBridgeInterface called for unknown bridge')
        if interface not in self.interfaces:
            raise NameError('addBridgeInterface called for unknown interface')
        self.ssh('brctl addif {0} {1}'.format(bridge, interface))
        self.checkExitCode(0)

    def deleteBridgeInterface(self, bridge, interface):
        """Delete an interface from a bridge"""
        if bridge not in self.interfaces:
            raise NameError('deleteBridgeInterface called for unknown bridge')
        if bridge not in self.getBridges():
            raise NameError('deleteBridgeInterface called for unknown bridge')
        if interface not in self.interfaces:
            raise NameError(
                'deleteBridgeInterface called for unknown interface')
        self.ssh('brctl delif {0} {1}'.format(bridge, interface))
        self.checkExitCode(0)

    def bridgeEnableVlanFiltering(self, bridge):
        """Enable VLAN filtering on the bridge"""
        if bridge not in self.interfaces:
            raise NameError('deleteBridgeInterface called for unknown bridge')
        if bridge not in self.getBridges():
            raise NameError('deleteBridgeInterface called for unknown bridge')
        self.ssh('echo 1 >/sys/class/net/{0}/bridge/vlan_filtering'.format(
            bridge))
        self.checkExitCode(0)

    def bridgeDisableVlanFiltering(self, bridge):
        """Disable VLAN filtering on the bridge"""
        if bridge not in self.interfaces:
            raise NameError('deleteBridgeInterface called for unknown bridge')
        if bridge not in self.getBridges():
            raise NameError('deleteBridgeInterface called for unknown bridge')
        self.ssh('echo 0 >/sys/class/net/{0}/bridge/vlan_filtering'.format(
            bridge))
        self.checkExitCode(0)

    def getFdb(self, interface):
        """Return a list of fdb entries on the given interface"""
        if interface not in self.interfaces:
            raise NameError('getFdb called for unknown interface')
        macs = []
        results = self.ssh('bridge fdb show dev {0}'.format(interface))
        pattern = re.compile('((?:[a-f0-9][a-f0-9]:){5}[a-f0-9][a-f0-9]) self')
        for line in results.splitlines():
            match = pattern.match(line)
            if match:
                macs.append(match.group(1))
        return macs

    def addFdb(self, interface, address):
        """Add a static fdb entry on the interface"""
        if interface not in self.interfaces:
            raise NameError('addFdb called for unknown interface')
        self.ssh('bridge fdb add {0} dev {1}'.format(address, interface))
        self.checkExitCode(0)
        self.fdb.append((interface, address))

    def delFdb(self, interface, address):
        """Add a static fdb entry on the interface"""
        if interface not in self.interfaces:
            raise NameError('addFdb called for unknown interface')
        self.ssh('bridge fdb del {0} dev {1}'.format(address, interface))
        self.checkExitCode(0)
        self.fdb.remove((interface, address))

    def flushFdb(self):
        """Flush all static fdb entries"""
        for entry in self.fdb:
            interface = entry[0]
            address = entry[1]
            self.ssh('bridge fdb del {0} dev {1}'.format(address, interface))
        self.checkExitCode(0)
        self.fdb = []

    def getFdbStats(self):
        """Get the ATU statistics"""
        pattern = re.compile(
            ' +0 +all +([0-9]+) +([0-9]+) +([0-9]+) +([0-9]+) +([0-9]+)')
        results = self.ssh('cat /sys/kernel/debug/mv88e6xxx/sw0/atu/0-stats')
        for line in results.splitlines():
            match = pattern.match(line)
            if match:
                return (int(match.group(1)),
                        int(match.group(2)),
                        int(match.group(3)),
                        int(match.group(4)),
                        int(match.group(5)))
        return None

    def _statsCheckRange(self, before, after, _range, unittest):
        """Perform the check that the statistics are within range"""
        delta = {}
        for key in after.keys():
            delta[key] = after[key] - before[key]
        for key in _range.keys():
            unittest.assertTrue(delta[key] >= _range[key][0],
                                '{0}={1} not between {2}-{3}\n{4}'.format(
                                    key, delta[key],
                                    _range[key][0], _range[key][1],
                                    pprint.pformat(delta)))
            unittest.assertTrue(delta[key] <= _range[key][1],
                                '{0}={1} not between {2}-{3}\n{4}'.format(
                                    key, delta[key],
                                    _range[key][0], _range[key][1],
                                    pprint.pformat(delta)))

    def _statsCheckRangeOne(self, key, value, _range):
        """Return true if the value with within range"""
        if key not in _range.keys():
            return False
        if value < _range[key][0]:
            return False
        if value > _range[key][1]:
            return False
        return True

    def _statsCheckRangeFail(self, key, value, range1, range2, unittest):
        """Test has failed, report why"""
        if key in range1.keys() and key in range2.keys():
            unittest.fail('{0}={1} not between {2}-{3} or {4}-{5}'.format(
                key, value,
                range1[key][0], range1[key][1],
                range2[key][0], range2[key][1]))
            return

        if key in range1.keys():
            unittest.fail('{0}={1} not between {2}-{3}'.format(
                key, value,
                range1[key][0], range1[key][1]))
            return

        if key in range2.keys():
            unittest.fail('{0}={1} not between {2}-{3}'.format(
                key, value,
                range2[key][0], range2[key][1]))
            return

    def _statsCheckRangeOr(self, before, after, range1, range2, unittest):
        """Perform the check that the statistics are within one of the other
           range"""
        delta = {}
        for key in after.keys():
            delta[key] = after[key] - before[key]
        for key in range1.keys():
            if not self._statsCheckRangeOne(key, delta[key], range1) and \
               not self._statsCheckRangeOne(key, delta[key], range2):
                self._statsCheckRangeFail(key, delta[key], range1, range2,
                                          unittest)

        for key in range2.keys():
            if not self._statsCheckRangeOne(key, delta[key], range1) and \
               not self._statsCheckRangeOne(key, delta[key], range2):
                self._statsCheckRangeFail(key, delta[key], range1, range2,
                                          unittest)

    def getEthtoolStats(self, interface):
        """Get the Ethtool statistics from an interface.

           NOTE: These statistics are not standardised in any way. The
           names will differ from driver to driver. It is best to use
           these for information only."""
        stats = {}
        pattern = re.compile('(.+): ([0-9]+)')
        if interface not in self.interfaces:
            raise NameError(
                'getEthtoolStats called for unknown interface')
        results = self.ssh('ethtool -S {0}'.format(interface))
        for line in results.splitlines():
            match = pattern.match(line)
            if match:
                key = match.group(1).strip()
                value = int(match.group(2))
                stats[key] = value
        return stats

    def checkEthtoolStatsRange(self, interface, before, _range, unittest):
        """Check that the stats have incremented within the expect range."""
        after = self.getEthtoolStats(interface)
        self._statsCheckRange(before, after, _range, unittest)

    def checkEthtoolStatsRangeOr(self, interface, before, range1, range2,
                                 unittest):
        """Check that the stats have incremented within one of the expect
           ranges."""
        after = self.getEthtoolStats(interface)
        self._statsCheckRangeOr(before, after, range1, range2, unittest)

    def _getNumberContents(self, filename):
        """Return the contents of the file"""
        pattern = re.compile('([a-z0-9]+).*')
        result = self.ssh('cat {0}'.format(filename))
        match = pattern.match(result)
        if match:
            return int(match.group(1))
        return None

    def getClassStats(self, interface):
        """Get the interface class stats.

           These values are standardized, so should be portable
           between drivers. We will see..."""
        stats = {}
        if interface not in self.interfaces:
            raise NameError(
                'getClassStats called for unknown interface')
        for stat in STATS_FILES:
            value = self._getNumberContents(
                '/sys/class/net/{0}/statistics/{1}'.format(interface, stat))
            stats[stat] = value
        return stats

    def checkClassStatsRange(self, interface, before, _range, unittest):
        """Check that the stats have incremented within the expect range."""
        after = self.getClassStats(interface)
        self._statsCheckRange(before, after, _range, unittest)

    def phcGet(self, interface):
        """Get the time from a Precision Hardware Counter"""
        results = self.ssh('phc_ctl {0} get'.format(interface))
        self.checkExitCode(0)
        pattern = re.compile(r'phc_ctl\[.*\]: clock time is ([0-9]*.[0-9]*) or (.*)$')
        for line in results.splitlines():
            match = pattern.match(line)
            if match:
                seconds = float(match.group(1))
                date = datetime.datetime.strptime(match.group(2), "%c")
                return seconds, date

    def phcSet(self, interface, seconds):
        """Set the time on a Precision Hardware Counter"""
        if seconds:
            self.ssh('phc_ctl {0} set {1}'.format(interface, seconds))
        else:
            self.ssh('phc_ctl {0} set'.format(interface))
        self.checkExitCode(0)

    def serviceStart(self, service):
        """Start a systemd service running on the SUT"""
        self.ssh('systemctl start {0}'.format(service))
        self.checkExitCode(0)

    def serviceStop(self, service):
        """Start a systemd service running on the SUT"""
        self.ssh('systemctl stop {0}'.format(service))

    def sftpPut(self, src, dst):
        """Copy the src file to the sut"""
        self.sftpClient.put(src, dst)

    def cleanSystem(self):
        """Clean the system i.e. remove all bridges, check there are no bonds,
           put all interfaces down"""
        bonds = self.getBonds()
        bridges = self.getBridges()
        interfaces = self.getInterfaces()
        interfaces = [interface for interface in interfaces if
                      (interface.startswith('lan') or
                       interface.startswith('optical') or
                       interface.startswith('net') or
                       interface.startswith('port') or
                       interface.startswith('eth_cu')) and
                      not interface.startswith(self.mgmt)]

        if len(bonds):
            #  Todo: Implement cleanup of bonds
            raise NameError('SUT has some bonds: {0}'.format(bonds))
        self.flushFdb()
        for bridge in bridges:
            self.deleteBridge(bridge)
        for interface in interfaces:
            self.flushAddresses(interface)
            self.down(interface)
