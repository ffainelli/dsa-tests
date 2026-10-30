Distributed Switch Architecture tests

The Python 3 lab runner uses **mz/mausezahn + tcpdump** and reuses
`boot_smoke.py` for UART boot/control. It supports Linksys EA4500,
SmartRG SR400AC and Lamobo R1 with the existing upstream-kernel boot profiles.
The default run focuses on [home/office scenarios and documented DSA configurations](docs/home-office.md).
Use `--profile all` for the complete regression and stress collection.
Runs use pytest progress bars and per-suite summaries; see
[progress, logs and reporting](docs/lab-runner.md#pytest-progress-and-summaries).
Board commands use a [verified staged-script transport](docs/lab-runner.md#verified-script-transport)
with checksummed uploads/results and result retrieval without re-execution.
See [the lab runner guide](docs/lab-runner.md) for cabling, dependencies,
coverage, capability skips, results and commands.
The [test catalog](docs/test-catalog.md) describes all 250 methods, including
190 added VLAN, bridge, FDB/MDB, isolation, TC and routing scenarios.

```sh
python3 run_lab.py --board ea4500 --plan
sudo .venv/bin/python run_lab.py --board ea4500
```


Directory structure:

bin
===

Collection of shell scripts for building, controlling relays/power controls of
boards and running a tftpboot sequence.

board-configs
=============

Board configuration files describing both the host device running the tests and
the System Under Test.

docs
====

Documentation sources under LyX format.

jenkins-jobs
============

YAML files to trigger builds and tests using jenkins-build.

kernel-configs
==============

Kernel configuration fragments to build the kernel with for the different boards
support.

src
===

Sources for miscellaneous applications used for testing (e.g: relay status
display).

tests
=====

Python sources of the different tests being ran on the boards.
