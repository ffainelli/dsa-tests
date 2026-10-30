"""Route host operations to one isolated network stack per physical cable."""
import ipaddress

# Configured once by HostLinks, after firmware/TFTP boot has completed.
INTERFACES = {}
ADDRESSES = {}


def register_upper(name, lower):
    if lower in INTERFACES:
        INTERFACES[name] = INTERFACES[lower]


def command(argv, interface=None):
    argv = list(argv)
    candidates = {INTERFACES[a] for a in argv if a in INTERFACES}
    if interface is not None and interface in INTERFACES:
        candidates.add(INTERFACES[interface])
    if not candidates and argv and argv[0] in ('ping', 'arp'):
        try:
            target = ipaddress.ip_address(argv[-1])
        except ValueError:
            target = None
        candidates = {INTERFACES[name] for name, networks in ADDRESSES.items()
                      if name in INTERFACES and target is not None
                      and any(target in network for network in networks)}
        if INTERFACES and not candidates:
            raise RuntimeError('No isolated host interface for: ' + ' '.join(argv))
    if len(candidates) > 1:
        raise RuntimeError('Ambiguous host namespace: ' + ' '.join(argv))
    return (['ip', 'netns', 'exec', candidates.pop()] if candidates else []) + argv


def address_added(interface, address):
    ADDRESSES.setdefault(interface, set()).add(ipaddress.ip_network(address, strict=False))
