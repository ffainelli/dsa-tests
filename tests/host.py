#!/usr/bin/env python
"""Model the Test Host"""
import os
import shlex
import subprocess
import socket
import struct
import sys
import select
import host_netns

DEBUG = False


def dbg_print(args):
    """Print debug messages if they are enabled"""
    if DEBUG:
        print(args)


class HOST(object):
    """Class representing the Host. Some of these methods require root
       access"""

    def __init__(self):
        """Check that we have root permissions, otherwise methods here
           are going to fail."""
        self.interfaces = []
        self.groups = []
        if os.geteuid() != 0:
            exit("You need to have root privileges to run this script.\n"
                 "Please try again, this time using 'sudo'. Exiting.")
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM,
                                  socket.IPPROTO_UDP)

    def _check_call(self, command):
        """Call the given command, breaking the string up on spaces.
           Will go horribly wrong for quoted strings etc."""
        args = host_netns.command(shlex.split(command))
        subprocess.check_call(args)

    def _call(self, command, interface=None):
        """Call the given command, breaking the string up on spaces.
           Will go horribly wrong for quoted strings etc."""
        null = open('/dev/null', 'w')
        args = host_netns.command(shlex.split(command), interface=interface)
        return subprocess.call(args, stdin=None, stdout=null, stderr=null)

    def _communicate(self, command, interface=None):
        """Execute a command and return a tuple of (stdout, stderr)"""
        args = host_netns.command(shlex.split(command), interface=interface)
        pipe = subprocess.Popen(args, stdin=None, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True)
        return pipe.communicate()

    def addInterface(self, interface):
        """Register a host interface to be used"""
        if interface not in self.interfaces:
            self.interfaces.append(interface)

    def addAddress(self, interface, address):
        """Add an address to an interface"""
        if interface not in self.interfaces:
            raise NameError('addAddress called for unknown interface')
        self._check_call('ip addr add {0} dev {1}'.format(address, interface))
        host_netns.address_added(interface, address)

    def delAddress(self, interface, address):
        """Delete an address from an interface"""
        if interface not in self.interfaces:
            raise NameError('delAddress called for unknown interface')
        self._check_call('ip addr del {0} dev {1}'.format(
            address, interface))

    def join(self, interface, address, group):
        """Join the multicast group on the interface"""
        if interface not in self.interfaces:
            raise NameError('join called for unknown interface')
        process = None
        if interface in host_netns.INTERFACES:
            # Create the membership socket in the client's network stack.
            code = ("import socket,struct,sys; "
                    "s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM,socket.IPPROTO_UDP); "
                    "s.setsockopt(socket.IPPROTO_IP,socket.IP_ADD_MEMBERSHIP,"
                    "struct.pack('4s4s',socket.inet_aton(sys.argv[1]),socket.inet_aton(sys.argv[2]))); "
                    "print('READY',flush=True); sys.stdin.buffer.read()")
            process = subprocess.Popen(host_netns.command(
                [sys.executable, '-u', '-c', code, group, address], interface=interface),
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            ready, _, _ = select.select([process.stdout], [], [], 5)
            if not ready or process.stdout.readline().strip() != b'READY':
                process.kill()
                _, error = process.communicate()
                raise RuntimeError('Multicast join failed: ' + error.decode(errors='replace'))
        else:
            mreq = struct.pack("4s4s", socket.inet_aton(group), socket.inet_aton(address))
            self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        record = {'interface': interface,
                  'address': address,
                  'group': group}
        if process is not None:
            record['process'] = process
        self.groups.append(record)

    def leave(self, interface, address, group):
        """Leave the multicast group on the interface"""
        if interface not in self.interfaces:
            raise NameError('join called for unknown interface')
        record = {'interface': interface,
                  'address': address,
                  'group': group}
        for group in self.groups:
            if all(group.get(key) == value for key, value in record.items()):
                if 'process' in group:
                    process = group['process']
                    try:
                        process.communicate(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.communicate()
                    self.groups.remove(group)
                    return
                mreq = struct.pack("4s4s",
                                   socket.inet_aton(record['group']),
                                   socket.inet_aton(record['address']))
                self.sock.setsockopt(socket.IPPROTO_IP,
                                     socket.IP_DROP_MEMBERSHIP,
                                     mreq)
                self.groups.remove(record)
                return
        raise NameError('leave for non-join group {0} {1} {2}'.format(
            interface, address, group))

    def leave_all(self):
        """Leave all multicast groups"""
        for group in self.groups[:]:
            self.leave(group['interface'], group['address'], group['group'])

    def up(self, interface):
        """Set an interface up"""
        if interface not in self.interfaces:
            raise NameError('up called for unknown interface')
        self._check_call('ip link set {0} up'.format(interface))

    def down(self, interface):
        """Set an interface down"""
        if interface not in self.interfaces:
            raise NameError('down called for unknown interface')
        self._check_call('ip link set {0} down'.format(interface))

    def ping(self, address, interface=None):
        """Ping the given address two times. Return True if we receive
           replies, or False if there is no answer"""
        ret = self._call('ping -c 1 -w 10 {0}'.format(address), interface=interface)
        if ret == 0:
            return True
        return False

    def pingbig(self, address, interface=None):
        """Ping the given address two times, using a big packet, so testing
           for MTU issues. Return True if we receive replies, or False
           if there is no answer"""
        ret = self._call('ping -c 1 -w 10 -s 2048 {0}'.format(address), interface=interface)
        if ret == 0:
            return True
        return False

    def pingdown(self, address, interface=None):
        """Test the address is down. Don't wait around long, since we don't
           expect an answer. Return True if we receive replies, or
           False if there is no answer"""
        ret = self._call('ping -c 1 -W 1 {0}'.format(address), interface=interface)
        if ret == 0:
            return True
        return False

    def arpGet(self, address, interface=None):
        """Return the MAC address ARP has determined for a given IP address"""
        stdout, _ = self._communicate('arp {0}'.format(address), interface=interface)
        for line in stdout.splitlines():
            words = line.split()
            if words[0] == address and words[1] == 'ether':
                return words[2]
        return None

    def arpDel(self, address):
        """Delete the ARP cache entry for the given IP address"""
        self._check_call('arp -d {0}'.format(address))

    def ndDel(self, address, interface):
        """Delete a neighbor discovery cache entry for the given IP address"""
        self._check_call('ip -6 neigh del {0} dev {1}'.format(address,
                                                              interface))

    def cleanSystem(self):
        """Remove all addresses from the test interfaces, ensure they are
           all up. Remove any multicast memberships"""
        self.leave_all()
        for interface in self.interfaces:
            host_netns.ADDRESSES.pop(interface, None)
            self.up(interface)
            self._check_call('ip addr flush dev {0}'.format(interface))
