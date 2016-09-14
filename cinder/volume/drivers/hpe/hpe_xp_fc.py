# Copyright (C) 2014-2015, Hitachi, Ltd.
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
"""
Fibre channel Cinder volume driver for Hewlett Packard Enterprise storage.

"""

from oslo_utils import importutils

from cinder import interface
from cinder import utils
from cinder.volume import driver
from cinder.volume.drivers.hpe import hpe_xp_opts as opts
from cinder.zonemanager import utils as fczm_utils

_DRIVER_DIR = 'cinder.volume.drivers.hpe'
_DRIVER_CLASS = 'hpe_xp_horcm_fc.HPEXPHORCMFC'


@interface.volumedriver
class HPEXPFCDriver(driver.FibreChannelDriver):
    """OpenStack Fibre Channel driver to enable HPE XP storage."""

    # ThirdPartySystems wiki page
    CI_WIKI_NAME = "XP_Storage_CI"

    def __init__(self, *args, **kwargs):
        """Initialize the driver."""
        super(HPEXPFCDriver, self).__init__(*args, **kwargs)

        self.configuration.append_config_values(opts.FC_VOLUME_OPTS)
        self.configuration.append_config_values(opts.COMMON_VOLUME_OPTS)
        self.common = importutils.import_object(
            '.'.join([_DRIVER_DIR, _DRIVER_CLASS]),
            self.configuration, 'FC', **kwargs)

    def check_for_setup_error(self):
        """Setup errors are already checked for in do_setup so return pass."""
        pass

    @utils.trace
    def create_volume(self, volume):
        """Create a volume."""
        return self.common.create_volume(volume)

    @utils.trace
    def create_volume_from_snapshot(self, volume, snapshot):
        """Create a volume from a snapshot."""
        return self.common.create_volume_from_snapshot(volume, snapshot)

    @utils.trace
    def create_cloned_volume(self, volume, src_vref):
        """Create a clone of the specified volume."""
        return self.common.create_cloned_volume(volume, src_vref)

    @utils.trace
    def delete_volume(self, volume):
        """Delete a volume."""
        self.common.delete_volume(volume)

    @utils.trace
    def create_snapshot(self, snapshot):
        """Create a snapshot."""
        return self.common.create_snapshot(snapshot)

    @utils.trace
    def delete_snapshot(self, snapshot):
        """Delete a snapshot."""
        self.common.delete_snapshot(snapshot)

    def local_path(self, volume):
        pass

    @utils.trace
    def get_volume_stats(self, refresh=False):
        """Get volume stats."""
        return self.common.get_volume_stats(refresh)

    @utils.trace
    def copy_image_to_volume(self, context, volume, image_service, image_id):
        """Fetch the image from image_service and write it to the volume.

        Call copy_image_to_volume() of super class and
        carry out original postprocessing.
        """
        super(HPEXPFCDriver, self).copy_image_to_volume(
            context, volume, image_service, image_id)
        self.common.copy_image_to_volume(
            context, volume, image_service, image_id)

    @utils.trace
    def after_volume_copy(self, context, src_vol, dest_vol, remote=None):
        """Driver-specific actions after copyvolume data.

        This method will be called after _copy_volume_data during volume
        migration
        """
        self.common.copy_volume_data(context, src_vol, dest_vol, remote)

    @utils.trace
    def restore_backup(self, context, backup, volume, backup_service):
        """Restore an existing backup to a new or existing volume.

        Call restore_backup() of super class and
        carry out original postprocessing.
        """
        super(HPEXPFCDriver, self).restore_backup(
            context, backup, volume, backup_service)
        self.common.restore_backup(context, backup, volume, backup_service)

    @utils.trace
    def extend_volume(self, volume, new_size):
        """Extend a volume."""
        self.common.extend_volume(volume, new_size)

    @utils.trace
    def manage_existing(self, volume, existing_ref):
        """Manage an existing HPE XP storage volume.

        existing_ref is a dictionary of the form:

        {'ldev': <logical device number on storage>,
         'storage_id': <product number of storage system>}
        """
        return self.common.manage_existing(volume, existing_ref)

    @utils.trace
    def manage_existing_get_size(self, volume, existing_ref):
        """Return size of volume for manage_existing."""
        return self.common.manage_existing_get_size(volume, existing_ref)

    def unmanage(self, volume):
        """Remove the specified volume from Cinder management."""
        self.common.unmanage(volume)

    def do_setup(self, context):
        """Setup and verify HPE XP storage connection."""
        self.common.do_setup(context)

    def ensure_export(self, context, volume):
        pass

    def create_export(self, context, volume, connector):
        pass

    def remove_export(self, context, volume):
        pass

    @utils.trace
    @fczm_utils.AddFCZone
    def initialize_connection(self, volume, connector):
        """Attach the volume to an instance."""
        return self.common.initialize_connection(volume, connector)

    @utils.trace
    @fczm_utils.RemoveFCZone
    def terminate_connection(self, volume, connector, **kwargs):
        """Detach a volume from an instance."""
        return self.common.terminate_connection(volume, connector, **kwargs)
