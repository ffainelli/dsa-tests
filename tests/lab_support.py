"""Host interface lifetime and direct-cable discovery."""
import json
from pathlib import Path
import re
import subprocess
import time
import uuid
import os
import host_netns

from traffic import Traffic, mac_text


def run(*args, check=True):
    return subprocess.run(host_netns.command(args), check=check, capture_output=True, text=True, timeout=30)


class HostLinks:
    def __init__(self, interfaces, output):
        self.interfaces, self.output = interfaces, Path(output)
        self.saved = {}
        self.changed = False
        self.namespaces = {}
        self.origin_pid = os.getpid()

    def recover(self):
        """Restore idle, recorded namespaces left by an interrupted lab run.

        Called under the runner's exclusive lab lock, before snapshot/TFTP.
        Never adopt unrelated namespaces or overwrite a visible interface.
        """
        missing = [name for name in self.interfaces if run(
            'ip', '-j', 'address', 'show', 'dev', name, check=False).returncode]
        if not missing:
            return {}
        available = {line.split()[0] for line in run('ip', 'netns', 'list').stdout.splitlines()
                     if line.split()}
        found = {}
        saved = {}
        for path in sorted(self.output.parent.glob('*/host-namespaces.json'), reverse=True):
            try:
                mapping = json.loads(path.read_text())
                before = json.loads((path.parent / 'host-before.json').read_text())
            except (OSError, ValueError):
                continue
            for interface in missing:
                namespace = mapping.get(interface, '')
                if (interface in found or interface not in before or
                        not re.fullmatch(r'dsatest-[0-9a-f]{10}-\d+', namespace) or
                        namespace not in available):
                    continue
                probe = run('ip', '-n', namespace, '-j', 'address', 'show',
                            'dev', interface, check=False)
                if probe.returncode == 0:
                    found[interface] = namespace
                    saved[interface] = before[interface]
        unresolved = set(missing) - found.keys()
        if unresolved:
            raise RuntimeError('Host interfaces missing with no recorded recoverable namespace: ' +
                               ', '.join(sorted(unresolved)) +
                               '. Check USB connections and host-namespaces.json from prior runs.')
        # Check all candidates before moving any device. A stale helper is
        # reported rather than killing a process we have not identified.
        for namespace in found.values():
            pids = run('ip', 'netns', 'pids', namespace).stdout.strip()
            if pids:
                raise RuntimeError('Cannot recover namespace %s with live processes: %s' %
                                   (namespace, pids.replace('\n', ', ')))
        recovery = HostLinks(list(found), self.output)
        recovery.changed = True
        recovery.saved, recovery.namespaces = saved, dict(found)
        host_netns.INTERFACES.update(found)
        (self.output / 'host-namespace-recovery.json').write_text(
            json.dumps(found, indent=2) + '\n')
        errors = recovery.restore()
        if errors:
            raise RuntimeError('Host namespace recovery failed: ' + '; '.join(errors))
        return found

    def snapshot(self):
        for interface in self.interfaces:
            probe = run('ip', '-j', 'address', 'show', 'dev', interface, check=False)
            if probe.returncode:
                raise RuntimeError('Cannot inspect host interface %s: %s' %
                                   (interface, probe.stderr.strip()))
            records = json.loads(probe.stdout)
            if len(records) != 1 or 'master' in records[0]:
                raise RuntimeError('Use dedicated, unenslaved test interfaces: ' + interface)
            state = records[0]
            state['saved_routes'] = {family: json.loads(run('ip', family, '-j', 'route', 'show',
                'table', 'main', 'dev', interface).stdout) for family in ('-4', '-6')}
            self.saved[interface] = state
        (self.output / 'host-before.json').write_text(json.dumps(self.saved, indent=2) + '\n')

    def isolate(self):
        """Move cables only after TFTP; track every mutation for partial cleanup."""
        self.changed = True
        for index, interface in enumerate(self.interfaces):
            namespace = 'dsatest-%s-%d' % (uuid.uuid4().hex[:10], index)
            run('ip', 'netns', 'add', namespace)
            self.namespaces[interface] = namespace
            (self.output / 'host-namespaces.json').write_text(
                json.dumps(self.namespaces, indent=2) + '\n')
            run('ip', 'link', 'set', 'dev', interface, 'down')
            run('ip', 'link', 'set', 'dev', interface, 'netns', namespace)
            host_netns.INTERFACES[interface] = namespace
            run('ip', 'netns', 'exec', namespace, 'ip', 'link', 'set', 'lo', 'up')
            run('ip', 'addr', 'flush', 'dev', interface)
        (self.output / 'host-namespaces.json').write_text(
            json.dumps(self.namespaces, indent=2) + '\n')

    def restore(self):
        if not self.changed:
            return []
        errors = []
        for interface, state in self.saved.items():
            namespace = self.namespaces.get(interface)
            try:
                if interface in host_netns.INTERFACES:
                    run('ip', 'link', 'set', 'dev', interface, 'down')
                    run('ip', 'link', 'set', 'dev', interface, 'netns', str(self.origin_pid))
                    for name in list(host_netns.INTERFACES):
                        if host_netns.INTERFACES[name] == namespace:
                            host_netns.INTERFACES.pop(name)
                            host_netns.ADDRESSES.pop(name, None)
                if namespace:
                    run('ip', 'netns', 'del', namespace)
                    self.namespaces.pop(interface, None)
                run('ip', 'link', 'set', 'dev', interface, 'mtu', str(state['mtu']))
                run('ip', 'address', 'flush', 'dev', interface)
                for address in state['addr_info']:
                    if address['family'] not in ('inet', 'inet6'):
                        continue
                    argv = ['ip', 'address', 'replace', '%s/%s' % (address['local'], address['prefixlen']),
                            'dev', interface]
                    if 'broadcast' in address:
                        argv += ['brd', address['broadcast']]
                    if address.get('label'):
                        argv += ['label', address['label']]
                    run(*argv)
                run('ip', 'link', 'set', 'dev', interface, 'up' if 'UP' in state['flags'] else 'down')
                for family, routes in state['saved_routes'].items():
                    for route in routes:
                        if route.get('protocol') == 'kernel':
                            continue
                        argv = ['ip', family, 'route', 'replace', route['dst']]
                        if route.get('gateway'):
                            argv += ['via', route['gateway']]
                        argv += ['dev', interface]
                        for key in ('metric', 'scope', 'protocol'):
                            if key in route:
                                argv += [key if key != 'protocol' else 'proto', str(route[key])]
                        run(*argv)
            except Exception as exc:
                errors.append('%s: %s' % (interface, exc))
        return errors


def check_b53_cpu_port(sut, port):
    """Require the device-tree CPU port to be the b53 IMP/management port."""
    driver = sut.checked('readlink /sys/class/net/%s/device/driver' % port).strip().rsplit('/', 1)[-1]
    if not (driver.startswith('b53') or driver in ('bcm53xx', 'brcm-sf2')):
        return None
    # DSA user interfaces point at the switch device. CPU ports carry an
    # ethernet phandle; reg is a big-endian device-tree cell, not a netdev ID.
    output = sut.checked(
        'node=/sys/class/net/%s/device/of_node; '
        'for p in "$node"/ports/port@* "$node"/ethernet-ports/port@* "$node"/port@*; do '
        'test -f "$p/ethernet" || continue; '
        'if test -f "$p/status"; then '
        'status=$(tr -d "\\000" < "$p/status") || exit 1; '
        'case "$status" in okay|ok) ;; *) continue ;; esac; fi; '
        'od -An -v -tu1 "$p/reg" || exit 1; done' % port)
    cells = [line.split() for line in output.splitlines() if line.strip()]
    if not cells or any(cell != ['0', '0', '0', '8'] for cell in cells):
        raise RuntimeError('b53 requires CPU port 8 (IMP/management port); '
                           'check the running device tree. CPU port reg bytes: ' + repr(output))
    return 8


def discover(sut, interfaces, candidates, output):
    """Map cables by learning unique mz source MACs on a temporary bridge.

    Administrative host link-down does not reliably power down a USB NIC's
    PHY. Packet reception, rather than a carrier transition, identifies peers.
    """
    sut.up(sut.master)
    for port in candidates:
        sut.up(port)
    for interface in interfaces:
        run('ip', 'link', 'set', 'dev', interface, 'up')
    time.sleep(5)
    bridge = 'brdsa' + uuid.uuid4().hex[:6]
    mapping = {}
    traffic = Traffic(interfaces, Path(output) / 'discovery')
    for interface in interfaces:
        traffic.addInterface(interface)
    sut.addBridge(bridge)
    try:
        sut.up(bridge)
        for port in candidates:
            sut.addBridgeInterface(bridge, port)
        for interface in interfaces:
            # Locally administered, unicast MAC unique to this probe. Match it
            # exactly so unrelated network traffic cannot affect the mapping.
            source_mac = 0x020000000000 | (uuid.uuid4().int & 0xffffffffff)
            source_text = mac_text(source_mac)
            traffic.addUDPBroadcastStream(interface, 3, 100, source_mac=source_mac)
            traffic.run()  # Requires ready tcpdump captures and verifies actual TX.
            output_text = ''
            ports = set()
            for attempt in range(3):
                output_text = sut.checked('bridge fdb show br ' + bridge)
                ports = set(re.findall(r'(?mi)^' + re.escape(source_text) +
                                       r'\s+dev\s+(\S+)', output_text))
                ports &= set(candidates)
                if ports:
                    break
                time.sleep(0.2)
            if len(ports) != 1:
                raise RuntimeError('Cannot identify a unique packet ingress for %s '
                    '(probe MAC %s, learned ports: %s). FDB: %s' %
                    (interface, source_text, sorted(ports), output_text))
            port = ports.pop()
            if port in mapping.values():
                raise RuntimeError('Two host links mapped to the same board port: ' + port)
            mapping[interface] = port
            print('[discovery] %s -> %s (probe %s)' % (interface, port, source_text), flush=True)
    finally:
        sut.deleteBridge(bridge)
    return mapping
