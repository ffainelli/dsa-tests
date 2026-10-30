# DSA lab test catalog

The runner discovers **250 methods: 60 existing + 190 added**, across 18
selectable suites. The default home-office profile selects 36 methods;
`--profile all` selects the complete collection. See [home/office scenarios and
the DSA documentation checklist](home-office.md). [Every method ID is listed here](test-list.json). Parameterized subtests exercise additional packet matrices.
The twelve new suites use independent setup/cleanup for every method and continue
after failures. They use the discovered four-cable mapping on EA4500, SR400AC
and Lamobo R1; the uncabled WAN/internet port is not required.

## Sources and validation

- [dsatest PR #27](https://github.com/netdsa/dsatest/pull/27), fetched head
  `19c594065f6d`: VLAN uppers, VIDs 0–9 on every host/board cable. The new
  runner reports each port/VID separately. Unlike the old setup, each of the 40 cases creates only its own upper. Four additional cases
  preserve simultaneous creation of all ten host/board VLAN uppers. VID 0 remains an explicit priority-tag regression case.
- [dsatest PR #17](https://github.com/netdsa/dsatest/pull/17), fetched head
  `125745c69191`: IPv4 ICMP payload sizes 1, 32, 1472. The old test's undefined
  `get_address` reference is not retained. Size 1472 produces a 1500-byte IP
  packet, or a 1514-byte Ethernet frame excluding FCS.
- Linux source: `/home/florian/dev/linux`, revision
  `3d79bab106de0576cdcc57af021c9528262e7f94`, under
  `tools/testing/selftests/net`, `net/forwarding`, and `drivers/net/dsa`.
  These are scenario adaptations, not unmodified shell selftest executions.
  The upstream scripts assume all NETIFS are controlled by one kernel. Here,
  mz/tcpdump and host configuration run locally; switch configuration runs over
  UART. No Ostinato dependency is introduced.

**Validation so far:** 55 offline checks pass, including real tcpdump decoding,
checksums, tag parsing, capture lifecycle, failure cleanup, and hardware-free
plans for all three boards. The 190 new hardware methods have **not** yet been
run on the boards. Discovery is not a hardware PASS result.

## Added suites

| Suite | Methods | Coverage |
| --- | ---: | --- |
| `home-office` | 8 | Flat LAN, WAN separation, staff/guest/IoT access/trunk, gateways, admission, reconnection, device moves and socket reassignment |
| `dsa-docs` | 10 | Documented single-port/bridge/gateway recipes, native/no-tag mode selection, FDB semantics and conduit lifecycle |
| `vlan` | 57 | 40 PR27 port/VID combinations; 4 simultaneous-upper cases; 10 IPv4/IPv6 VLAN-termination topologies; 3 CPU VLAN-admission regressions |
| `vlan-admission` | 17 | Tagged membership, ingress rejection, no-PVID handling, priority tags, PVID classification, tag insertion/removal/preservation, deletion/rejoin/toggle regressions |
| `ping-sizes` | 24 | PR17's three payload sizes on every standalone port and every port of a common bridge |
| `linux-bridge` | 36 | Bridge flags/lifecycle, VLAN dumps, backup port, FDB flush, STP modes, VLAN bridge binding, MTU |
| `linux-fdb` | 9 | Learning/forwarding/roaming, sticky/extern/local entries, ageing, per-VID lookup, concurrent FDB operations |
| `linux-mdb` | 7 | IPv4/IPv6 permanent MDB membership, VLAN-scoped membership, programming a down port, VLAN isolation |
| `linux-locked-port` | 4 | IPv4/IPv6/tagged admission and static authorization; basic MAB authorization |
| `linux-no-forwarding` | 3 | Standalone ports, separate bridges, and one bridge with different PVIDs |
| `linux-tc` | 7 | Hardware flower drop matches and ingress mirror/redirect |
| `linux-routing` | 8 | IPv4/IPv6 routing through standalone ports, VLAN uppers, VLAN-unaware bridge, VLAN-aware bridge upper |

### `vlan`: 57 methods

`test_pr27_port{0..3}_vid{0..9}` checks CPU reachability through matching host
and standalone DSA-port VLAN uppers: 40 independently reported tests.
`test_pr27_simultaneous_uppers_port{0..3}` additionally creates all ten uppers
on both ends of a cable before pinging each VID, preserving PR27's setup.
Distinct IP subnets avoid route ambiguity between the concurrent uppers.

`test_{standalone,port_unaware,port_aware,bridge_unaware,bridge_aware}_ipv{4,6}`
checks 10 configurations with VID 100. `port_*` creates a VLAN upper on the
physical bridge member; `bridge_*` creates it on the bridge device. Aware
bridge uppers explicitly configure port and bridge-self VID membership. These
adapt the termination topologies from Linux `local_termination.sh`; they test
ICMP termination, not that script's entire MAC filtering/multicast/PTP matrix.

Three additional methods check wrong-VID rejection, missing bridge-self VID
rejection, and successful bridge-self member reception. CPU termination tests
check ping's result and independently capture ICMP replies using host tcpdump.

### `vlan-admission`: 17 methods

| Method suffix | Assertion |
| --- | --- |
| `tagged_member` | An admitted VID reaches only its member, with its tag intact |
| `tagged_nonmember` | A VID known elsewhere is rejected on a nonmember ingress |
| `unknown_vid` | An unconfigured VID is rejected |
| `untagged_no_pvid` | Untagged ingress is rejected without a PVID |
| `priority_tag_no_pvid` | PCP-tagged VID 0 ingress is rejected without a PVID |
| `access_to_trunk` | PVID classification inserts the egress 802.1Q tag |
| `trunk_to_access` | Access egress strips the admitted VID |
| `priority_tag_uses_pvid` | VID 0 selects ingress PVID 100 and preserves PCP 5 on trunk egress |
| `pvid_change` | Changing ingress PVID moves traffic into the other VLAN |
| `delete_member` | Deleting/restoring egress membership drops/restores delivery |
| `delete_unrelated_vlan_preserves_pvid` | Deleting another VID leaves PVID forwarding working |
| `no_pvid_survives_link_cycle` | Down/up does not re-enable untagged/VID-0 admission |
| `no_pvid_survives_bridge_rejoin` | Leave/rejoin does not install a default PVID when disabled |
| `unaware_preserves_vlan` | Filtering-off bridge preserves VIDs 1, 100, 4094 |
| `filtering_transition` | Filtering 0→1 rejects unknown VID, membership admits it, 1→0 passes other VIDs |
| `other_tpid_uses_pvid` | 802.1ad/802.1Q stack is treated as untagged by an 802.1Q bridge; removing PVID drops it |
| `high_vid` | VID 4094 membership and wire encoding work |

Sources: `bridge_vlan_aware.sh`, `bridge_vlan_unaware.sh`,
`local_termination.sh`, plus explicit admission and transition regressions.
Every switching assertion checks all four host ingress captures. Tag-sensitive
cases check the full outer-to-inner TPID/TCI stack, including PCP bits.

### `ping-sizes`: 24 methods

`test_pr17_{standalone,bridge}_port{0..3}_size{1,32,1472}` uses a host interface
bound ping and captures echo replies. This preserves the payload sizes and
extends them to a common four-port bridge. Fragmented ping is already covered
by `individual`.

### `linux-bridge`: 36 methods

- 12 bridge data-path cases: learning on/off; unknown-unicast, broadcast and
  multicast flooding flags; isolated ports (IPv4, IPv6, unknown unicast);
  hairpin; link down/up; leave/rejoin; PVID changes with filtering off;
  manually blocked port under userspace STP; reserved link-local MACs 00–0f
  (00 forwards with STP disabled; 01–0f do not).
- 3 control/data-path cases: add/dump/delete VID range 100–110; backup-port
  configuration/persistence across primary down/up with primary forwarding
  before/after; software dynamic FDB flush preserving software static state
  and static forwarding. These do not assume that bridge backup configuration
  offloads hardware failover, or that a master flush clears autonomous hardware
  self entries.
- 8 STP-mode cases: default auto, mode changes, rejection of mode changes
  while STP is active, idempotent active mode, userspace mode, kernel mode,
  atomic mode+state, persistence across disable/enable. Unlike the upstream
  netns test these operate on a temporary bridge with real DSA members.
  Auto-mode helper fallback inside a non-init netns is not ported.
- 8 VLAN bridge-binding cases: binding on/off and toggling in both directions
  while up, while the upper is down, and while the bridge lower is down.
  Carrier follows actual membership of VID 100 when binding is enabled.
- 5 MTU cases: below-minimum/above-maximum rejection on every port, and a
  2000-byte IP echo on each of four ports. Hosts whose advertised maximum MTU
  is below 2000 skip before either MTU is changed. MTUs are restored on cleanup.

Sources: `bridge_port_isolation.sh`, `bridge_vlan_{aware,unaware}.sh`,
`bridge_stp_mode.sh`, `bridge_vlan_dump.sh`, `test_bridge_backup_port.sh`,
`fdb_flush.sh`, `vlan_bridge_binding.sh`, `min_max_mtu.sh`, plus bridge flag and
lifecycle regressions. These are focused subsets where the upstream script
has additional cases.

### `linux-fdb`: 9 methods

Static entry steers known unicast and deletion restores flooding; dynamic MAC
roams between ports; sticky entry resists roaming; extern-learn entry roams;
dynamic entry ages out; extern-learn entry survives ageing; the same destination
MAC resolves to different ports in VIDs 100 and 200; a local entry is not
forwarded; concurrent add/delete and FDB dump complete without a stale entry.
Ageing cases request 3000 USER_HZ ticks (30 seconds), verify bridge readback and
reject logged offload errors; dynamic expiry allows 90 seconds for hardware
quantization and scan timing. This accommodates MV88E6171's 15-second minimum.

Sources: `bridge_vlan_{aware,unaware}.sh`, `bridge_sticky_fdb.sh`, and
`drivers/net/dsa/test_bridge_fdb_stress.sh`. The concurrency test is bounded
(100 add/delete pairs, 30 dumps) rather than an unbounded worker with 150 seconds
of dumps. It verifies command status and final state; deferred driver warnings
still require UART log review. Kernel panic monitoring remains active.

### `linux-mdb`: 7 methods

IPv4 and IPv6 add/forward/delete/drop; those same two cases on VID 100;
program/delete permanent membership while a port is down and verify behavior
when raised (IPv4 and IPv6); same group confined to VID 100 rather than VID 200.
Unknown multicast flooding is disabled on receiver ports to make membership
assertions meaningful. Sources: `bridge_mdb.sh`, `bridge_mld.sh`,
`bridge_vlan_mcast.sh`, `bridge_mdb_port_down.sh` and their DSA wrappers.
These cases use static MDB programming; they do not emulate the full IGMP/MLD
protocol state machines or every source-specific membership combination.

### `linux-locked-port`: 4 methods

IPv4, IPv6 and tagged VID-100 traffic pass before locking, drop after locking,
pass after source-MAC authorization, and pass after unlocking. MAB creates a
locked FDB record, then replacing it with an authorized static record permits
traffic. Source: the basic admission and MAB cases of `bridge_locked_port.sh`.
MAB roaming/flush/redirect and authentication protocol exchange are not ported.

### `linux-no-forwarding`: 3 methods, 315 packet subcases

Each topology checks seven packet classes (IPv4/IPv6 unicast and multicast,
non-IP unicast/multicast, broadcast), untagged plus 14 VLAN values:
0, 1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 4000, 4094.
None may leak into another standalone port, bridge or PVID. Source:
`no_forwarding.sh` and the DSA wrapper. Experimental EtherType 0x88b5 replaces
the upstream four-byte 802.3 payload for the non-IP classes. The source script's
repeated VID 1000 is deduplicated. Each packet run has a fresh random signature.

### `linux-tc`: 7 methods

Flower drop by IPv4 destination, IPv6 destination, UDP destination port,
802.1Q VID, and source MAC; mirred ingress-to-egress mirror and redirect to the
fourth port. Each case checks the baseline, programmed action, and rule removal.
Rules use `skip_sw`, require `in_hw`, and verify real host captures. Sources:
`tc_flower.sh`, `tc_actions.sh`, `mirror_vlan.sh`/`mirror_lib.sh` (basic physical
port action semantics, without the upstream mirror topology/VLAN matrix).

### `linux-routing`: 8 methods

IPv4 and IPv6 forwarding through standalone switch ports, matching VLAN uppers,
a VLAN-unaware bridge ingress, and a VLAN-aware bridge VLAN-upper ingress.
Static egress neighbors avoid ARP/ND dependencies in raw signed packet tests.
Forwarding sysctls, addresses and neighbors are restored/removed afterward.
Sources: `router.sh`, `router_bridge.sh`, `router_bridge_vlan.sh`,
`router_bridge_vlan_upper.sh`. These test functional CPU forwarding, not
hardware routing offload or the upstream multipath/neighbor matrices.

## Existing suites retained: 60 methods

| Suite | Methods | Coverage |
| --- | ---: | --- |
| `individual` | 7 | Standalone IPv4/IPv6, ARP MAC, fragmented ping, link-down negative |
| `bridge-ping` | 12 | Move each port's address between standalone and single-port bridge |
| `bridge` | 6 | IPv4/IPv6 unicast, broadcast, isolation, blocked port, MAC move |
| `two-bridges` | 15 | Two-bridge forwarding, isolation and hardware/software counters |
| `multicast` | 11 | IGMP snooping, selection, isolation and optional CPU membership |
| `mac-learning` | 9 | FDB learning at 384/1020/3072 MACs; chip-specific ATU cases |

The original six suites still stop at their first failure because their methods
share state. Their existing capability skips remain explicit. In particular,
the legacy `bridge` blocked-port test still has the previously diagnosed STP
setup issue; the new independent blocked-port case uses explicit userspace STP.
The 3072-MAC test retains its existing strict expectation pending collision
analysis. These earlier issues have not been silently converted to passes.

## Requirements and boundaries

Host: root/NET_ADMIN+NET_RAW, iproute2, ping, mz/mausezahn and tcpdump.
Board: iproute2 (`ip`, `bridge`, JSON output for relevant control tests), ethtool,
BusyBox/shell, IPv4/IPv6, DSA, bridge and 8021q. No target packet generator or
capture process is needed. `tc` and flower/mirred support are optional for the
TC suite. Explicit bridge `stp_mode` requires a sufficiently new kernel **and**
iproute2. See `kernel-configs/lab-switch-selftests.config`.

An unsupported optional kernel/driver/CLI operation is SKIP with its command
and reason. Invalid configurations and packet mismatches fail. Mandatory
802.1Q setup is not skipped. A software-forwarded functional test can pass;
only `linux-tc` explicitly requires hardware rule offload. Packet capture
validates delivery rather than inferring it solely from counters.

This addition **does not port the entire Linux networking selftest tree**.
`docs/linux-selftest-port-map.json` inventories the source scripts, adapted
coverage and unported portions. Bridge notifications, detailed MDB limits/host
membership, FDB learning limits/local-address sharing, the rest of the MAB and
TC matrices remain follow-up work. Larger LAG, ECMP, tunnel/VXLAN/GRE, MPLS,
PHC/PTP/TSN and scheduler-specific tests need additional adaptation, topology,
tools or driver capabilities; they are not counted as added tests. DSA's
forwarding wrappers reference the same scenarios, not separate duplicate tests.

## Running and artifacts

```sh
python3 run_lab.py --board sr400ac --plan --list-tests
sudo .venv/bin/python -u run_lab.py --board sr400ac --attach --strict-counters \
  --suite vlan --suite vlan-admission --suite ping-sizes
sudo .venv/bin/python -u run_lab.py --board sr400ac --attach --strict-counters \
  --suite linux-bridge --suite linux-fdb --suite linux-mdb \
  --suite linux-locked-port --suite linux-no-forwarding --suite linux-tc \
  --suite linux-routing
```

Omitting `--suite` uses the selected profile (default: home-office, 36 methods).
`--profile all` runs all 18 suites. `--vlan-filtering` still controls the
legacy bridge suite only; new tests explicitly choose each required filtering
mode. The isolation matrix alone contains 315 capture runs, so a complete run
can take tens of minutes, especially over UART.

Results retain the usual JSON/JUnit, kernel identity, cabling and UART log.
Each new method has its own directory containing `commands.json`, signed mz
stream metadata, directional pcaps, capture diagnostics, counts and
`vlan-tags.json`. CPU ping cases also save per-probe ping logs and inbound ICMP pcaps.
Unique upper/bridge names and per-method cleanup remove created devices,
addresses, routes/neighbors and restore modified MTUs/sysctls. A cleanup error
fails the method instead of being hidden. Host snapshot restoration still runs
at the end of the complete invocation.
