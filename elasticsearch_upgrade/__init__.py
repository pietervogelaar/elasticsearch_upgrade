#!/usr/bin/env python

# elasticsearch_upgrade.py
# https://github.com/pietervogelaar/elasticsearch_upgrade
#
# Performs a rolling upgrade of an Elasticsearch cluster
# Based on instructions at https://www.elastic.co/guide/en/elasticsearch/reference/5.4/rolling-upgrades.html
#
# Installing dependencies:
# pip install requests
#
# MIT License
#
# Copyright (c) 2017 Pieter Vogelaar
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

import argparse
import json
import re
import requests
import subprocess
import sys
import time
from distutils.version import StrictVersion
from requests.auth import HTTPBasicAuth
from requests.exceptions import ConnectionError


class ElasticsearchUpgrader:
    """
    Performs a rolling upgrade of an Elasticsearch cluster
    """

    def __init__(self,
                 nodes,
                 username=None,
                 password=None,
                 port=9200,
                 ssl=False,
                 service_stop_command='sudo systemctl stop elasticsearch',
                 service_start_command='sudo systemctl start elasticsearch',
                 upgrade_command='sudo yum clean all && sudo yum install -y elasticsearch',
                 latest_version_command="sudo yum clean all >/dev/null 2>&1 && yum list all elasticsearch |"
                                        " grep elasticsearch | awk '{ print $2 }' | cut -d '-' -f1 |"
                                        " sort --version-sort -r | head -n 1",
                 version='latest',
                 upgrade_system_command='sudo yum clean all && sudo yum update -y',
                 upgrade_system=False,
                 reboot=False,
                 force_reboot=False,
                 verbose=False,
                 ):
        """
        Constructor
        :param nodes: list Host names or IP addresses of nodes
        :param username: string
        :param password: string
        :param port: int
        :param ssl: bool
        :param service_stop_command: string
        :param service_start_command: string
        :param upgrade_command: string
        :param latest_version_command: string
        :param version: string
        :param upgrade_system_command: string
        :param upgrade_system: string
        :param reboot: bool
        :param force_reboot: bool
        :param verbose: bool
        """

        self._nodes = nodes
        self._username = username
        self._password = password
        self._port = port
        self._ssl = ssl
        self._service_stop_command = service_stop_command
        self._service_start_command = service_start_command
        self._upgrade_command = upgrade_command
        self._latest_version_command = latest_version_command
        self._version = version
        self._upgrade_system_command = upgrade_system_command
        self._upgrade_system = upgrade_system
        self._reboot = reboot
        self._force_reboot = force_reboot
        self._verbose = verbose

        # Internal class attributes
        self._rebooting = False
        self._elasticsearch_upgrades_available = False
        self._os_upgrades_available = False

    def verbose_response(self, response):
        if self._verbose:
            print('Response status code: {}'.format(response.status_code))
            print('Response headers: {}'.format(response.headers))
            print('Response content: {}'.format(response.text))

    def current_version_lower(self, node):
        """
        Checks if the current version of Elasticsearch on the node
        is lower than the version to upgrade to
        :param node: string
        :return: bool
        """
        if self._username:
            auth = HTTPBasicAuth(self._username, self._password)
        else:
            auth = None

        response = requests.get(self.get_node_url(node), auth=auth)
        self.verbose_response(response)

        if response.status_code == 200:
            data = response.json()
            if 'version' in data and 'number' in data['version']:
                if StrictVersion(data['version']['number']) == StrictVersion(self._version):
                    print('Skipping upgrade, the current version {} is the same as the version to upgrade to'
                          .format(data['version']['number']))
                    return False
                elif StrictVersion(data['version']['number']) > StrictVersion(self._version):
                    print('Skipping upgrade, the current version {} is higher than version {} to upgrade to'
                          .format(data['version']['number'], self._version))
                    return False
                else:
                    print('The current version {} is lower than version {} to upgrade to'
                          .format(data['version']['number'], self._version))
                    return True
            else:
                sys.stderr.write("Could not determine the current version\n")
        else:
            sys.stderr.write("Could not retrieve the current version\n")

        return False

    def disable_shard_allocation(self, node):
        """
        Disables shard allocation for the cluster
        :param node: string
        :return: bool
        """
        if self._username:
            auth = HTTPBasicAuth(self._username, self._password)
        else:
            auth = None

        data = {
            'transient': {
                'cluster.routing.allocation.enable': 'none'
            }
        }

        url = '{}/_cluster/settings'.format(self.get_node_url(node))
        response = requests.put(url, json=data, auth=auth)
        self.verbose_response(response)

        return response.status_code == 200

    def enable_shard_allocation(self, node):
        """
        Enables shard allocation for the cluster
        :param node: string
        :return: bool
        """
        if self._username:
            auth = HTTPBasicAuth(self._username, self._password)
        else:
            auth = None

        data = {
            'transient': {
                'cluster.routing.allocation.enable': 'all'
            }
        }

        url = '{}/_cluster/settings'.format(self.get_node_url(node))
        response = requests.put(url, json=data, auth=auth)
        self.verbose_response(response)

        return response.status_code == 200

    def do_synced_flush(self, node):
        """
        Stops non-essential indexing and performs a synced flush to increase shard recovery speed
        :param node: string
        :return: bool
        """
        if self._username:
            auth = HTTPBasicAuth(self._username, self._password)
        else:
            auth = None

        data = {}
        url = '{}/_flush/synced'.format(self.get_node_url(node))
        response = requests.post(url, json=data, auth=auth)
        self.verbose_response(response)

        # This operation is best effort, so ignore the response status code
        return True

    def stop_service(self, node):
        """
        Stops the Elasticsearch service on the node
        :param node: string
        :return: bool
        """

        result = self.ssh_command(node, self._service_stop_command)
        if result['exit_code'] != 0:
            return False

        return True

    def upgrade_elasticsearch(self, node):
        """
        Upgrades the Elasticsearch software on the node
        :param node: string
        :return: bool
        """

        result = self.ssh_command(node, self._upgrade_command)

        if self._verbose:
            print('stdout:')
            print(result['stdout'])
            print('stderr:')
            print(result['stderr'])

        if result['exit_code'] != 0:
            return False

        if 'Nothing to do' in result['stdout']:
            self._elasticsearch_upgrades_available = False
        else:
            self._elasticsearch_upgrades_available = True

        return True

    def upgrade_system(self, node):
        """
        Upgrades the operating system
        :param node: string
        :return: bool
        """
        result = self.ssh_command(node, self._upgrade_system_command)

        if self._verbose:
            print('stdout:')
            print(result['stdout'])
            print('stderr:')
            print(result['stderr'])

        if result['exit_code'] != 0:
            return False

        if 'No packages marked for update' in result['stdout']:
            self._os_upgrades_available = False
        else:
            self._os_upgrades_available = True

        return True

    def start_service(self, node):
        """
        Starts the Elasticsearch service on the node
        :param node: string
        :return: bool
        """

        result = self.ssh_command(node, self._service_start_command)
        if result['exit_code'] != 0:
            return False

        return True

    def wait_until_joined(self, node):
        """
        Waits until the node joined the cluster
        :param node:
        :return: bool
        """

        print('- Waiting until node joins the cluster')

        if self._username:
            auth = HTTPBasicAuth(self._username, self._password)
        else:
            auth = None

        url = '{}/_cat/nodes'.format(self.get_node_url(node))

        while True:
            time.sleep(5)

            try:
                response = requests.get(url, auth=auth)
                self.verbose_response(response)

                if response.status_code == 200 and node in response.text:
                    if self._verbose:
                        print("Node joined the cluster")
                    else:
                        sys.stdout.write(".\n")
                        sys.stdout.flush()

                    return True
            except ConnectionError as exception:
                if self._verbose:
                    print('Could not connect to node')

            if self._verbose:
                print("Node hasn't joined the cluster yet")
            else:
                sys.stdout.write('.')
                sys.stdout.flush()

    def wait_until_status_green(self, node):
        """
        Waits until the cluster status is green
        :param node:
        :return: bool
        """
        print('- Waiting until cluster status is green')

        while True:
            time.sleep(5)

            if self.get_cluster_status(node) == 'green':
                if self._verbose:
                    print('Cluster status is green')
                else:
                    sys.stdout.write(".\n")
                    sys.stdout.flush()

                return True

            if self._verbose:
                print('Cluster status is not green yet')
            else:
                sys.stdout.write('.')
                sys.stdout.flush()

    def get_cluster_status(self, node):
        """
        Gets the cluster status
        :param node:
        :return: string "green", "yellow" or "red"
        """
        cluster_status = None

        if self._username:
            auth = HTTPBasicAuth(self._username, self._password)
        else:
            auth = None

        url = '{}/_cat/health'.format(self.get_node_url(node))

        try:
            response = requests.get(url, auth=auth)
            self.verbose_response(response)

            if response.status_code == 200:
                if 'green' in response.text:
                    cluster_status = 'green'
                elif 'yellow' in response.text:
                    cluster_status = 'yellow'
                elif 'red' in response.text:
                    cluster_status = 'red'
        except ConnectionError as exception:
            if self._verbose:
                sys.stderr.write("Could not connect to node\n")

        return cluster_status

    def get_latest_version(self, node):
        """
        Gets the latest version available in the repository
        :param node: string
        :return: bool
        """

        result = self.ssh_command(node, self._latest_version_command)
        if result['exit_code'] != 0:
            return False

        latest_version = result['stdout'].strip()
        if StrictVersion(latest_version) > StrictVersion('0.0.0'):
            return latest_version

        return False

    def reboot(self, node):
        print('- Rebooting')
        self._rebooting = True
        self.ssh_command(node, 'sudo /sbin/reboot')

    def get_node_url(self, node):
        """
        Gets a node URL
        :param node: string
        :return: string
        """
        if self._ssl:
            protocol = 'https'
        else:
            protocol = 'http'

        return '{}://{}:{}'.format(protocol, node, self._port)

    def ssh_command(self, host, command):
        """
        Executes a SSH command
        :param host: string
        :param command: string
        :return: dict
        """
        p = subprocess.Popen(['ssh', '%s' % host, command],
                             shell=False,
                             stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE)

        stdout = p.stdout.readlines()
        stderr = p.stderr.readlines()

        stdout_string = ''.join(stdout)
        stderr_string = ''.join(stderr)

        # Remove clutter
        regex = re.compile(r"Connection .+? closed by remote host\.\n?", re.IGNORECASE)
        stderr_string = regex.sub('', stderr_string).strip()

        if stderr_string:
            sys.stderr.write("SSH error from host {}: {}\n".format(host, stderr_string))

        # Make a return code available
        p.communicate()[0]

        result = {
            'stdout': stdout_string,
            'stderr': stderr_string,
            'exit_code': p.returncode,
        }

        return result

    def upgrade_node(self, node):
        print('# Node {}'.format(node))

        self._rebooting = False
        self._elasticsearch_upgrades_available = False
        self._os_upgrades_available = False

        if self._version:
            # Only upgrade node if the current version is lower than the version to upgrade to
            if not self.current_version_lower(node):
                # Elasticsearch already up to date

                if self._upgrade_system:
                    print('- Upgrading operating system')
                    if not self.upgrade_system(node):
                        sys.stderr.write("Failed to upgrade operating system\n")
                        return False
                    else:
                        if not self._os_upgrades_available:
                            print('No operating system upgrades available')

                if self._force_reboot or (self._reboot and self._os_upgrades_available):
                    # Disable shard allocation
                    print('- Disabling shard allocation')
                    if not self.disable_shard_allocation(node):
                        sys.stderr.write("Failed to disable shard allocation\n")
                        return False

                    # Stop non-essential indexing and perform a synced flush to increase shard recovery speed
                    print('- Performing a synced flush')
                    if not self.do_synced_flush(node):
                        sys.stderr.write("Failed to perform a synced flush\n")
                        return False

                    # Reboot
                    self.reboot(node)
                else:
                    return True

        if not self._rebooting:
            # Disable shard allocation
            print('- Disabling shard allocation')
            if not self.disable_shard_allocation(node):
                sys.stderr.write("Failed to disable shard allocation\n")
                return False

            # Stop non-essential indexing and perform a synced flush to increase shard recovery speed
            print('- Performing a synced flush')
            if not self.do_synced_flush(node):
                sys.stderr.write("Failed to perform a synced flush\n")
                return False

            # Stop Elasticsearch service
            print('- Stopping Elasticsearch service')
            if not self.stop_service(node):
                sys.stderr.write("Failed to stop Elasticsearch service\n")
                return False

            # Upgrade the Elasticsearch software
            print('- Upgrading Elasticsearch software')
            if not self.upgrade_elasticsearch(node):
                sys.stderr.write("Failed to upgrade Elasticsearch software\n")
                return False

            if self._upgrade_system:
                print('- Upgrading operating system')
                if not self.upgrade_system(node):
                    sys.stderr.write("Failed to upgrade operating system\n")
                    return False
                else:
                    if not self._os_upgrades_available:
                        print('No operating system upgrades available')

            if (self._force_reboot or
               (self._reboot and (self._elasticsearch_upgrades_available or self._os_upgrades_available))):
                self.reboot(node)

            if not self._rebooting:
                # Start Elasticsearch service
                print('- Starting Elasticsearch service')
                if not self.start_service(node):
                    sys.stderr.write("Failed to start Elasticsearch service\n")
                    return False

        self.wait_until_joined(node)

        # Enable shard allocation
        print('- Enabling shard allocation')
        if not self.enable_shard_allocation(node):
            sys.stderr.write("Failed to enable shard allocation\n")
            return False

        self.wait_until_status_green(node)

        return True

    def upgrade(self):
        print('Performing a rolling upgrade of the Elasticsearch cluster')

        if self._verbose:
            print('Cluster nodes: {}'.format(json.dumps(self._nodes)))

        if self._version == 'latest':
            print('Determining the latest version')

            latest_version = self.get_latest_version(self._nodes[0])
            if latest_version:
                print('Using latest version {} as version to upgrade to'.format(latest_version))
                self._version = latest_version
            else:
                sys.stderr.write("Failed to determine the latest version\n")
                return False

        # Only start upgrading the cluster if the cluster status is green
        print('Checking if cluster status is green')
        if self.get_cluster_status(self._nodes[0]) != 'green':
            sys.stderr.write("Did not start upgrading the cluster because the status is not green\n")
            return False

        for node in self._nodes:
            if not self.upgrade_node(node):
                sys.stderr.write("Failed to patch the Elasticsearch cluster\n")
                return False

        print ('Successfully upgraded all nodes of the Elasticsearch cluster')

        return True


def main():
    parser = argparse.ArgumentParser(description='Performs a rolling upgrade of an Elasticsearch cluster')
    parser.add_argument('-n', '--nodes', help='Comma separated list of host names or IP addresses of nodes',
                        required=True)
    parser.add_argument('-u', '--username', help='Username for authentication')
    parser.add_argument('-P', '--password', help='Password for authentication')
    parser.add_argument('-p', '--port', help='Elasticsearch HTTP port. Default 9200', type=int, default=9200)
    parser.add_argument('-s', '--ssl', help='Connect with https', action='store_true')
    parser.add_argument('--service-stop-command',
                        help="Shell command to stop the Elasticsearch service on a node. "
                             "Default 'sudo systemctl stop elasticsearch'",
                        default='sudo systemctl stop elasticsearch')
    parser.add_argument('--service-start-command',
                        help="Shell command to start the Elasticsearch service on a node. "
                             "Default 'sudo systemctl start elasticsearch'",
                        default='sudo systemctl start elasticsearch')
    parser.add_argument('--upgrade-command',
                        help="Command to upgrade Elasticsearch on a node. "
                             "Default 'sudo yum clean all && sudo yum install -y elasticsearch'",
                        default='sudo yum clean all && sudo yum install -y elasticsearch')
    parser.add_argument('--latest-version-command',
                        help="Command to get the latest version in the repository. "
                             "Default \"sudo yum clean all >/dev/null 2>&1 && sudo yum list all elasticsearch |"
                             " grep elasticsearch | awk '{ print $2 }' | cut -d '-' -f1 | sort --version-sort -r |"
                             " head -n 1\"",
                        default="sudo yum clean all >/dev/null 2>&1 && sudo yum list all elasticsearch |"
                                " grep elasticsearch | awk '{ print $2 }' | cut -d '-' -f1 | sort --version-sort -r |"
                                " head -n 1")
    parser.add_argument('--version',
                        help="A specific version to upgrade to or 'latest'. If 'latest', then the highest"
                             " available version in the repository will be determined. Nodes with a version"
                             " equal or higher will be skipped. Default 'latest'",
                        default='latest')
    parser.add_argument('--upgrade-system-command',
                        help="Command to upgrade operating system. Default 'sudo yum clean all && sudo yum update -y'",
                        default='sudo yum clean all && sudo yum update -y')
    parser.add_argument('--upgrade-system', help='Upgrades the operating system also after upgrading Elasticsearch',
                        action='store_true')
    parser.add_argument('--reboot', help='Reboots the server if an actual upgrade took place', action='store_true')
    parser.add_argument('--force-reboot', help='Always reboots the server, even though no upgrade occurred because'
                                               ' the version was already the latest', action='store_true')
    parser.add_argument('-v', '--verbose', help='Display of more information', action='store_true')
    args = parser.parse_args()

    # Create nodes list from comma separated string
    nodes = args.nodes.replace(' ', '').split(',')

    elasticsearch_upgrader = ElasticsearchUpgrader(nodes,
                                                   args.username,
                                                   args.password,
                                                   args.port,
                                                   args.ssl,
                                                   args.service_stop_command,
                                                   args.service_start_command,
                                                   args.upgrade_command,
                                                   args.latest_version_command,
                                                   args.version,
                                                   args.upgrade_system_command,
                                                   args.upgrade_system,
                                                   args.reboot,
                                                   args.force_reboot,
                                                   args.verbose)

    if not elasticsearch_upgrader.upgrade():
        exit(1)


if __name__ == '__main__':
    main()
