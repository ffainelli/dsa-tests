# SPDX-License-Identifier: GPL-2.0
"""Classic 802.1D kernel STP on the existing independent-client topology."""
import struct

from switch_case import SwitchCase
from traffic import mac_bytes, mac_text, pcap_frames


def config_bpdu(frame):
    """Return an untagged 802.1D configuration BPDU, excluding padding."""
    if (len(frame) < 52 or frame[:6] != bytes.fromhex('0180c2000000') or
            not 38 <= int.from_bytes(frame[12:14], 'big') <= 1500 or
            frame[14:21] != b'\x42\x42\x03\x00\x00\x00\x00'):
        return None
    return dict(root=frame[22:30].hex(), cost=struct.unpack('!I', frame[30:34])[0],
                bridge=frame[34:42].hex(), port=struct.unpack('!H', frame[42:44])[0])


def configuration_frame(priority=0x1000, address=0x02000057b001, port=0x8001,
                        cost=0, message_age=0, max_age=12, hello=1, delay=8):
    """802.3 + LLC + classic config BPDU; timer fields use 1/256 seconds."""
    identity = struct.pack('!H', priority) + mac_bytes(address)
    body = (b'\x00\x00\x00\x00\x00' + identity + struct.pack('!I', cost) + identity +
            struct.pack('!HHHHH', port, message_age * 256, max_age * 256,
                        hello * 256, delay * 256))
    payload = b'\x42\x42\x03' + body
    return bytes.fromhex('0180c2000000') + mac_bytes(address) + struct.pack('!H', len(payload)) + payload


class KernelStp(SwitchCase):
    def prepare(self):
        self.bridge(members=())
        # Select before enabling STP. Older kernels lack this attribute; their
        # auto/helper path is acceptable only if it actually selected kernel STP.
        code, output = self.attempt('ip link set dev %s type bridge stp_mode kernel' % self.br)
        if code and not any(word in output.lower() for word in (
                'not supported', 'unknown', 'garbage')):
            self.fail('Cannot select kernel STP: ' + output)
        self.cmd('ip link set dev %s type bridge priority 32768 forward_delay 800 hello_time 100 stp_state 1' % self.br)
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


    def bridge_value(self, name):
        return self.cmd('cat /sys/class/net/%s/bridge/%s' % (self.br, name)).strip()

    def assert_root(self, identity, index=None, cost=0):
        self.assertEqual(self.bridge_value('root_id').replace('.', ''), identity)
        number = (0 if index is None else int(self.cmd(
            'cat /sys/class/net/%s/brport/port_no' % self.ports[index]).strip(), 0))
        self.assertEqual(int(self.bridge_value('root_port')), number)
        self.assertEqual(int(self.bridge_value('root_path_cost')), cost)

    def inject(self, index, frame):
        self.traffic.addEthernetStream(self.hosts[index], frame)
        self.traffic.run()  # Includes exact signed TX-count and capture-drop checks.
        directory = self.traffic.output / ('%04d' % self.traffic.sequence)
        # Locally generated replies have the board's source MAC. No client may
        # receive a forwarded copy with the injected source and complete body.
        for host in self.hosts:
            self.assertFalse(any(packet.startswith(frame) for packet in
                pcap_frames(directory / (host + '-in.pcap'))),
                'Injected BPDU leaked to client ' + host)

    def peer_setup(self):
        self.prepare()
        self.forwarding()
        for index in range(3):
            self.cmd('bridge link set dev %s cost 100' % self.ports[index])
        return self.bridge_value('bridge_id').replace('.', '')

    def test_injected_superior_bpdu_on_each_user_port(self):
        self.peer_setup()
        for index in range(4):
            with self.subTest(port=self.ports[index]):
                if index == 3:
                    self.sut.addBridgeInterface(self.br, self.ports[index])
                    self.cmd('bridge link set dev %s cost 100' % self.ports[index])
                    self.wait_state(index, 3)
                # Each subsequent root wins over the preceding advertisement.
                frame = configuration_frame(priority=0x3000 - index * 0x1000)
                self.inject(index, frame)
                self.assert_root(config_bpdu(frame)['root'], index, 100)
                self.probe(source=index, receivers=tuple(i for i in range(4 if index == 3 else 3) if i != index))
                self.assert_root(config_bpdu(frame)['root'], index, 100)

    def test_injected_inferior_bpdu_does_not_replace_root(self):
        own = self.peer_setup()
        for index in range(3):
            with self.subTest(port=self.ports[index]):
                self.inject(index, configuration_frame(priority=0xf000))
                self.assert_root(own)
                self.probe(source=index, receivers=tuple(i for i in range(3) if i != index))
        # Positive control: this same receive path must accept a superior peer.
        frame = configuration_frame()
        self.inject(2, frame)
        self.assert_root(config_bpdu(frame)['root'], 2, 100)

    def test_injected_invalid_bpdus_do_not_replace_root(self):
        own = self.peer_setup()
        valid = configuration_frame()
        variants = {}
        for name, offset, value in (('protocol', 17, 1), ('version', 19, 0xff),
                                     ('type', 20, 0x7f)):
            frame = bytearray(valid)
            frame[offset] = value
            variants[name] = bytes(frame)
        variants['expired'] = configuration_frame(message_age=13)
        # A real short 802.3 body: the marker appended in Ethernet padding must
        # not accidentally supply the missing BPDU bytes after LLC trimming.
        variants['short'] = valid[:12] + struct.pack('!H', 10) + valid[14:24]
        for name, frame in variants.items():
            with self.subTest(variant=name):
                self.inject(0, frame)
                self.assert_root(own)
                self.probe()
        self.inject(0, valid)
        self.assert_root(config_bpdu(valid)['root'], 0, 100)

    def test_injected_competing_paths_block_higher_cost_port(self):
        self.peer_setup()
        self.cmd('bridge link set dev %s cost 200' % self.ports[1])
        first = configuration_frame(port=0x8001)
        second = configuration_frame(port=0x8002)
        self.inject(0, first)
        self.inject(1, second)
        self.assert_root(config_bpdu(first)['root'], 0, 100)
        self.wait_state(1, 4, timeout=5)
        self.probe(source=2, receivers=(0,))
        self.inject(0, first)
        self.inject(1, second)
        self.probe(source=1, receivers=())
        self.assertEqual(self.state(1), '4')
        self.assert_root(config_bpdu(first)['root'], 0, 100)

    def test_injected_bpdu_guard_disables_and_recovers_port(self):
        own = self.peer_setup()
        self.cmd('bridge link set dev %s guard on' % self.ports[1], optional=True)
        self.inject(1, configuration_frame())
        self.wait_state(1, 0, timeout=5)
        self.assert_root(own)
        self.probe(receivers=(2,))
        self.probe(source=1, receivers=())
        self.cmd('bridge link set dev %s guard off' % self.ports[1])
        self.sut.down(self.ports[1])
        self.sut.up(self.ports[1])
        self.wait_links()
        self.wait_state(1, 3)
        self.probe()
        self.probe(source=1, receivers=(0, 2))

    def test_injected_root_guard_prevents_root_takeover(self):
        own = self.peer_setup()
        self.cmd('bridge link set dev %s root_block on' % self.ports[1], optional=True)
        self.inject(1, configuration_frame())
        self.assert_root(own)
        # Kernel root guard starts listening rather than guaranteeing state 4.
        self.assertIn(self.state(1), ('1', '2', '4'))
        self.probe(receivers=(2,))
        self.inject(1, configuration_frame())
        self.probe(source=1, receivers=())
        self.assert_root(own)
        frame = configuration_frame(priority=0x0000)
        self.inject(0, frame)
        self.assert_root(config_bpdu(frame)['root'], 0, 100)

    def test_injected_root_expires_without_refresh(self):
        own = self.peer_setup()
        frame = configuration_frame(max_age=6, delay=4)
        self.inject(0, frame)
        self.assert_root(config_bpdu(frame)['root'], 0, 100)
        # Do not refresh: the recorded root must revert within max_age plus
        # scheduling margin. No fixed sleep can turn failed adoption into PASS.
        path = '/sys/class/net/%s/bridge/root_id' % self.br
        self.cmd('for i in $(seq 1 12); do r=$(cat %s) || exit 1; '
                 'echo "$r"; test "$r" = %s && exit 0; sleep 1; done; exit 1' %
                 (path, own[:4] + '.' + own[4:]))
        self.assert_root(own)
        self.forwarding()
        self.probe()
