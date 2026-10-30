# Home and small-office acceptance tests

The default profile tests common switch configurations and ordinary changes
people make to them. Linux selftests supply useful assertions, but their full
feature matrix and synthetic stress loads are additional diagnostics.

```sh
python3 run_lab.py --board sr400ac --plan --list-tests
sudo .venv/bin/python -u run_lab.py --board sr400ac --attach --strict-counters
```

The default `home-office` profile selects **36 methods**:

| Suite | Methods | Purpose |
| --- | ---: | --- |
| `home-office` | 8 | Realistic four-cable topologies and changes, listed below |
| `dsa-docs` | 10 | Model-independent DSA documentation acceptance examples |
| `individual` | 7 | Basic IPv4/IPv6, ARP, fragmented ping, link-down behavior |
| `multicast` | 11 | IGMP snooping/receiver selection and optional CPU membership |

## Eight everyday scenarios

1. **Flat LAN:** workstation, printer, NAS and access-point roles share a
   bridge. Learned IPv4/IPv6 unicast reaches only the destination socket;
   broadcast reaches the other LAN sockets.
2. **LAN/WAN separation:** three LAN sockets form a bridge; a separate
   router-facing interface remains reachable from its directly connected
   peer. Ethernet broadcasts cannot cross between WAN and LAN.
3. **Staff, guest and IoT networks:** three untagged access sockets use VIDs
   10, 20 and 30. A fourth socket carries those VLANs tagged to a router,
   managed access point or another switch. Both directions and both IP
   versions are checked, including tag insertion/removal and isolation.
4. **Gateway access:** each access VLAN reaches its own switch-CPU VLAN
   interface using IPv4 and IPv6.
5. **Admission:** an untagged frame on the tagged-only uplink, an unknown VID,
   and another VLAN injected through an access socket are rejected.
6. **Disconnect/reconnect:** one socket is lowered and raised while the
   remaining LAN sockets continue forwarding; the returning socket works.
7. **Move a device:** a source MAC moves from one socket to another; subsequent
   unicast follows it rather than continuing toward its old socket.
8. **Reassign a socket:** a guest socket is moved to the staff VLAN without
   rebooting; forwarding and isolation immediately follow the new membership.

Host endpoints represent these roles using ordinary ICMP and signed mz packets.
These tests verify switching and CPU reachability; they do not run printer,
NAS, DHCP, DNS, NAT, firewall or application workloads. Gateway reachability
alone is not a claim that Internet routing or firewall policy was tested.
The multicast suite supplies additional real IGMP membership coverage.

Roles refer to the four **discovered, cabled** ports. They do not depend on
physical labels, switch model, or a cable on the EA4500 Internet jack. In the
WAN-separation case, the fourth test socket represents a WAN-facing port. Each
home-office method saves `topology.json` with its role-to-cable mapping.

## Documentation acceptance checklist

Source: `$HOME/dev/linux/Documentation/networking/dsa/`, revision
`3d79bab106de0576cdcc57af021c9528262e7f94`. The directory has seven RST files;
each is accounted for below. These are implemented test cases, not hardware
PASS claims. Board execution of the new suites remains outstanding.

| Source and example | Executable coverage |
| --- | --- |
| `configuration.rst`: single port with native tagging | `dsa-docs.test_single_port_showcase`: three independent interfaces, exact documented /30 addresses, CPU reception and no peer leakage |
| `configuration.rst`: bridge with native tagging | `dsa-docs.test_bridge_showcase`: exact bridge address .129/25, CPU ping from each of three LAN peers, LAN broadcast and outside-port isolation |
| `configuration.rst`: gateway with native tagging | `dsa-docs.test_gateway_showcase`: LAN bridge .129/25 and separate WAN .1/30, CPU reception on both sides and no L2 leakage |
| `configuration.rst`: single port without a DSA tagger | Same single-port case selects conduit VLAN uppers 1/2/3 when the runtime `dsa/tagging` value is `none` |
| `configuration.rst`: bridge without a DSA tagger | Same bridge case selects a filtering bridge with the conduit VID-1 upper joined as documented |
| `configuration.rst`: gateway without a DSA tagger | Same gateway case selects LAN VID 1 and WAN VID 2 using conduit VLAN uppers |
| `b53.rst`: tagged configuration | Uses the same three cases as every native DSA tagger |
| `b53.rst`: three tag-protocol-none configurations | Same no-tag recipes; default VID 1 is removed from access ports assigned another PVID. This is applied by configuration semantics, not a b53/model-name check |
| `configuration.rst`: conduit/user administrative state | `test_user_up_automatically_raises_conduit`, `test_conduit_down_lowers_users`; also verify reception after recovery |
| `configuration.rst`: current `master static` FDB recipe | `test_master_static_fdb`: add/steer/delete, using the shared Linux FDB assertions |
| `configuration.rst`: current local FDB semantics | `test_master_local_fdb` verifies bridge-local termination. `test_implicit_local_fdb_is_device_local` verifies exclusive device-local add/delete/re-add and absence from the bridge master FDB; it does not require hardware flooding suppression |
| `configuration.rst`: obsolete `self static` recipe | `test_self_static_is_not_a_hardware_forwarding_entry`: it cannot steer hardware unicast; the specific default-handler EINVAL or unsupported-operation rejection is accepted, then unchanged forwarding is checked |
| `dsa.rst`: inspect active tagging protocol | `test_read_tagging_protocol`, and recipe selection in each topology case |
| `lan9303.rst`: separate ports, join bridge, leave bridge | Existing individual tests, bridge showcase, and `home-office` device forwarding; detailed leave/rejoin regression remains selectable in `linux-bridge` |
| `bcm_sf2.rst`: ordinary tagged DSA operation | The same single-port/bridge/gateway cases; no separate switch-model expectations |
| `sja1105.rst`: ordinary standalone and aware/unaware bridging | The same common recipes, plus `vlan`/`vlan-admission` filtering-mode tests in the full profile |
| `index.rst` | Index only; no executable configuration example |

Only the active tagger's recipe runs on a particular boot. A native-tagger
board cannot validate reception on a `DSA_TAG_PROTO_NONE` CPU path by replaying
commands meant for different hardware. The selected recipe and actual protocol
are saved in `documentation-layout.json`. No driver name selects pass/fail
expectations or silently skips these common cases.

The no-tag recipe is implemented but has not been validated on a no-tag board.
The runner's existing discovery also relies on readable learned hardware FDB
entries; a future no-tag target must support that discovery mechanism or supply
an adapted discovery method. All current lab targets use native tagging.

The documentation sometimes discusses historical bugs and pre-5.12/pre-5.14
semantics. The suite checks current upstream behavior, not the old bugs. The
no-tag single-port recipe additionally applies the default-VID cleanup
explicitly documented in b53.rst instead of retaining accidental membership.

## Specialist prerequisites, outside the default acceptance scope

As agreed, these examples are documented rather than counted as implemented
home/office tests:

| Documentation example | Requirements for meaningful validation |
| --- | --- |
| Static affinity to multiple CPU ports | At least two firmware-described eligible CPU/conduit links, working alternate data paths, and a configuration declaring those links |
| Explicit/implicit CPU-port bonding | Those multiple CPU links, bonding/team support, switch/driver LAG support and failover/load-distribution measurements |
| Runtime tag-protocol change | Hardware/driver support for an alternate tagger, matching kernel modules and receive/transmit validation before and after the change; all user ports and conduit down during the change |
| SJA1105 redirect+trap / MAC+VID+PCP flow examples | Matching flower keys and composed actions offloaded by the hardware, and a CPU receive observer for the trap copy. Basic drop/redirect/mirror diagnostics are available in `linux-tc`, but do not fully implement these examples |
| SJA1105 cyclic TAPRIO schedule | TSN-capable egress port, compatible queue mapping, PHC/PTP tools and hardware timestamping to verify the 100/400-microsecond windows |
| SJA1105 phase-aligned ingress gate and sender TAPRIO | Two capable endpoints, synchronized PHCs, propagation-delay budget and timestamped validation of admission windows |
| SJA1105 shared gate across ingress ports | Shared hardware flow blocks/gate support, multiple ingress sources and the same timing instrumentation |
| SF2 MoCA / Wake-on-LAN | MoCA firmware/coax peer or a suspend/resume/WoL-capable platform and independent wake observer |
| Board PHY delays, RMII clock wiring and device-tree examples | Appropriate board design and physical instrumentation; software setup success alone is not a validation |

## Diagnostics and regression profiles

`--profile all` retains every existing VLAN/bridge/FDB/MDB/TC/routing test and
stress case. It currently discovers **250 methods** across 18 suites. Repeated
`--suite` options override either profile for focused investigation.

```sh
sudo .venv/bin/python -u run_lab.py --board sr400ac --attach --profile all
sudo .venv/bin/python -u run_lab.py --board sr400ac --attach --suite dsa-docs
```

The complete catalog and upstream source map remain in `test-catalog.md`,
`test-list.json` and `linux-selftest-port-map.json`. Functional results and
hardware offload assertions are distinguished there. New scenarios have
independent cleanup and continue after a failure. Unsupported optional
capabilities are explicit SKIPs, never inferred from a switch model name.
