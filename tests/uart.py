"""Synchronous legacy SUT API backed by boot_smoke's asynchronous UART reader."""
import asyncio
import importlib.util
import math
import os
from pathlib import Path
import pwd
import re
import shlex
import sys
import threading
import uuid

from sut import SUT
from uart_scripts import ScriptTransport


def user_path(value):
    """Expand a lab path in the invoking user's home, including under sudo."""
    value = os.fspath(value)
    if (value == '~' or value.startswith('~/')) and os.geteuid() == 0:
        sudo_uid = os.environ.get('SUDO_UID')
        if sudo_uid is not None:
            home = Path(pwd.getpwuid(int(sudo_uid)).pw_dir)
            return home / value[2:] if value.startswith('~/') else home
    return Path(value).expanduser()


def load_boot_smoke(path):
    spec = importlib.util.spec_from_file_location('dsa_boot_smoke', user_path(path))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class UART:
    def __init__(self, module, lab, target, mode, output, attach=False, linux_uart=None):
        self.module, self.lab = module, lab
        self.cfg = lab['targets'][target]
        self.profile = self.cfg['profiles'][mode]
        self.attach = attach
        linux_uart = linux_uart or {}
        self.use_script_transport = linux_uart.get('script_transport', True)
        if not isinstance(self.use_script_transport, bool):
            raise ValueError('Linux UART script_transport must be true or false')
        self.script_transport = None
        self.output = Path(output)
        self.linux_write_chunk_size = int(linux_uart.get('write_chunk_size', 16))
        self.linux_write_delay_s = float(linux_uart.get('write_delay_s', 0.005))
        level = linux_uart.get('console_loglevel')
        self.console_loglevel = None if level is None else int(level)
        if self.console_loglevel is not None and not 1 <= self.console_loglevel <= 8:
            raise ValueError('Console loglevel must be between 1 and 8')
        if self.linux_write_chunk_size < 1 or not math.isfinite(self.linux_write_delay_s) or self.linux_write_delay_s < 0:
            raise ValueError('Linux UART chunk size must be positive and delay finite/nonnegative')
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()
        self.console = module.Console(self.cfg['console'], Path(output) / 'console.log')
        self.started = False
        self.printk_time_before = None
        self.console_printk_before = None

    def call(self, coroutine):
        return asyncio.run_coroutine_threadsafe(coroutine, self.loop).result()

    async def _start(self):
        await self.console.connect()
        cfg, profile, lab = self.cfg, self.profile, self.lab
        if self.attach:
            self.console.mark()
            self.console.monitor_panics()
            await self.console.send('', newline=True)
        else:
            variables = {'pdu_host': lab['power'].get('hostname', 'pdu'),
                'outlet': cfg['outlet'], 'image': profile.get('image', ''),
                'nfsroot': profile.get('nfsroot', ''), 'ping_host': cfg.get('ping_host', ''),
                **profile.get('variables', {}), 'python': sys.executable,
                'wti_script': str(user_path(lab['power'].get('wti_script',
                    '~/dev/github/misc-scripts/wti-nps.py')))}
            await self.module.wait_for_firmware_and_boot(self.console, cfg['boot'], profile,
                                                        lab['power'], variables, False)
        await self.module.login(self.console, cfg.get('login', {}))
        self.console.write_chunk_size = self.linux_write_chunk_size
        self.console.write_delay_s = self.linux_write_delay_s
        print('[uart] Linux pacing: %d bytes per write, %.3f s gap' %
              (self.linux_write_chunk_size, self.linux_write_delay_s), flush=True)
        await self._enable_kernel_timestamps()
        await self._configure_console_loglevel()
        if self.use_script_transport:
            self.script_transport = ScriptTransport(self.console, self._legacy_command, self.output)
            await self.script_transport.start()
            print('[uart] Verified script agent ready; cached scripts and checksummed results', flush=True)
        self.started = True

    async def _enable_kernel_timestamps(self):
        code, value = await self._command('cat /sys/module/printk/parameters/time', 10)
        if code or value not in ('Y', 'N'):
            raise RuntimeError('Cannot read kernel printk.time setting: ' + value)
        if value == 'N':
            self.printk_time_before = value
            code, output = await self._command("printf 'Y\\n' > /sys/module/printk/parameters/time", 10)
            if code:
                raise RuntimeError('Cannot enable kernel console timestamps: ' + output)
            print('[uart] Enabled kernel timestamps for reliable command framing', flush=True)

    async def _configure_console_loglevel(self):
        if self.console_loglevel is None:
            return
        code, value = await self._command('cat /proc/sys/kernel/printk', 10)
        if code or not re.fullmatch(r'\s*\d+\s+\d+\s+\d+\s+\d+\s*', value):
            raise RuntimeError('Cannot read kernel console loglevel: ' + value)
        levels = value.split()
        if int(levels[0]) != self.console_loglevel:
            self.console_printk_before = ' '.join(levels)
            levels[0] = str(self.console_loglevel)
            code, output = await self._command("printf '%s\\n' " +
                shlex.quote(' '.join(levels)) + ' > /proc/sys/kernel/printk', 10)
            if code:
                raise RuntimeError('Cannot set kernel console loglevel: ' + output)
            print('[uart] Console loglevel %d (restored on close)' %
                  self.console_loglevel, flush=True)

    def start(self):
        self.call(self._start())

    async def _command(self, command, timeout):
        transport = getattr(self, 'script_transport', None)
        if transport is not None and transport.started:
            return await transport.command(command, timeout)
        return await self._legacy_command(command, timeout)

    async def _legacy_command(self, command, timeout):
        # boot_smoke includes UART command echo in its returned transcript.
        # A split begin marker makes numeric/statistic parsers independent of echo.
        begin = '__DSA_BEGIN_' + uuid.uuid4().hex + '__'
        split = len(begin) // 2
        code, output = await self.console.command(
            "printf '%s%s\\n' " + shlex.quote(begin[:split]) + ' ' +
            shlex.quote(begin[split:]) + '; ' + command, timeout)
        noise = r'(?:\[\s*\d+\.\d+\][^\r\n]*(?:\r\n|\n|\r))*'
        match = re.search(noise.join(re.escape(char) for char in begin) + noise + r'(?:\r\n|\r|\n)', output)
        if not match:
            diagnostic = output.replace('\r\n', '\n').strip()[-4000:]
            raise RuntimeError('UART output begin marker missing (exit status %s) for %r; output: %s' %
                               (code, command, diagnostic))
        return code, output[match.end():].replace('\r\n', '\n').strip()

    def command(self, command, timeout=30):
        print('[uart] ' + command, flush=True)
        return self.call(self._command(command, timeout))

    def close(self):
        try:
            self.call(self._close())
        finally:
            self.loop.call_soon_threadsafe(self.loop.stop)
            self.thread.join(timeout=5)
            self.loop.close()

    async def _close(self):
        errors = []
        try:
            if self.console.connected and not self.console.kernel_panic:
                settings = [(getattr(self, 'console_printk_before', None),
                             '/proc/sys/kernel/printk', 'kernel console loglevel'),
                            (self.printk_time_before, '/sys/module/printk/parameters/time',
                             'kernel console timestamps')]
                for value, path, label in settings:
                    if value is None:
                        continue
                    try:
                        code, output = await self._command("printf '%s\\n' " + shlex.quote(value) +
                                                          ' > ' + path, 10)
                        if code:
                            errors.append('Cannot restore %s: %s' % (label, output))
                    except Exception as exc:
                        errors.append('Cannot restore %s: %s' % (label, exc))
        finally:
            try:
                transport = getattr(self, 'script_transport', None)
                if transport is not None:
                    await transport.close()
            except Exception as exc:
                errors.append('Cannot stop UART script agent: ' + str(exc))
            finally:
                await self.console.close()
        if errors:
            raise RuntimeError('; '.join(errors))


class UartSUT(SUT):
    def __init__(self, uart, ports, master, strict_counters=False, counter_map=None):
        self.uart = uart
        self.ports = ports
        self.master = master
        self.mgmt = '__no_management__'
        self.exit_code = None
        self.error = ''
        self.fdb = []
        self.created_bridges = set()
        self.processes = set()
        self.strict_counters = strict_counters
        self.counter_map = counter_map or {}
        self.unavailable_counters = set()
        self.interfaces = self.getInterfaces()

    def ssh(self, command):
        # Retain the legacy method name; every operation uses UART.
        self.exit_code, output = self.uart.command(command, 60)
        self.error = output
        return output

    def checked(self, command):
        output = self.ssh(command)
        self.checkExitCode(0)
        return output

    def addBridge(self, bridge):
        self.checked('ip link add name %s type bridge forward_delay 200' % shlex.quote(bridge))
        self.created_bridges.add(bridge)
        self.getInterfaces()

    def deleteBridge(self, bridge):
        self.checked('ip link del dev ' + shlex.quote(bridge))
        self.created_bridges.discard(bridge)
        self.getInterfaces()

    def addBridgeInterface(self, bridge, interface):
        self.checked('ip link set dev %s master %s' % (shlex.quote(interface), shlex.quote(bridge)))

    def deleteBridgeInterface(self, bridge, interface):
        self.checked('ip link set dev %s nomaster' % shlex.quote(interface))

    def getFdb(self, interface):
        output = self.checked('bridge fdb show dev ' + shlex.quote(interface))
        return sorted(set(re.findall(r'(?m)^([0-9a-f:]{17})\b', output)))

    def getClassStats(self, interface):
        output = self.checked('for f in /sys/class/net/%s/statistics/*; do '
                              'printf "%%s " "${f##*/}"; cat "$f"; done' % shlex.quote(interface))
        return {name: int(value) for name, value in re.findall(r'(?m)^(\w+) (\d+)$', output)}

    def getEthtoolStats(self, interface):
        stats = super().getEthtoolStats(interface)
        for canonical, driver_name in self.counter_map.items():
            if driver_name in stats:
                stats[canonical] = stats[driver_name]
        return stats

    def _available(self, before, after, expected):
        missing = set(expected) - (set(before) & set(after))
        if missing and self.strict_counters:
            raise AssertionError('Required counters unavailable: ' + ', '.join(sorted(missing)))
        self.unavailable_counters.update(missing)
        return {key: value for key, value in expected.items() if key not in missing}

    def checkEthtoolStatsRange(self, interface, before, expected, case):
        after = self.getEthtoolStats(interface)
        expected = self._available(before, after, expected)
        self._statsCheckRange(before, after, expected, case)

    def checkEthtoolStatsRangeOr(self, interface, before, first, second, case):
        after = self.getEthtoolStats(interface)
        first = self._available(before, after, first)
        second = self._available(before, after, second)
        self._statsCheckRangeOr(before, after, first, second, case)

    def start_ssh(self, command):
        output = self.checked('(%s) >/tmp/dsatest-background.log 2>&1 & echo $!' % command)
        pid = int(output.splitlines()[-1])
        self.processes.add(pid)
        return pid

    def stop_ssh(self, pid):
        if pid in self.processes:
            self.checked('kill %d' % pid)
            self.processes.remove(pid)

    def cleanSystem(self):
        # Only configured test ports and bridges created by this run are touched.
        for pid in list(self.processes):
            self.stop_ssh(pid)
        for interface, address in self.fdb[:]:
            self.delFdb(interface, address)
        for bridge in list(self.created_bridges):
            self.deleteBridge(bridge)
        for port in self.ports:
            name = shlex.quote(port)
            self.checked('ip link set dev %s nomaster && ip addr flush dev %s && ip link set dev %s down' %
                         (name, name, name))
