# elasticsearch_upgrade

Performs a rolling upgrade of an Elasticsearch cluster. It's great for keeping your cluster automatically
patched without downtime.

Nodes that already have the correct version are skipped. So the script can be executed multiple times if desired. 

Heavily tested with Elasticsearch version 5.6.3.

# Usage

    usage: elasticsearch_upgrade.py [-h] -n NODES [-u USERNAME] [-P PASSWORD]
                                    [-p PORT] [-s]
                                    [--service-stop-command SERVICE_STOP_COMMAND]
                                    [--service-start-command SERVICE_START_COMMAND]
                                    [--upgrade-command UPGRADE_COMMAND]
                                    [--latest-version-command LATEST_VERSION_COMMAND]
                                    [--version VERSION]
                                    [--upgrade-system-command UPGRADE_SYSTEM_COMMAND]
                                    [--upgrade-system] [--reboot] [--force-reboot]
                                    [-v]
    
    Performs a rolling upgrade of an Elasticsearch cluster
    
    optional arguments:
      -h, --help            show this help message and exit
      -n NODES, --nodes NODES
                            Comma separated list of host names or IP addresses of
                            nodes
      -u USERNAME, --username USERNAME
                            Username for authentication
      -P PASSWORD, --password PASSWORD
                            Password for authentication
      -p PORT, --port PORT  Elasticsearch HTTP port. Default 9200
      -s, --ssl             Connect with https
      --service-stop-command SERVICE_STOP_COMMAND
                            Shell command to stop the Elasticsearch service on a
                            node. Default 'sudo systemctl stop elasticsearch'
      --service-start-command SERVICE_START_COMMAND
                            Shell command to start the Elasticsearch service on a
                            node. Default 'sudo systemctl start elasticsearch'
      --upgrade-command UPGRADE_COMMAND
                            Command to upgrade Elasticsearch on a node. Default
                            'sudo yum clean all && sudo yum install -y
                            elasticsearch'
      --latest-version-command LATEST_VERSION_COMMAND
                            Command to get the latest version in the repository.
                            Default "sudo yum clean all >/dev/null 2>&1 && sudo
                            yum list all elasticsearch | grep elasticsearch | awk
                            '{ print $2 }' | cut -d '-' -f1 | sort --version-sort
                            -r | head -n 1"
      --version VERSION     A specific version to upgrade to or 'latest'. If
                            'latest', then the highest available version in the
                            repository will be determined. Nodes with a version
                            equal or higher will be skipped. Default 'latest'
      --upgrade-system-command UPGRADE_SYSTEM_COMMAND
                            Command to upgrade operating system. Default 'sudo yum
                            clean all && sudo yum update -y'
      --upgrade-system      Upgrades the operating system also after upgrading
                            Elasticsearch
      --reboot              Reboots the server if an actual upgrade took place
      --force-reboot        Always reboots the server, even though no upgrade
                            occurred because the version was already the latest
      -v, --verbose         Display of more information

Only the nodes parameter is required. This script works by default with a YUM installation
of Elasticsearch. But with the command parameters it can be configured for other operating
systems as well. It should also work with archive (tar) based installations.

**As root user**:

    ./elasticsearch_upgrade.py --nodes host1,host2,host3
                
**As non-root user with restrictive sudo rights**:

    ./elasticsearch_upgrade.py\
     --nodes host1,host2,host3\
     --service-stop-command 'sudo /usr/local/bin/esctl service stop elasticsearch'\
     --service-start-command 'sudo /usr/local/bin/esctl service start elasticsearch'\
     --upgrade-command 'sudo /usr/local/bin/esctl update'\
     --latest-version-command 'sudo /usr/local/bin/esctl latest-version'

# Restrictive sudo rights

The upgrade script requires several actions that must be executed as root. But it would be
better to let a non-root user execute the upgrade script with restrictive sudo rights. A nice way
to do that is with sudo line and script below. 

**/etc/sudoers.d/esctl**

    # Allow myuser to use esctl that can stop/start/restart the elasticsearch service
    myuser ALL=(root) NOPASSWD: /usr/local/bin/esctl

**/usr/local/bin/esctl**

    #!/bin/bash
    
    # Elasticsearch ctl
    # This file exists to perform limited actions with sudo
    
    if [ "$1" == "service" ]; then
      if [ "$2" != 'start' ] && [ "$2" != 'stop' ] && [ "$2" != 'restart' ]; then
        echo 'Service sub command must be start, stop or restart'
        exit 1
      fi
    
      # Check if service name is empty
      if [[ -z "$3" ]]; then
        echo 'Service name must be specified'
        exit 1
      fi
    
      # Check if service name starts with "elasticsearch"
      if [[ "$3" != "elasticsearch"* ]]; then
        echo 'Service name must start with elasticsearch'
        exit 1
      fi
    
      systemctl $2 $3
    elif [ "$1" == "latest-version" ]; then
      sudo yum clean all >/dev/null 2>&1 &&
      yum list all elasticsearch | grep elasticsearch | awk '{ print $2 }' | cut -d '-' -f1 |
      sort --version-sort -r | head -n 1
    elif [ "$1" == "update" ]; then
      sudo yum clean all && sudo yum install -y elasticsearch
    elif [[ ! -z "$1" ]] ; then
      echo 'This sub command is not allowed'
      exit 1
    else
      echo 'Usage:'
      echo "./esctl service (start|stop|restart) elasticsearch"
      echo "./esctl latest-version"
      echo "./esctl update"
    fi

# Disable SSH strict host key checking

If you have a trusted environment, you can disable strict host key checking to avoid having to type "yes"
for a SSH connection to each node. However, keep in mind that this could be a security risk.

Add to the ~/.ssh/config file of the user how executes this script:

    StrictHostKeyChecking no
    UserKnownHostsFile=/dev/null
    LogLevel ERROR
