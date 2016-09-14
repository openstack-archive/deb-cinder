# Copyright (c) 2016 FalconStor, Inc.
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
"""Volume driver for FalconStor FSS storage system.

This driver requires FSS-8.00-8865 or later.
"""

import math
import re

from oslo_config import cfg
from oslo_log import log as logging
from oslo_utils import excutils
from oslo_utils import units
import six

from cinder import exception
from cinder.i18n import _, _LE, _LI, _LW
from cinder.image import image_utils
from cinder.volume.drivers.falconstor import rest_proxy
from cinder.volume.drivers.san import san

LOG = logging.getLogger(__name__)

FSS_OPTS = [
    cfg.IntOpt('fss_pool',
               default='',
               help='FSS pool id in which FalconStor volumes are stored.'),
    cfg.BoolOpt('fss_debug',
                default=False,
                help="Enable HTTP debugging to FSS"),
    cfg.StrOpt('additional_retry_list',
               default='',
               help='FSS additional retry list, separate by ;')
]

CONF = cfg.CONF
CONF.register_opts(FSS_OPTS)


class FalconstorBaseDriver(san.SanDriver):

    def __init__(self, *args, **kwargs):
        super(FalconstorBaseDriver, self).__init__(*args, **kwargs)
        if self.configuration:
            self.configuration.append_config_values(FSS_OPTS)

        self.proxy = rest_proxy.RESTProxy(self.configuration)
        self._backend_name = (
            self.configuration.safe_get('volume_backend_name') or 'FalconStor')
        self._storage_protocol = 'iSCSI'

    def do_setup(self, context):
        self.proxy.do_setup()
        LOG.info(_LI('Activate FalconStor cinder volume driver.'))

    def check_for_setup_error(self):
        if self.proxy.session_id is None:
            msg = (_('FSS cinder volume driver not ready: Unable to determine '
                     'session id.'))
            raise exception.VolumeBackendAPIException(data=msg)

        if not self.configuration.fss_pool:
            msg = _('Pool is not available in the cinder configuration '
                    'fields.')
            raise exception.InvalidHost(reason=msg)

        self._pool_checking(self.configuration.fss_pool)

    def _pool_checking(self, pool_id):
        pool_count = 0
        try:
            output = self.proxy.list_pool_info(pool_id)
            if "name" in output['data']:
                pool_count = len(re.findall(rest_proxy.GROUP_PREFIX,
                                            output['data']['name']))
            if pool_count is 0:
                msg = (_('The given pool info must include the storage pool '
                         'and naming start with OpenStack-'))
                raise exception.VolumeBackendAPIException(data=msg)
        except Exception:
            msg = (_('Unexpected exception during pool checking.'))
            LOG.exception(msg)
            raise exception.VolumeBackendAPIException(data=msg)

    def _check_multipath(self):
        if self.configuration.use_multipath_for_image_xfer:
            if not self.configuration.san_secondary_ip:
                msg = (_('The san_secondary_ip param is null.'))
                raise exception.VolumeBackendAPIException(data=msg)
            output = self.proxy._check_iocluster_state()
            if not output:
                msg = (_('FSS do not support multipathing.'))
                raise exception.VolumeBackendAPIException(data=msg)
            return output
        else:
            return False

    def create_volume(self, volume):
        """Creates a volume.

        We  use the metadata of the volume to create variety volume.

        Create a thin provisioned volume :
        [Usage] create --volume-type FSS --metadata thinprovisioned=true
            thinsize=<thin-volume-size>

        Create a LUN that is a Timeview of another LUN at a specified CDP tag:
        [Usage] create --volume-type FSS --metadata timeview=<vid>
            cdptag=<tag> volume-size

        Create a LUN that is a Timeview of another LUN at a specified Timemark:
        [Usage] create --volume-type FSS --metadata timeview=<vid>
            rawtimestamp=<rawtimestamp> volume-size

        """

        volume_metadata = self._get_volume_metadata(volume)
        if not volume_metadata:
            volume_name, fss_metadata = self.proxy.create_vdev(volume)
        else:
            if ("timeview" in volume_metadata and
                    ("cdptag" in volume_metadata) or
                    ("rawtimestamp" in volume_metadata)):
                volume_name, fss_metadata = self.proxy.create_tv_from_cdp_tag(
                    volume_metadata, volume)
            elif ("thinprovisioned" in volume_metadata and
                    "thinsize" in volume_metadata):
                volume_name, fss_metadata = self.proxy.create_thin_vdev(
                    volume_metadata, volume)
            else:
                volume_name, fss_metadata = self.proxy.create_vdev(volume)
            fss_metadata.update(volume_metadata)

        if type(volume['metadata']) is dict:
            fss_metadata.update(volume['metadata'])
        if volume['consistencygroup_id']:
            self.proxy._add_volume_to_consistency_group(
                volume['consistencygroup_id'],
                volume_name
            )
        return {'metadata': fss_metadata}

    def _get_volume_metadata(self, volume):
        volume_metadata = {}
        if 'volume_metadata' in volume:
            for metadata in volume['volume_metadata']:
                volume_metadata[metadata['key']] = metadata['value']
        return volume_metadata

    def create_cloned_volume(self, volume, src_vref):
        """Creates a clone of the specified volume."""
        new_vol_name = self.proxy._get_fss_volume_name(volume)
        src_name = self.proxy._get_fss_volume_name(src_vref)
        vol_size = volume["size"]
        src_size = src_vref["size"]
        fss_metadata = self.proxy.clone_volume(new_vol_name, src_name)
        self.proxy.extend_vdev(new_vol_name, src_size, vol_size)

        if volume['consistencygroup_id']:
            self.proxy._add_volume_to_consistency_group(
                volume['consistencygroup_id'],
                new_vol_name
            )
        volume_metadata = self._get_volume_metadata(volume)
        fss_metadata.update(volume_metadata)

        if type(volume['metadata']) is dict:
            fss_metadata.update(volume['metadata'])
        return {'metadata': fss_metadata}

    def extend_volume(self, volume, new_size):
        """Extend volume to new_size."""
        volume_name = self.proxy._get_fss_volume_name(volume)
        self.proxy.extend_vdev(volume_name, volume["size"], new_size)

    def delete_volume(self, volume):
        """Disconnect all hosts and delete the volume"""
        try:
            self.proxy.delete_vdev(volume)
        except rest_proxy.FSSHTTPError as err:
            with excutils.save_and_reraise_exception() as ctxt:
                ctxt.reraise = False
                LOG.warning(_LW("Volume deletion failed with message: %s"),
                            err.reason)

    def create_snapshot(self, snapshot):
        """Creates a snapshot."""
        snap_metadata = snapshot["metadata"]
        metadata = self.proxy.create_snapshot(snapshot)
        snap_metadata.update(metadata)
        return {'metadata': snap_metadata}

    def delete_snapshot(self, snapshot):
        """Deletes a snapshot."""
        try:
            self.proxy.delete_snapshot(snapshot)
        except rest_proxy.FSSHTTPError as err:
            with excutils.save_and_reraise_exception() as ctxt:
                ctxt.reraise = False
                LOG.error(
                    _LE("Snapshot deletion failed with message: %s"),
                    err.reason)

    def create_volume_from_snapshot(self, volume, snapshot):
        """Creates a volume from a snapshot."""
        vol_size = volume['size']
        snap_size = snapshot['volume_size']
        volume_name, fss_metadata = self.proxy.create_volume_from_snapshot(
            volume, snapshot)

        if vol_size != snap_size:
            try:
                extend_volume_name = self.proxy._get_fss_volume_name(volume)
                self.proxy.extend_vdev(extend_volume_name, snap_size, vol_size)
            except rest_proxy.FSSHTTPError as err:
                with excutils.save_and_reraise_exception() as ctxt:
                    ctxt.reraise = False
                    LOG.error(_LE(
                        "Resizing %(id)s failed with message: %(msg)s. "
                        "Cleaning volume."), {'id': volume["id"],
                                              'msg': err.reason})

        if type(volume['metadata']) is dict:
            fss_metadata.update(volume['metadata'])

        if volume['consistencygroup_id']:
            self.proxy._add_volume_to_consistency_group(
                volume['consistencygroup_id'],
                volume_name)
        return {'metadata': fss_metadata}

    def ensure_export(self, context, volume):
        pass

    def create_export(self, context, volume, connector):
        pass

    def remove_export(self, context, volume):
        pass

    # Attach/detach volume to instance/host
    def attach_volume(self, context, volume, instance_uuid, host_name,
                      mountpoint):
        pass

    def detach_volume(self, context, volume, attachment=None):
        pass

    def get_volume_stats(self, refresh=False):
        total_capacity = 0
        free_space = 0
        if refresh:
            try:
                info = self.proxy._get_pools_info()
                if info:
                    total_capacity = int(info['total_capacity_gb'])
                    used_space = int(info['used_gb'])
                    free_space = int(total_capacity - used_space)

                data = {"vendor_name": "FalconStor",
                        "volume_backend_name": self._backend_name,
                        "driver_version": self.VERSION,
                        "storage_protocol": self._storage_protocol,
                        "total_capacity_gb": total_capacity,
                        "free_capacity_gb": free_space,
                        "reserved_percentage": 0,
                        "consistencygroup_support": True
                        }

                self._stats = data

            except Exception as exc:
                LOG.error(_LE('Cannot get volume status %(exc)s.'),
                          {'exc': exc})
        return self._stats

    def create_consistencygroup(self, context, group):
        """Creates a consistencygroup."""
        self.proxy.create_group(group)
        model_update = {'status': 'available'}
        return model_update

    def delete_consistencygroup(self, context, group, volumes):
        """Deletes a consistency group."""
        self.proxy.destroy_group(group)
        volume_updates = []
        for volume in volumes:
            self.delete_volume(volume)
            volume_updates.append({
                'id': volume.id,
                'status': 'deleted'
            })

        model_update = {'status': group['status']}
        return model_update, volume_updates

    def update_consistencygroup(self, context, group,
                                add_volumes=None, remove_volumes=None):
        addvollist = []
        remvollist = []
        if add_volumes:
            for volume in add_volumes:
                addvollist.append(self.proxy._get_fss_volume_name(volume))
        if remove_volumes:
            for volume in remove_volumes:
                remvollist.append(self.proxy._get_fss_volume_name(volume))

        self.proxy.set_group(group['id'], addvollist=addvollist,
                             remvollist=remvollist)
        return None, None, None

    def create_cgsnapshot(self, context, cgsnapshot, snapshots):
        """Creates a cgsnapshot."""
        cgsnapshot_id = cgsnapshot['id']
        try:
            self.proxy.create_cgsnapshot(cgsnapshot)
        except Exception as e:
            msg = _('Failed to create cg snapshot %(id)s '
                    'due to %(reason)s.') % {'id': cgsnapshot_id,
                                             'reason': six.text_type(e)}
            raise exception.VolumeBackendAPIException(data=msg)

        snapshot_updates = []
        for snapshot in snapshots:
            snapshot_updates.append({
                'id': snapshot.id,
                'status': 'available'
            })
        model_update = {'status': 'available'}
        return model_update, snapshot_updates

    def delete_cgsnapshot(self, context, cgsnapshot, snapshots):
        """Deletes a cgsnapshot."""
        cgsnapshot_id = cgsnapshot.id
        try:
            self.proxy.delete_cgsnapshot(cgsnapshot)
        except Exception as e:
            msg = _('Failed to delete cgsnapshot %(id)s '
                    'due to %(reason)s.') % {'id': cgsnapshot_id,
                                             'reason': six.text_type(e)}
            raise exception.VolumeBackendAPIException(data=msg)

        snapshot_updates = []
        for snapshot in snapshots:
            snapshot_updates.append({
                'id': snapshot.id,
                'status': 'deleted',
            })
        model_update = {'status': cgsnapshot.status}
        return model_update, snapshot_updates

    def manage_existing(self, volume, existing_ref):
        """Convert an existing FSS volume to a Cinder volume.

        We expect a volume id in the existing_ref that matches one in FSS.
        """
        volume_metadata = {}
        self.proxy._get_existing_volume_ref_vid(existing_ref)
        self.proxy._manage_existing_volume(existing_ref['source-id'], volume)
        volume_metadata['FSS-vid'] = existing_ref['source-id']
        updates = {'metadata': volume_metadata}
        return updates

    def manage_existing_get_size(self, volume, existing_ref):
        """Get size of an existing FSS volume.

        We expect a volume id in the existing_ref that matches one in FSS.
        """
        sizemb = self.proxy._get_existing_volume_ref_vid(existing_ref)
        size = int(math.ceil(float(sizemb) / units.Ki))
        return size

    def unmanage(self, volume):
        """Remove Cinder management from FSS volume"""
        self.proxy.unmanage(volume)

    def copy_image_to_volume(self, context, volume, image_service, image_id):
        with image_utils.temporary_file() as tmp:
            image_utils.fetch_verify_image(context, image_service,
                                           image_id, tmp)
        image_utils.fetch_to_raw(context,
                                 image_service,
                                 image_id,
                                 tmp,
                                 self.configuration.volume_dd_blocksize,
                                 size=volume['size'])
