# b53 MDB and mirror regressions

Run the seven hardware cases on a four-cable b53/SF2 board:

```sh
.venv/bin/python run_lab.py --board sr400ac --suite b53-regressions --plan --list-tests
sudo .venv/bin/python run_lab.py --board sr400ac --suite b53-regressions
```

Use `--attach` only when the board already runs the kernel to test. The suite
is also included in the `all` profile. Other switch-driver families skip it.
Each case owns its bridge, clsact qdiscs and filters; the existing runner owns
host network namespaces and restores the host interfaces.

| Case | Regression |
|---|---|
| `test_r6_mdb_reuses_hole_without_stale_ports` | A new multicast entry must not inherit another ARL bin's destination ports. |
| `test_r7_ingress_conflicting_destination` | A second ingress capture destination must be rejected without disturbing the first. |
| `test_r7_egress_conflicting_destination` | The corresponding egress check. |
| `test_r7_cross_direction_conflicting_destination` | Ingress and egress share the same global capture destination. |
| `test_r8_ingress_shared_membership` | Deleting one of two filters must retain their shared ingress source bit. |
| `test_r8_egress_shared_membership` | The corresponding egress reference-count check. |
| `test_r8_direction_memberships_are_independent` | Deleting an egress membership must not clear ingress mirroring on the same source port. |

All mirror rules use `matchall skip_sw`; the test also requires `in_hw` in
`tc -s filter show`. The first unsupported mirror installation may skip;
subsequent unexpected failures fail the case. Normal unicast forwarding is
checked alongside the capture, and both possible capture ports are monitored.
R7/R8 also check destination reuse after the last rule is deleted.

R6 requires more than ordinary MDB add/delete testing. It creates five IPv4
multicast addresses whose Ethernet MACs have identical full CRC-CCITT values.
Four permanent MDB entries target the fourth port. Each must report hardware
offload. The fifth must fail with ENOSPC or finish with `offload_failed`,
confirming saturation before the test creates a hole. A pending result is not
accepted as saturation. If all five offload, the hash/bucket fixture is not
applicable and the test explicitly skips instead of passing.

After removing the first entry, the fifth is installed for the second port.
A signed UDP probe must reach only that port, including no packet on the old
fourth-port destination. An unrelated colliding entry must still forward to
the fourth port. Unknown multicast flooding and learning are disabled; the
bridge's startup-querier interval is allowed to expire. This is intended for
the four-bin b53 ARL layout; other layouts or unavailable offload evidence
produce an explicit fixture skip. The exact groups, board commands, MDB JSON,
`tc` output, per-probe PCAPs and counts remain in the suite's result directory.

Offline checks, without touching hardware:

```sh
.venv/bin/python -m pytest selftests/test_b53_regressions.py
```

These validate collision generation, fixture verification, hardware-family
selection, and scenario detection against injected faults. They are not a
substitute for running the seven cases against the old and patched kernels.
