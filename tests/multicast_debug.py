"""Persistent host captures and switch evidence for legacy IGMP scenarios."""
import json
from pathlib import Path
import re
import signal
import subprocess
import time

import host_netns
from traffic import Traffic


def mdb_ports(rows, group):
    """Accept both iproute2's nested MDB arrays and flat JSON entries."""
    result = set()
    if isinstance(rows, list):
        for row in rows:
            result.update(mdb_ports(row, group))
    elif isinstance(rows, dict):
        if rows.get('grp') == group and rows.get('port'):
            result.add(rows['port'])
        for value in rows.values():
            if isinstance(value, (list, dict)):
                result.update(mdb_ports(value, group))
    return result


class MulticastDebug:
    def __init__(self, sut, config, folder):
        self.sut, self.config = sut, config
        self.folder = Path(folder) / 'debug'
        self.folder.mkdir(parents=True, exist_ok=True)
        self.hosts = [config['HOST_LAN%d' % i] for i in range(4)]
        self.ports = [config['SUT_LAN%d' % i] for i in range(4)]
        self.captures = []
        self.records = []

    def save(self, record):
        record['time_unix_s'] = time.time()
        self.records.append(record)
        (self.folder / 'state.json').write_text(json.dumps(self.records, indent=2) + '\n')

    def board(self, command):
        try:
            code, output = self.sut.uart.command(command, 20)
            return dict(command=command, status=code, output=output)
        except Exception as exc:
            return dict(command=command, error='%s: %s' % (type(exc).__name__, exc))

    def start(self):
        try:
            for host in self.hosts:
                path = self.folder / (host + '.pcap')
                logpath = self.folder / (host + '-tcpdump.log')
                log = logpath.open('wb')
                try:
                    # No BPF: retain IGMP, VLAN-tagged control frames and data.
                    # -p observes without altering NIC promiscuity/filtering.
                    proc = subprocess.Popen(host_netns.command([
                        'tcpdump', '--immediate-mode', '-p', '-i', host,
                        '-n', '-s', '0', '-U', '-w', str(path)], interface=host),
                        stdout=subprocess.DEVNULL, stderr=log)
                except BaseException:
                    log.close()
                    raise
                self.captures.append((proc, log, logpath))
            deadline = time.monotonic() + 10
            for proc, _, path in self.captures:
                while b'listening on' not in path.read_bytes():
                    if proc.poll() is not None or time.monotonic() > deadline:
                        raise RuntimeError('IGMP capture did not start: ' + path.read_text())
                    time.sleep(.02)
        except BaseException:
            self.close()
            raise

    def counters(self):
        result = {}
        for port in self.ports:
            try:
                result[port] = self.sut.getEthtoolStats(port)
            except Exception as exc:
                result[port] = {'error': str(exc)}
        return result

    def snapshot(self, phase, counters=False):
        commands = ['bridge -j -d mdb show dev br1', 'bridge -j -d link show',
                    'ip -j -d link show dev br1', 'bridge -j -s fdb show br br1']
        record = dict(phase=phase, board=[self.board(cmd) for cmd in commands], hosts={})
        for host in self.hosts:
            entries = []
            for argv in (['ip', '-j', 'maddress', 'show', 'dev', host],
                         ['cat', '/proc/net/igmp'],
                         ['ip', '-j', '-s', 'link', 'show', 'dev', host]):
                try:
                    done = subprocess.run(host_netns.command(argv, interface=host),
                        capture_output=True, text=True, timeout=10)
                    entries.append(dict(command=argv, status=done.returncode,
                                        output=done.stdout, stderr=done.stderr))
                except Exception as exc:
                    entries.append(dict(command=argv, error=str(exc)))
            record['hosts'][host] = entries
        if counters:
            record['counters'] = self.counters()
            if phase == 'dynamic-after':
                before = next((item['counters'] for item in reversed(self.records)
                               if item['phase'] == 'dynamic-before-counters'), {})
                record['counter_deltas'] = {
                    port: {name: value - before[port][name]
                           for name, value in values.items()
                           if isinstance(value, int) and
                           isinstance(before.get(port, {}).get(name), int)}
                    for port, values in record['counters'].items() if port in before}
        self.save(record)
        return record

    def wait_memberships(self, group='224.42.42.42', timeout=15):
        expected = set(self.ports[1:3])
        deadline = time.monotonic() + timeout
        polls = []
        present = set()
        while True:
            result = self.board('bridge -j -d mdb show dev br1')
            polls.append(result)
            if result.get('status') == 0:
                try:
                    clean = re.sub(r'\[\s*\d+\.\d+\][^\r\n]*(?:\r\n|\r|\n|$)', '', result['output'])
                    present = mdb_ports(json.loads(clean), group)
                except (ValueError, TypeError):
                    pass
            if expected <= present or time.monotonic() >= deadline:
                break
            time.sleep(1)
        ready = expected <= present
        self.save(dict(phase='membership-readiness', group=group, ready=ready,
                       expected=sorted(expected), present=sorted(present), polls=polls))
        print('[multicast] MDB membership %s: expected=%s present=%s; evidence in %s' %
              ('ready' if ready else 'MISSING', sorted(expected), sorted(present), self.folder), flush=True)
        # Do not bypass data/control reproduction if membership is missing.
        return ready

    def static_control(self, filtering=False):
        """Separate group isolates replication from dynamic IGMP learning."""
        group = '239.42.42.43'
        suffix = ' vid 1' if filtering else ''
        add = 'bridge mdb add dev br1 port %s grp %s permanent%s' % (self.ports[2], group, suffix)
        delete = 'bridge mdb del dev br1 port %s grp %s%s' % (self.ports[2], group, suffix)
        installed = self.board(add)
        self.save(dict(phase='static-control-install', result=installed))
        if installed.get('status') != 0:
            return
        try:
            self.snapshot('static-control-before', counters=True)
            probe = Traffic(self.hosts, self.folder / 'static-control')
            for host in self.hosts:
                probe.addInterface(host)
            probe.addUDPMulticastStream(self.hosts[1], group, 50, 50)
            try:
                probe.run()
            finally:
                self.snapshot('static-control-after', counters=True)
            self.save(dict(phase='static-control-result', group=group,
                           expected_receiver=self.hosts[2], expected_count=50, stats=probe.stats))
        except Exception as exc:
            self.save(dict(phase='static-control-error', error=str(exc)))
        finally:
            self.save(dict(phase='static-control-remove', result=self.board(delete)))

    def close(self):
        errors = []
        # Signal all first, then reap all even if an individual capture failed.
        captures, self.captures = self.captures, []
        for proc, _, _ in captures:
            if proc.poll() is None:
                proc.send_signal(signal.SIGINT)
        for proc, log, path in captures:
            try:
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
                    errors.append('Capture required SIGKILL: ' + str(path))
                log.close()
                text = path.read_text(errors='replace')
                if proc.returncode != 0 or '0 packets dropped by kernel' not in text:
                    errors.append('Capture failed or dropped packets: ' + str(path))
            finally:
                log.close()
        if errors:
            raise RuntimeError('; '.join(errors))
