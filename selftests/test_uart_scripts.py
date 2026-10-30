"""Real PTY tests of staged scripts, checksum rejection and result replay."""
import asyncio
import base64
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import termios
import time

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tests'))
from uart_scripts import ScriptTransport, digest, parse_result


class PtyConsole:
    def __init__(self, env=None):
        self.master, self.slave = os.openpty()
        self.original = termios.tcgetattr(self.slave)
        os.set_blocking(self.master, False)
        self.text = ''
        self.cursor = 0
        self.process = None
        self.env = env
        self.requests = []
        self.corrupt_result = False
        self.corrupt_upload = False

    def mark(self):
        self.cursor = len(self.text)

    async def send(self, content, newline=False):
        self.requests.append(content)
        if content.startswith('sh /tmp/dsatest-'):
            self.process = subprocess.Popen(content.split(), stdin=self.slave,
                                            stdout=self.slave, stderr=self.slave, env=self.env)
            return
        if self.corrupt_upload and content.startswith('P '):
            fields = content.split()
            fields[-1] = base64.b64encode(b'corrupted command bytes').decode()
            content = ' '.join(fields)
            self.corrupt_upload = False
        os.write(self.master, (content + ('\r' if newline else '')).encode())

    async def expect(self, pattern, timeout, label):
        deadline = time.monotonic() + min(timeout, 1)
        while True:
            try:
                self.text += os.read(self.master, 65536).decode()
            except BlockingIOError:
                pass
            match = re.search(pattern, self.text[self.cursor:])
            if match:
                if self.corrupt_result and 'script result' in label:
                    # Damage one encoded output byte, leaving frame and hash.
                    part = self.text[self.cursor:]
                    frame = re.search(r' BEGIN .*?\n(.)', part)
                    if frame:
                        index = self.cursor + frame.start(1)
                        self.text = self.text[:index] + ('A' if self.text[index] != 'A' else 'B') + self.text[index + 1:]
                    self.corrupt_result = False
                result = re.compile(pattern).search(self.text, self.cursor)
                self.cursor = result.end()
                return result
            if time.monotonic() > deadline:
                raise RuntimeError('PTY timeout: ' + label)
            await asyncio.sleep(.005)

    async def bootstrap(self, command, timeout):
        process = await asyncio.create_subprocess_exec('sh', '-c', command,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, env=self.env)
        output, _ = await asyncio.wait_for(process.communicate(), timeout)
        return process.returncode, output.decode().strip()

    def cleanup(self):
        if self.process is not None:
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                self.process.wait(timeout=2)
        os.close(self.master)
        os.close(self.slave)


@pytest.mark.parametrize('busybox', [False, True])
def test_agent_over_real_pty_rejects_bad_upload_and_replays_corrupt_output(tmp_path, busybox):
    env = None
    if busybox:
        executable = shutil.which('busybox')
        if not executable:
            pytest.skip('BusyBox not installed')
        tools = tmp_path / 'busybox-bin'
        tools.mkdir()
        for name in ('sh', 'stty', 'base64', 'sha256sum', 'mkdir', 'mv', 'rm', 'cat', 'wc'):
            (tools / name).symlink_to(executable)
        env = dict(os.environ, PATH=str(tools) + ':' + os.environ['PATH'])
    async def exercise():
        console = PtyConsole(env=env)
        transport = ScriptTransport(console, console.bootstrap, tmp_path)
        counter = tmp_path / 'execution-count'
        try:
            await transport.start()
            console.corrupt_upload = True
            console.corrupt_result = True
            assert await transport.command("printf x >> '%s'; printf 'hello\\n'; exit 7" % counter, 5) == (7, 'hello')
            assert counter.read_text() == 'x'
            assert len([item for item in console.requests if item.startswith('X ')]) == 1
            assert any(item.startswith('R ') for item in console.requests)
            assert len([item for item in console.requests if item.startswith('P ')]) == 2
            # Even an explicit duplicate EXEC after host acknowledgement cannot
            # repeat a mutation whose board-side output was already released.
            execution = next(item for item in console.requests if item.startswith('X '))
            console.mark()
            await console.send(execution, newline=True)
            await console.expect(re.escape('@DSA_' + transport.session) + r' UNKNOWN 1\n', 5, 'released execution')
            assert counter.read_text() == 'x'
            await transport.close()
            console.process.wait(timeout=2)
            assert termios.tcgetattr(console.slave) == console.original
            assert not Path(transport.root).exists()
        finally:
            console.cleanup()
    asyncio.run(exercise())


def test_multi_chunk_script_preserves_quoting_and_stdin_is_not_protocol(tmp_path):
    async def exercise():
        console = PtyConsole()
        transport = ScriptTransport(console, console.bootstrap, tmp_path)
        try:
            await transport.start()
            script = '# ' + 'large payload ' * 60 + '\n' + "printf '%s\\n' 'spaces $variables `literal`'; read unexpected; exit 3"
            assert await transport.command(script, 5) == (3, 'spaces $variables `literal`')
            assert len([item for item in console.requests if item.startswith('P ')]) > 1
            await transport.close()
        finally:
            console.cleanup()
    asyncio.run(exercise())


def test_repeated_command_uses_cached_script_with_new_execution_id(tmp_path):
    async def exercise():
        console = PtyConsole()
        transport = ScriptTransport(console, console.bootstrap, tmp_path)
        try:
            await transport.start()
            assert await transport.command('printf ok', 5) == (0, 'ok')
            uploads = len([item for item in console.requests if item.startswith('P ')])
            assert await transport.command('printf ok', 5) == (0, 'ok')
            assert len([item for item in console.requests if item.startswith('P ')]) == uploads
            assert [item.split()[1] for item in console.requests if item.startswith('X ')] == ['1', '2']
            await transport.close()
        finally:
            console.cleanup()
    asyncio.run(exercise())


def test_result_digest_catches_valid_base64_corruption():
    output = b'actual reply\n'
    prefix = '@DSA_test'
    text = '%s BEGIN 1 0 %d %s\n%s\n%s END 1\n' % (
        prefix, len(output), digest(b'0\n' + output), base64.b64encode(output).decode(), prefix)
    assert parse_result(text, 'test', 1) == (0, output)
    assert parse_result(text.replace('BEGIN', '[123.456] printk\nBEGIN'), 'test', 1) == (0, output)
    with pytest.raises(RuntimeError, match='integrity'):
        parse_result(text.replace(base64.b64encode(output).decode(), base64.b64encode(b'wrong reply!\n').decode()), 'test', 1)
    with pytest.raises(RuntimeError, match='integrity'):
        parse_result(text.replace('BEGIN 1 0 ', 'BEGIN 1 1 '), 'test', 1)
