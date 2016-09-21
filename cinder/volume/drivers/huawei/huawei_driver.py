# Copyright (c) 2016 Huawei Technologies Co., Ltd.
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

import collections
import json
import math
import re
import six
import uuid

from oslo_config import cfg
from oslo_log import log as logging
from oslo_utils import excutils
from oslo_utils import units

from cinder import context
from cinder import exception
from cinder.i18n import _, _LE, _LI, _LW
from cinder import interface
from cinder import utils
from cinder.volume import driver
from cinder.volume.drivers.huawei import constants
from cinder.volume.drivers.huawei import fc_zone_helper
from cinder.volume.drivers.huawei import huawei_conf
from cinder.volume.drivers.huawei import huawei_utils
from cinder.volume.drivers.huawei import hypermetro
from cinder.volume.drivers.huawei import replication
from cinder.volume.drivers.huawei import rest_client
from cinder.volume.drivers.huawei import smartx
from cinder.volume import utils as volume_utils
from cinder.volume import volume_types
from cinder.zonemanager import utils as fczm_utils

LOG = logging.getLogger(__name__)

huawei_opts = [
    cfg.StrOpt('cinder_huawei_conf_file',
               default='/etc/cinder/cinder_huawei_conf.xml',
               help='The configuration file for the Cinder Huawei driver.'),
    cfg.StrOpt('hypermetro_devices',
               default=None,
               help='The remote device hypermetro will use.'),
    cfg.StrOpt('metro_san_user',
               default=None,
               help='The remote metro device san user.'),
    cfg.StrOpt('metro_san_password',
               default=None,
               help='The remote metro device san password.'),
    cfg.StrOpt('metro_domain_name',
               default=None,
               help='The remote metro device domain name.'),
    cfg.StrOpt('metro_san_address',
               default=None,
               help='The remote metro device request url.'),
    cfg.StrOpt('metro_storage_pools',
               default=None,
               help='The remote metro device pool names.'),
]

CONF = cfg.CONF
CONF.register_opts(huawei_opts)

snap_attrs = ('id', 'volume_id', 'volume', 'provider_location')
Snapshot = collections.namedtuple('Snapshot', snap_attrs)
vol_attrs = ('id', 'lun_type', 'provider_location', 'metadata')
Volume = collections.namedtuple('Volume', vol_attrs)


class HuaweiBaseDriver(driver.VolumeDriver):

    # ThirdPartySytems wiki page
    CI_WIKI_NAME = "Huawei_volume_CI"

    def __init__(self, *args, **kwargs):
        super(HuaweiBaseDriver, self).__init__(*args, **kwargs)

        if not self.configuration:
            msg = _('Configuration is not found.')
            raise exception.InvalidInput(reason=msg)

        self.active_backend_id = kwargs.get('active_backend_id')

        self.configuration.append_config_values(huawei_opts)
        self.huawei_conf = huawei_conf.HuaweiConf(self.configuration)
        self.metro_flag = False
        self.replica = None

    def get_local_and_remote_dev_conf(self):
        self.loc_dev_conf = self.huawei_conf.get_local_device()

        # Now just support one replication device.
        replica_devs = self.huawei_conf.get_replication_devices()
        self.replica_dev_conf = replica_devs[0] if replica_devs else {}

    def get_local_and_remote_client_conf(self):
        if self.active_backend_id:
            return self.replica_dev_conf, self.loc_dev_conf
        else:
            return self.loc_dev_conf, self.replica_dev_conf

    def do_setup(self, context):
        """Instantiate common class and login storage system."""
        # Set huawei private configuration into Configuration object.
        self.huawei_conf.update_config_value()

        self.get_local_and_remote_dev_conf()
        client_conf, replica_client_conf = (
            self.get_local_and_remote_client_conf())

        # init local client
        if not client_conf:
            msg = _('Get active client failed.')
            LOG.error(msg)
            raise exception.VolumeBackendAPIException(data=msg)

        self.client = rest_client.RestClient(self.configuration,
                                             **client_conf)
        self.client.login()

        # init remote client
        metro_san_address = self.configuration.safe_get("metro_san_address")
        metro_san_user = self.configuration.safe_get("metro_san_user")
        metro_san_password = self.configuration.safe_get("metro_san_password")
        if metro_san_address and metro_san_user and metro_san_password:
            metro_san_address = metro_san_address.split(";")
            self.rmt_client = rest_client.RestClient(self.configuration,
                                                     metro_san_address,
                                                     metro_san_user,
                                                     metro_san_password)

            self.rmt_client.login()
            self.metro_flag = True
        else:
            self.metro_flag = False
            LOG.warning(_LW("Remote device not configured in cinder.conf"))
        # init replication manager
        if replica_client_conf:
            self.replica_client = rest_client.RestClient(self.configuration,
                                                         **replica_client_conf)
            self.replica_client.try_login()
            self.replica = replication.ReplicaPairManager(self.client,
                                                          self.replica_client,
                                                          self.configuration)

    def check_for_setup_error(self):
        pass

    def get_volume_stats(self, refresh=False):
        """Get volume status and reload huawei config file."""
        self.huawei_conf.update_config_value()
        stats = self.client.update_volume_stats()
        stats = self.update_hypermetro_capability(stats)

        if self.replica:
            stats = self.replica.update_replica_capability(stats)
            targets = [self.replica_dev_conf['backend_id']]
            stats['replication_targets'] = targets
            stats['replication_enabled'] = True

        return stats

    def update_hypermetro_capability(self, stats):
        if self.metro_flag:
            version = self.client.find_array_version()
            rmt_version = self.rmt_client.find_array_version()
            if (version >= constants.ARRAY_VERSION
                    and rmt_version >= constants.ARRAY_VERSION):
                for pool in stats['pools']:
                    pool['hypermetro'] = True
                    pool['consistencygroup_support'] = True

        return stats

    def _get_volume_type(self, volume):
        volume_type = None
        type_id = volume.volume_type_id
        if type_id:
            ctxt = context.get_admin_context()
            volume_type = volume_types.get_volume_type(ctxt, type_id)

        return volume_type

    def _get_volume_params(self, volume_type):
        """Return the parameters for creating the volume."""
        specs = {}
        if volume_type:
            specs = dict(volume_type).get('extra_specs')

        opts = self._get_volume_params_from_specs(specs)
        return opts

    def _get_consistencygroup_type(self, group):
        specs = {}
        opts = {}
        type_id = group.volume_type_id.split(",")
        if type_id[0] and len(type_id) == 2:
            ctxt = context.get_admin_context()
            volume_type = volume_types.get_volume_type(ctxt, type_id[0])
            specs = dict(volume_type).get('extra_specs')
            opts = self._get_volume_params_from_specs(specs)
        return opts

    def _get_volume_params_from_specs(self, specs):
        """Return the volume parameters from extra specs."""
        opts_capability = {
            'smarttier': False,
            'smartcache': False,
            'smartpartition': False,
            'thin_provisioning_support': False,
            'thick_provisioning_support': False,
            'hypermetro': False,
            'replication_enabled': False,
            'replication_type': 'async',
        }

        opts_value = {
            'policy': None,
            'partitionname': None,
            'cachename': None,
        }

        opts_associate = {
            'smarttier': 'policy',
            'smartcache': 'cachename',
            'smartpartition': 'partitionname',
        }

        opts = self._get_opts_from_specs(opts_capability,
                                         opts_value,
                                         opts_associate,
                                         specs)
        opts = smartx.SmartX().get_smartx_specs_opts(opts)
        opts = replication.get_replication_opts(opts)
        LOG.debug('volume opts %(opts)s.', {'opts': opts})
        return opts

    def _get_opts_from_specs(self, opts_capability, opts_value,
                             opts_associate, specs):
        """Get the well defined extra specs."""
        opts = {}
        opts.update(opts_capability)
        opts.update(opts_value)

        for key, value in specs.items():
            # Get the scope, if it is using scope format.
            scope = None
            key_split = key.split(':')
            if len(key_split) > 2 and key_split[0] != "capabilities":
                continue

            if len(key_split) == 1:
                key = key_split[0].lower()
            else:
                scope = key_split[0].lower()
                key = key_split[1].lower()

            if ((not scope or scope == 'capabilities')
                    and key in opts_capability):
                words = value.split()
                if words and len(words) == 2 and words[0] in ('<is>', '<in>'):
                    opts[key] = words[1].lower()
                elif key == 'replication_type':
                    LOG.error(_LE("Extra specs must be specified as "
                                  "replication_type='<in> sync' or "
                                  "'<in> async'."))
                else:
                    LOG.error(_LE("Extra specs must be specified as "
                                  "capabilities:%s='<is> True'."), key)

            if ((scope in opts_capability)
                    and (key in opts_value)
                    and (scope in opts_associate)
                    and (opts_associate[scope] == key)):
                opts[key] = value

        return opts

    def _get_lun_params(self, volume, opts):
        pool_name = volume_utils.extract_host(volume.host, level='pool')
        params = {
            'TYPE': '11',
            'NAME': huawei_utils.encode_name(volume.id),
            'PARENTTYPE': '216',
            'PARENTID': self.client.get_pool_id(pool_name),
            'DESCRIPTION': volume.name,
            'ALLOCTYPE': opts.get('LUNType', self.configuration.lun_type),
            'CAPACITY': huawei_utils.get_volume_size(volume),
            'WRITEPOLICY': self.configuration.lun_write_type,
            'MIRRORPOLICY': self.configuration.lun_mirror_switch,
            'PREFETCHPOLICY': self.configuration.lun_prefetch_type,
            'PREFETCHVALUE': self.configuration.lun_prefetch_value,
            'DATATRANSFERPOLICY':
                opts.get('policy', self.configuration.lun_policy),
            'READCACHEPOLICY': self.configuration.lun_read_cache_policy,
            'WRITECACHEPOLICY': self.configuration.lun_write_cache_policy, }

        LOG.info(_LI('volume: %(volume)s, lun params: %(params)s.'),
                 {'volume': volume.id, 'params': params})
        return params

    def _create_volume(self, volume, lun_params):
        # Create LUN on the array.
        model_update = {}
        lun_info = self.client.create_lun(lun_params)
        model_update['provider_location'] = lun_info['ID']

        admin_metadata = huawei_utils.get_admin_metadata(volume)
        admin_metadata.update({'huawei_lun_wwn': lun_info['WWN']})
        model_update['admin_metadata'] = admin_metadata
        metadata = huawei_utils.get_volume_metadata(volume)
        model_update['metadata'] = metadata
        return lun_info, model_update

    def _create_base_type_volume(self, opts, volume, volume_type):
        """Create volume and add some base type.

        Base type is the service type which doesn't conflict with the other.
        """
        lun_params = self._get_lun_params(volume, opts)
        lun_info, model_update = self._create_volume(volume, lun_params)
        lun_id = lun_info['ID']

        try:
            qos = smartx.SmartQos.get_qos_by_volume_type(volume_type)
            if qos:
                smart_qos = smartx.SmartQos(self.client)
                smart_qos.add(qos, lun_id)

            smartpartition = smartx.SmartPartition(self.client)
            smartpartition.add(opts, lun_id)

            smartcache = smartx.SmartCache(self.client)
            smartcache.add(opts, lun_id)
        except Exception as err:
            self._delete_lun_with_check(lun_id)
            msg = _('Create volume error. Because %s.') % six.text_type(err)
            raise exception.VolumeBackendAPIException(data=msg)

        return lun_params, lun_info, model_update

    def _add_extend_type_to_volume(self, opts, lun_params, lun_info,
                                   model_update):
        """Add the extend type.

        Extend type is the service type which may conflict with the other.
        So add it after those services.
        """
        lun_id = lun_info['ID']
        if opts.get('hypermetro') == 'true':
            metro = hypermetro.HuaweiHyperMetro(self.client,
                                                self.rmt_client,
                                                self.configuration)
            try:
                metro_info = metro.create_hypermetro(lun_id, lun_params)
                model_update['metadata'].update(metro_info)
            except exception.VolumeBackendAPIException as err:
                LOG.error(_LE('Create hypermetro error: %s.'), err)
                self._delete_lun_with_check(lun_id)
                raise

        if opts.get('replication_enabled') == 'true':
            replica_model = opts.get('replication_type')
            try:
                replica_info = self.replica.create_replica(lun_info,
                                                           replica_model)
                model_update.update(replica_info)
            except Exception as err:
                LOG.exception(_LE('Create replication volume error.'))
                self._delete_lun_with_check(lun_id)
                raise

        return model_update

    def create_volume(self, volume):
        """Create a volume."""
        volume_type = self._get_volume_type(volume)
        opts = self._get_volume_params(volume_type)
        if (opts.get('hypermetro') == 'true'
                and opts.get('replication_enabled') == 'true'):
            err_msg = _("Hypermetro and Replication can not be "
                        "used in the same volume_type.")
            LOG.error(err_msg)
            raise exception.VolumeBackendAPIException(data=err_msg)

        lun_params, lun_info, model_update = (
            self._create_base_type_volume(opts, volume, volume_type))

        model_update = self._add_extend_type_to_volume(opts, lun_params,
                                                       lun_info, model_update)
        return model_update

    def _delete_volume(self, volume):
        lun_id = volume.provider_location
        if not lun_id:
            return

        lun_group_ids = self.client.get_lungroupids_by_lunid(lun_id)
        if lun_group_ids and len(lun_group_ids) == 1:
            self.client.remove_lun_from_lungroup(lun_group_ids[0], lun_id)

        self.client.delete_lun(lun_id)

    def delete_volume(self, volume):
        """Delete a volume.

        Three steps:
        Firstly, remove associate from lungroup.
        Secondly, remove associate from QoS policy.
        Thirdly, remove the lun.
        """
        lun_id = self._check_volume_exist_on_array(
            volume, constants.VOLUME_NOT_EXISTS_WARN)
        if not lun_id:
            return

        qos_id = self.client.get_qosid_by_lunid(lun_id)
        if qos_id:
            smart_qos = smartx.SmartQos(self.client)
            smart_qos.remove(qos_id, lun_id)

        metadata = huawei_utils.get_volume_metadata(volume)
        if 'hypermetro_id' in metadata:
            metro = hypermetro.HuaweiHyperMetro(self.client,
                                                self.rmt_client,
                                                self.configuration)
            try:
                metro.delete_hypermetro(volume)
            except exception.VolumeBackendAPIException as err:
                LOG.error(_LE('Delete hypermetro error: %s.'), err)
                # We have checked the LUN WWN above,
                # no need to check again here.
                self._delete_volume(volume)
                raise

        # Delete a replication volume
        replica_data = volume.replication_driver_data
        if replica_data:
            try:
                self.replica.delete_replica(volume)
            except exception.VolumeBackendAPIException as err:
                with excutils.save_and_reraise_exception():
                    LOG.exception(_LE("Delete replication error."))
                    self._delete_volume(volume)

        self._delete_volume(volume)

    def _delete_lun_with_check(self, lun_id, lun_wwn=None):
        if not lun_id:
            return

        if self.client.check_lun_exist(lun_id, lun_wwn):
            qos_id = self.client.get_qosid_by_lunid(lun_id)
            if qos_id:
                smart_qos = smartx.SmartQos(self.client)
                smart_qos.remove(qos_id, lun_id)

            self.client.delete_lun(lun_id)

    def _is_lun_migration_complete(self, src_id, dst_id):
        result = self.client.get_lun_migration_task()
        found_migration_task = False
        if 'data' not in result:
            return False

        for item in result['data']:
            if (src_id == item['PARENTID'] and dst_id == item['TARGETLUNID']):
                found_migration_task = True
                if constants.MIGRATION_COMPLETE == item['RUNNINGSTATUS']:
                    return True
                if constants.MIGRATION_FAULT == item['RUNNINGSTATUS']:
                    msg = _("Lun migration error.")
                    LOG.error(msg)
                    raise exception.VolumeBackendAPIException(data=msg)

        if not found_migration_task:
            err_msg = _("Cannot find migration task.")
            LOG.error(err_msg)
            raise exception.VolumeBackendAPIException(data=err_msg)

        return False

    def _is_lun_migration_exist(self, src_id, dst_id):
        try:
            result = self.client.get_lun_migration_task()
        except Exception:
            LOG.error(_LE("Get LUN migration error."))
            return False

        if 'data' in result:
            for item in result['data']:
                if (src_id == item['PARENTID']
                        and dst_id == item['TARGETLUNID']):
                    return True
        return False

    def _migrate_lun(self, src_id, dst_id):
        try:
            self.client.create_lun_migration(src_id, dst_id)

            def _is_lun_migration_complete():
                return self._is_lun_migration_complete(src_id, dst_id)

            wait_interval = constants.MIGRATION_WAIT_INTERVAL
            huawei_utils.wait_for_condition(_is_lun_migration_complete,
                                            wait_interval,
                                            self.configuration.lun_timeout)
        # Clean up if migration failed.
        except Exception as ex:
            raise exception.VolumeBackendAPIException(data=ex)
        finally:
            if self._is_lun_migration_exist(src_id, dst_id):
                self.client.delete_lun_migration(src_id, dst_id)
            self._delete_lun_with_check(dst_id)

        LOG.debug("Migrate lun %s successfully.", src_id)
        return True

    def _wait_volume_ready(self, lun_id):
        wait_interval = self.configuration.lun_ready_wait_interval

        def _volume_ready():
            result = self.client.get_lun_info(lun_id)
            if (result['HEALTHSTATUS'] == constants.STATUS_HEALTH
               and result['RUNNINGSTATUS'] == constants.STATUS_VOLUME_READY):
                return True
            return False

        huawei_utils.wait_for_condition(_volume_ready,
                                        wait_interval,
                                        wait_interval * 10)

    def _get_original_status(self, volume):
        return 'in-use' if volume.volume_attachment else 'available'

    def update_migrated_volume(self, ctxt, volume, new_volume,
                               original_volume_status=None):
        original_name = huawei_utils.encode_name(volume.id)
        current_name = huawei_utils.encode_name(new_volume.id)

        lun_id = self.client.get_lun_id_by_name(current_name)
        try:
            self.client.rename_lun(lun_id, original_name)
        except exception.VolumeBackendAPIException:
            LOG.error(_LE('Unable to rename lun %s on array.'), current_name)
            return {'_name_id': new_volume.name_id}

        LOG.debug("Renamed lun from %(current_name)s to %(original_name)s "
                  "successfully.",
                  {'current_name': current_name,
                   'original_name': original_name})

        model_update = {'_name_id': None}

        return model_update

    def migrate_volume(self, ctxt, volume, host, new_type=None):
        """Migrate a volume within the same array."""
        self._check_volume_exist_on_array(volume,
                                          constants.VOLUME_NOT_EXISTS_RAISE)

        # NOTE(jlc): Replication volume can't migrate. But retype
        # can remove replication relationship first then do migrate.
        # So don't add this judgement into _check_migration_valid().
        volume_type = self._get_volume_type(volume)
        opts = self._get_volume_params(volume_type)
        if opts.get('replication_enabled') == 'true':
            return (False, None)

        return self._migrate_volume(volume, host, new_type)

    def _check_migration_valid(self, host, volume):
        if 'pool_name' not in host['capabilities']:
            return False

        target_device = host['capabilities']['location_info']

        # Source and destination should be on same array.
        if target_device != self.client.device_id:
            return False

        # Same protocol should be used if volume is in-use.
        protocol = self.configuration.san_protocol
        if (host['capabilities']['storage_protocol'] != protocol
                and self._get_original_status(volume) == 'in-use'):
            return False

        pool_name = host['capabilities']['pool_name']
        if len(pool_name) == 0:
            return False

        return True

    def _migrate_volume(self, volume, host, new_type=None):
        if not self._check_migration_valid(host, volume):
            return (False, None)

        type_id = volume.volume_type_id

        volume_type = None
        if type_id:
            volume_type = volume_types.get_volume_type(None, type_id)

        pool_name = host['capabilities']['pool_name']
        pools = self.client.get_all_pools()
        pool_info = self.client.get_pool_info(pool_name, pools)
        src_volume_name = huawei_utils.encode_name(volume.id)
        dst_volume_name = six.text_type(hash(src_volume_name))
        src_id = volume.provider_location

        opts = None
        qos = None
        if new_type:
            # If new type exists, use new type.
            new_specs = new_type['extra_specs']
            opts = self._get_volume_params_from_specs(new_specs)
            if 'LUNType' not in opts:
                opts['LUNType'] = self.configuration.lun_type

            qos = smartx.SmartQos.get_qos_by_volume_type(new_type)
        elif volume_type:
            qos = smartx.SmartQos.get_qos_by_volume_type(volume_type)

        if not opts:
            opts = self._get_volume_params(volume_type)

        lun_info = self.client.get_lun_info(src_id)

        policy = lun_info['DATATRANSFERPOLICY']
        if opts['policy']:
            policy = opts['policy']
        lun_params = {
            'NAME': dst_volume_name,
            'PARENTID': pool_info['ID'],
            'DESCRIPTION': lun_info['DESCRIPTION'],
            'ALLOCTYPE': opts.get('LUNType', lun_info['ALLOCTYPE']),
            'CAPACITY': lun_info['CAPACITY'],
            'WRITEPOLICY': lun_info['WRITEPOLICY'],
            'MIRRORPOLICY': lun_info['MIRRORPOLICY'],
            'PREFETCHPOLICY': lun_info['PREFETCHPOLICY'],
            'PREFETCHVALUE': lun_info['PREFETCHVALUE'],
            'DATATRANSFERPOLICY': policy,
            'READCACHEPOLICY': lun_info['READCACHEPOLICY'],
            'WRITECACHEPOLICY': lun_info['WRITECACHEPOLICY'],
            'OWNINGCONTROLLER': lun_info['OWNINGCONTROLLER'], }

        lun_info = self.client.create_lun(lun_params)
        lun_id = lun_info['ID']

        if qos:
            LOG.info(_LI('QoS: %s.'), qos)
            SmartQos = smartx.SmartQos(self.client)
            SmartQos.add(qos, lun_id)
        if opts:
            smartpartition = smartx.SmartPartition(self.client)
            smartpartition.add(opts, lun_id)
            smartcache = smartx.SmartCache(self.client)
            smartcache.add(opts, lun_id)

        dst_id = lun_info['ID']
        self._wait_volume_ready(dst_id)
        moved = self._migrate_lun(src_id, dst_id)

        return moved, {}

    def create_volume_from_snapshot(self, volume, snapshot):
        """Create a volume from a snapshot.

        We use LUNcopy to copy a new volume from snapshot.
        The time needed increases as volume size does.
        """
        volume_type = self._get_volume_type(volume)
        opts = self._get_volume_params(volume_type)
        if (opts.get('hypermetro') == 'true'
                and opts.get('replication_enabled') == 'true'):
            err_msg = _("Hypermetro and Replication can not be "
                        "used in the same volume_type.")
            LOG.error(err_msg)
            raise exception.VolumeBackendAPIException(data=err_msg)

        snapshotname = huawei_utils.encode_name(snapshot.id)
        snapshot_id = snapshot.provider_location
        if snapshot_id is None:
            snapshot_id = self.client.get_snapshot_id_by_name(snapshotname)
        if snapshot_id is None:
            err_msg = (_(
                'create_volume_from_snapshot: Snapshot %(name)s '
                'does not exist.')
                % {'name': snapshotname})
            LOG.error(err_msg)
            raise exception.VolumeBackendAPIException(data=err_msg)

        lun_params, lun_info, model_update = (
            self._create_base_type_volume(opts, volume, volume_type))

        tgt_lun_id = model_update['provider_location']
        luncopy_name = huawei_utils.encode_name(volume.id)
        LOG.info(_LI(
            'create_volume_from_snapshot: src_lun_id: %(src_lun_id)s, '
            'tgt_lun_id: %(tgt_lun_id)s, copy_name: %(copy_name)s.'),
            {'src_lun_id': snapshot_id,
             'tgt_lun_id': tgt_lun_id,
             'copy_name': luncopy_name})

        wait_interval = self.configuration.lun_ready_wait_interval

        def _volume_ready():
            result = self.client.get_lun_info(tgt_lun_id)

            if (result['HEALTHSTATUS'] == constants.STATUS_HEALTH
               and result['RUNNINGSTATUS'] == constants.STATUS_VOLUME_READY):
                return True
            return False

        huawei_utils.wait_for_condition(_volume_ready,
                                        wait_interval,
                                        wait_interval * 10)

        self._copy_volume(volume, luncopy_name,
                          snapshot_id, tgt_lun_id)

        # NOTE(jlc): Actually, we just only support replication here right
        # now, not hypermetro.
        model_update = self._add_extend_type_to_volume(opts, lun_params,
                                                       lun_info, model_update)
        return model_update

    def create_cloned_volume(self, volume, src_vref):
        """Clone a new volume from an existing volume."""
        self._check_volume_exist_on_array(src_vref,
                                          constants.VOLUME_NOT_EXISTS_RAISE)

        # Form the snapshot structure.
        snapshot = Snapshot(id=uuid.uuid4().__str__(),
                            volume_id=src_vref.id,
                            volume=src_vref,
                            provider_location=None)

        # Create snapshot.
        self.create_snapshot(snapshot)

        try:
            # Create volume from snapshot.
            model_update = self.create_volume_from_snapshot(volume, snapshot)
        finally:
            try:
                # Delete snapshot.
                self.delete_snapshot(snapshot)
            except exception.VolumeBackendAPIException:
                LOG.warning(_LW(
                    'Failure deleting the snapshot %(snapshot_id)s '
                    'of volume %(volume_id)s.'),
                    {'snapshot_id': snapshot.id,
                     'volume_id': src_vref.id},)

        return model_update

    def _check_volume_exist_on_array(self, volume, action):
        """Check whether the volume exists on the array.

        If the volume exists on the array, return the LUN ID.
        If not exists, raise or log warning.
        """
        # Firstly, try to find LUN ID by volume.provider_location.
        lun_id = volume.provider_location
        # If LUN ID not recorded, find LUN ID by LUN NAME.
        if not lun_id:
            volume_name = huawei_utils.encode_name(volume.id)
            lun_id = self.client.get_lun_id_by_name(volume_name)
            if not lun_id:
                msg = (_("Volume %s does not exist on the array.")
                       % volume.id)
                if action == constants.VOLUME_NOT_EXISTS_WARN:
                    LOG.warning(msg)
                if action == constants.VOLUME_NOT_EXISTS_RAISE:
                    raise exception.VolumeBackendAPIException(data=msg)
                return

        metadata = huawei_utils.get_admin_metadata(volume)
        lun_wwn = metadata.get('huawei_lun_wwn') if metadata else None
        if not lun_wwn:
            LOG.debug("No LUN WWN recorded for volume %s.", volume.id)

        if not self.client.check_lun_exist(lun_id, lun_wwn):
            msg = (_("Volume %s does not exist on the array.")
                   % volume.id)
            if action == constants.VOLUME_NOT_EXISTS_WARN:
                LOG.warning(msg)
            if action == constants.VOLUME_NOT_EXISTS_RAISE:
                raise exception.VolumeBackendAPIException(data=msg)
            return
        return lun_id

    def extend_volume(self, volume, new_size):
        """Extend a volume."""
        lun_id = self._check_volume_exist_on_array(
            volume, constants.VOLUME_NOT_EXISTS_RAISE)

        volume_type = self._get_volume_type(volume)
        opts = self._get_volume_params(volume_type)
        if opts.get('replication_enabled') == 'true':
            msg = (_("Can't extend replication volume, volume: %(id)s") %
                   {"id": volume.id})
            LOG.error(msg)
            raise exception.VolumeBackendAPIException(data=msg)

        lun_info = self.client.get_lun_info(lun_id)
        old_size = int(lun_info.get('CAPACITY'))

        new_size = int(new_size) * units.Gi / 512

        if new_size == old_size:
            LOG.info(_LI("New size is equal to the real size from backend"
                         " storage, no need to extend."
                         " realsize: %(oldsize)s, newsize: %(newsize)s."),
                     {'oldsize': old_size,
                      'newsize': new_size})
            return
        if new_size < old_size:
            msg = (_("New size should be bigger than the real size from "
                     "backend storage."
                     " realsize: %(oldsize)s, newsize: %(newsize)s."),
                   {'oldsize': old_size,
                    'newsize': new_size})
            LOG.error(msg)
            raise exception.VolumeBackendAPIException(data=msg)
        volume_name = huawei_utils.encode_name(volume.id)

        LOG.info(_LI(
            'Extend volume: %(volumename)s, '
            'oldsize: %(oldsize)s, newsize: %(newsize)s.'),
            {'volumename': volume_name,
             'oldsize': old_size,
             'newsize': new_size})

        self.client.extend_lun(lun_id, new_size)

    def create_snapshot(self, snapshot):
        volume = snapshot.volume
        if not volume:
            msg = (_("Can't get volume id from snapshot, snapshot: %(id)s")
                   % {"id": snapshot.id})
            LOG.error(msg)
            raise exception.VolumeBackendAPIException(data=msg)

        volume_name = huawei_utils.encode_name(snapshot.volume_id)
        lun_id = self.client.get_lun_id(volume, volume_name)
        snapshot_name = huawei_utils.encode_name(snapshot.id)
        snapshot_description = snapshot.id
        snapshot_info = self.client.create_snapshot(lun_id,
                                                    snapshot_name,
                                                    snapshot_description)
        snapshot_id = snapshot_info['ID']
        self.client.activate_snapshot(snapshot_id)

        return {'provider_location': snapshot_info['ID'],
                'lun_info': snapshot_info}

    def delete_snapshot(self, snapshot):
        snapshotname = huawei_utils.encode_name(snapshot.id)
        volume_name = huawei_utils.encode_name(snapshot.volume_id)

        LOG.info(_LI(
            'stop_snapshot: snapshot name: %(snapshot)s, '
            'volume name: %(volume)s.'),
            {'snapshot': snapshotname,
             'volume': volume_name},)

        snapshot_id = snapshot.provider_location
        if snapshot_id is None:
            snapshot_id = self.client.get_snapshot_id_by_name(snapshotname)

        if snapshot_id and self.client.check_snapshot_exist(snapshot_id):
            self.client.stop_snapshot(snapshot_id)
            self.client.delete_snapshot(snapshot_id)
        else:
            LOG.warning(_LW("Can't find snapshot on the array."))

    def retype(self, ctxt, volume, new_type, diff, host):
        """Convert the volume to be of the new type."""
        LOG.debug("Enter retype: id=%(id)s, new_type=%(new_type)s, "
                  "diff=%(diff)s, host=%(host)s.", {'id': volume.id,
                                                    'new_type': new_type,
                                                    'diff': diff,
                                                    'host': host})
        self._check_volume_exist_on_array(
            volume, constants.VOLUME_NOT_EXISTS_RAISE)

        # Check what changes are needed
        migration, change_opts, lun_id = self.determine_changes_when_retype(
            volume, new_type, host)

        model_update = {}
        replica_enabled_change = change_opts.get('replication_enabled')
        replica_type_change = change_opts.get('replication_type')
        if replica_enabled_change and replica_enabled_change[0] == 'true':
            try:
                self.replica.delete_replica(volume)
                model_update.update({'replication_status': 'disabled',
                                     'replication_driver_data': None})
            except exception.VolumeBackendAPIException:
                LOG.exception(_LE('Retype volume error. '
                                  'Delete replication failed.'))
                return False

        try:
            if migration:
                LOG.debug("Begin to migrate LUN(id: %(lun_id)s) with "
                          "change %(change_opts)s.",
                          {"lun_id": lun_id, "change_opts": change_opts})
                if not self._migrate_volume(volume, host, new_type):
                    LOG.warning(_LW("Storage-assisted migration failed during "
                                    "retype."))
                    return False
            else:
                # Modify lun to change policy
                self.modify_lun(lun_id, change_opts)
        except exception.VolumeBackendAPIException:
            LOG.exception(_LE('Retype volume error.'))
            return False

        if replica_enabled_change and replica_enabled_change[1] == 'true':
            try:
                # If replica_enabled_change is not None, the
                # replica_type_change won't be None. See function
                # determine_changes_when_retype.
                lun_info = self.client.get_lun_info(lun_id)
                replica_info = self.replica.create_replica(
                    lun_info, replica_type_change[1])
                model_update.update(replica_info)
            except exception.VolumeBackendAPIException:
                LOG.exception(_LE('Retype volume error. '
                                  'Create replication failed.'))
                return False

        return (True, model_update)

    def modify_lun(self, lun_id, change_opts):
        if change_opts.get('partitionid'):
            old, new = change_opts['partitionid']
            old_id = old[0]
            old_name = old[1]
            new_id = new[0]
            new_name = new[1]
            if old_id:
                self.client.remove_lun_from_partition(lun_id, old_id)
            if new_id:
                self.client.add_lun_to_partition(lun_id, new_id)
            LOG.info(_LI("Retype LUN(id: %(lun_id)s) smartpartition from "
                         "(name: %(old_name)s, id: %(old_id)s) to "
                         "(name: %(new_name)s, id: %(new_id)s) success."),
                     {"lun_id": lun_id,
                      "old_id": old_id, "old_name": old_name,
                      "new_id": new_id, "new_name": new_name})

        if change_opts.get('cacheid'):
            old, new = change_opts['cacheid']
            old_id = old[0]
            old_name = old[1]
            new_id = new[0]
            new_name = new[1]
            if old_id:
                self.client.remove_lun_from_cache(lun_id, old_id)
            if new_id:
                self.client.add_lun_to_cache(lun_id, new_id)
            LOG.info(_LI("Retype LUN(id: %(lun_id)s) smartcache from "
                         "(name: %(old_name)s, id: %(old_id)s) to "
                         "(name: %(new_name)s, id: %(new_id)s) successfully."),
                     {'lun_id': lun_id,
                      'old_id': old_id, "old_name": old_name,
                      'new_id': new_id, "new_name": new_name})

        if change_opts.get('policy'):
            old_policy, new_policy = change_opts['policy']
            self.client.change_lun_smarttier(lun_id, new_policy)
            LOG.info(_LI("Retype LUN(id: %(lun_id)s) smarttier policy from "
                         "%(old_policy)s to %(new_policy)s success."),
                     {'lun_id': lun_id,
                      'old_policy': old_policy,
                      'new_policy': new_policy})

        if change_opts.get('qos'):
            old_qos, new_qos = change_opts['qos']
            old_qos_id = old_qos[0]
            old_qos_value = old_qos[1]
            if old_qos_id:
                smart_qos = smartx.SmartQos(self.client)
                smart_qos.remove(old_qos_id, lun_id)
            if new_qos:
                smart_qos = smartx.SmartQos(self.client)
                smart_qos.add(new_qos, lun_id)
            LOG.info(_LI("Retype LUN(id: %(lun_id)s) smartqos from "
                         "%(old_qos_value)s to %(new_qos)s success."),
                     {'lun_id': lun_id,
                      'old_qos_value': old_qos_value,
                      'new_qos': new_qos})

    def get_lun_specs(self, lun_id):
        lun_opts = {
            'policy': None,
            'partitionid': None,
            'cacheid': None,
            'LUNType': None,
        }

        lun_info = self.client.get_lun_info(lun_id)
        lun_opts['LUNType'] = int(lun_info['ALLOCTYPE'])
        if lun_info.get('DATATRANSFERPOLICY'):
            lun_opts['policy'] = lun_info['DATATRANSFERPOLICY']
        if lun_info.get('SMARTCACHEPARTITIONID'):
            lun_opts['cacheid'] = lun_info['SMARTCACHEPARTITIONID']
        if lun_info.get('CACHEPARTITIONID'):
            lun_opts['partitionid'] = lun_info['CACHEPARTITIONID']

        return lun_opts

    def _check_needed_changes(self, lun_id, old_opts, new_opts,
                              change_opts, new_type):
        new_cache_id = None
        new_cache_name = new_opts['cachename']
        if new_cache_name:
            new_cache_id = self.client.get_cache_id_by_name(new_cache_name)
            if new_cache_id is None:
                msg = (_(
                    "Can't find cache name on the array, cache name is: "
                    "%(name)s.") % {'name': new_cache_name})
                LOG.error(msg)
                raise exception.VolumeBackendAPIException(data=msg)

        new_partition_id = None
        new_partition_name = new_opts['partitionname']
        if new_partition_name:
            new_partition_id = self.client.get_partition_id_by_name(
                new_partition_name)
            if new_partition_id is None:
                msg = (_(
                    "Can't find partition name on the array, partition name "
                    "is: %(name)s.") % {'name': new_partition_name})
                LOG.error(msg)
                raise exception.VolumeBackendAPIException(data=msg)

        # smarttier
        if old_opts['policy'] != new_opts['policy']:
            change_opts['policy'] = (old_opts['policy'], new_opts['policy'])

        # smartcache
        old_cache_id = old_opts['cacheid']
        if old_cache_id != new_cache_id:
            old_cache_name = None
            if old_cache_id:
                cache_info = self.client.get_cache_info_by_id(old_cache_id)
                old_cache_name = cache_info['NAME']
            change_opts['cacheid'] = ([old_cache_id, old_cache_name],
                                      [new_cache_id, new_cache_name])

        # smartpartition
        old_partition_id = old_opts['partitionid']
        if old_partition_id != new_partition_id:
            old_partition_name = None
            if old_partition_id:
                partition_info = self.client.get_partition_info_by_id(
                    old_partition_id)
                old_partition_name = partition_info['NAME']
            change_opts['partitionid'] = ([old_partition_id,
                                           old_partition_name],
                                          [new_partition_id,
                                           new_partition_name])

        # smartqos
        new_qos = smartx.SmartQos.get_qos_by_volume_type(new_type)
        old_qos_id = self.client.get_qosid_by_lunid(lun_id)
        old_qos = self._get_qos_specs_from_array(old_qos_id)
        if old_qos != new_qos:
            change_opts['qos'] = ([old_qos_id, old_qos], new_qos)

        return change_opts

    def determine_changes_when_retype(self, volume, new_type, host):
        migration = False
        change_opts = {
            'policy': None,
            'partitionid': None,
            'cacheid': None,
            'qos': None,
            'host': None,
            'LUNType': None,
            'replication_enabled': None,
            'replication_type': None,
        }

        lun_id = volume.provider_location
        old_opts = self.get_lun_specs(lun_id)

        new_specs = new_type['extra_specs']
        new_opts = self._get_volume_params_from_specs(new_specs)

        if 'LUNType' not in new_opts:
            new_opts['LUNType'] = self.configuration.lun_type

        if volume.host != host['host']:
            migration = True
            change_opts['host'] = (volume.host, host['host'])
        if old_opts['LUNType'] != new_opts['LUNType']:
            migration = True
            change_opts['LUNType'] = (old_opts['LUNType'], new_opts['LUNType'])

        volume_type = self._get_volume_type(volume)
        volume_opts = self._get_volume_params(volume_type)
        if (volume_opts['replication_enabled'] == 'true'
                or new_opts['replication_enabled'] == 'true'):
            # If replication_enabled changes,
            # then replication_type in change_opts will be set.
            change_opts['replication_enabled'] = (
                volume_opts['replication_enabled'],
                new_opts['replication_enabled'])

            change_opts['replication_type'] = (volume_opts['replication_type'],
                                               new_opts['replication_type'])

        change_opts = self._check_needed_changes(lun_id, old_opts, new_opts,
                                                 change_opts, new_type)

        LOG.debug("Determine changes when retype. Migration: "
                  "%(migration)s, change_opts: %(change_opts)s.",
                  {'migration': migration, 'change_opts': change_opts})
        return migration, change_opts, lun_id

    def _get_qos_specs_from_array(self, qos_id):
        qos = {}
        qos_info = {}
        if qos_id:
            qos_info = self.client.get_qos_info(qos_id)

        for key, value in qos_info.items():
            key = key.upper()
            if key in constants.QOS_KEYS:
                if key == 'LATENCY' and value == '0':
                    continue
                else:
                    qos[key] = value
        return qos

    def create_export(self, context, volume, connector):
        """Export a volume."""
        pass

    def ensure_export(self, context, volume):
        """Synchronously recreate an export for a volume."""
        pass

    def remove_export(self, context, volume):
        """Remove an export for a volume."""
        pass

    def create_export_snapshot(self, context, snapshot, connector):
        """Export a snapshot."""
        pass

    def remove_export_snapshot(self, context, snapshot):
        """Remove an export for a snapshot."""
        pass

    def backup_use_temp_snapshot(self):
        # This config option has a default to be False, So just return it.
        return self.configuration.safe_get("backup_use_temp_snapshot")

    def _copy_volume(self, volume, copy_name, src_lun, tgt_lun):
        luncopy_id = self.client.create_luncopy(copy_name,
                                                src_lun,
                                                tgt_lun)
        wait_interval = self.configuration.lun_copy_wait_interval

        try:
            self.client.start_luncopy(luncopy_id)

            def _luncopy_complete():
                luncopy_info = self.client.get_luncopy_info(luncopy_id)
                if luncopy_info['status'] == constants.STATUS_LUNCOPY_READY:
                    # luncopy_info['status'] means for the running status of
                    # the luncopy. If luncopy_info['status'] is equal to '40',
                    # this luncopy is completely ready.
                    return True
                elif luncopy_info['state'] != constants.STATUS_HEALTH:
                    # luncopy_info['state'] means for the healthy status of the
                    # luncopy. If luncopy_info['state'] is not equal to '1',
                    # this means that an error occurred during the LUNcopy
                    # operation and we should abort it.
                    err_msg = (_(
                        'An error occurred during the LUNcopy operation. '
                        'LUNcopy name: %(luncopyname)s. '
                        'LUNcopy status: %(luncopystatus)s. '
                        'LUNcopy state: %(luncopystate)s.')
                        % {'luncopyname': luncopy_id,
                           'luncopystatus': luncopy_info['status'],
                           'luncopystate': luncopy_info['state']},)
                    LOG.error(err_msg)
                    raise exception.VolumeBackendAPIException(data=err_msg)
            huawei_utils.wait_for_condition(_luncopy_complete,
                                            wait_interval,
                                            self.configuration.lun_timeout)

        except Exception:
            with excutils.save_and_reraise_exception():
                self.client.delete_luncopy(luncopy_id)
                self.delete_volume(volume)

        self.client.delete_luncopy(luncopy_id)

    def _check_lun_valid_for_manage(self, lun_info, external_ref):
        lun_id = lun_info.get('ID')

        # Check whether the LUN is already in LUN group.
        if lun_info.get('ISADD2LUNGROUP') == 'true':
            msg = (_("Can't import LUN %s to Cinder. Already exists in a LUN "
                     "group.") % lun_id)
            raise exception.ManageExistingInvalidReference(
                existing_ref=external_ref, reason=msg)

        # Check whether the LUN is Normal.
        if lun_info.get('HEALTHSTATUS') != constants.STATUS_HEALTH:
            msg = _("Can't import LUN %s to Cinder. LUN status is not "
                    "normal.") % lun_id
            raise exception.ManageExistingInvalidReference(
                existing_ref=external_ref, reason=msg)

        # Check whether the LUN exists in a HyperMetroPair.
        try:
            hypermetro_pairs = self.client.get_hypermetro_pairs()
        except exception.VolumeBackendAPIException:
            hypermetro_pairs = []
            LOG.debug("Can't get hypermetro info, pass the check.")

        for pair in hypermetro_pairs:
            if pair.get('LOCALOBJID') == lun_id:
                msg = (_("Can't import LUN %s to Cinder. Already exists in a "
                         "HyperMetroPair.") % lun_id)
                raise exception.ManageExistingInvalidReference(
                    existing_ref=external_ref, reason=msg)

        # Check whether the LUN exists in a SplitMirror.
        try:
            split_mirrors = self.client.get_split_mirrors()
        except exception.VolumeBackendAPIException as ex:
            if re.search('License is unavailable', ex.msg):
                # Can't check whether the LUN has SplitMirror with it,
                # just pass the check and log it.
                split_mirrors = []
                LOG.warning(_LW('No license for SplitMirror.'))
            else:
                msg = _("Failed to get SplitMirror.")
                raise exception.VolumeBackendAPIException(data=msg)

        for mirror in split_mirrors:
            try:
                target_luns = self.client.get_target_luns(mirror.get('ID'))
            except exception.VolumeBackendAPIException:
                msg = _("Failed to get target LUN of SplitMirror.")
                raise exception.VolumeBackendAPIException(data=msg)

            if (mirror.get('PRILUNID') == lun_id) or (lun_id in target_luns):
                msg = (_("Can't import LUN %s to Cinder. Already exists in a "
                         "SplitMirror.") % lun_id)
                raise exception.ManageExistingInvalidReference(
                    existing_ref=external_ref, reason=msg)

        # Check whether the LUN exists in a migration task.
        try:
            migration_tasks = self.client.get_migration_task()
        except exception.VolumeBackendAPIException as ex:
            if re.search('License is unavailable', ex.msg):
                # Can't check whether the LUN has migration task with it,
                # just pass the check and log it.
                migration_tasks = []
                LOG.warning(_LW('No license for migration.'))
            else:
                msg = _("Failed to get migration task.")
                raise exception.VolumeBackendAPIException(data=msg)

        for migration in migration_tasks:
            if lun_id in (migration.get('PARENTID'),
                          migration.get('TARGETLUNID')):
                msg = (_("Can't import LUN %s to Cinder. Already exists in a "
                         "migration task.") % lun_id)
                raise exception.ManageExistingInvalidReference(
                    existing_ref=external_ref, reason=msg)

        # Check whether the LUN exists in a LUN copy task.
        lun_copy = lun_info.get('LUNCOPYIDS')
        if lun_copy and lun_copy[1:-1]:
            msg = (_("Can't import LUN %s to Cinder. Already exists in "
                     "a LUN copy task.") % lun_id)
            raise exception.ManageExistingInvalidReference(
                existing_ref=external_ref, reason=msg)

        # Check whether the LUN exists in a remote replication task.
        rmt_replication = lun_info.get('REMOTEREPLICATIONIDS')
        if rmt_replication and rmt_replication[1:-1]:
            msg = (_("Can't import LUN %s to Cinder. Already exists in "
                     "a remote replication task.") % lun_id)
            raise exception.ManageExistingInvalidReference(
                existing_ref=external_ref, reason=msg)

        # Check whether the LUN exists in a LUN mirror.
        if self.client.is_lun_in_mirror(lun_id):
            msg = (_("Can't import LUN %s to Cinder. Already exists in "
                     "a LUN mirror.") % lun_id)
            raise exception.ManageExistingInvalidReference(
                existing_ref=external_ref, reason=msg)

    def manage_existing(self, volume, external_ref):
        """Manage an existing volume on the backend storage."""
        # Check whether the LUN is belonged to the specified pool.
        pool = volume_utils.extract_host(volume.host, 'pool')
        LOG.debug("Pool specified is: %s.", pool)
        lun_info = self._get_lun_info_by_ref(external_ref)
        lun_id = lun_info.get('ID')
        description = lun_info.get('DESCRIPTION', '')
        if len(description) <= (
                constants.MAX_VOL_DESCRIPTION - len(volume.name) - 1):
            description = volume.name + ' ' + description

        lun_pool = lun_info.get('PARENTNAME')
        LOG.debug("Storage pool of existing LUN %(lun)s is %(pool)s.",
                  {"lun": lun_id, "pool": lun_pool})
        if pool != lun_pool:
            msg = (_("The specified LUN does not belong to the given "
                     "pool: %s.") % pool)
            raise exception.ManageExistingInvalidReference(
                existing_ref=external_ref, reason=msg)

        # Check other stuffs to determine whether this LUN can be imported.
        self._check_lun_valid_for_manage(lun_info, external_ref)
        type_id = volume.volume_type_id
        new_opts = None
        if type_id:
            # Handle volume type if specified.
            old_opts = self.get_lun_specs(lun_id)
            volume_type = volume_types.get_volume_type(None, type_id)
            new_specs = volume_type.get('extra_specs')
            new_opts = self._get_volume_params_from_specs(new_specs)
            if ('LUNType' in new_opts and
                    old_opts['LUNType'] != new_opts['LUNType']):
                msg = (_("Can't import LUN %(lun_id)s to Cinder. "
                         "LUN type mismatched.") % lun_id)
                raise exception.ManageExistingVolumeTypeMismatch(reason=msg)
            if volume_type:
                change_opts = {'policy': None, 'partitionid': None,
                               'cacheid': None, 'qos': None}
                change_opts = self._check_needed_changes(lun_id, old_opts,
                                                         new_opts, change_opts,
                                                         volume_type)
                self.modify_lun(lun_id, change_opts)

        # Rename the LUN to make it manageable for Cinder.
        new_name = huawei_utils.encode_name(volume.id)
        LOG.debug("Rename LUN %(old_name)s to %(new_name)s.",
                  {'old_name': lun_info.get('NAME'),
                   'new_name': new_name})
        self.client.rename_lun(lun_id, new_name, description)
        metadata = huawei_utils.get_admin_metadata(volume)
        metadata.update({'huawei_lun_wwn': lun_info['WWN']})

        model_update = {}
        model_update.update({'admin_metadata': metadata})
        model_update.update({'provider_location': lun_id})

        if new_opts and new_opts.get('replication_enabled'):
            LOG.debug("Manage volume need to create replication.")
            try:
                lun_info = self.client.get_lun_info(lun_id)
                replica_info = self.replica.create_replica(
                    lun_info, new_opts.get('replication_type'))
                model_update.update(replica_info)
            except exception.VolumeBackendAPIException:
                with excutils.save_and_reraise_exception():
                    LOG.exception(_LE("Manage exist volume failed."))

        return model_update

    def _get_lun_info_by_ref(self, external_ref):
        LOG.debug("Get external_ref: %s", external_ref)
        name = external_ref.get('source-name')
        id = external_ref.get('source-id')
        if not (name or id):
            msg = _('Must specify source-name or source-id.')
            raise exception.ManageExistingInvalidReference(
                existing_ref=external_ref, reason=msg)

        lun_id = id or self.client.get_lun_id_by_name(name)
        if not lun_id:
            msg = _("Can't find LUN on the array, please check the "
                    "source-name or source-id.")
            raise exception.ManageExistingInvalidReference(
                existing_ref=external_ref, reason=msg)

        lun_info = self.client.get_lun_info(lun_id)
        return lun_info

    def unmanage(self, volume):
        """Export Huawei volume from Cinder."""
        LOG.debug("Unmanage volume: %s.", volume.id)

    def manage_existing_get_size(self, volume, external_ref):
        """Get the size of the existing volume."""
        lun_info = self._get_lun_info_by_ref(external_ref)
        size = int(math.ceil(lun_info.get('CAPACITY') /
                             constants.CAPACITY_UNIT))
        return size

    def _check_snapshot_valid_for_manage(self, snapshot_info, external_ref):
        snapshot_id = snapshot_info.get('ID')

        # Check whether the snapshot is normal.
        if snapshot_info.get('HEALTHSTATUS') != constants.STATUS_HEALTH:
            msg = _("Can't import snapshot %s to Cinder. "
                    "Snapshot status is not normal"
                    " or running status is not online.") % snapshot_id
            raise exception.ManageExistingInvalidReference(
                existing_ref=external_ref, reason=msg)

        if snapshot_info.get('EXPOSEDTOINITIATOR') != 'false':
            msg = _("Can't import snapshot %s to Cinder. "
                    "Snapshot is exposed to initiator.") % snapshot_id
            raise exception.ManageExistingInvalidReference(
                existing_ref=external_ref, reason=msg)

    def _get_snapshot_info_by_ref(self, external_ref):
        LOG.debug("Get snapshot external_ref: %s.", external_ref)
        name = external_ref.get('source-name')
        id = external_ref.get('source-id')
        if not (name or id):
            msg = _('Must specify snapshot source-name or source-id.')
            raise exception.ManageExistingInvalidReference(
                existing_ref=external_ref, reason=msg)

        snapshot_id = id or self.client.get_snapshot_id_by_name(name)
        if not snapshot_id:
            msg = _("Can't find snapshot on array, please check the "
                    "source-name or source-id.")
            raise exception.ManageExistingInvalidReference(
                existing_ref=external_ref, reason=msg)

        snapshot_info = self.client.get_snapshot_info(snapshot_id)
        return snapshot_info

    def manage_existing_snapshot(self, snapshot, existing_ref):
        snapshot_info = self._get_snapshot_info_by_ref(existing_ref)
        snapshot_id = snapshot_info.get('ID')
        volume = snapshot.volume
        lun_id = volume.provider_location
        if lun_id != snapshot_info.get('PARENTID'):
            msg = (_("Can't import snapshot %s to Cinder. "
                     "Snapshot doesn't belong to volume."), snapshot_id)
            raise exception.ManageExistingInvalidReference(
                existing_ref=existing_ref, reason=msg)

        # Check whether this snapshot can be imported.
        self._check_snapshot_valid_for_manage(snapshot_info, existing_ref)

        # Rename the snapshot to make it manageable for Cinder.
        description = snapshot.id
        snapshot_name = huawei_utils.encode_name(snapshot.id)
        self.client.rename_snapshot(snapshot_id, snapshot_name, description)
        if snapshot_info.get('RUNNINGSTATUS') != constants.STATUS_ACTIVE:
            self.client.activate_snapshot(snapshot_id)

        LOG.debug("Rename snapshot %(old_name)s to %(new_name)s.",
                  {'old_name': snapshot_info.get('NAME'),
                   'new_name': snapshot_name})

        return {'provider_location': snapshot_id}

    def manage_existing_snapshot_get_size(self, snapshot, existing_ref):
        """Get the size of the existing snapshot."""
        snapshot_info = self._get_snapshot_info_by_ref(existing_ref)
        size = int(math.ceil(snapshot_info.get('USERCAPACITY') /
                             constants.CAPACITY_UNIT))
        return size

    def unmanage_snapshot(self, snapshot):
        """Unmanage the specified snapshot from Cinder management."""
        LOG.debug("Unmanage snapshot: %s.", snapshot.id)

    def remove_host_with_check(self, host_id):
        wwns_in_host = (
            self.client.get_host_fc_initiators(host_id))
        iqns_in_host = (
            self.client.get_host_iscsi_initiators(host_id))
        if not (wwns_in_host or iqns_in_host or
           self.client.is_host_associated_to_hostgroup(host_id)):
            self.client.remove_host(host_id)

    def create_consistencygroup(self, context, group):
        """Creates a consistencygroup."""
        model_update = {'status': 'available'}
        opts = self._get_consistencygroup_type(group)
        if (opts.get('hypermetro') == 'true'):
            metro = hypermetro.HuaweiHyperMetro(self.client,
                                                self.rmt_client,
                                                self.configuration)
            metro.create_consistencygroup(group)
            return model_update

        # Array will create CG at create_cgsnapshot time. Cinder will
        # maintain the CG and volumes relationship in the db.
        return model_update

    def delete_consistencygroup(self, context, group, volumes):
        opts = self._get_consistencygroup_type(group)
        if opts.get('hypermetro') == 'true':
            metro = hypermetro.HuaweiHyperMetro(self.client,
                                                self.rmt_client,
                                                self.configuration)
            return metro.delete_consistencygroup(context, group, volumes)

        model_update = {}
        volumes_model_update = []
        model_update.update({'status': group.status})

        for volume_ref in volumes:
            try:
                self.delete_volume(volume_ref)
                volumes_model_update.append(
                    {'id': volume_ref.id, 'status': 'deleted'})
            except Exception:
                volumes_model_update.append(
                    {'id': volume_ref.id, 'status': 'error_deleting'})

        return model_update, volumes_model_update

    def update_consistencygroup(self, context, group,
                                add_volumes,
                                remove_volumes):
        model_update = {'status': 'available'}
        opts = self._get_consistencygroup_type(group)
        if opts.get('hypermetro') == 'true':
            metro = hypermetro.HuaweiHyperMetro(self.client,
                                                self.rmt_client,
                                                self.configuration)
            metro.update_consistencygroup(context, group,
                                          add_volumes,
                                          remove_volumes)
            return model_update, None, None

        # Array will create CG at create_cgsnapshot time. Cinder will
        # maintain the CG and volumes relationship in the db.
        return model_update, None, None

    def create_cgsnapshot(self, context, cgsnapshot, snapshots):
        """Create cgsnapshot."""
        LOG.info(_LI('Create cgsnapshot for consistency group'
                     ': %(group_id)s'),
                 {'group_id': cgsnapshot.consistencygroup_id})

        model_update = {}
        snapshots_model_update = []
        added_snapshots_info = []

        try:
            for snapshot in snapshots:
                volume = snapshot.volume
                if not volume:
                    msg = (_("Can't get volume id from snapshot, "
                             "snapshot: %(id)s") % {"id": snapshot.id})
                    LOG.error(msg)
                    raise exception.VolumeBackendAPIException(data=msg)

                volume_name = huawei_utils.encode_name(volume.id)

                lun_id = self.client.get_lun_id(volume, volume_name)
                snapshot_name = huawei_utils.encode_name(snapshot.id)
                snapshot_description = snapshot.id
                info = self.client.create_snapshot(lun_id,
                                                   snapshot_name,
                                                   snapshot_description)
                snapshot_model_update = {'id': snapshot.id,
                                         'status': 'available',
                                         'provider_location': info['ID']}
                snapshots_model_update.append(snapshot_model_update)
                added_snapshots_info.append(info)
        except Exception:
            with excutils.save_and_reraise_exception():
                LOG.error(_LE("Create cgsnapshots failed. "
                              "Cgsnapshot id: %s."), cgsnapshot.id)
        snapshot_ids = [added_snapshot['ID']
                        for added_snapshot in added_snapshots_info]
        try:
            self.client.activate_snapshot(snapshot_ids)
        except Exception:
            with excutils.save_and_reraise_exception():
                LOG.error(_LE("Active cgsnapshots failed. "
                              "Cgsnapshot id: %s."), cgsnapshot.id)

        model_update['status'] = 'available'

        return model_update, snapshots_model_update

    def delete_cgsnapshot(self, context, cgsnapshot, snapshots):
        """Delete consistency group snapshot."""
        LOG.info(_LI('Delete cgsnapshot %(snap_id)s for consistency group: '
                     '%(group_id)s'),
                 {'snap_id': cgsnapshot.id,
                  'group_id': cgsnapshot.consistencygroup_id})

        model_update = {}
        snapshots_model_update = []
        model_update['status'] = cgsnapshot.status

        for snapshot in snapshots:
            try:
                self.delete_snapshot(snapshot)
                snapshots_model_update.append({'id': snapshot.id,
                                               'status': 'deleted'})
            except Exception:
                with excutils.save_and_reraise_exception():
                    LOG.error(_LE("Delete cg snapshots failed. "
                                  "Cgsnapshot id: %s"), cgsnapshot.id)

        return model_update, snapshots_model_update

    def _classify_volume(self, volumes):
        normal_volumes = []
        replica_volumes = []

        for v in volumes:
            volume_type = self._get_volume_type(v)
            opts = self._get_volume_params(volume_type)
            if opts.get('replication_enabled') == 'true':
                replica_volumes.append(v)
            else:
                normal_volumes.append(v)

        return normal_volumes, replica_volumes

    def _failback_normal_volumes(self, volumes):
        volumes_update = []
        for v in volumes:
            v_update = {}
            v_update['volume_id'] = v.id
            metadata = huawei_utils.get_volume_metadata(v)
            old_status = 'available'
            if 'old_status' in metadata:
                old_status = metadata['old_status']
                del metadata['old_status']
            v_update['updates'] = {'status': old_status,
                                   'metadata': metadata}
            volumes_update.append(v_update)

        return volumes_update

    def _failback(self, volumes):
        if self.active_backend_id in ('', None):
            return 'default', []

        normal_volumes, replica_volumes = self._classify_volume(volumes)
        volumes_update = []

        replica_volumes_update = self.replica.failback(replica_volumes)
        volumes_update.extend(replica_volumes_update)

        normal_volumes_update = self._failback_normal_volumes(normal_volumes)
        volumes_update.extend(normal_volumes_update)

        self.active_backend_id = ""
        secondary_id = 'default'

        # Switch array connection.
        self.client, self.replica_client = self.replica_client, self.client
        self.replica = replication.ReplicaPairManager(self.client,
                                                      self.replica_client,
                                                      self.configuration)
        return secondary_id, volumes_update

    def _failover_normal_volumes(self, volumes):
        volumes_update = []

        for v in volumes:
            v_update = {}
            v_update['volume_id'] = v.id
            metadata = huawei_utils.get_volume_metadata(v)
            metadata.update({'old_status': v.status})
            v_update['updates'] = {'status': 'error',
                                   'metadata': metadata}
            volumes_update.append(v_update)

        return volumes_update

    def _failover(self, volumes):
        if self.active_backend_id not in ('', None):
            return self.replica_dev_conf['backend_id'], []

        normal_volumes, replica_volumes = self._classify_volume(volumes)
        volumes_update = []

        replica_volumes_update = self.replica.failover(replica_volumes)
        volumes_update.extend(replica_volumes_update)

        normal_volumes_update = self._failover_normal_volumes(normal_volumes)
        volumes_update.extend(normal_volumes_update)

        self.active_backend_id = self.replica_dev_conf['backend_id']
        secondary_id = self.active_backend_id

        # Switch array connection.
        self.client, self.replica_client = self.replica_client, self.client
        self.replica = replication.ReplicaPairManager(self.client,
                                                      self.replica_client,
                                                      self.configuration)
        return secondary_id, volumes_update

    def failover_host(self, context, volumes, secondary_id=None):
        """Failover all volumes to secondary."""
        if secondary_id == 'default':
            secondary_id, volumes_update = self._failback(volumes)
        elif (secondary_id == self.replica_dev_conf['backend_id']
                or secondary_id is None):
            secondary_id, volumes_update = self._failover(volumes)
        else:
            msg = _("Invalid secondary id %s.") % secondary_id
            LOG.error(msg)
            raise exception.VolumeBackendAPIException(data=msg)

        return secondary_id, volumes_update

    def initialize_connection_snapshot(self, snapshot, connector, **kwargs):
        """Map a snapshot to a host and return target iSCSI information."""
        # From the volume structure.
        volume = Volume(id=snapshot.id,
                        provider_location=snapshot.provider_location,
                        lun_type=constants.SNAPSHOT_TYPE,
                        metadata=None)

        return self.initialize_connection(volume, connector)

    def terminate_connection_snapshot(self, snapshot, connector, **kwargs):
        """Delete map between a snapshot and a host."""
        # From the volume structure.
        volume = Volume(id=snapshot.id,
                        provider_location=snapshot.provider_location,
                        lun_type=constants.SNAPSHOT_TYPE,
                        metadata=None)

        return self.terminate_connection(volume, connector)

    def get_lun_id_and_type(self, volume):
        if hasattr(volume, 'lun_type'):
            lun_id = volume.provider_location
            lun_type = constants.SNAPSHOT_TYPE
        else:
            lun_id = self._check_volume_exist_on_array(
                volume, constants.VOLUME_NOT_EXISTS_RAISE)
            lun_type = constants.LUN_TYPE

        return lun_id, lun_type


@interface.volumedriver
class HuaweiISCSIDriver(HuaweiBaseDriver, driver.ISCSIDriver):
    """ISCSI driver for Huawei storage arrays.

    Version history:

    .. code-block:: none

        1.0.0 - Initial driver
        1.1.0 - Provide Huawei OceanStor storage 18000 driver
        1.1.1 - Code refactor
                CHAP support
                Multiple pools support
                ISCSI multipath support
                SmartX support
                Volume migration support
                Volume retype support
        2.0.0 - Rename to HuaweiISCSIDriver
        2.0.1 - Manage/unmanage volume support
        2.0.2 - Refactor HuaweiISCSIDriver
        2.0.3 - Manage/unmanage snapshot support
        2.0.5 - Replication V2 support
        2.0.6 - Support iSCSI configuration in Replication
        2.0.7 - Hypermetro support
                Hypermetro consistency group support
                Consistency group support
                Cgsnapshot support
        2.0.8 - Backup snapshot optimal path support
        2.0.9 - Support reporting disk type of pool
    """

    VERSION = "2.0.9"

    def __init__(self, *args, **kwargs):
        super(HuaweiISCSIDriver, self).__init__(*args, **kwargs)

    def get_volume_stats(self, refresh=False):
        """Get volume status."""
        data = HuaweiBaseDriver.get_volume_stats(self, refresh=False)
        backend_name = self.configuration.safe_get('volume_backend_name')
        data['volume_backend_name'] = backend_name or self.__class__.__name__
        data['storage_protocol'] = 'iSCSI'
        data['driver_version'] = self.VERSION
        data['vendor_name'] = 'Huawei'
        return data

    @utils.synchronized('huawei', external=True)
    def initialize_connection(self, volume, connector):
        """Map a volume to a host and return target iSCSI information."""
        lun_id, lun_type = self.get_lun_id_and_type(volume)
        initiator_name = connector['initiator']
        LOG.info(_LI(
            'initiator name: %(initiator_name)s, '
            'LUN ID: %(lun_id)s.'),
            {'initiator_name': initiator_name,
             'lun_id': lun_id})

        (iscsi_iqns,
         target_ips,
         portgroup_id) = self.client.get_iscsi_params(connector)
        LOG.info(_LI('initialize_connection, iscsi_iqn: %(iscsi_iqn)s, '
                     'target_ip: %(target_ip)s, '
                     'portgroup_id: %(portgroup_id)s.'),
                 {'iscsi_iqn': iscsi_iqns,
                  'target_ip': target_ips,
                  'portgroup_id': portgroup_id},)

        # Create hostgroup if not exist.
        original_host_name = connector['host']
        host_name = huawei_utils.encode_host_name(original_host_name)
        host_id = self.client.add_host_with_check(host_name,
                                                  original_host_name)

        # Add initiator to the host.
        self.client.ensure_initiator_added(initiator_name,
                                           host_id)
        hostgroup_id = self.client.add_host_to_hostgroup(host_id)

        # Mapping lungroup and hostgroup to view.
        self.client.do_mapping(lun_id, hostgroup_id,
                               host_id, portgroup_id,
                               lun_type)

        hostlun_id = self.client.get_host_lun_id(host_id, lun_id,
                                                 lun_type)

        LOG.info(_LI("initialize_connection, host lun id is: %s."),
                 hostlun_id)

        chapinfo = self.client.find_chap_info(self.client.iscsi_info,
                                              initiator_name)

        # Return iSCSI properties.
        properties = {}
        properties['target_discovered'] = False
        properties['volume_id'] = volume.id
        multipath = connector.get('multipath', False)
        hostlun_id = int(hostlun_id)
        if not multipath:
            properties['target_portal'] = ('%s:3260' % target_ips[0])
            properties['target_iqn'] = iscsi_iqns[0]
            properties['target_lun'] = hostlun_id
        else:
            properties['target_iqns'] = [iqn for iqn in iscsi_iqns]
            properties['target_portals'] = [
                '%s:3260' % ip for ip in target_ips]
            properties['target_luns'] = [hostlun_id] * len(target_ips)

        # If use CHAP, return CHAP info.
        if chapinfo:
            chap_username, chap_password = chapinfo.split(';')
            properties['auth_method'] = 'CHAP'
            properties['auth_username'] = chap_username
            properties['auth_password'] = chap_password

        LOG.info(_LI("initialize_connection success. Return data: %s."),
                 properties)
        return {'driver_volume_type': 'iscsi', 'data': properties}

    @utils.synchronized('huawei', external=True)
    def terminate_connection(self, volume, connector, **kwargs):
        """Delete map between a volume and a host."""
        lun_id, lun_type = self.get_lun_id_and_type(volume)
        initiator_name = connector['initiator']
        host_name = connector['host']
        lungroup_id = None

        LOG.info(_LI(
            'terminate_connection: initiator name: %(ini)s, '
            'LUN ID: %(lunid)s.'),
            {'ini': initiator_name,
             'lunid': lun_id},)

        portgroup = None
        portgroup_id = None
        view_id = None
        left_lunnum = -1
        for ini in self.client.iscsi_info:
            if ini['Name'] == initiator_name:
                for key in ini:
                    if key == 'TargetPortGroup':
                        portgroup = ini['TargetPortGroup']
                        break

        if portgroup:
            portgroup_id = self.client.get_tgt_port_group(portgroup)
        host_name = huawei_utils.encode_host_name(host_name)
        host_id = self.client.get_host_id_by_name(host_name)
        if host_id:
            mapping_view_name = constants.MAPPING_VIEW_PREFIX + host_id
            view_id = self.client.find_mapping_view(mapping_view_name)
            if view_id:
                lungroup_id = self.client.find_lungroup_from_map(view_id)

        # Remove lun from lungroup.
        if lun_id and lungroup_id:
            lungroup_ids = self.client.get_lungroupids_by_lunid(
                lun_id, lun_type)
            if lungroup_id in lungroup_ids:
                self.client.remove_lun_from_lungroup(lungroup_id,
                                                     lun_id,
                                                     lun_type)
            else:
                LOG.warning(_LW("LUN is not in lungroup. "
                                "LUN ID: %(lun_id)s. "
                                "Lungroup id: %(lungroup_id)s."),
                            {"lun_id": lun_id,
                             "lungroup_id": lungroup_id})

        # Remove portgroup from mapping view if no lun left in lungroup.
        if lungroup_id:
            left_lunnum = self.client.get_obj_count_from_lungroup(lungroup_id)

        if portgroup_id and view_id and (int(left_lunnum) <= 0):
            if self.client.is_portgroup_associated_to_view(view_id,
                                                           portgroup_id):
                self.client.delete_portgroup_mapping_view(view_id,
                                                          portgroup_id)
        if view_id and (int(left_lunnum) <= 0):
            self.client.remove_chap(initiator_name)

            if self.client.lungroup_associated(view_id, lungroup_id):
                self.client.delete_lungroup_mapping_view(view_id,
                                                         lungroup_id)
            self.client.delete_lungroup(lungroup_id)
            if self.client.is_initiator_associated_to_host(initiator_name):
                self.client.remove_iscsi_from_host(initiator_name)
            hostgroup_name = constants.HOSTGROUP_PREFIX + host_id
            hostgroup_id = self.client.find_hostgroup(hostgroup_name)
            if hostgroup_id:
                if self.client.hostgroup_associated(view_id, hostgroup_id):
                    self.client.delete_hostgoup_mapping_view(view_id,
                                                             hostgroup_id)
                self.client.remove_host_from_hostgroup(hostgroup_id,
                                                       host_id)
                self.client.delete_hostgroup(hostgroup_id)
            self.client.remove_host(host_id)
            self.client.delete_mapping_view(view_id)


@interface.volumedriver
class HuaweiFCDriver(HuaweiBaseDriver, driver.FibreChannelDriver):
    """FC driver for Huawei OceanStor storage arrays.

    Version history:

    .. code-block:: none

        1.0.0 - Initial driver
        1.1.0 - Provide Huawei OceanStor 18000 storage volume driver
        1.1.1 - Code refactor
                Multiple pools support
                SmartX support
                Volume migration support
                Volume retype support
                FC zone enhancement
                Volume hypermetro support
        2.0.0 - Rename to HuaweiFCDriver
        2.0.1 - Manage/unmanage volume support
        2.0.2 - Refactor HuaweiFCDriver
        2.0.3 - Manage/unmanage snapshot support
        2.0.4 - Balanced FC port selection
        2.0.5 - Replication V2 support
        2.0.7 - Hypermetro support
                Hypermetro consistency group support
                Consistency group support
                Cgsnapshot support
        2.0.8 - Backup snapshot optimal path support
        2.0.9 - Support reporting disk type of pool
    """

    VERSION = "2.0.9"

    def __init__(self, *args, **kwargs):
        super(HuaweiFCDriver, self).__init__(*args, **kwargs)
        self.fcsan = None

    def get_volume_stats(self, refresh=False):
        """Get volume status."""
        data = HuaweiBaseDriver.get_volume_stats(self, refresh=False)
        backend_name = self.configuration.safe_get('volume_backend_name')
        data['volume_backend_name'] = backend_name or self.__class__.__name__
        data['storage_protocol'] = 'FC'
        data['driver_version'] = self.VERSION
        data['vendor_name'] = 'Huawei'
        return data

    @utils.synchronized('huawei', external=True)
    @fczm_utils.AddFCZone
    def initialize_connection(self, volume, connector):
        lun_id, lun_type = self.get_lun_id_and_type(volume)
        wwns = connector['wwpns']
        LOG.info(_LI(
            'initialize_connection, initiator: %(wwpns)s,'
            ' LUN ID: %(lun_id)s.'),
            {'wwpns': wwns,
             'lun_id': lun_id},)

        portg_id = None

        original_host_name = connector['host']
        host_name = huawei_utils.encode_host_name(original_host_name)
        host_id = self.client.add_host_with_check(host_name,
                                                  original_host_name)

        if not self.fcsan:
            self.fcsan = fczm_utils.create_lookup_service()

        if self.fcsan:
            # Use FC switch.
            zone_helper = fc_zone_helper.FCZoneHelper(self.fcsan, self.client)
            try:
                (tgt_port_wwns, portg_id, init_targ_map) = (
                    zone_helper.build_ini_targ_map(wwns, host_id, lun_id,
                                                   lun_type))
            except Exception as err:
                self.remove_host_with_check(host_id)
                msg = _('build_ini_targ_map fails. %s') % err
                raise exception.VolumeBackendAPIException(data=msg)

            for ini in init_targ_map:
                self.client.ensure_fc_initiator_added(ini, host_id)
        else:
            # Not use FC switch.
            online_wwns_in_host = (
                self.client.get_host_online_fc_initiators(host_id))
            online_free_wwns = self.client.get_online_free_wwns()
            for wwn in wwns:
                if (wwn not in online_wwns_in_host
                        and wwn not in online_free_wwns):
                    wwns_in_host = (
                        self.client.get_host_fc_initiators(host_id))
                    iqns_in_host = (
                        self.client.get_host_iscsi_initiators(host_id))
                    if not (wwns_in_host or iqns_in_host or
                       self.client.is_host_associated_to_hostgroup(host_id)):
                        self.client.remove_host(host_id)

                    msg = _('No FC initiator can be added to host.')
                    LOG.error(msg)
                    raise exception.VolumeBackendAPIException(data=msg)

            for wwn in wwns:
                if wwn in online_free_wwns:
                    self.client.add_fc_port_to_host(host_id, wwn)

            (tgt_port_wwns, init_targ_map) = (
                self.client.get_init_targ_map(wwns))

        # Add host into hostgroup.
        hostgroup_id = self.client.add_host_to_hostgroup(host_id)
        map_info = self.client.do_mapping(lun_id, hostgroup_id,
                                          host_id, portg_id,
                                          lun_type)
        host_lun_id = self.client.get_host_lun_id(host_id, lun_id,
                                                  lun_type)

        # Return FC properties.
        fc_info = {'driver_volume_type': 'fibre_channel',
                   'data': {'target_lun': int(host_lun_id),
                            'target_discovered': True,
                            'target_wwn': tgt_port_wwns,
                            'volume_id': volume.id,
                            'initiator_target_map': init_targ_map,
                            'map_info': map_info}, }

        # Deal with hypermetro connection.
        metadata = huawei_utils.get_volume_metadata(volume)
        LOG.info(_LI("initialize_connection, metadata is: %s."), metadata)
        if 'hypermetro_id' in metadata:
            loc_tgt_wwn = fc_info['data']['target_wwn']
            local_ini_tgt_map = fc_info['data']['initiator_target_map']
            hyperm = hypermetro.HuaweiHyperMetro(self.client,
                                                 self.rmt_client,
                                                 self.configuration)
            rmt_fc_info = hyperm.connect_volume_fc(volume, connector)

            rmt_tgt_wwn = rmt_fc_info['data']['target_wwn']
            rmt_ini_tgt_map = rmt_fc_info['data']['initiator_target_map']
            fc_info['data']['target_wwn'] = (loc_tgt_wwn + rmt_tgt_wwn)
            wwns = connector['wwpns']
            for wwn in wwns:
                if (wwn in local_ini_tgt_map
                        and wwn in rmt_ini_tgt_map):
                    fc_info['data']['initiator_target_map'][wwn].extend(
                        rmt_ini_tgt_map[wwn])

                elif (wwn not in local_ini_tgt_map
                        and wwn in rmt_ini_tgt_map):
                    fc_info['data']['initiator_target_map'][wwn] = (
                        rmt_ini_tgt_map[wwn])
                # else, do nothing

            loc_map_info = fc_info['data']['map_info']
            rmt_map_info = rmt_fc_info['data']['map_info']
            same_host_id = self._get_same_hostid(loc_map_info,
                                                 rmt_map_info)

            self.client.change_hostlun_id(loc_map_info, same_host_id)
            hyperm.rmt_client.change_hostlun_id(rmt_map_info, same_host_id)

            fc_info['data']['target_lun'] = same_host_id
            hyperm.rmt_client.logout()

        LOG.info(_LI("Return FC info is: %s."), fc_info)
        return fc_info

    def _get_same_hostid(self, loc_fc_info, rmt_fc_info):
        loc_aval_luns = loc_fc_info['aval_luns']
        loc_aval_luns = json.loads(loc_aval_luns)

        rmt_aval_luns = rmt_fc_info['aval_luns']
        rmt_aval_luns = json.loads(rmt_aval_luns)
        same_host_id = None

        for i in range(1, 512):
            if i in rmt_aval_luns and i in loc_aval_luns:
                same_host_id = i
                break

        LOG.info(_LI("The same hostid is: %s."), same_host_id)
        if not same_host_id:
            msg = _("Can't find the same host id from arrays.")
            LOG.error(msg)
            raise exception.VolumeBackendAPIException(data=msg)

        return same_host_id

    @utils.synchronized('huawei', external=True)
    @fczm_utils.RemoveFCZone
    def terminate_connection(self, volume, connector, **kwargs):
        """Delete map between a volume and a host."""
        lun_id, lun_type = self.get_lun_id_and_type(volume)
        wwns = connector['wwpns']

        host_name = connector['host']
        left_lunnum = -1
        lungroup_id = None
        view_id = None
        LOG.info(_LI('terminate_connection: wwpns: %(wwns)s, '
                     'LUN ID: %(lun_id)s.'),
                 {'wwns': wwns, 'lun_id': lun_id})

        host_name = huawei_utils.encode_host_name(host_name)
        host_id = self.client.get_host_id_by_name(host_name)
        if host_id:
            mapping_view_name = constants.MAPPING_VIEW_PREFIX + host_id
            view_id = self.client.find_mapping_view(mapping_view_name)
            if view_id:
                lungroup_id = self.client.find_lungroup_from_map(view_id)

        if lun_id and lungroup_id:
            lungroup_ids = self.client.get_lungroupids_by_lunid(lun_id,
                                                                lun_type)
            if lungroup_id in lungroup_ids:
                self.client.remove_lun_from_lungroup(lungroup_id,
                                                     lun_id,
                                                     lun_type)
            else:
                LOG.warning(_LW("LUN is not in lungroup. "
                                "LUN ID: %(lun_id)s. "
                                "Lungroup id: %(lungroup_id)s."),
                            {"lun_id": lun_id,
                             "lungroup_id": lungroup_id})

        else:
            LOG.warning(_LW("Can't find lun on the array."))
        if lungroup_id:
            left_lunnum = self.client.get_obj_count_from_lungroup(lungroup_id)
        if int(left_lunnum) > 0:
            fc_info = {'driver_volume_type': 'fibre_channel',
                       'data': {}}
        else:
            fc_info, portg_id = self._delete_zone_and_remove_fc_initiators(
                wwns, host_id)
            if lungroup_id:
                if view_id and self.client.lungroup_associated(
                        view_id, lungroup_id):
                    self.client.delete_lungroup_mapping_view(view_id,
                                                             lungroup_id)
                self.client.delete_lungroup(lungroup_id)
            if portg_id:
                if view_id and self.client.is_portgroup_associated_to_view(
                        view_id, portg_id):
                    self.client.delete_portgroup_mapping_view(view_id,
                                                              portg_id)
                    self.client.delete_portgroup(portg_id)

            if host_id:
                hostgroup_name = constants.HOSTGROUP_PREFIX + host_id
                hostgroup_id = self.client.find_hostgroup(hostgroup_name)
                if hostgroup_id:
                    if view_id and self.client.hostgroup_associated(
                            view_id, hostgroup_id):
                        self.client.delete_hostgoup_mapping_view(
                            view_id, hostgroup_id)
                    self.client.remove_host_from_hostgroup(
                        hostgroup_id, host_id)
                    self.client.delete_hostgroup(hostgroup_id)

                if not self.client.check_fc_initiators_exist_in_host(
                        host_id):
                    self.client.remove_host(host_id)

            if view_id:
                self.client.delete_mapping_view(view_id)

        # Deal with hypermetro connection.
        metadata = huawei_utils.get_volume_metadata(volume)
        LOG.info(_LI("Detach Volume, metadata is: %s."), metadata)

        if 'hypermetro_id' in metadata:
            hyperm = hypermetro.HuaweiHyperMetro(self.client,
                                                 self.rmt_client,
                                                 self.configuration)
            hyperm.disconnect_volume_fc(volume, connector)

        LOG.info(_LI("terminate_connection, return data is: %s."),
                 fc_info)

        return fc_info

    def _delete_zone_and_remove_fc_initiators(self, wwns, host_id):
        # Get tgt_port_wwns and init_targ_map to remove zone.
        portg_id = None
        if not self.fcsan:
            self.fcsan = fczm_utils.create_lookup_service()
        if self.fcsan:
            zone_helper = fc_zone_helper.FCZoneHelper(self.fcsan,
                                                      self.client)
            (tgt_port_wwns, portg_id, init_targ_map) = (
                zone_helper.get_init_targ_map(wwns, host_id))
        else:
            (tgt_port_wwns, init_targ_map) = (
                self.client.get_init_targ_map(wwns))

        # Remove the initiators from host if need.
        if host_id:
            fc_initiators = self.client.get_host_fc_initiators(host_id)
            for wwn in wwns:
                if wwn in fc_initiators:
                    self.client.remove_fc_from_host(wwn)

        info = {'driver_volume_type': 'fibre_channel',
                'data': {'target_wwn': tgt_port_wwns,
                         'initiator_target_map': init_targ_map}}
        return info, portg_id
