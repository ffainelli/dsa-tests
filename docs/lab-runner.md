# UART-controlled mz/tcpdump DSA tests

`run_lab.py` runs legacy and independent switch test families under Python 3.11+.
Ostinato, Drone, Python 2 and SSH are unnecessary. The second `dsatest`
repository remains unchanged. The runner imports `~/boot_smoke.py` for its
continuous UART reader, PDU control, firmware interruption, TFTP boot steps,
login handling, panic detection and commands with verified exit status.
Boot profiles remain in `~/lab.toml`; the test runner does not duplicate them.
For b53/SF2 switches, the running device tree must select hardware CPU port 8,
the IMP/management port. The runner checks CPU-port `reg` properties before
cable discovery and rejects other ports or an unverifiable device tree.
The board's `master` setting names the CPU-facing Ethernet interface; it does
not select the hardware switch port. Fix the board device tree if this check fails.
Under `sudo`, `~/` in configured paths refers to the invoking user's home
(from `SUDO_UID`), including the boot module, lab file and PDU helper.
After Linux login, the runner sends 16-byte chunks with 5 ms gaps by default
(about 3.2 KB/s, below a 115200-baud 8N1 link's capacity). Configure these under
`[linux_uart]` in the test configuration. Firmware input retains the boot
profile's pacing. Board-specific `[boards.NAME.linux_uart]` values override
the global Linux settings. EA4500 uses 4-byte writes with 10 ms gaps after an
observed RX overrun during bridge configuration. Lamobo R1 uses the same
conservative pacing after corrupted UART markers. SR400AC also uses this pacing
after confirmed RX overruns during bridge configuration. All three boards temporarily set `console_loglevel = 5`, suppressing
NOTICE/INFO/DEBUG console output while retaining WARNING and higher priorities.
The complete original `/proc/sys/kernel/printk` setting is restored on exit,
including when timestamp restoration also needs to run. Command execution is
never automatically retried when response framing is lost. The staged script
transport can retrieve an existing result again without executing the command.
Bootstrap/legacy missing begin-marker errors include shell status and output.
FIFO size alone does not guarantee a particular send rate;
the terminal server and receive software also need to keep up.
The runner temporarily enables `/sys/module/printk/parameters/time` when
needed, so kernel messages interrupting a command marker can be recognized.
It restores the previous setting when closing the console and retains the
raw UART transcript throughout.

### Verified script transport

After login and console setup, the runner uploads `tests/uart_agent.sh` as base64
into a unique `/tmp/dsatest-*` directory and verifies its SHA-256 before starting
it. BusyBox `sh`, `base64`, `sha256sum` and `stty` suffice; all three existing
root filesystems include these applets. No network connection to the board or
kernel rebuild is required. Set `script_transport = false` under `[linux_uart]`
only to diagnose the legacy command path; the default is true.

The complete command list cannot be known at startup: discovery, UUID interface
names, feature probes and host packet results determine subsequent commands.
Instead, the dispatcher is preloaded once and generated commands are staged as
scripts when first used. Repeated script contents reuse a bounded cache (128
scripts). Uploaded chunks and the complete script are checksummed before any
script executes. Calls are serialized, terminal echo is disabled, and each script
runs in a child shell with protocol input disconnected. Interactive board
commands requiring stdin are unsupported.

The host sends a short execution request with a unique sequence number. The
agent captures combined stdout/stderr and returns base64 data, exit status,
length and SHA-256 covering both status and output. Corruption therefore cannot
silently change an interface name or turn a failed command into success. Up to
three chunk transmissions or result retrievals are permitted. Execution requests
are sent once: result retries request only saved output. A lost execution
request fails explicitly rather than being sent again. Started-but-incomplete
and already-acknowledged sequence numbers cannot execute again. Kernel printk
lines outside encoded results remain visible in the raw console transcript.

`uart-commands/NNNNNN.sh` and `.out` retain decoded commands and results;
`commands.jsonl` records checksums, exit status and retrieval count. The staged
agent source is saved there too. Once a verified result is stored on the host,
the board releases its output cache. Normal shutdown restores terminal settings
and removes the private staging directory. Abrupt disconnect/reboot can still
interrupt a command; checksums detect corruption rather than repairing the UART
hardware. These changes preserve host/board synchronization between packet
probes; they do not turn a whole test into an opaque batch.

## Setup and execution

On the Linux test host install `iproute2` (including `bridge`), `iputils`
(`ping`), `net-tools` (`arp`), `tcpdump`, and `netsniff-ng` (which provides
`mausezahn`; `mz` is also accepted). Use the distribution's equivalent package
names if different.

```sh
cd ~/dev/github/dsa-tests
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python run_lab.py --board ea4500 --plan
sudo .venv/bin/python run_lab.py --board ea4500
sudo .venv/bin/python run_lab.py --board sr400ac
sudo .venv/bin/python run_lab.py --board lamobo-r1
```

`--plan` imports the suites and prints their test counts and configured
interfaces without opening consoles, changing links or sending packets.
Its `ready` field describes cable configuration, not hardware health.
All three boards have four configured host interfaces. Lamobo R1's fourth
interface is `enxf8e43ba6b4f6`; `enxf8e43ba6ce10` belongs exclusively to EA4500.

The privileged command needs root privileges to create and enter network namespaces
(including mount operations used by `ip netns`), `NET_ADMIN` and `NET_RAW`, plus access
to the terminal server, PDU and TFTP server. A sandbox with no network
capabilities cannot perform hardware validation. All boards run sequentially;
a process lock prevents concurrent runs from changing the same lab.

Select or repeat suites to shorten a run:

```sh
sudo .venv/bin/python run_lab.py --board ea4500 --suite two-bridges
sudo .venv/bin/python run_lab.py --board sr400ac --suite bridge --suite multicast
sudo .venv/bin/python run_lab.py --board sr400ac --vlan-filtering
```

`--attach` uses a board already booted into Linux instead of cycling power.
It still changes the dedicated Ethernet links and board test ports.
`--output PATH` selects the artifact directory.

## Cabling and boot

`board-configs/lab.toml` lists dedicated host adapters, candidate DSA ports,
the conduit and the corresponding target/profile in the boot configuration.
EA4500 tests only `ethernet1`–`ethernet4`; its uncabled `internet` port is
excluded from discovery and test configuration. SR400AC and Lamobo R1 use
`lan1`–`lan4` and `wan`. The default files reference the existing local builds:

| Board | Boot profile | Kernel image |
| --- | --- | --- |
| EA4500 | `ea4500/arm` | `tftp/e4500/uImage` |
| SR400AC | `smartrg/arm` | `zImage.bcm4708-smartrg-sr400ac` |
| Lamobo R1 | `lamobo-r1/arm` | `lamobo-r1/zImage` plus its DTB |

The runner snapshots host link/address/main-route state without changing it,
then boots and logs in. Only after boot does it raise the conduit/ports and
host links. Each selected adapter moves into its own network namespace after boot.
The control/UART process stays in the original host namespace. It temporarily bridges the candidate DSA ports, sends three signed
mz broadcast packets with a unique source MAC from each host interface, and
reads the bridge FDB over UART to identify the learned ingress port. tcpdump
must be ready and confirm transmission of the probes. This does not depend on
host link-down causing physical carrier loss. The temporary bridge is removed
on completion and on probe failure.

No test configuration is changed during initial TFTP transfer. `cabling.json`
records the discovered mapping; list order assigns the historical `HOST_LAN0`
through `HOST_LAN3` names. Discovery pcaps, probe MACs and TX/RX counts are saved
under `discovery/`. Each host interface must map to a distinct candidate DSA
port. Missing or ambiguous learning fails explicitly, with the probe MAC and
FDB output in the error. Direct cables remain the intended test topology.

Tests run on dedicated, unenslaved host interfaces. Each cable gets an independent
network stack, so clients may share a subnet without sharing local routes, ARP/ND,
firewall state or multicast memberships. `mz`, `tcpdump`, ping, host VLAN uppers
and multicast membership helpers run in the corresponding namespace. Legacy
ping/ARP calls select the client by its configured subnet; ambiguous calls fail
explicitly (the ping/ARP-read helper API also accepts an explicit interface).
`host-namespaces.json` records names as namespaces are created, including partial
setup. Namespace names are unique to the run. Cleanup reaps multicast helpers,
returns physical interfaces to the original namespace, removes the owned
namespaces, and restores MTUs along with the saved host configuration.
On startup, under the exclusive lab lock, the runner checks for missing host
interfaces. It can recover them from idle namespaces recorded in earlier results
for the same board, using the saved original settings before taking the new
snapshot or starting TFTP. It refuses namespaces with live processes and never
adopts unrecorded namespaces. `host-namespace-recovery.json` records recovery.

If the runner is killed with SIGKILL, namespaces may remain: use the recorded
mapping to move each adapter back with `ip -n NAME link set dev INTERFACE netns
HOST_PID` (a process in the original host namespace), then `ip netns del NAME`.
The original settings remain recorded in `host-before.json` for recovery.

Tests run on dedicated, unenslaved host interfaces. Normal completion and
exceptions trigger host address, basic main-route and administrative link-state
restoration. `host-before.json` retains the snapshot, and restoration failures
fail the run. Complex policy routes, DHCP lease state and network-manager
configuration are not restored. The board remains booted, with test addresses
removed, run-created bridges removed and candidate ports down. The runner
restores board port MAC addresses changed by the tests. It does not reconstruct
arbitrary board networking from before the run.

The root filesystem needs `ip`, `bridge`, `ethtool`, a shell and the ordinary
BusyBox commands used by `boot_smoke.py`. No generator or capture tool is
required on the board. New control tests use full iproute2 JSON output; TC
cases additionally use target `tc`. Explicit userspace STP needs recent
iproute2 `stp_mode` support and otherwise reports SKIP. Kernel support must include DSA and the board's switch
and tag drivers, bridge/IGMP snooping, IPv4/IPv6 8021q, and bridge VLAN filtering for
the new VLAN tests and legacy `--vlan-filtering`. Existing EA4500 and Lamobo builds enable these features.
The runner records `uname -a` and `/proc/cmdline` to identify the actual build.

## Test coverage

The default profile is now `home-office`: 36 methods in `home-office`,
`dsa-docs`, `individual` and `multicast`. See [home/office acceptance coverage](home-office.md)
for everyday scenarios and the DSA documentation checklist. Use `--profile all`
for all 250 methods; explicit `--suite` options override the profile.


The runner now discovers 250 methods across 18 suites, including 190 added
VLAN and Linux switch scenarios. See [the complete test catalog](test-catalog.md)
for every new case, upstream sources, capability requirements and porting limits.
`--plan --list-tests` lists every individual method without hardware access.
The twelve added suites clean up every method independently and continue after
a failure; the six legacy suites below retain their stateful ordering.


| Suite | Methods | Coverage |
| --- | ---: | --- |
| `individual` | 7 | Individual-port IPv4/IPv6 ping, ARP MAC, fragmented ping, link down |
| `bridge-ping` | 12 | Move each port's IP between a standalone port and single-port bridge |
| `bridge` | 6 | IPv4/IPv6 learned and unknown unicast, broadcast, standalone-port isolation, blocked port, MAC movement |
| `two-bridges` | 15 | Existing two-bridge IPv4/IPv6 forwarding and cross-bridge isolation |
| `multicast` | 11 | Existing IGMP snooping, receiver selection, standalone isolation, optional CPU membership |
| `mac-learning` | 9 | Existing FDB checks and 384/1020/3072 source-MAC learning stress; two chip-specific skips |

In the six legacy suites, numeric method ordering is intentional: each suite builds a
topology, tests it and tears it down. A failure stops that suite because later
methods depend on its state. Subsequent selected suites receive a clean test
topology. JSON includes planned and executed counts and explicit `NOT_RUN` records
for methods blocked by an earlier failure.

The Marvell 6352/6390 collision-hash and member-violation tests are explicitly
skipped: these boards use a Marvell 6171 or Broadcom b53, rather than those
chips. CPU multicast membership tests require the legacy `join-mcast-group`
helper in the target PATH. Without it, those three methods are explicitly
skipped; ordinary multicast forwarding and isolation remain mandatory.
Eight-port, multi-chip and PTP scenarios remain in the repository but are not
selected by this four-port runner. PTP requires additional capable targets.

Driver-specific ethtool counters are supplementary. Missing names appear in
`unavailable_counters` in `results.json`; they never replace tcpdump reception
checks. `--strict-counters` fails if a requested counter is unavailable.
SR400AC maps the four hardware counters used by these suites as follows:

| Test counter | b53 counter |
| --- | --- |
| `in_unicast` | `RxUnicastPkts` |
| `out_unicast` | `TxUnicastPkts` |
| `in_multicasts` | `RxMulticastPkts` |
| `out_multicasts` | `TxMulticastPkts` |

The DSA software `rx_packets`/`tx_packets` counters retain their own meanings;
they are not substituted with hardware counts. Use `--strict-counters` for
the SR400AC comparison so missing mappings cannot silently bypass assertions.
If a driver exposes equivalent counters under different names, add a mapping
under `[boards.NAME.counter_map]`, for example a legacy name as the key and
the observed driver counter name as the value. Only map counters with verified
equivalent semantics. Traffic/FDB tests demonstrate forwarding and learning;
they alone do not prove that every packet was forwarded in hardware.

## Packet accounting and artifacts

The retained `Traffic` API builds valid 128-byte Ethernet/UDP frames and asks
mz to transmit them in raw Ethernet mode. IPv4/IPv6 checksums, multicast MAC
mapping and deterministic stepped source MAC addresses are generated locally.
Every stream is finite. Independent source ports transmit concurrently; streams
on the same source retain their order.

Before transmitting, tcpdump must report ready on every participating host
interface in both `-Q in` and `-Q out` directions. It uses `--immediate-mode`
to disable libpcap capture buffering, plus `-U` to flush delivered packets to
the pcap file; this also captures short, sparse discovery probes. After transmission it gets a
bounded drain interval and SIGINT to flush pcaps. A unique per-run binary
payload signature distinguishes test packets from ARP, IGMP, unrelated UDP and
previous runs. Packet counts come from the captured frames, including TX;
a mismatch between requested and captured TX fails the run. Capture startup
failure, a nonzero exit, dropped kernel packets and truncated pcaps also fail.
Exact reception assertions catch both missing packets and leakage onto ports
that should receive zero packets.

Each timestamped run produces:

- `console.log`, discovered `cabling.json`, and `host-before.json`.
- `results.json` with per-method status, skips, kernel identity, counter gaps,
  infrastructure failures and cleanup errors; `junit.xml` for CI.
- Per-suite `captures/NNNN/` containing inbound/outbound pcaps and tcpdump logs,
  mz output, stream descriptions/signature, and captured statistics.

Kernel panics detected on the live console fail the run. The runner does not
request a `dmesg` dump; kernel messages already sent to UART remain in
`console.log`.

## Offline verification

```sh
python3 -m unittest discover -s selftests -v
python3 -m compileall -q tests run_lab.py
```

Regression tests check v4/v6 packet checksums, real tcpdump decoding of generated
pcaps, signature filtering and truncated capture detection, finite mz commands
and source-MAC stepping, UART echo removal, missing-counter behavior and cable
packet-based cable discovery and failure cleanup. These are offline checks, not evidence of board tests passing.

## Pytest progress and summaries

Install/update the runner environment, then use the usual command:

```sh
.venv/bin/pip install -r requirements.txt
sudo .venv/bin/python -u run_lab.py --board sr400ac --attach --strict-counters
```

One pytest session covers all selected methods, with an ASCII progress bar,
current case, completed/planned counts, percentage, per-case duration and running
PASS/FAIL/ERROR/SKIP counts. Lines work both directly in a terminal and through
`tee`; a long method remains marked RUN until its packet subcases complete.
The final summary has one row per suite, with separate NOT_RUN counts and
failure/skip explanations. Pytest's native summary includes NOT_RUN cases in
its skipped total; the DSA table, JSON and JUnit distinguish the two.

Command chatter and subprocess output are captured by default and saved as
`SUITE/Class.test_method.log`. The continuous raw UART transcript is still
`console.log`; packet pcaps and scenario artifacts retain their existing paths.
Add `--live-log` to print command/UART output during the progress display.
For example, in another terminal:

```sh
tail -f results/sr400ac/<run>/console.log
```

`results.json` updates atomically after each completed method, with durations
and log paths. Final JSON and `junit.xml` also include cleanup/infrastructure
errors after pytest exits. Global boot/discovery still happens before pytest
starts. A suite initialization failure blocks that suite; a suite cleanup
failure stops further testing so the next topology cannot inherit broken state.
Independent scenarios continue after ordinary test failures. Stateful legacy
suites preserve their original stop-on-first-failure behavior. Ctrl+C records
an interrupted case as ERROR and attempts case, suite and global cleanup.

The adapter uses pytest's custom collector interface and runs existing unittest
cases with their subtest and cleanup semantics intact. It disables automatic
third-party plugin loading to keep hardware ordering deterministic. Plan-only
commands still work without importing pytest or accessing hardware.

For offline checks, bare pytest is configured to collect only `selftests/`:

```sh
.venv/bin/python -m pytest
```

Use `run_lab.py` for hardware runs so the UART, power, discovery and restoration
steps are always applied. Pytest reports provide presentation; the existing
DSA result categories remain the authoritative distinction between an assertion
failure, command/setup error and an unexecuted state-dependent case.

### Namespace regression checks

The offline checks cover namespace selection, overlapping subnets, VLAN upper
placement, multicast helper lifetime and cleanup after a partial setup failure.
A root-only smoke test also creates a disposable veth pair, puts each end in a
separate namespace, and checks ping, multicast socket creation, and actual
mz/tcpdump packet counts. It does not touch board or USB interfaces:

```sh
sudo .venv/bin/python -m pytest -q selftests/test_host_netns.py
```

The smoke test requires the same host tools and namespace privileges as the lab
runner. It is skipped in unprivileged offline runs.

### Multicast debugging evidence

The legacy `multicast` suite starts continuous, non-promiscuous captures on all
four host interfaces before bridge setup. These captures retain all Ethernet
frames (including IGMP, tagged traffic and multicast data) in `multicast/debug/`,
alongside tcpdump completion/drop statistics. Captures stop and processes are
reaped before suite cleanup, including when a test fails.

`multicast/debug/state.json` records host namespace memberships (`ip maddress`
and `/proc/net/igmp`), host link statistics, bridge MDB, bridge/router-port link
state, bridge attributes and FDB dumps. MDB polling checks for both intended
members of `224.42.42.42` after the normal join delay. Missing membership is
reported explicitly and the data reproduction still runs. Diagnostic command
errors are saved with their raw output; a software FDB/MDB dump is not guaranteed
to expose an independent hardware table, so compare offload flags and counters.

Before/after switch counters and dynamic packet results are saved before any
forwarding assertion. If dynamic reception/isolation is wrong, a separate static
MDB control uses `239.42.42.43`, the same sender and intended receiver, and 50
packets. Its entry is deleted afterward. Its own captures, statistics and
before/after counters stay separate from the original failing probe. The
original assertions remain authoritative; the control never turns a failure into
a pass. Capture failures or kernel capture drops fail suite cleanup.

A focused rerun on an already booted board is:

```sh
sudo .venv/bin/python -u run_lab.py --board sr400ac --attach \
    --strict-counters --suite multicast
```
