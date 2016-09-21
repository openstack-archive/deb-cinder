# Copyright 2010 United States Government as represented by the
# Administrator of the National Aeronautics and Space Administration.
# All Rights Reserved.
# Copyright 2012 Red Hat, Inc.
# Copyright 2013 NTT corp.
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

"""Command-line flag library.

Emulates gflags by wrapping cfg.ConfigOpts.

The idea is to move fully to cfg eventually, and this wrapper is a
stepping stone.

"""

import socket

from oslo_config import cfg
from oslo_log import log as logging
from oslo_middleware import cors
from oslo_utils import netutils


CONF = cfg.CONF
logging.register_options(CONF)

core_opts = [
    cfg.StrOpt('state_path',
               default='/var/lib/cinder',
               deprecated_name='pybasedir',
               help="Top-level directory for maintaining cinder's state"), ]

debug_opts = [
]

CONF.register_cli_opts(core_opts)
CONF.register_cli_opts(debug_opts)

global_opts = [
    cfg.StrOpt('my_ip',
               default=netutils.get_my_ipv4(),
               help='IP address of this host'),
    cfg.ListOpt('glance_api_servers',
                default=None,
                help='A list of the URLs of glance API servers available to '
                     'cinder ([http[s]://][hostname|ip]:port). If protocol '
                     'is not specified it defaults to http.'),
    cfg.IntOpt('glance_api_version',
               default=1,
               help='Version of the glance API to use'),
    cfg.IntOpt('glance_num_retries',
               min=0,
               default=0,
               help='Number retries when downloading an image from glance'),
    cfg.BoolOpt('glance_api_insecure',
                default=False,
                help='Allow to perform insecure SSL (https) requests to '
                     'glance (https will be used but cert validation will '
                     'not be performed).'),
    cfg.BoolOpt('glance_api_ssl_compression',
                default=False,
                help='Enables or disables negotiation of SSL layer '
                     'compression. In some cases disabling compression '
                     'can improve data throughput, such as when high '
                     'network bandwidth is available and you use '
                     'compressed image formats like qcow2.'),
    cfg.StrOpt('glance_ca_certificates_file',
               help='Location of ca certificates file to use for glance '
                    'client requests.'),
    cfg.IntOpt('glance_request_timeout',
               help='http/https timeout value for glance operations. If no '
                    'value (None) is supplied here, the glanceclient default '
                    'value is used.'),
    cfg.BoolOpt('enable_v1_api',
                default=True,
                deprecated_for_removal=True,
                help="DEPRECATED: Deploy v1 of the Cinder API."),
    cfg.BoolOpt('enable_v2_api',
                default=True,
                deprecated_for_removal=True,
                help="DEPRECATED: Deploy v2 of the Cinder API."),
    cfg.BoolOpt('enable_v3_api',
                default=True,
                help="Deploy v3 of the Cinder API."),
    cfg.BoolOpt('api_rate_limit',
                default=True,
                help='Enables or disables rate limit of the API.'),
    cfg.ListOpt('osapi_volume_ext_list',
                default=[],
                help='Specify list of extensions to load when using osapi_'
                     'volume_extension option with cinder.api.contrib.'
                     'select_extensions'),
    cfg.MultiStrOpt('osapi_volume_extension',
                    default=['cinder.api.contrib.standard_extensions'],
                    help='osapi volume extension to load'),
    cfg.StrOpt('volume_manager',
               default='cinder.volume.manager.VolumeManager',
               help='Full class name for the Manager for volume'),
    cfg.StrOpt('backup_manager',
               default='cinder.backup.manager.BackupManager',
               help='Full class name for the Manager for volume backup'),
    cfg.StrOpt('scheduler_manager',
               default='cinder.scheduler.manager.SchedulerManager',
               help='Full class name for the Manager for scheduler'),
    cfg.StrOpt('host',
               default=socket.gethostname(),
               help='Name of this node.  This can be an opaque identifier. '
                    'It is not necessarily a host name, FQDN, or IP address.'),
    # NOTE(vish): default to nova for compatibility with nova installs
    cfg.StrOpt('storage_availability_zone',
               default='nova',
               help='Availability zone of this node'),
    cfg.StrOpt('default_availability_zone',
               help='Default availability zone for new volumes. If not set, '
                    'the storage_availability_zone option value is used as '
                    'the default for new volumes.'),
    cfg.BoolOpt('allow_availability_zone_fallback',
                default=False,
                help='If the requested Cinder availability zone is '
                     'unavailable, fall back to the value of '
                     'default_availability_zone, then '
                     'storage_availability_zone, instead of failing.'),
    cfg.StrOpt('default_volume_type',
               help='Default volume type to use'),
    cfg.StrOpt('default_group_type',
               help='Default group type to use'),
    cfg.StrOpt('volume_usage_audit_period',
               default='month',
               help='Time period for which to generate volume usages. '
                    'The options are hour, day, month, or year.'),
    cfg.StrOpt('rootwrap_config',
               default='/etc/cinder/rootwrap.conf',
               help='Path to the rootwrap configuration file to use for '
                    'running commands as root'),
    cfg.BoolOpt('monkey_patch',
                default=False,
                help='Enable monkey patching'),
    cfg.ListOpt('monkey_patch_modules',
                default=[],
                help='List of modules/decorators to monkey patch'),
    cfg.IntOpt('service_down_time',
               default=60,
               help='Maximum time since last check-in for a service to be '
                    'considered up'),
    cfg.StrOpt('volume_api_class',
               default='cinder.volume.api.API',
               help='The full class name of the volume API class to use'),
    cfg.StrOpt('backup_api_class',
               default='cinder.backup.api.API',
               help='The full class name of the volume backup API class'),
    cfg.StrOpt('auth_strategy',
               default='keystone',
               choices=['noauth', 'keystone'],
               help='The strategy to use for auth. Supports noauth or '
                    'keystone.'),
    cfg.ListOpt('enabled_backends',
                help='A list of backend names to use. These backend names '
                     'should be backed by a unique [CONFIG] group '
                     'with its options'),
    cfg.BoolOpt('no_snapshot_gb_quota',
                default=False,
                help='Whether snapshots count against gigabyte quota'),
    cfg.StrOpt('transfer_api_class',
               default='cinder.transfer.api.API',
               help='The full class name of the volume transfer API class'),
    cfg.StrOpt('replication_api_class',
               default='cinder.replication.api.API',
               help='The full class name of the volume replication API class'),
    cfg.StrOpt('consistencygroup_api_class',
               default='cinder.consistencygroup.api.API',
               help='The full class name of the consistencygroup API class'),
    cfg.StrOpt('group_api_class',
               default='cinder.group.api.API',
               help='The full class name of the group API class'),
    cfg.StrOpt('os_privileged_user_name',
               help='OpenStack privileged account username. Used for requests '
                    'to other services (such as Nova) that require an account '
                    'with special rights.'),
    cfg.StrOpt('os_privileged_user_password',
               help='Password associated with the OpenStack privileged '
                    'account.',
               secret=True),
    cfg.StrOpt('os_privileged_user_tenant',
               help='Tenant name associated with the OpenStack privileged '
                    'account.'),
    cfg.StrOpt('os_privileged_user_auth_url',
               help='Auth URL associated with the OpenStack privileged '
                    'account.'),
]

CONF.register_opts(core_opts)
CONF.register_opts(global_opts)


def set_middleware_defaults():
    """Update default configuration options for oslo.middleware."""
    # CORS Defaults
    # TODO(krotscheck): Update with https://review.openstack.org/#/c/285368/
    cfg.set_defaults(cors.CORS_OPTS,
                     allow_headers=['X-Auth-Token',
                                    'X-Identity-Status',
                                    'X-Roles',
                                    'X-Service-Catalog',
                                    'X-User-Id',
                                    'X-Tenant-Id',
                                    'X-OpenStack-Request-ID',
                                    'X-Trace-Info',
                                    'X-Trace-HMAC',
                                    'OpenStack-API-Version'],
                     expose_headers=['X-Auth-Token',
                                     'X-Subject-Token',
                                     'X-Service-Token',
                                     'X-OpenStack-Request-ID',
                                     'OpenStack-API-Version'],
                     allow_methods=['GET',
                                    'PUT',
                                    'POST',
                                    'DELETE',
                                    'PATCH',
                                    'HEAD']
                     )
