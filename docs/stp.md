# Classic STP tests

The `stp` suite tests the Linux bridge's in-kernel 802.1D STP implementation.
It does not require mstpd. Run it using the normal four independent host
clients; do not connect the clients together or change the lab cabling:

```sh
.venv/bin/python run_lab.py --board ea4500 --suite stp --plan --list-tests
sudo .venv/bin/python run_lab.py --board ea4500 --suite stp
```

The suite is included in `--profile all`. The default home-office profile
remains unchanged. Each of the six cases creates and cleans up its own bridge:

| Case | Required behavior |
| --- | --- |
| Listening | No source-MAC learning or data forwarding; subsequent convergence restores forwarding. |
| Learning | Learn the source MAC without forwarding data; after convergence, unicast reaches the learned port. |
| Root/BPDUs | The standalone bridge is root, emits classic configuration BPDUs on each member with its own bridge/root ID and zero root cost, and consumes the reserved bridge-group destination. |
| Link recovery | A down port becomes disabled and receives no data; bringing it up reconverges and restores bidirectional forwarding. |
| Rejoin | Removing a member isolates it; rejoining reconverges and restores bidirectional forwarding. |
| Disable | Disabling STP preserves forwarding; a link bounce restores forwarding without STP delay. |

Tests select `stp_mode kernel` before enabling STP. If the kernel or iproute2
predates explicit mode selection, the legacy auto/helper path is allowed,
but `/sys/class/net/BRIDGE/bridge/stp_state` must report **1**. A helper that
selects userspace STP (**2**) produces a capability skip with a diagnostic.
The suite does not stop daemons or alter `/sbin/bridge-stp` on the board.
Unexpected selection errors fail rather than becoming capability skips.

The forwarding delay is eight seconds per listening/learning stage and the
hello interval is one second (iproute2 arguments are 800 and 100 clock ticks).
State waits are bounded and run on the board to avoid polling over UART.
Listening/learning probes check the state after the packet/FDB assertions;
a probe that overruns its state window fails explicitly. Expect several
minutes for the complete suite. Commands and state observations are saved in
`commands.json`; raw incoming PCAPs retain BPDUs as well as test packets.
Signed traffic counters exclude BPDUs, so protocol traffic cannot satisfy a
data-forwarding assertion.

These are single-bridge state-machine and data-path tests, not a protocol
conformance suite. The current clients do not send competing BPDUs. Root
competition, redundant-path blocking, topology-change handling and failover
need a controlled peer bridge or BPDU generator and additional assertions.
RSTP rapid convergence and MSTP instance/VLAN behavior require a userspace
protocol engine such as mstpd. Enabling `stp_mode user` alone does not start
such an engine. MSTP also requires compatible kernel, driver and VLAN-instance
support; installing a daemon alone does not establish that support.

References: [Linux bridge STP mode selection](https://www.kernel.org/doc/html/latest/networking/bridge.html#stp-mode-selection)
and the [mstpd project](https://github.com/mstpd/mstpd).

Offline validation:

```sh
.venv/bin/python -m pytest selftests/test_stp.py
```

Offline tests check BPDU parsing, ownership selection and failure detection
for missing learning or missed state windows. Hardware execution is still
required to validate timers, BPDU delivery and switch forwarding behavior.
