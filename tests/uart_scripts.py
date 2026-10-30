"""Verified script staging and replayable UART results, using a POSIX shell agent."""
import base64
import asyncio
import binascii
from collections import OrderedDict
import hashlib
import json
from pathlib import Path
import re
import uuid


def digest(data):
    return hashlib.sha256(data).hexdigest()


def parse_result(text, session, sequence):
    # Only strip asynchronous console printk outside the encoded command output.
    text = re.sub(r'\[\s*\d+\.\d+\][^\r\n]*(?:\r\n|\r|\n|$)', '', text)
    text = text.replace('\r\n', '\n')
    prefix = re.escape('@DSA_' + session)
    match = re.search(prefix + r' BEGIN ' + str(sequence) +
                      r' (\d+) (\d+) ([0-9a-f]{64})\n(.*?)' + prefix +
                      r' END ' + str(sequence) + r'\n', text, re.S)
    if not match:
        raise RuntimeError('Missing UART script result frame')
    status, length, checksum, payload = match.groups()
    try:
        output = base64.b64decode(''.join(payload.split()), validate=True)
    except (ValueError, binascii.Error) as exc:
        raise RuntimeError('Invalid base64 in UART result') from exc
    if (len(output) != int(length) or digest((status + '\n').encode() + output) != checksum or
            not 0 <= int(status) <= 255):
        raise RuntimeError('UART script result integrity check failed')
    return int(status), output


class ScriptTransport:
    def __init__(self, console, bootstrap, output):
        self.console, self.bootstrap = console, bootstrap
        self.output = Path(output) / 'uart-commands'
        self.output.mkdir(parents=True, exist_ok=True)
        self.session = uuid.uuid4().hex[:12]
        self.root = '/tmp/dsatest-' + self.session
        self.sequence = 0
        self.scripts = OrderedDict()
        self.script_sequence = 0
        self.lock = asyncio.Lock()
        self.started = False

    async def start(self):
        # Bootstrap is read-only or overwrites private staging files, so retrying
        # it cannot repeat a network mutation. Full agent hash must match.
        code, output = await self.bootstrap('command -v base64 && command -v sha256sum && command -v stty', 15)
        if code:
            raise RuntimeError('UART script transport needs base64, sha256sum and stty: ' + output)
        agent = Path(__file__).with_name('uart_agent.sh').read_bytes()
        (self.output / 'agent.sh').write_bytes(agent)
        encoded = base64.b64encode(agent).decode()
        chunks = [encoded[i:i + 512] for i in range(0, len(encoded), 512)]
        for attempt in range(3):
            try:
                code, output = await self.bootstrap('mkdir -p ' + self.root, 15)
                if code:
                    raise RuntimeError(output)
                for index, chunk in enumerate(chunks):
                    code, output = await self.bootstrap("printf '%s' " + chunk + ' > ' +
                                                       self.root + '/p%04d' % index, 15)
                    if code:
                        raise RuntimeError(output)
                code, output = await self.bootstrap('cat ' + self.root + '/p* | base64 -d > ' +
                    self.root + '/agent; sha256sum ' + self.root + '/agent', 15)
                if code or not re.search(r'(?m)^' + digest(agent) + r'\s', output):
                    raise RuntimeError('UART agent checksum mismatch')
                break
            except Exception:
                if attempt == 2:
                    raise
        self.console.mark()
        await self.console.send('sh ' + self.root + '/agent ' + self.session, newline=True)
        self.started = True  # Also attempt terminal restoration if READY is corrupted.
        await self.console.expect(re.escape('@DSA_' + self.session) + r' READY\r?\n', 15,
                                  'verified script agent startup')

    async def stage(self, script):
        checksum = digest(script)
        if checksum in self.scripts:
            self.scripts.move_to_end(checksum)
            return self.scripts[checksum]
        self.script_sequence += 1
        identifier = self.script_sequence
        chunks = [script[i:i + 192] for i in range(0, len(script), 192)]
        for index, chunk in enumerate(chunks):
            expected = '@DSA_%s ACK %d %d %s' % (self.session, identifier, index, digest(chunk))
            request = 'P %d %d %s %s' % (identifier, index, digest(chunk),
                                        base64.b64encode(chunk).decode())
            for attempt in range(3):
                self.console.mark()
                await self.console.send(request, newline=True)
                try:
                    await self.console.expect(re.escape(expected) + r'\r?\n', 5, 'script chunk checksum ACK')
                    break
                except Exception:
                    if attempt == 2:
                        raise
        self.scripts[checksum] = (identifier, len(chunks), checksum)
        if len(self.scripts) > 128:
            _, (old, _, _) = self.scripts.popitem(last=False)
            await self.console.send('F %d' % old, newline=True)
        return self.scripts[checksum]

    async def command(self, command, timeout):
        async with self.lock:
            return await self._command(command, timeout)

    async def _command(self, command, timeout):
        script = (command + '\n').encode()
        identifier, chunks, checksum = await self.stage(script)
        self.sequence += 1
        sequence = self.sequence
        (self.output / ('%06d.sh' % sequence)).write_bytes(script)
        request = 'X %d %d %s %d' % (sequence, identifier, checksum, chunks)
        errors = []
        # EXEC is sent exactly once. All retries only retrieve cached output.
        for attempt in range(3):
            self.console.mark()
            start = self.console.cursor
            await self.console.send(request if attempt == 0 else 'R %d' % sequence, newline=True)
            try:
                match = await self.console.expect(re.escape('@DSA_' + self.session) +
                    r' (?:END ' + str(sequence) + r'|(?:MISSING|UNKNOWN|INVALID) ' +
                    str(sequence) + r')\r?\n', timeout if attempt == 0 else 15,
                    'script result (execution is never retried)')
                code, output = parse_result(self.console.text[start:match.end()], self.session, sequence)
                (self.output / ('%06d.out' % sequence)).write_bytes(output)
                with (self.output / 'commands.jsonl').open('a') as log:
                    log.write(json.dumps(dict(sequence=sequence, script_sha256=checksum,
                        status=code, output_sha256=digest(output), bytes=len(output),
                        result_retrievals=attempt + 1)) + '\n')
                await self.console.send('D %d' % sequence, newline=True)
                return code, output.decode(errors='replace').replace('\r\n', '\n').strip()
            except Exception as exc:
                errors.append(str(exc))
        raise RuntimeError('UART result unavailable for script %d; command was NOT re-executed. %s' %
                           (sequence, '; '.join(errors)))

    async def close(self):
        if self.started:
            self.console.mark()
            await self.console.send('Q 0', newline=True)
            await self.console.expect(re.escape('@DSA_' + self.session) + r' BYE\r?\n', 15,
                                      'script agent exit and terminal restoration')
            self.started = False
