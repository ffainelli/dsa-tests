"""Command line parsing, which is common to all tests"""
import argparse
import configparser as ConfigParser
import sys


class dotdict(dict):
    """dot.notation access to dictionary attributes"""
    def __getattr__(self, attr):
        return self.get(attr)
    __setattr__ = dict.__setitem__
    __delattr__ = dict.__delitem__


def readConfig(filename, fourPorts=True):
    """Read the configuration file and return the config to use"""
    parser = ConfigParser.ConfigParser()
    parser.read_file(open(filename))

    config = {}
    config['HOST_LAN0'] = parser.get('host', 'lan0')
    config['HOST_LAN1'] = parser.get('host', 'lan1')
    config['HOST_LAN2'] = parser.get('host', 'lan2')
    config['HOST_LAN3'] = parser.get('host', 'lan3')
    if not fourPorts:
        config['HOST_LAN4'] = parser.get('host', 'lan4')
        config['HOST_LAN5'] = parser.get('host', 'lan5')
        config['HOST_LAN6'] = parser.get('host', 'lan6')
        config['HOST_OPTICAL3'] = parser.get('host', 'optical3')

    config['SUT_MASTER'] = parser.get('sut', 'master')
    config['SUT_LAN0'] = parser.get('sut', 'lan0')
    config['SUT_LAN1'] = parser.get('sut', 'lan1')
    config['SUT_LAN2'] = parser.get('sut', 'lan2')
    config['SUT_LAN3'] = parser.get('sut', 'lan3')

    try:
        config['SUT_MGMT'] = parser.get('sut', 'mgmt')
    except:
        config['SUT_MGMT'] = None

    if not fourPorts:
        config['SUT_LAN4'] = parser.get('sut', 'lan4')
        config['SUT_LAN5'] = parser.get('sut', 'lan5')
        config['SUT_LAN6'] = parser.get('sut', 'lan6')
        config['SUT_OPTICAL3'] = parser.get('sut', 'optical3')

    config['hostname'] = parser.get('sut', 'hostname')
    config['key'] = parser.get('sut', 'key')
    try:
        config['ptp'] = parser.get('sut', 'ptp')
    except:
        config['ptp'] = None

    return dotdict(config)


def params():
    """Command line argument parsing"""
    parser = argparse.ArgumentParser(description='Run some Bridge tests.')
    parser.add_argument("--config", "-c", help="Configuration file",
                        required=True)
    parser.add_argument("--config-master", "-m",
                        help="PTP Master Configuration file",
                        required=False)
    parser.add_argument("--config-master-interface", "-i",
                        help="PTP Master interface",
                        required=False)
    parser.add_argument("--verbose", "-v", type=int,
                        help="Run the test with a verbose level",
                        default=0)
    parser.add_argument("--failfast", "-f",
                        help="Exit the test as soon as a test fails",
                        default=False, action='store_true')
    parser.add_argument("--xml",
                        help='Output results in XML',
                        default=False, action='store_true')
    parser.add_argument("--vlanfiltering",
                        default=False, action='store_true')
    args = parser.parse_args()
    del sys.argv[1:]
    return args
