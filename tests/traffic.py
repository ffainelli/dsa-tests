#!/usr/bin/env python3
"""Finite mz streams and directional tcpdump captures; no traffic daemon.

The historical Traffic API is retained so the original tests exercise the new
backend. Only packets carrying this run's random signature enter statistics.
"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import ipaddress
import json
from pathlib import Path
import shutil
import signal
import struct
import subprocess
import host_netns
import time
import uuid


def checksum(data):
    data += b'\0' * (len(data) % 2)
    total = sum(struct.unpack('!%dH' % (len(data) // 2), data))
    while total >> 16:
        total = (total & 65535) + (total >> 16)
    return (~total) & 65535


def mac_bytes(value):
    if isinstance(value, str):
        value = int(value.replace(':', '').replace('-', ''), 16)
    return value.to_bytes(6, 'big')


def mac_text(value):
    return ':'.join('%02x' % x for x in mac_bytes(value))


def udp_frame(src_mac, dst_mac, src_ip, dst_ip, signature):
    """Build a 128-byte Ethernet/UDP frame with valid v4/v6 checksums."""
    src, dst = ipaddress.ip_address(src_ip), ipaddress.ip_address(dst_ip)
    if src.version != dst.version:
        raise ValueError('IP version mismatch')
    size = 20 if src.version == 4 else 40
    payload = signature.ljust(128 - 14 - size - 8, b'\0')
    udp = struct.pack('!HHHH', 0x1234, 0x4321, 8 + len(payload), 0) + payload
    if src.version == 4:
        pseudo = src.packed + dst.packed + struct.pack('!BBH', 0, 17, len(udp))
        ip = struct.pack('!BBHHHBBH4s4s', 0x45, 0, size + len(udp), 0, 0, 64,
                         17, 0, src.packed, dst.packed)
        ip = ip[:10] + struct.pack('!H', checksum(ip)) + ip[12:]
        ethertype = 0x0800
    else:
        pseudo = src.packed + dst.packed + struct.pack('!I3xB', len(udp), 17)
        ip = struct.pack('!IHBB16s16s', 6 << 28, len(udp), 17, 64, src.packed, dst.packed)
        ethertype = 0x86dd
    udp = udp[:6] + struct.pack('!H', checksum(pseudo + udp) or 65535) + udp[8:]
    return mac_bytes(dst_mac) + mac_bytes(src_mac) + struct.pack('!H', ethertype) + ip + udp


def pcap_frames(path):
    """Read classic Ethernet pcap, rejecting truncated/unsupported captures."""
    with Path(path).open('rb') as capture:
        header = capture.read(24)
        if len(header) != 24:
            raise RuntimeError('Missing pcap header: %s' % path)
        endian = {b'\xd4\xc3\xb2\xa1': '<', b'\xa1\xb2\xc3\xd4': '>',
                  b'\x4d\x3c\xb2\xa1': '<', b'\xa1\xb2\x3c\x4d': '>'}.get(header[:4])
        if endian is None or struct.unpack(endian + 'I', header[20:24])[0] != 1:
            raise RuntimeError('Expected classic Ethernet pcap: %s' % path)
        while record := capture.read(16):
            if len(record) != 16:
                raise RuntimeError('Truncated pcap record')
            _, _, length, wire_length = struct.unpack(endian + 'IIII', record)
            if length > 65535:
                raise RuntimeError('Invalid pcap packet length')
            frame = capture.read(length)
            if len(frame) != length or length != wire_length:
                raise RuntimeError('Truncated pcap packet')
            yield frame


def count_pcap(path, signature):
    return sum(signed_frame(frame, signature) for frame in pcap_frames(path))


def vlan_stack(frame):
    """Return outer-to-inner (TPID, TCI) values without losing PCP/DEI."""
    result, offset = [], 12
    while len(frame) >= offset + 6:
        tpid, tci = struct.unpack('!HH', frame[offset:offset + 4])
        if tpid not in (0x8100, 0x88a8):
            break
        result.append((tpid, tci))
        offset += 4
    return tuple(result)


def signed_frame(frame, signature):
    """Match our actual UDP/raw payload, excluding ICMP quotations of it."""
    offset = 14 + 4 * len(vlan_stack(frame))
    if len(frame) < offset:
        return False
    ethertype = int.from_bytes(frame[offset - 2:offset], 'big')
    if ethertype <= 1500:
        # Test markers live after the declared 802.3 payload, in padding.
        # They must not change an LLC/BPDU body or make a short PDU valid.
        return frame[offset + ethertype:].startswith(signature)
    if ethertype == 0x0800:
        if len(frame) < offset + 20 or frame[offset + 9] != 17:
            return False
        offset += (frame[offset] & 15) * 4
    elif ethertype == 0x86dd:
        if len(frame) < offset + 40 or frame[offset + 6] != 17:
            return False
        offset += 40
    else:
        return frame[offset:].startswith(signature)
    return frame[offset:offset + 4] == b'\x12\x34\x43\x21' and frame[offset + 8:].startswith(signature)


def tag_frame(frame, tags):
    for tpid, tci in reversed(tags):
        if tpid not in (0x8100, 0x88a8) or not 0 <= tci <= 65535:
            raise ValueError('Invalid VLAN TPID/TCI')
        frame = frame[:12] + struct.pack('!HH', tpid, tci) + frame[12:]
    return frame


@dataclass
class Stream:
    source: str
    destination_mac: int
    destination_ip: str
    count: int
    rate: float
    source_mac: int
    version: int = 4
    mac_count: int = 0
    mac_step: int = 1
    tags: tuple = ()
    ethertype: int = 0
    ethernet: str = ''


class Traffic:
    def __init__(self, interfaces=None, output='captures', mz=None, prefix=None):
        self.interfaces = list(interfaces or [])
        self.addedInterfaces = []
        self.output = Path(output)
        self.output.mkdir(parents=True, exist_ok=True)
        self.mz = mz or shutil.which('mz') or shutil.which('mausezahn')
        if not self.mz or not shutil.which('tcpdump'):
            raise RuntimeError('Install mausezahn (or mz) and tcpdump')
        self.prefix = list(prefix or [])
        self.streams = []
        self.stats = {}
        self.sequence = 0

    def getInterfaceNames(self):
        return list(self.interfaces)

    def addInterface(self, name):
        if name not in self.interfaces:
            if self.interfaces:
                raise ValueError('Interface is not configured: ' + name)
            raise ValueError('Pass the dedicated host interfaces to Traffic')
        if name not in self.addedInterfaces:
            self.addedInterfaces.append(name)

    def getInterfaceMacAddress(self, name):
        return mac_text(0x001020304000 + self.interfaces.index(name))

    def _ip(self, name, version=4):
        index = self.interfaces.index(name)
        return str(ipaddress.ip_address((0xc0a83a0a + index) if version == 4
                   else int(ipaddress.IPv6Address('fd42:4242::10')) + index))

    def _add(self, source, destination_mac, destination_ip, count, rate,
             source_mac=None, version=4, mac_count=0, mac_step=1, tags=(), ethertype=0):
        if source not in self.addedInterfaces or count <= 0 or rate <= 0:
            raise ValueError('Register source and use finite positive count/rate')
        mac = source_mac if source_mac is not None else self.getInterfaceMacAddress(source)
        self.streams.append(Stream(source, int.from_bytes(mac_bytes(destination_mac), 'big'),
                            destination_ip, count, rate, int.from_bytes(mac_bytes(mac), 'big'),
                            version, mac_count, mac_step, tuple(tags), ethertype))

    def addPacketStream(self, source, destination_mac=0xffffffffffff, count=5, rate=100,
                        version=4, tags=(), ethertype=0, source_mac=None, destination_ip=None):
        """Raw packet matrix API, retaining signed capture accounting."""
        self._add(source, destination_mac, destination_ip or
                  ('192.0.2.200' if version == 4 else '2001:db8::200'), count, rate,
                  source_mac=source_mac, version=version, tags=tags,
                  ethertype=ethertype)

    def addEthernetStream(self, source, frame, count=3, rate=5):
        """Send an exact 802.3/LLC body with a run marker outside its length."""
        if len(frame) < 14:
            raise ValueError('Ethernet header is missing')
        length = int.from_bytes(frame[12:14], 'big')
        if length > 1500 or len(frame) != 14 + length:
            raise ValueError('Pass an unpadded 802.3 frame with its declared payload length')
        self._add(source, int.from_bytes(frame[:6], 'big'), '192.0.2.200', count, rate,
                  source_mac=int.from_bytes(frame[6:12], 'big'))
        self.streams[-1].ethernet = frame.hex()

    def addUDPStream(self, source, destination, count, rate):
        self._add(source, self.getInterfaceMacAddress(destination), self._ip(destination), count, rate)

    def addUDPv6Stream(self, source, destination, count, rate):
        self._add(source, self.getInterfaceMacAddress(destination), self._ip(destination, 6),
                  count, rate, version=6)

    def addUDPBroadcastStream(self, source, count, rate, source_mac=None):
        self._add(source, 0xffffffffffff, '192.168.58.255', count, rate, source_mac=source_mac)

    def addUDPMulticastStream(self, source, group, count, rate):
        self._add(source, 0x01005e000000 | (int(ipaddress.IPv4Address(group)) & 0x7fffff),
                  group, count, rate)

    def addUDPMacIncStream(self, source, destination, source_mac, count, rate,
                          src_mac_count=0, src_mac_step=1):
        self._add(source, self.getInterfaceMacAddress(destination), self._ip(destination),
                  count, rate, source_mac, mac_count=src_mac_count, mac_step=src_mac_step)

    def learningStream(self, name):
        self.addUDPBroadcastStream(name, 2, 1)

    def learning(self):
        for name in self.addedInterfaces:
            self.learningStream(name)
        self.run()

    def _send(self, streams, signature, directory):
        for number, stream in streams:
            # Raw Ethernet mode is shared by old mz and current mausezahn,
            # including IPv6 and arbitrary source-MAC stepping.
            changing = bool(stream.mac_count)
            with (directory / ('mz-%d.log' % number)).open('wb') as log:
                for index in range(stream.count if changing else 1):
                    mac = stream.source_mac + ((index % stream.mac_count) * stream.mac_step if changing else 0)
                    frame = udp_frame(mac, stream.destination_mac, self._ip(stream.source, stream.version),
                                      stream.destination_ip, signature)
                    if stream.ethertype:
                        frame = (mac_bytes(stream.destination_mac) + mac_bytes(mac) +
                                 struct.pack('!H', stream.ethertype) + signature.ljust(114, b'\0'))
                    if stream.ethernet:
                        frame = (bytes.fromhex(stream.ethernet) + signature).ljust(60, b'\0')
                    frame = tag_frame(frame, stream.tags)
                    argv = self.prefix + host_netns.command([self.mz, stream.source, '-q', '-c',
                            str(1 if changing else stream.count), '-d',
                            '%dusec' % max(1, round(1e6 / stream.rate)), frame.hex(':')], interface=stream.source)
                    subprocess.run(argv, stdout=log, stderr=subprocess.STDOUT, check=True,
                                   timeout=max(15, stream.count / stream.rate + 10))
                    if changing:
                        time.sleep(1 / stream.rate)

    def run(self):
        self.sequence += 1
        directory = self.output / ('%04d' % self.sequence)
        directory.mkdir()
        signature = b'DSATEST:' + uuid.uuid4().bytes
        captures = []
        self.stats = {}
        self.tag_stats = {}
        streams, self.streams = self.streams, []
        metadata = {'signature': signature.hex(), 'streams': [vars(s) for s in streams]}
        (directory / 'streams.json').write_text(json.dumps(metadata, indent=2) + '\n')
        try:
            for name in self.addedInterfaces:
                for direction in ('in', 'out'):
                    path = directory / ('%s-%s.pcap' % (name, direction))
                    logpath = path.with_suffix('.log')
                    log = logpath.open('wb')
                    # -U flushes tcpdump's output, but does not disable libpcap's
                    # capture buffering. Sparse probes must reach userspace before
                    # SIGINT, even when they do not fill a TPACKET ring block.
                    proc = subprocess.Popen(self.prefix + host_netns.command(['tcpdump', '--immediate-mode', '-i', name, '-Q', direction,
                        '-n', '-s', '0', '-U', '-w', str(path)], interface=name),
                        stdout=subprocess.DEVNULL, stderr=log)
                    captures.append((name, direction, proc, log, path, logpath))
            deadline = time.monotonic() + 10
            for name, direction, proc, log, path, logpath in captures:
                while b'listening on' not in logpath.read_bytes():
                    if proc.poll() is not None or time.monotonic() > deadline:
                        raise RuntimeError('tcpdump did not start: ' + logpath.read_text())
                    time.sleep(0.02)
            # Streams sharing an ingress port keep their order; ports run concurrently.
            with ThreadPoolExecutor(max_workers=max(1, len(self.addedInterfaces))) as pool:
                jobs = [pool.submit(self._send, [(i, s) for i, s in enumerate(streams) if s.source == name],
                                    signature, directory) for name in self.addedInterfaces]
                for job in jobs:
                    job.result()
            time.sleep(0.5)
        finally:
            for _, _, proc, _, _, _ in captures:
                if proc.poll() is None:
                    proc.send_signal(signal.SIGINT)
            for _, _, proc, log, _, _ in captures:
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
                log.close()
        for name, direction, proc, _, path, logpath in captures:
            report = logpath.read_text()
            if proc.returncode != 0 or '0 packets dropped by kernel' not in report:
                raise RuntimeError('Capture failed or dropped packets: ' + report)
            stats = self.stats.setdefault(name, {'rx_pkts': 0, 'tx_pkts': 0})
            frames = [frame for frame in pcap_frames(path) if signed_frame(frame, signature)]
            stats['rx_pkts' if direction == 'in' else 'tx_pkts'] = len(frames)
            self.tag_stats[(name, direction)] = [vlan_stack(frame) for frame in frames]
        (directory / 'vlan-tags.json').write_text(json.dumps({name + '-' + direction: tags
            for (name, direction), tags in self.tag_stats.items()}, indent=2) + '\n')
        (directory / 'stats.json').write_text(json.dumps(self.stats, indent=2) + '\n')
        for name in self.addedInterfaces:
            expected = sum(s.count for s in streams if s.source == name)
            if self.stats[name]['tx_pkts'] != expected:
                raise RuntimeError('TX capture count on %s: %d != %d' %
                                   (name, self.stats[name]['tx_pkts'], expected))

    def getStats(self, name):
        return dict(self.stats[name])
