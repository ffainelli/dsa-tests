#!/usr/bin/env python
"""Model the Test Host"""
import os
import subprocess

DEBUG = False


def dbg_print(args):
    """Print debug messages if they are enabled"""
    if DEBUG:
        print args


class HOST(object):
    """Class representing the Host. Some of these methods require root
       access"""

    def __init__(self):
        """Check that we have root permissions, otherwise methods here
           are going to fail."""
        self.interfaces = []
        if os.geteuid() != 0:
            exit("You need to have root privileges to run this script.\n"
                 "Please try again, this time using 'sudo'. Exiting.")

    def _check_call(self, command):
        """Call the given command, breaking the string up on spaces.
           Will go horribly wrong for quoted strings etc."""
        args = command.split(' ')
        subprocess.check_call(args)

    def _call(self, command):
        """Call the given command, breaking the string up on spaces.
           Will go horribly wrong for quoted strings etc."""
        null = open('/dev/null', 'w')
        args = command.split(' ')
        return subprocess.call(args, stdin=None, stdout=null, stderr=null)

    def addInterface(self, interface):
        """Register a host interface to be used"""
        self.interfaces.append(interface)

    def addAddress(self, interface, address):
        """Add an address to an interface"""
        if interface not in self.interfaces:
            raise NameError('addAddress called for unknown interface')
        self._check_call('ip addr add {0} dev {1}'.format(address, interface))

    def delAddress(self, interface, address):
        """Delete an address from an interface"""
        if interface not in self.interfaces:
            raise NameError('delAddress called for unknown interface')
        self._check_call('ip addr del {0} dev {1}'.format(
            address, interface))

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

    def ping(self, address):
        """Ping the given address two times. Return True if we receive
           replies, or False if there is no answer"""
        ret = self._call('ping -c 2 -W 1 {0}'.format(address))
        if ret == 0:
            return True
        return False

    def cleanSystem(self):
        """Remove all addresses from the test interfaces, ensure they are
           all up"""
        for interface in self.interfaces:
            self.up(interface)
            self._check_call('ip addr flush dev {0}'.format(interface))
