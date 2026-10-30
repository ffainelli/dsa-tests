# Classic STP tests

The `stp` suite tests the Linux bridge's in-kernel 802.1D STP implementation.
It does not require mstpd. Run it using the normal four independent host
clients; do not connect the clients together or change the lab cabling:

```sh
.venv/bin/python run_lab.py --board ea4500 --suite stp --plan --list-tests
sudo .venv/bin/python run_lab.py --board ea4500 --suite stp
```

The suite is included in `--profile all`. The default home-office profile
remains unchanged. Each of the thirteen cases creates and cleans up its own bridge:

| Case | Required behavior |
| --- | --- |
| Listening | No source-MAC learning or data forwarding; subsequent convergence restores forwarding. |
| Learning | Learn the source MAC without forwarding data; after convergence, unicast reaches the learned port. |
| Root/BPDUs | The standalone bridge is root, emits classic configuration BPDUs on each member with its own bridge/root ID and zero root cost, and consumes the reserved bridge-group destination. |
| Link recovery | A down port becomes disabled and receives no data; bringing it up reconverges and restores bidirectional forwarding. |
| Rejoin | Removing a member isolates it; rejoining reconverges and restores bidirectional forwarding. |
| Injected superior | A superior BPDU on each of the four mapped DSA user ports selects the advertised root, correct root port and path cost. |
| Injected inferior | Inferior peers leave the local bridge as root; a superior positive control must still work. |
| Invalid BPDUs | Bad protocol ID, unsupported version/type, expired message age and a truncated body do not replace the root; a valid positive control must still work. |
| Competing paths | Two advertisements from one peer select the lower-cost port and block data in both directions on the other port. |
| BPDU guard | A valid received BPDU disables the guarded port, suppresses data in both directions, and a link bounce after clearing guard restores forwarding. |
| Root guard | A protected port cannot take over as root or forward data; an unprotected port still accepts a superior root. |
| Peer expiry | After verified root adoption, stopping advertisements restores the local bridge as root within a bounded deadline. |
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
Signed data counters exclude ordinary BPDUs, so protocol traffic cannot satisfy
a data-forwarding assertion. Injected BPDUs have their own marked runs: a unique
marker follows the declared 802.3 LLC/BPDU payload in Ethernet padding. The
marker preserves every BPDU byte and the declared length, including truncated
negative cases. The host TX capture must contain exactly three marked frames,
and no client incoming capture may contain a forwarded copy of an injected
frame. Raw PCAPs, stream metadata and mz logs retain the input and evidence.

The injected frames model a root/designated peer, including two ports on the
same peer for competing-path tests. Default wire timers are max age 12 seconds,
hello 1 second and forward delay 8 seconds; the expiry case uses max age 6 and
forward delay 4. Advertisements are finite bursts, not a running protocol peer.
The competing-path case refreshes both advertisements before its second data
probe and checks root/port state afterward to detect expiry during observation.
The superior-root case exercises all four mapped DSA user ports, adding the
fourth to the bridge for its final subtest. Other cases keep the fourth client
outside the bridge and check it for leaked injected frames. The bridge priority
is explicitly set to 32768 so superior/inferior comparisons are deterministic.

These are state-machine and data-path regression tests, not a protocol
conformance suite. Physical loop failover, continuous peer keepalives,
topology-change notification/acknowledgement and RSTP/MSTP interoperability
still require additional scenarios. No host clients are bridged together.
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
for missing learning, missed state windows, incorrect root identity/port/cost
and leaked injected frames. They also check wire lengths/timers, signed raw
frame accounting, short-body preservation and decoding with real tcpdump. Hardware execution is still
required to validate timers, BPDU delivery and switch forwarding behavior.
