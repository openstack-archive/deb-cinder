# Copyright 2013 IBM Corp.
# Copyright (c) 2013 OpenStack Foundation
# All Rights Reserved.
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
#
# Authors:
#   Erik Zaadi <erikz@il.ibm.com>
#   Avishay Traeger <avishay@il.ibm.com>

"""
IBM Storage driver is a unified Volume driver for IBM XIV, Spectrum Accelerate,
FlashSystem A9000, FlashSystem A9000R and DS8000 storage systems.
"""

from oslo_config import cfg
from oslo_log import log as logging
from oslo_utils import importutils

from cinder import exception
from cinder.volume import driver
from cinder.volume.drivers.san import san

driver_opts = [
    cfg.StrOpt(
        'proxy',
        default='storage.proxy.IBMStorageProxy',
        help='Proxy driver that connects to the IBM Storage Array'),
    cfg.StrOpt(
        'connection_type',
        default='iscsi',
        choices=['fibre_channel', 'iscsi'],
        help='Connection type to the IBM Storage Array'),
    cfg.StrOpt(
        'chap',
        default='disabled',
        choices=['disabled', 'enabled'],
        help='CHAP authentication mode, effective only for iscsi'
        ' (disabled|enabled)'),
    cfg.StrOpt(
        'management_ips',
        default='',
        help='List of Management IP addresses (separated by commas)'),
]

CONF = cfg.CONF
CONF.register_opts(driver_opts)

LOG = logging.getLogger(__name__)


class IBMStorageDriver(san.SanDriver,
                       driver.ManageableVD,
                       driver.ExtendVD,
                       driver.SnapshotVD,
                       driver.MigrateVD,
                       driver.ConsistencyGroupVD,
                       driver.CloneableImageVD,
                       driver.TransferVD):
    """IBM Storage driver

    IBM Storage driver is a unified Volume driver for IBM XIV, Spectrum
    Accelerate, FlashSystem A9000, FlashSystem A9000R and DS8000 storage
    systems.
    """

    # ThirdPartySystems wiki page
    CI_WIKI_NAME = "IBM_XIV-DS8K_CI"

    def __init__(self, *args, **kwargs):
        """Initialize the driver."""

        super(IBMStorageDriver, self).__init__(*args, **kwargs)

        self.configuration.append_config_values(driver_opts)

        proxy = importutils.import_class(self.configuration.proxy)

        active_backend_id = kwargs.get('active_backend_id', None)

        # Driver additional flags should be specified in the cinder.conf
        # preferably in each backend configuration.

        self.proxy = proxy(
            {
                "user": self.configuration.san_login,
                "password": self.configuration.san_password,
                "address": self.configuration.san_ip,
                "vol_pool": self.configuration.san_clustername,
                "connection_type": self.configuration.connection_type,
                "chap": self.configuration.chap,
                "management_ips": self.configuration.management_ips
            },
            LOG,
            exception,
            driver=self,
            active_backend_id=active_backend_id)

    def do_setup(self, context):
        """Setup and verify connection to IBM Storage."""

        self.proxy.setup(context)

    def ensure_export(self, context, volume):
        """Ensure an export."""

        return self.proxy.ensure_export(context, volume)

    def create_export(self, context, volume, connector):
        """Create an export."""

        return self.proxy.create_export(context, volume)

    def create_volume(self, volume):
        """Create a volume on the IBM Storage system."""

        return self.proxy.create_volume(volume)

    def delete_volume(self, volume):
        """Delete a volume on the IBM Storage system."""

        self.proxy.delete_volume(volume)

    def remove_export(self, context, volume):
        """Disconnect a volume from an attached instance."""

        return self.proxy.remove_export(context, volume)

    def initialize_connection(self, volume, connector):
        """Map the created volume."""

        return self.proxy.initialize_connection(volume, connector)

    def terminate_connection(self, volume, connector, **kwargs):
        """Terminate a connection to a volume."""

        return self.proxy.terminate_connection(volume, connector)

    def create_volume_from_snapshot(self, volume, snapshot):
        """Create a volume from a snapshot."""

        return self.proxy.create_volume_from_snapshot(
            volume,
            snapshot)

    def create_snapshot(self, snapshot):
        """Create a snapshot."""

        return self.proxy.create_snapshot(snapshot)

    def delete_snapshot(self, snapshot):
        """Delete a snapshot."""

        return self.proxy.delete_snapshot(snapshot)

    def get_volume_stats(self, refresh=False):
        """Get volume stats."""

        return self.proxy.get_volume_stats(refresh)

    def create_cloned_volume(self, tgt_volume, src_volume):
        """Create Cloned Volume."""

        return self.proxy.create_cloned_volume(tgt_volume, src_volume)

    def extend_volume(self, volume, new_size):
        """Extend Created Volume."""

        self.proxy.extend_volume(volume, new_size)

    def migrate_volume(self, context, volume, host):
        """Migrate the volume to the specified host."""

        return self.proxy.migrate_volume(context, volume, host)

    def manage_existing(self, volume, existing_ref):
        """Brings an existing backend storage object under Cinder management.

        existing_ref is passed straight through from the API request's
        manage_existing_ref value, and it is up to the driver how this should
        be interpreted.  It should be sufficient to identify a storage object
        that the driver should somehow associate with the newly-created cinder
        volume structure.
        In the case of XIV family and FlashSystem A9000 family, the
        existing_ref consists of a single field named 'existing_ref'
        representing the name of the volume on the storage.

        There are two ways to do this:

        1. Rename the backend storage object so that it matches the,
           volume['name'] which is how drivers traditionally map between a
           cinder volume and the associated backend storage object.

        2. Place some metadata on the volume, or somewhere in the backend, that
           allows other driver requests (e.g. delete, clone, attach, detach...)
           to locate the backend storage object when required.

        If the existing_ref doesn't make sense, or doesn't refer to an existing
        backend storage object, raise a ManageExistingInvalidReference
        exception.

        The volume may have a volume_type, and the driver can inspect that and
        compare against the properties of the referenced backend storage
        object.  If they are incompatible, raise a
        ManageExistingVolumeTypeMismatch, specifying a reason for the failure.
        """
        return self.proxy.manage_volume(volume, existing_ref)

    def manage_existing_get_size(self, volume, existing_ref):
        """Return size of volume to be managed by manage_existing."""

        return self.proxy.manage_volume_get_size(volume, existing_ref)

    def unmanage(self, volume):
        """Removes the specified volume from Cinder management."""

        return self.proxy.unmanage_volume(volume)

    def freeze_backend(self, context):
        """Notify the backend that it's frozen. """

        return self.proxy.freeze_backend(context)

    def thaw_backend(self, context):
        """Notify the backend that it's unfrozen/thawed. """

        return self.proxy.thaw_backend(context)

    def failover_host(self, context, volumes, secondary_id=None):
        """Failover a backend to a secondary replication target. """

        return self.proxy.failover_host(
            context, volumes, secondary_id)

    def get_replication_status(self, context, volume):
        """Return replication status."""

        return self.proxy.get_replication_status(context, volume)

    def retype(self, ctxt, volume, new_type, diff, host):
        """Convert the volume to be of the new type."""

        return self.proxy.retype(ctxt, volume, new_type, diff, host)

    def create_consistencygroup(self, context, group):
        """Creates a consistency group."""

        return self.proxy.create_consistencygroup(context, group)

    def delete_consistencygroup(self, context, group, volumes):
        """Deletes a consistency group."""

        return self.proxy.delete_consistencygroup(
            context, group, volumes)

    def create_cgsnapshot(self, context, cgsnapshot, snapshots):
        """Creates a consistency group snapshot."""

        return self.proxy.create_cgsnapshot(
            context, cgsnapshot, snapshots)

    def delete_cgsnapshot(self, context, cgsnapshot, snapshots):
        """Deletes a consistency group snapshot."""

        return self.proxy.delete_cgsnapshot(
            context, cgsnapshot, snapshots)

    def update_consistencygroup(self, context, group,
                                add_volumes, remove_volumes):
        """Adds or removes volume(s) to/from an existing consistency group."""

        return self.proxy.update_consistencygroup(
            context, group, add_volumes, remove_volumes)

    def create_consistencygroup_from_src(
            self, context, group, volumes, cgsnapshot, snapshots,
            source_cg=None, source_vols=None):
        """Creates a consistencygroup from source."""

        return self.proxy.create_consistencygroup_from_src(
            context, group, volumes, cgsnapshot, snapshots,
            source_cg, source_vols)
