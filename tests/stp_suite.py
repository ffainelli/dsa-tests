# SPDX-License-Identifier: GPL-2.0
"""Classic 802.1D kernel STP on the existing independent-client topology."""
import struct

from switch_case import SwitchCase
from traffic import mac_text, pcap_frames


def config_bpdu(frame):
    """Return an untagged 802.1D configuration BPDU, excluding padding."""
    if (len(frame) < 52 or frame[:6] != bytes.fromhex('0180c2000000') or
            not 38 <= int.from_bytes(frame[12:14], 'big') <= 1500 or
            frame[14:21] != b'\x42\x42\x03\x00\x00\x00\x00'):
        return None
    return dict(root=frame[22:30].hex(), cost=struct.unpack('!I', frame[30:34])[0],
                bridge=frame[34:42].hex(), port=struct.unpack('!H', frame[42:44])[0])


class KernelStp(SwitchCase):
    def prepare(self):
        self.bridge(members=())
        # Select before enabling STP. Older kernels lack this attribute; their
        # auto/helper path is acceptable only if it actually selected kernel STP.
        code, output = self.attempt('ip link set dev %s type bridge stp_mode kernel' % self.br)
        if code and not any(word in output.lower() for word in (
                'not supported', 'unknown', 'garbage')):
            self.fail('Cannot select kernel STP: ' + output)
        self.cmd('ip link set dev %s type bridge forward_delay 800 hello_time 100 stp_state 1' % self.br)
        state = self.cmd('cat /sys/class/net/%s/bridge/stp_state' % self.br).strip()
        if state == '2':
            self.skipTest('A bridge-stp helper selected userspace STP; use stp_mode kernel '
                          'with a supporting kernel/iproute2 or disable helper handoff in the lab image')
        self.assertEqual(state, '1', 'Kernel STP did not become active')
        # Enslaving after STP is active starts real listening/learning timers.
        # Enabling STP on already-forwarding members need not reset their state.
        self.cmd('for p in %s; do ip link set dev "$p" master %s || exit 1; done' %
                 (' '.join(self.ports[:3]), self.br))

    def state(self, index):
        return self.cmd('cat /sys/class/net/%s/brport/state' % self.ports[index]).strip()

    def wait_state(self, index, state, timeout=25):
        # Poll on the board, so UART latency does not dominate the observation.
        path = '/sys/class/net/%s/brport/state' % self.ports[index]
        self.cmd('for i in $(seq 1 %d); do s=$(cat %s) || exit 1; '
                 'echo "$s"; test "$s" = %d && exit 0; sleep 1; done; exit 1' %
                 (timeout, path, state))

    def forwarding(self):
        for index in range(3):
            self.wait_state(index, 3)

    def test_listening_does_not_learn_or_forward(self):
        self.prepare()
        self.assertEqual(self.state(0), '1')
        mac = 0x02000057a001
        self.probe(source_mac=mac, receivers=())
        self.assertNotIn(mac_text(mac), self.sut.getFdb(self.ports[0]))
        self.assertEqual(self.state(0), '1', 'Probe exceeded the listening window')
        self.forwarding()
        self.probe()

    def test_learning_learns_without_forwarding(self):
        self.prepare()
        self.wait_state(0, 2)
        mac = 0x02000057a002
        self.probe(source_mac=mac, receivers=())
        self.assertIn(mac_text(mac), self.sut.getFdb(self.ports[0]))
        self.assertEqual(self.state(0), '2', 'Probe exceeded the learning window')
        self.forwarding()
        self.probe(source=1, destination=mac, receivers=(0,))

    def test_root_bpdus_and_reserved_destination(self):
        self.prepare()
        self.forwarding()
        root = self.cmd('cat /sys/class/net/%s/bridge/bridge_id' % self.br).strip().replace('.', '')
        self.assertEqual(self.cmd('cat /sys/class/net/%s/bridge/root_id' % self.br).strip().replace('.', ''), root)
        # Capture for > two one-second hello intervals. Raw PCAPs retain BPDUs
        # even though Traffic's signed packet counters exclude control traffic.
        self.traffic.addPacketStream(self.hosts[0], count=6, rate=2)
        self.traffic.run()
        directory = self.traffic.output / ('%04d' % self.traffic.sequence)
        for index in range(3):
            with self.subTest(port=self.ports[index]):
                frames = pcap_frames(directory / (self.hosts[index] + '-in.pcap'))
                bpdus = [bpdu for frame in frames if (bpdu := config_bpdu(frame)) is not None]
                self.assertTrue(bpdus, 'No configuration BPDU on ' + self.ports[index])
                for bpdu in bpdus:
                    self.assertEqual(bpdu['root'], root)
                    self.assertEqual(bpdu['bridge'], root)
                    self.assertEqual(bpdu['cost'], 0)
        self.probe(destination=0x0180c2000000, ethertype=0x88b5, receivers=())
        self.probe()

    def test_link_down_up_reconverges(self):
        self.prepare()
        self.forwarding()
        self.probe()
        self.sut.down(self.ports[1])
        self.wait_state(1, 0)
        self.probe(receivers=(2,))
        self.sut.up(self.ports[1])
        self.wait_links()
        self.wait_state(1, 3)
        self.probe()
        self.probe(source=1, receivers=(0, 2))

    def test_port_rejoin_reconverges(self):
        self.prepare()
        self.forwarding()
        self.sut.deleteBridgeInterface(self.br, self.ports[1])
        self.probe(receivers=(2,))
        self.sut.addBridgeInterface(self.br, self.ports[1])
        self.wait_state(1, 3)
        self.probe()
        self.probe(source=1, receivers=(0, 2))

    def test_disable_stp_restores_forwarding(self):
        self.prepare()
        self.forwarding()
        self.cmd('ip link set dev %s type bridge stp_state 0' % self.br)
        self.assertEqual(self.cmd('cat /sys/class/net/%s/bridge/stp_state' % self.br).strip(), '0')
        self.sut.down(self.ports[1])
        self.sut.up(self.ports[1])
        self.wait_links()
        for index in range(3):
            self.assertEqual(self.state(index), '3')
        self.probe()
