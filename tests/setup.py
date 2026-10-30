from setuptools import setup

setup(
        name='dsa-tests',
        version='0.2',
        description='Distributed Switch Architecture tests',
        url='https://github.com/lunn/dsa-tests',
        author='Andrew Lunn',
        author_email='andrew@lunn.ch',
        license='GPLv3',
        install_requires=[line for line in open('requirements.txt')],
)
