#    Copyright 2012 OpenStack Foundation
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

from oslo_utils import timeutils

from cinder import exception
from cinder import objects
from cinder.objects import fields
from cinder.tests.unit.brick import fake_lvm
from cinder.volume import driver
from cinder.volume.drivers import lvm
from cinder.zonemanager import utils as fczm_utils


class FakeISCSIDriver(lvm.LVMVolumeDriver):
    """Logs calls instead of executing."""
    def __init__(self, *args, **kwargs):
        super(FakeISCSIDriver, self).__init__(execute=self.fake_execute,
                                              *args, **kwargs)
        self.vg = fake_lvm.FakeBrickLVM('cinder-volumes', False,
                                        None, 'default',
                                        self.fake_execute)

    def check_for_setup_error(self):
        """No setup necessary in fake mode."""
        pass

    def create_volume(self, volume):
        pass

    def initialize_connection(self, volume, connector):
        # NOTE(thangp): There are several places in the core cinder code where
        # the volume passed through is a dict and not an oslo_versionedobject.
        # We need to react appropriately to what type of volume is passed in,
        # until the switch over to oslo_versionedobjects is complete.
        if isinstance(volume, objects.Volume):
            volume_metadata = volume.admin_metadata
        else:
            volume_metadata = {}
            for metadata in volume['volume_admin_metadata']:
                volume_metadata[metadata['key']] = metadata['value']

        access_mode = volume_metadata.get('attached_mode')
        if access_mode is None:
            access_mode = ('ro'
                           if volume_metadata.get('readonly') == 'True'
                           else 'rw')

        return {'driver_volume_type': 'iscsi',
                'data': {'access_mode': access_mode}}

    def initialize_connection_snapshot(self, snapshot, connector):
        return {
            'driver_volume_type': 'iscsi',
        }

    def terminate_connection(self, volume, connector, **kwargs):
        pass

    def _update_pools_and_stats(self, data):
        fake_pool = {}
        fake_pool.update(dict(
            pool_name=data["volume_backend_name"],
            total_capacity_gb='infinite',
            free_capacity_gb='infinite',
            provisioned_capacity_gb=0,
            reserved_percentage=100,
            QoS_support=False,
            filter_function=self.get_filter_function(),
            goodness_function=self.get_goodness_function()
        ))
        data["pools"].append(fake_pool)
        self._stats = data

    @staticmethod
    def fake_execute(cmd, *_args, **_kwargs):
        """Execute that simply logs the command."""
        return (None, None)


class FakeISERDriver(FakeISCSIDriver):
    def __init__(self, *args, **kwargs):
        super(FakeISERDriver, self).__init__(execute=self.fake_execute,
                                             *args, **kwargs)

    def initialize_connection(self, volume, connector):
        return {
            'driver_volume_type': 'iser',
            'data': {}
        }


class FakeFibreChannelDriver(driver.FibreChannelDriver):

    @fczm_utils.AddFCZone
    def initialize_connection(self, volume, connector):
        return {
            'driver_volume_type': 'fibre_channel',
            'data': {
                'initiator_target_map': {'fake_wwn': ['fake_wwn2']},
            }}

    @fczm_utils.AddFCZone
    def no_zone_initialize_connection(self, volume, connector):
        """This shouldn't call the ZM."""
        return {
            'driver_volume_type': 'bogus',
            'data': {
                'initiator_target_map': {'fake_wwn': ['fake_wwn2']},
            }}

    @fczm_utils.RemoveFCZone
    def terminate_connection(self, volume, connector, **kwargs):
        return {
            'driver_volume_type': 'fibre_channel',
            'data': {
                'initiator_target_map': {'fake_wwn': ['fake_wwn2']},
            }}

    @fczm_utils.RemoveFCZone
    def no_zone_terminate_connection(self, volume, connector, **kwargs):
        return {
            'driver_volume_type': 'bogus',
            'data': {
                'initiator_target_map': {'fake_wwn': ['fake_wwn2']},
            }}


class LoggingVolumeDriver(driver.VolumeDriver):
    """Logs and records calls, for unit tests."""

    def check_for_setup_error(self):
        pass

    def create_volume(self, volume):
        self.log_action('create_volume', volume)

    def delete_volume(self, volume):
        self.clear_volume(volume)
        self.log_action('delete_volume', volume)

    def clear_volume(self, volume):
        self.log_action('clear_volume', volume)

    def local_path(self, volume):
        raise NotImplementedError()

    def ensure_export(self, context, volume):
        self.log_action('ensure_export', volume)

    def create_export(self, context, volume):
        self.log_action('create_export', volume)

    def remove_export(self, context, volume):
        self.log_action('remove_export', volume)

    def initialize_connection(self, volume, connector):
        self.log_action('initialize_connection', volume)

    def terminate_connection(self, volume, connector):
        self.log_action('terminate_connection', volume)

    def create_export_snapshot(self, context, snapshot):
        self.log_action('create_export_snapshot', snapshot)

    def remove_export_snapshot(self, context, snapshot):
        self.log_action('remove_export_snapshot', snapshot)

    def initialize_connection_snapshot(self, snapshot, connector):
        self.log_action('initialize_connection_snapshot', snapshot)

    def terminate_connection_snapshot(self, snapshot, connector):
        self.log_action('terminate_connection_snapshot', snapshot)

    def create_cloned_volume(self, volume, src_vol):
        self.log_action('create_cloned_volume', volume)

    _LOGS = []

    @staticmethod
    def clear_logs():
        LoggingVolumeDriver._LOGS = []

    @staticmethod
    def log_action(action, parameters):
        """Logs the command."""
        log_dictionary = {}
        if parameters:
            log_dictionary = dict(parameters)
        log_dictionary['action'] = action
        LoggingVolumeDriver._LOGS.append(log_dictionary)

    @staticmethod
    def all_logs():
        return LoggingVolumeDriver._LOGS

    @staticmethod
    def logs_like(action, **kwargs):
        matches = []
        for entry in LoggingVolumeDriver._LOGS:
            if entry['action'] != action:
                continue
            match = True
            for k, v in kwargs.items():
                if entry.get(k) != v:
                    match = False
                    break
            if match:
                matches.append(entry)
        return matches

    def get_volume_stats(self, refresh=False):
        return {
            'volume_backend_name': self.configuration.safe_get(
                'volume_backend_name'),
            'vendor_name': 'LoggingVolumeDriver',
            'total_capacity_gb': 'infinite',
            'free_capacity_gb': 'infinite',
        }


class FakeGateDriver(lvm.LVMVolumeDriver):
    """Class designation for FakeGateDriver.

    FakeGateDriver is for TESTING ONLY. There are a few
    driver features such as CG and replication that are not
    supported by the reference driver LVM currently. Adding
    those functions in this fake driver will help detect
    problems when changes are introduced in those functions.

    Implementation of this driver is NOT meant for production.
    They are implemented simply to make sure calls to the driver
    functions are passing in the correct parameters, and the
    results returned by the driver are handled properly by the manager.

    """
    def __init__(self, *args, **kwargs):
        super(FakeGateDriver, self).__init__(*args, **kwargs)

    def _update_volume_stats(self):
        super(FakeGateDriver, self)._update_volume_stats()
        self._stats["pools"][0]["consistencygroup_support"] = True
        self._stats["pools"][0]["replication_enabled"] = True

    # NOTE(xyang): Consistency Group functions implemented below
    # are for testing purpose only. Data consistency cannot be
    # achieved by running these functions.
    def create_consistencygroup(self, context, group):
        """Creates a consistencygroup."""
        # A consistencygroup entry is already created in db
        # This driver just returns a status
        now = timeutils.utcnow()
        model_update = {'status': fields.ConsistencyGroupStatus.AVAILABLE,
                        'updated_at': now}

        return model_update

    def create_consistencygroup_from_src(self, context, group, volumes,
                                         cgsnapshot=None, snapshots=None,
                                         soure_cg=None, source_vols=None):
        """Creates a consistencygroup from cgsnapshot or source cg."""
        for vol in volumes:
            try:
                if snapshots:
                    for snapshot in snapshots:
                        if vol['snapshot_id'] == snapshot['id']:
                            self.create_volume_from_snapshot(vol, snapshot)
                            break
            except Exception:
                raise
            try:
                if source_vols:
                    for source_vol in source_vols:
                        if vol['source_volid'] == source_vol['id']:
                            self.create_cloned_volume(vol, source_vol)
                            break
            except Exception:
                raise
        return None, None

    def delete_consistencygroup(self, context, group, volumes):
        """Deletes a consistencygroup and volumes in the group."""
        model_update = {'status': group.status}
        volume_model_updates = []
        for volume_ref in volumes:
            volume_model_update = {'id': volume_ref.id}
            try:
                self.remove_export(context, volume_ref)
                self.delete_volume(volume_ref)
                volume_model_update['status'] = 'deleted'
            except exception.VolumeIsBusy:
                volume_model_update['status'] = 'available'
            except Exception:
                volume_model_update['status'] = 'error'
                model_update['status'] = fields.ConsistencyGroupStatus.ERROR
            volume_model_updates.append(volume_model_update)

        return model_update, volume_model_updates

    def update_consistencygroup(self, context, group,
                                add_volumes=None, remove_volumes=None):
        """Updates a consistency group."""
        return None, None, None

    def create_cgsnapshot(self, context, cgsnapshot, snapshots):
        """Creates a cgsnapshot.

        Snapshots created here are NOT consistent. This is for
        testing purpose only.
        """
        model_update = {'status': 'available'}
        snapshot_model_updates = []
        for snapshot in snapshots:
            snapshot_model_update = {'id': snapshot.id}
            try:
                self.create_snapshot(snapshot)
                snapshot_model_update['status'] = 'available'
            except Exception:
                snapshot_model_update['status'] = 'error'
                model_update['status'] = 'error'
            snapshot_model_updates.append(snapshot_model_update)

        return model_update, snapshot_model_updates

    def delete_cgsnapshot(self, context, cgsnapshot, snapshots):
        """Deletes a cgsnapshot."""
        model_update = {'status': cgsnapshot.status}
        snapshot_model_updates = []
        for snapshot in snapshots:
            snapshot_model_update = {'id': snapshot.id}
            try:
                self.delete_snapshot(snapshot)
                snapshot_model_update['status'] = 'deleted'
            except exception.SnapshotIsBusy:
                snapshot_model_update['status'] = 'available'
            except Exception:
                snapshot_model_update['status'] = 'error'
                model_update['status'] = 'error'
            snapshot_model_updates.append(snapshot_model_update)

        return model_update, snapshot_model_updates
