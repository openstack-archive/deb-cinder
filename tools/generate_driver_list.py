#! /usr/bin/env python
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

"""Generate list of cinder drivers"""

import argparse
import os

from cinder.interface import util


parser = argparse.ArgumentParser(prog="generate_driver_list")

parser.add_argument("--format", default='str', choices=['str', 'dict'],
                    help="Output format type")

# Keep backwards compatibilty with the gate-docs test
# The tests pass ['docs'] on the cmdln, but it's never been used.
parser.add_argument("output_list", default=None, nargs='?')

CI_WIKI_ROOT = "https://wiki.openstack.org/wiki/ThirdPartySystems/"


class Output(object):

    def __init__(self, base_dir, output_list):
        # At this point we don't care what was passed in, just a trigger
        # to write this out to the doc tree for now
        self.driver_file = None
        if output_list:
            self.driver_file = open(
                '%s/doc/source/drivers.rst' % base_dir, 'w+')
            self.driver_file.write('===================\n')
            self.driver_file.write('Available Drivers\n')
            self.driver_file.write('===================\n\n')

    def __enter__(self):
        return self

    def __exit__(self, type, value, traceback):
        if self.driver_file:
            self.driver_file.close()

    def write(self, text):
        if self.driver_file:
            self.driver_file.write('%s\n' % text)
        else:
            print(text)


def format_description(desc, output):
    desc = desc or '<None>'
    lines = desc.rstrip('\n').split('\n')
    for line in lines:
        output.write('    %s' % line)


def print_drivers(drivers, config_name, output):
    for driver in sorted(drivers, key=lambda x: x.class_fqn):
        output.write(driver.class_name)
        output.write('-' * len(driver.class_name))
        if driver.version:
            output.write('* Version: %s' % driver.version)
        output.write('* %s=%s' % (config_name, driver.class_fqn))
        if driver.ci_wiki_name:
            output.write('* CI info: %s%s' % (CI_WIKI_ROOT,
                                              driver.ci_wiki_name))
        output.write('* Description:')
        format_description(driver.desc, output)
        output.write('')
    output.write('')


def output_str(cinder_root, args):
    with Output(cinder_root, args.output_list) as output:
        output.write('Volume Drivers')
        output.write('==============')
        print_drivers(util.get_volume_drivers(), 'volume_driver', output)

        output.write('Backup Drivers')
        output.write('==============')
        print_drivers(util.get_backup_drivers(), 'backup_driver', output)

        output.write('FC Zone Manager Drivers')
        output.write('=======================')
        print_drivers(util.get_fczm_drivers(), 'zone_driver', output)


def collect_driver_info(driver):
    """Build the dictionary that describes this driver."""

    info = {'name': driver.class_name,
            'version': driver.version,
            'fqn': driver.class_fqn,
            'description': driver.desc,
            'ci_wiki_name': driver.ci_wiki_name}

    return info


def output_dict():

    import pprint
    driver_list = []
    drivers = util.get_volume_drivers()
    for driver in drivers:
        driver_list.append(collect_driver_info(driver))

    pprint.pprint(driver_list)


def main():
    tools_dir = os.path.dirname(os.path.abspath(__file__))
    cinder_root = os.path.dirname(tools_dir)
    cur_dir = os.getcwd()
    os.chdir(cinder_root)
    args = parser.parse_args()

    try:
        if args.format == 'str':
            output_str(cinder_root, args)
        elif args.format == 'dict':
            output_dict()

    finally:
        os.chdir(cur_dir)

if __name__ == '__main__':
    main()
