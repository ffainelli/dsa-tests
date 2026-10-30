# SPDX-License-Identifier: GPL-2.0
"""Independent split-host/UART scenarios derived from Linux switch selftests."""
import json
from pathlib import Path
import shlex
import re
import signal
import subprocess
import host_netns
import time
import unittest
import uuid

from lab_support import run
from traffic import Traffic, pcap_frames, vlan_stack


def icmp_echo_reply(frame, version):
    """Recognize echo replies after Ethernet and any 802.1Q/802.1ad headers."""
    offset = 14 + 4 * len(vlan_stack(frame))
    ethertype = frame[offset - 2:offset]
    if version == 4 and ethertype == b'\x08\x00' and len(frame) >= offset + 20:
        ihl = (frame[offset] & 15) * 4
        return (frame[offset] >> 4 == 4 and ihl >= 20 and
                frame[offset + 9] == 1 and len(frame) >= offset + ihl + 8 and
                frame[offset + ihl:offset + ihl + 2] == b'\x00\x00')
    if version == 6 and ethertype == b'\x86\xdd' and len(frame) >= offset + 48:
        return (frame[offset] >> 4 == 6 and frame[offset + 6] == 58 and
                frame[offset + 40:offset + 42] == b'\x81\x00')
    return False


class SwitchCase(unittest.TestCase):
    ENV = None

    def setUp(self):
        self.sut, self.config, self.output = self.ENV
        self.ports = [self.config['SUT_LAN%d' % i] for i in range(4)]
        self.hosts = [self.config['HOST_LAN%d' % i] for i in range(4)]
        self.token = uuid.uuid4().hex[:6]
        self.br = 'dsab' + self.token
        self.folder = Path(self.output) / self._testMethodName
        self.folder.mkdir(parents=True)
        self.commands = []
        self.addCleanup(self.sut.cleanSystem)
        self.sut.cleanSystem()
        self.sut.up(self.config.SUT_MASTER)
        for port, host in zip(self.ports, self.hosts):
            self.sut.up(port)
            run('ip', 'link', 'set', 'dev', host, 'up')
        self.traffic = Traffic(self.hosts, self.folder / 'captures')
        for host in self.hosts:
            self.traffic.addInterface(host)
        self.wait_links()

    def wait_links(self):
        self.cmd('for i in $(seq 1 15); do ready=1; for p in %s; do '
                 'test "$(cat /sys/class/net/$p/carrier)" = 1 || ready=0; done; '
                 'test "$ready" = 1 && exit 0; sleep 1; done; exit 1' % " ".join(self.ports))

    def attempt(self, command, timeout=60):
        code, output = self.sut.uart.command(command, timeout)
        self.commands.append(dict(command=command, status=code, output=output))
        (self.folder / 'commands.json').write_text(json.dumps(self.commands, indent=2) + '\n')
        return code, output

    def cmd(self, command, optional=False):
        code, output = self.attempt(command)
        if code and optional and (command.startswith('command -v ') or any(s in output.lower() for s in (
                'operation not supported', 'not supported', 'unknown command', 'unknown option',
                'unknown argument', 'qdisc kind is unknown', 'unknown qdisc',
                'unknown filter', 'does not support offload', 'garbage'))):
            self.skipTest('Unsupported optional capability: %s: %s' % (command, output))
        self.assertEqual(code, 0, '%s: %s' % (command, output))
        return output

    def read_json(self, command, optional=False):
        output = self.cmd(command, optional)
        try:
            return json.loads(output)
        except json.JSONDecodeError:
            # printk may interrupt a JSON line at any byte. The raw transcript
            # and command artifact retain it; only parsing removes timestamped
            # kernel messages. A second parse error remains a test error.
            clean = re.sub(r'\[\s*\d+\.\d+\][^\r\n]*(?:\r\n|\r|\n|$)', '', output)
            return json.loads(clean)

    def bridge(self, filtering=0, members=(0, 1, 2), snooping=0, default_pvid=1):
        self.sut.addBridge(self.br)
        self.cmd('ip link set dev %s type bridge vlan_filtering %d mcast_snooping %d vlan_default_pvid %d' %
                 (self.br, filtering, snooping, default_pvid))
        self.sut.up(self.br)
        for i in members:
            self.sut.addBridgeInterface(self.br, self.ports[i])
        return self.br

    def vlan(self, index, vid, flags=''):
        self.cmd('bridge vlan add dev %s vid %d %s' % (self.ports[index], vid, flags))

    def probe(self, source=0, receivers=(1, 2), tags=(), out_tags=None,
              destination=0xffffffffffff, version=4, ethertype=0, source_mac=None,
              destination_ip=None):
        self.traffic.addPacketStream(self.hosts[source], destination, version=version,
            tags=tags, ethertype=ethertype, source_mac=source_mac, destination_ip=destination_ip)
        self.traffic.run()
        for i, host in enumerate(self.hosts):
            expected = 5 if i in receivers else 0
            self.assertEqual(self.traffic.getStats(host)['rx_pkts'], expected,
                             'unexpected reception on %s' % self.ports[i])
            if i in receivers and out_tags is not None:
                expected_tags = out_tags[i] if isinstance(out_tags, dict) else out_tags
                self.assertEqual(self.traffic.tag_stats[(host, 'in')], [tuple(expected_tags)] * 5,
                                 'wrong egress VLAN encoding on ' + self.ports[i])

    def new_link(self, lower, kind, args='', host=False):
        name = ('dsah' if host else 'dsau') + uuid.uuid4().hex[:6]
        command = 'ip link add link %s name %s type %s %s' % (lower, name, kind, args)
        if host:
            host_netns.register_upper(name, lower)
            run(*shlex.split(command))
            self.addCleanup(run, 'ip', 'link', 'del', 'dev', name)
            run('ip', 'link', 'set', 'dev', name, 'up')
        else:
            self.cmd(command)
            self.addCleanup(self.cmd, 'ip link del dev ' + name)
            self.cmd('ip link set dev %s up' % name)
            self.sut.getInterfaces()
        return name

    def local_ping(self, lower, host_index=0, vid=None, version=4, allowed=True, size=56, host_device=None, subnet=2,
                   addresses=None, assign_board=True):
        """CPU termination: ICMP plus an independent host tcpdump reply check."""
        host = host_device or self.hosts[host_index]
        if vid is not None and host_device is None:
            host = self.new_link(host, 'vlan', 'id %d' % vid, host=True)
        board_ip, host_ip, prefix = (('192.0.%d.2' % subnet, '192.0.%d.1' % subnet, 24) if version == 4 else
                                    ('2001:db8:%x::2' % subnet, '2001:db8:%x::1' % subnet, 64))
        if addresses is not None:
            board_ip, host_ip, prefix = addresses
        if assign_board:
            self.cmd('ip addr add %s/%d dev %s' % (board_ip, prefix, lower))
            self.addCleanup(self.cmd, 'ip addr del %s/%d dev %s' % (board_ip, prefix, lower))
        run('ip', 'addr', 'add', '%s/%d' % (host_ip, prefix), 'dev', host)
        host_address_present = True
        def remove_host_address():
            nonlocal host_address_present
            if host_address_present:
                run('ip', 'addr', 'del', '%s/%d' % (host_ip, prefix), 'dev', host)
                host_address_present = False
        self.addCleanup(remove_host_address)
        try:
            time.sleep(2)  # IPv6 duplicate-address detection
            stem = 'icmp-ipv%d-%s-net%d-size%d' % (version, host, subnet, size)
            path = self.folder / (stem + '.pcap')
            logpath = self.folder / (stem + '-tcpdump.log')
            with logpath.open('wb') as log:
                proc = subprocess.Popen(host_netns.command(['tcpdump', '--immediate-mode', '-p', '-i', host, '-Q', 'in',
                    '-n', '-s', '0', '-U', '-w', str(path), 'src', 'host', board_ip], interface=host), stderr=log,
                    stdout=subprocess.DEVNULL)
                try:
                    deadline = time.monotonic() + 5
                    while b'listening on' not in logpath.read_bytes():
                        if proc.poll() is not None or time.monotonic() > deadline:
                            self.fail('CPU tcpdump failed to start')
                        time.sleep(.02)
                    result = subprocess.run(host_netns.command(['ping', '-%d' % version, '-n', '-I', host, '-c', '3',
                        '-W', '1', '-w', '5', '-s', str(size), board_ip], interface=host), capture_output=True, text=True, timeout=10)
                    (self.folder / (stem + '-ping.log')).write_text(result.stdout + result.stderr)
                    time.sleep(.2)
                finally:
                    if proc.poll() is None:
                        proc.send_signal(signal.SIGINT)
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill(); proc.wait()
                        self.fail('CPU tcpdump failed to stop')
            self.assertEqual(proc.returncode, 0)
            self.assertIn('0 packets dropped by kernel', logpath.read_text())
            self.assertEqual(result.returncode == 0, allowed, result.stdout + result.stderr)
            # Check ICMP echo replies, excluding ARP/ND and incoming echo requests.
            replies = [frame for frame in pcap_frames(path) if icmp_echo_reply(frame, version)]
            self.assertEqual(bool(replies), allowed, 'ICMP reply capture disagrees with expectation')
        finally:
            remove_host_address()
