# Copyright (c) 2014 Hitachi Data Systems, Inc.
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

"""
Volume driver for HNAS NFS storage.
"""

import math
import os
import socket

from oslo_concurrency import processutils
from oslo_config import cfg
from oslo_log import log as logging
from oslo_utils import units
import six

from cinder import exception
from cinder.i18n import _, _LE, _LI
from cinder.image import image_utils
from cinder import interface
from cinder import utils as cutils
from cinder.volume.drivers.hitachi import hnas_backend
from cinder.volume.drivers.hitachi import hnas_utils
from cinder.volume.drivers import nfs
from cinder.volume import utils


HNAS_NFS_VERSION = '5.0.0'

LOG = logging.getLogger(__name__)

NFS_OPTS = [
    cfg.StrOpt('hds_hnas_nfs_config_file',
               default='/opt/hds/hnas/cinder_nfs_conf.xml',
               help='Legacy configuration file for HNAS NFS Cinder plugin. '
                    'This is not needed if you fill all configuration on '
                    'cinder.conf',
               deprecated_for_removal=True)
]

CONF = cfg.CONF
CONF.register_opts(NFS_OPTS)

HNAS_DEFAULT_CONFIG = {'ssc_cmd': 'ssc', 'ssh_port': '22'}


@interface.volumedriver
class HNASNFSDriver(nfs.NfsDriver):
    """Base class for Hitachi NFS driver.

    Executes commands relating to Volumes.

    Version history:

    ..  code-block:: none

        Version 1.0.0: Initial driver version
        Version 2.2.0: Added support to SSH authentication
        Version 3.0.0: Added pool aware scheduling
        Version 4.0.0: Added manage/unmanage features
        Version 4.1.0: Fixed XML parser checks on blank options
        Version 5.0.0: Remove looping in driver initialization
                       Code cleaning up
                       New communication interface between the driver and HNAS
                       Removed the option to use local SSC (ssh_enabled=False)
                       Updated to use versioned objects
                       Changed the class name to HNASNFSDriver
                       Deprecated XML config file
                       Added support to manage/unmanage snapshots features
                       Fixed driver stats reporting
    """
    # ThirdPartySystems wiki page
    CI_WIKI_NAME = "Hitachi_HNAS_CI"
    VERSION = HNAS_NFS_VERSION

    def __init__(self, *args, **kwargs):
        self._execute = None
        self.context = None
        self.configuration = kwargs.get('configuration', None)

        service_parameters = ['volume_type', 'hdp']
        optional_parameters = ['ssc_cmd', 'cluster_admin_ip0']

        if self.configuration:
            self.configuration.append_config_values(
                hnas_utils.drivers_common_opts)
            self.configuration.append_config_values(NFS_OPTS)
            self.config = {}

            # Trying to get HNAS configuration from cinder.conf
            self.config = hnas_utils.read_cinder_conf(
                self.configuration, 'nfs')

            # If HNAS configuration are not set on cinder.conf, tries to use
            # the deprecated XML configuration file
            if not self.config:
                self.config = hnas_utils.read_xml_config(
                    self.configuration.hds_hnas_nfs_config_file,
                    service_parameters,
                    optional_parameters)

        super(HNASNFSDriver, self).__init__(*args, **kwargs)
        self.backend = hnas_backend.HNASSSHBackend(self.config)

    def _get_service(self, volume):
        """Get service parameters.

        Get the available service parameters for a given volume using
        its type.

        :param volume: dictionary volume reference
        :returns: Tuple containing the service parameters (label,
        export path and export file system) or error if no configuration is
        found.
        :raises: ParameterNotFound
        """
        LOG.debug("_get_service: volume: %(vol)s", {'vol': volume})
        label = utils.extract_host(volume.host, level='pool')

        if label in self.config['services'].keys():
            svc = self.config['services'][label]
            LOG.debug("_get_service: %(lbl)s->%(svc)s",
                      {'lbl': label, 'svc': svc['export']['fs']})
            service = (svc['hdp'], svc['export']['path'], svc['export']['fs'])
        else:
            LOG.info(_LI("Available services: %(svc)s"),
                     {'svc': self.config['services'].keys()})
            LOG.error(_LE("No configuration found for service: %(lbl)s"),
                      {'lbl': label})
            raise exception.ParameterNotFound(param=label)

        return service

    @cutils.trace
    def extend_volume(self, volume, new_size):
        """Extend an existing volume.

        :param volume: dictionary volume reference
        :param new_size: int size in GB to extend
        :raises: InvalidResults
        """
        nfs_mount = volume.provider_location
        path = self._get_volume_path(nfs_mount, volume.name)

        # Resize the image file on share to new size.
        LOG.info(_LI("Checking file for resize."))

        if not self._is_file_size_equal(path, new_size):
            LOG.info(_LI("Resizing file to %(sz)sG"), {'sz': new_size})
            image_utils.resize_image(path, new_size)

        if self._is_file_size_equal(path, new_size):
            LOG.info(_LI("LUN %(id)s extended to %(size)s GB."),
                     {'id': volume.id, 'size': new_size})
        else:
            msg = _("Resizing image file failed.")
            LOG.error(msg)
            raise exception.InvalidResults(msg)

    def _is_file_size_equal(self, path, size):
        """Checks if file size at path is equal to size."""
        data = image_utils.qemu_img_info(path)
        virt_size = data.virtual_size / units.Gi

        if virt_size == size:
            return True
        else:
            return False

    @cutils.trace
    def create_volume_from_snapshot(self, volume, snapshot):
        """Creates a volume from a snapshot.

        :param volume: volume to be created
        :param snapshot: source snapshot
        :returns: the provider_location of the volume created
        """
        self._clone_volume(snapshot.volume, volume.name, snapshot.name)
        share = snapshot.volume.provider_location

        return {'provider_location': share}

    @cutils.trace
    def create_snapshot(self, snapshot):
        """Create a snapshot.

        :param snapshot: dictionary snapshot reference
        :returns: the provider_location of the snapshot created
        """
        self._clone_volume(snapshot.volume, snapshot.name)

        share = snapshot.volume.provider_location
        LOG.debug('Share: %(shr)s', {'shr': share})

        # returns the mount point (not path)
        return {'provider_location': share}

    @cutils.trace
    def delete_snapshot(self, snapshot):
        """Deletes a snapshot.

        :param snapshot: dictionary snapshot reference
        """
        nfs_mount = snapshot.volume.provider_location

        if self._volume_not_present(nfs_mount, snapshot.name):
            return True

        self._execute('rm', self._get_volume_path(nfs_mount, snapshot.name),
                      run_as_root=True)

    def _volume_not_present(self, nfs_mount, volume_name):
        """Check if volume does not exist.

        :param nfs_mount: string path of the nfs share
        :param volume_name: string volume name
        :returns: boolean (true for volume not present and false otherwise)
        """
        try:
            self._try_execute('ls',
                              self._get_volume_path(nfs_mount, volume_name))
        except processutils.ProcessExecutionError:
            # If the volume isn't present
            return True

        return False

    def _get_volume_path(self, nfs_share, volume_name):
        """Get volume path (local fs path) for given name on given nfs share.

        :param nfs_share string, example 172.18.194.100:/var/nfs
        :param volume_name string,
        example volume-91ee65ec-c473-4391-8c09-162b00c68a8c
        :returns: the local path according to the parameters
        """
        return os.path.join(self._get_mount_point_for_share(nfs_share),
                            volume_name)

    @cutils.trace
    def create_cloned_volume(self, volume, src_vref):
        """Creates a clone of the specified volume.

        :param volume: reference to the volume being created
        :param src_vref: reference to the source volume
        :returns: the provider_location of the cloned volume
        """
        vol_size = volume.size
        src_vol_size = src_vref.size

        self._clone_volume(src_vref, volume.name, src_vref.name)

        share = src_vref.provider_location

        if vol_size > src_vol_size:
            volume.provider_location = share
            self.extend_volume(volume, vol_size)

        return {'provider_location': share}

    def get_volume_stats(self, refresh=False):
        """Get volume stats.

        :param refresh: if it is True, update the stats first.
        :returns: dictionary with the stats from HNAS
        _stats['pools'] = {
            'total_capacity_gb': total size of the pool,
            'free_capacity_gb': the available size,
            'QoS_support': bool to indicate if QoS is supported,
            'reserved_percentage': percentage of size reserved,
            'max_over_subscription_ratio': oversubscription rate,
            'thin_provisioning_support': thin support (True),
            }
        """
        LOG.info(_LI("Getting volume stats"))

        _stats = super(HNASNFSDriver, self).get_volume_stats(refresh)
        _stats["vendor_name"] = 'Hitachi'
        _stats["driver_version"] = HNAS_NFS_VERSION
        _stats["storage_protocol"] = 'NFS'

        max_osr = self.max_over_subscription_ratio

        for pool in self.pools:
            capacity, free, provisioned = self._get_capacity_info(pool['fs'])
            pool['total_capacity_gb'] = capacity / float(units.Gi)
            pool['free_capacity_gb'] = free / float(units.Gi)
            pool['provisioned_capacity_gb'] = provisioned / float(units.Gi)
            pool['QoS_support'] = 'False'
            pool['reserved_percentage'] = self.reserved_percentage
            pool['max_over_subscription_ratio'] = max_osr
            pool['thin_provisioning_support'] = True

        _stats['pools'] = self.pools

        LOG.debug('Driver stats: %(stat)s', {'stat': _stats})

        return _stats

    def do_setup(self, context):
        """Perform internal driver setup."""
        version_info = self.backend.get_version()
        LOG.info(_LI("HNAS NFS driver."))
        LOG.info(_LI("HNAS model: %(mdl)s"), {'mdl': version_info['model']})
        LOG.info(_LI("HNAS version: %(ver)s"),
                 {'ver': version_info['version']})
        LOG.info(_LI("HNAS hardware: %(hw)s"),
                 {'hw': version_info['hardware']})
        LOG.info(_LI("HNAS S/N: %(sn)s"), {'sn': version_info['serial']})

        self.context = context
        self._load_shares_config(
            getattr(self.configuration, self.driver_prefix + '_shares_config'))
        LOG.info(_LI("Review shares: %(shr)s"), {'shr': self.shares})

        elist = self.backend.get_export_list()

        # Check for all configured exports
        for svc_name, svc_info in self.config['services'].items():
            server_ip = svc_info['hdp'].split(':')[0]
            mountpoint = svc_info['hdp'].split(':')[1]

            # Ensure export are configured in HNAS
            export_configured = False
            for export in elist:
                if mountpoint == export['name'] and server_ip in export['evs']:
                    svc_info['export'] = export
                    export_configured = True

            # Ensure export are reachable
            try:
                out, err = self._execute('showmount', '-e', server_ip)
            except processutils.ProcessExecutionError:
                LOG.exception(_LE("NFS server %(srv)s not reachable!"),
                              {'srv': server_ip})
                raise

            export_list = out.split('\n')[1:]
            export_list.pop()
            mountpoint_not_found = mountpoint not in map(
                lambda x: x.split()[0], export_list)
            if (len(export_list) < 1 or
                    mountpoint_not_found or
                    not export_configured):
                LOG.error(_LE("Configured share %(share)s is not present"
                              "in %(srv)s."),
                          {'share': mountpoint, 'srv': server_ip})
                msg = _('Section: %(svc_name)s') % {'svc_name': svc_name}
                raise exception.InvalidParameterValue(err=msg)

        LOG.debug("Loading services: %(svc)s", {
            'svc': self.config['services']})

        service_list = self.config['services'].keys()
        for svc in service_list:
            svc = self.config['services'][svc]
            pool = {}
            pool['pool_name'] = svc['volume_type']
            pool['service_label'] = svc['volume_type']
            pool['fs'] = svc['hdp']

            self.pools.append(pool)

        LOG.debug("Configured pools: %(pool)s", {'pool': self.pools})
        LOG.info(_LI("HNAS NFS Driver loaded successfully."))

    def _clone_volume(self, src_vol, clone_name, src_name=None):
        """Clones mounted volume using the HNAS file_clone.

        :param src_vol: object source volume
        :param clone_name: string clone name (or snapshot)
        :param src_name: name of the source volume.
        """

        # when the source is a snapshot, we need to pass the source name and
        # use the information of the volume that originated the snapshot to
        # get the clone path.
        if not src_name:
            src_name = src_vol.name

        # volume-ID snapshot-ID, /cinder
        LOG.info(_LI("Cloning with volume_name %(vname)s, clone_name %(cname)s"
                     " ,export_path %(epath)s"),
                 {'vname': src_name, 'cname': clone_name,
                     'epath': src_vol.provider_location})

        (fs, path, fs_label) = self._get_service(src_vol)

        target_path = '%s/%s' % (path, clone_name)
        source_path = '%s/%s' % (path, src_name)

        self.backend.file_clone(fs_label, source_path, target_path)

    @cutils.trace
    def create_volume(self, volume):
        """Creates a volume.

        :param volume: volume reference
        :returns: the volume provider_location
        """
        self._ensure_shares_mounted()

        (fs_id, path, fslabel) = self._get_service(volume)

        volume.provider_location = fs_id

        LOG.info(_LI("Volume service: %(label)s. Casted to: %(loc)s"),
                 {'label': fslabel, 'loc': volume.provider_location})

        self._do_create_volume(volume)

        return {'provider_location': fs_id}

    def _convert_vol_ref_share_name_to_share_ip(self, vol_ref):
        """Converts the share point name to an IP address.

        The volume reference may have a DNS name portion in the share name.
        Convert that to an IP address and then restore the entire path.

        :param vol_ref: driver-specific information used to identify a volume
        :returns: a volume reference where share is in IP format or raises
         error
         :raises: e.strerror
        """

        # First strip out share and convert to IP format.
        share_split = vol_ref.split(':')

        try:
            vol_ref_share_ip = cutils.resolve_hostname(share_split[0])
        except socket.gaierror as e:
            LOG.exception(_LE('Invalid hostname %(host)s'),
                          {'host': share_split[0]})
            LOG.debug('error: %(err)s', {'err': e.strerror})
            raise

        # Now place back into volume reference.
        vol_ref_share = vol_ref_share_ip + ':' + share_split[1]

        return vol_ref_share

    def _get_share_mount_and_vol_from_vol_ref(self, vol_ref):
        """Get the NFS share, the NFS mount, and the volume from reference.

        Determine the NFS share point, the NFS mount point, and the volume
        (with possible path) from the given volume reference. Raise exception
        if unsuccessful.

        :param vol_ref: driver-specific information used to identify a volume
        :returns: NFS Share, NFS mount, volume path or raise error
        :raises: ManageExistingInvalidReference
        """
        # Check that the reference is valid.
        if 'source-name' not in vol_ref:
            reason = _('Reference must contain source-name element.')
            raise exception.ManageExistingInvalidReference(
                existing_ref=vol_ref, reason=reason)
        vol_ref_name = vol_ref['source-name']

        self._ensure_shares_mounted()

        # If a share was declared as '1.2.3.4:/a/b/c' in the nfs_shares_config
        # file, but the admin tries to manage the file located at
        # 'my.hostname.com:/a/b/c/d.vol', this might cause a lookup miss below
        # when searching self._mounted_shares to see if we have an existing
        # mount that would work to access the volume-to-be-managed (a string
        # comparison is done instead of IP comparison).
        vol_ref_share = self._convert_vol_ref_share_name_to_share_ip(
            vol_ref_name)
        for nfs_share in self._mounted_shares:
            cfg_share = self._convert_vol_ref_share_name_to_share_ip(nfs_share)
            (orig_share, work_share,
             file_path) = vol_ref_share.partition(cfg_share)
            if work_share == cfg_share:
                file_path = file_path[1:]  # strip off leading path divider
                LOG.debug("Found possible share %(shr)s; checking mount.",
                          {'shr': work_share})
                nfs_mount = self._get_mount_point_for_share(nfs_share)
                vol_full_path = os.path.join(nfs_mount, file_path)
                if os.path.isfile(vol_full_path):
                    LOG.debug("Found share %(share)s and vol %(path)s on "
                              "mount %(mnt)s.",
                              {'share': nfs_share, 'path': file_path,
                               'mnt': nfs_mount})
                    return nfs_share, nfs_mount, file_path
            else:
                LOG.debug("vol_ref %(ref)s not on share %(share)s.",
                          {'ref': vol_ref_share, 'share': nfs_share})

        raise exception.ManageExistingInvalidReference(
            existing_ref=vol_ref,
            reason=_('Volume/Snapshot not found on configured storage '
                     'backend.'))

    @cutils.trace
    def manage_existing(self, volume, existing_vol_ref):
        """Manages an existing volume.

        The specified Cinder volume is to be taken into Cinder management.
        The driver will verify its existence and then rename it to the
        new Cinder volume name. It is expected that the existing volume
        reference is an NFS share point and some [/path]/volume;
        e.g., 10.10.32.1:/openstack/vol_to_manage
        or 10.10.32.1:/openstack/some_directory/vol_to_manage

        :param volume: cinder volume to manage
        :param existing_vol_ref: driver-specific information used to identify a
        volume
        :returns: the provider location
        :raises: VolumeBackendAPIException
        """

        # Attempt to find NFS share, NFS mount, and volume path from vol_ref.
        (nfs_share, nfs_mount, vol_name
         ) = self._get_share_mount_and_vol_from_vol_ref(existing_vol_ref)

        LOG.info(_LI("Asked to manage NFS volume %(vol)s, "
                     "with vol ref %(ref)s."),
                 {'vol': volume.id,
                  'ref': existing_vol_ref['source-name']})

        self._check_pool_and_share(volume, nfs_share)

        if vol_name == volume.name:
            LOG.debug("New Cinder volume %(vol)s name matches reference name: "
                      "no need to rename.", {'vol': volume.name})
        else:
            src_vol = os.path.join(nfs_mount, vol_name)
            dst_vol = os.path.join(nfs_mount, volume.name)
            try:
                self._try_execute("mv", src_vol, dst_vol, run_as_root=False,
                                  check_exit_code=True)
                LOG.debug("Setting newly managed Cinder volume name "
                          "to %(vol)s.", {'vol': volume.name})
                self._set_rw_permissions_for_all(dst_vol)
            except (OSError, processutils.ProcessExecutionError) as err:
                msg = (_("Failed to manage existing volume "
                         "%(name)s, because rename operation "
                         "failed: Error msg: %(msg)s.") %
                       {'name': existing_vol_ref['source-name'],
                        'msg': six.text_type(err)})
                LOG.error(msg)
                raise exception.VolumeBackendAPIException(data=msg)
        return {'provider_location': nfs_share}

    def _check_pool_and_share(self, volume, nfs_share):
        """Validates the pool and the NFS share.

        Checks if the NFS share for the volume-type chosen matches the
        one passed in the volume reference. Also, checks if the pool
        for the volume type matches the pool for the host passed.

        :param volume: cinder volume reference
        :param nfs_share: NFS share passed to manage
        :raises: ManageExistingVolumeTypeMismatch
        """
        pool_from_vol_type = hnas_utils.get_pool(self.config, volume)

        pool_from_host = utils.extract_host(volume.host, level='pool')
        pool = self.config['services'][pool_from_vol_type]['hdp']
        if pool != nfs_share:
            msg = (_("Failed to manage existing volume because the pool of "
                     "the volume type chosen (%(pool)s) does not match the "
                     "NFS share passed in the volume reference (%(share)s).")
                   % {'share': nfs_share, 'pool': pool})
            LOG.error(msg)
            raise exception.ManageExistingVolumeTypeMismatch(reason=msg)

        if pool_from_host != pool_from_vol_type:
            msg = (_("Failed to manage existing volume because the pool of "
                     "the volume type chosen (%(pool)s) does not match the "
                     "pool of the host %(pool_host)s") %
                   {'pool': pool_from_vol_type,
                    'pool_host': pool_from_host})
            LOG.error(msg)
            raise exception.ManageExistingVolumeTypeMismatch(reason=msg)

    @cutils.trace
    def manage_existing_get_size(self, volume, existing_vol_ref):
        """Returns the size of volume to be managed by manage_existing.

        When calculating the size, round up to the next GB.

        :param volume: cinder volume to manage
        :param existing_vol_ref: existing volume to take under management
        :returns: the size of the volume or raise error
        :raises: VolumeBackendAPIException
        """
        return self._manage_existing_get_size(existing_vol_ref)

    @cutils.trace
    def unmanage(self, volume):
        """Removes the specified volume from Cinder management.

        It does not delete the underlying backend storage object. A log entry
        will be made to notify the Admin that the volume is no longer being
        managed.

        :param volume: cinder volume to unmanage
        """
        vol_str = CONF.volume_name_template % volume.id
        path = self._get_mount_point_for_share(volume.provider_location)

        new_str = "unmanage-" + vol_str

        vol_path = os.path.join(path, vol_str)
        new_path = os.path.join(path, new_str)

        try:
            self._try_execute("mv", vol_path, new_path,
                              run_as_root=False, check_exit_code=True)

            LOG.info(_LI("The volume with path %(old)s is no longer being "
                         "managed by Cinder. However, it was not deleted "
                         "and can be found in the new path %(cr)s."),
                     {'old': vol_path, 'cr': new_path})

        except (OSError, ValueError):
            LOG.exception(_LE("The NFS Volume %(cr)s does not exist."),
                          {'cr': new_path})

    def _manage_existing_get_size(self, existing_ref):
        # Attempt to find NFS share, NFS mount, and path from vol_ref.
        (nfs_share, nfs_mount, path
         ) = self._get_share_mount_and_vol_from_vol_ref(existing_ref)

        try:
            LOG.debug("Asked to get size of NFS ref %(ref)s.",
                      {'ref': existing_ref['source-name']})

            file_path = os.path.join(nfs_mount, path)
            file_size = float(cutils.get_file_size(file_path)) / units.Gi
            # Round up to next Gb
            size = int(math.ceil(file_size))
        except (OSError, ValueError):
            exception_message = (_("Failed to manage existing volume/snapshot "
                                   "%(name)s, because of error in getting "
                                   "its size."),
                                 {'name': existing_ref['source-name']})
            LOG.exception(exception_message)
            raise exception.VolumeBackendAPIException(data=exception_message)

        LOG.debug("Reporting size of NFS ref %(ref)s as %(size)d GB.",
                  {'ref': existing_ref['source-name'], 'size': size})

        return size

    def _check_snapshot_parent(self, volume, old_snap_name, share):

        volume_name = 'volume-' + volume.id
        (fs, path, fs_label) = self._get_service(volume)
        # 172.24.49.34:/nfs_cinder

        export_path = self.backend.get_export_path(share.split(':')[1],
                                                   fs_label)
        volume_path = os.path.join(export_path, volume_name)

        return self.backend.check_snapshot_parent(volume_path, old_snap_name,
                                                  fs_label)

    def manage_existing_snapshot(self, snapshot, existing_ref):
        # Attempt to find NFS share, NFS mount, and volume path from ref.
        (nfs_share, nfs_mount, src_snapshot_name
         ) = self._get_share_mount_and_vol_from_vol_ref(existing_ref)

        LOG.info(_LI("Asked to manage NFS snapshot %(snap)s for volume "
                     "%(vol)s, with vol ref %(ref)s."),
                 {'snap': snapshot.id,
                  'vol': snapshot.volume_id,
                  'ref': existing_ref['source-name']})

        volume = snapshot.volume

        # Check if the snapshot belongs to the volume
        real_parent = self._check_snapshot_parent(volume, src_snapshot_name,
                                                  nfs_share)

        if not real_parent:
            msg = (_("This snapshot %(snap)s doesn't belong "
                     "to the volume parent %(vol)s.") %
                   {'snap': snapshot.id, 'vol': volume.id})
            raise exception.ManageExistingInvalidReference(
                existing_ref=existing_ref, reason=msg)

        if src_snapshot_name == snapshot.name:
            LOG.debug("New Cinder snapshot %(snap)s name matches reference "
                      "name. No need to rename.", {'snap': snapshot.name})
        else:
            src_snap = os.path.join(nfs_mount, src_snapshot_name)
            dst_snap = os.path.join(nfs_mount, snapshot.name)
            try:
                self._try_execute("mv", src_snap, dst_snap, run_as_root=False,
                                  check_exit_code=True)
                LOG.info(_LI("Setting newly managed Cinder snapshot name "
                         "to %(snap)s."), {'snap': snapshot.name})
                self._set_rw_permissions_for_all(dst_snap)
            except (OSError, processutils.ProcessExecutionError) as err:
                msg = (_("Failed to manage existing snapshot "
                         "%(name)s, because rename operation "
                         "failed: Error msg: %(msg)s.") %
                       {'name': existing_ref['source-name'],
                        'msg': six.text_type(err)})
                LOG.error(msg)
                raise exception.VolumeBackendAPIException(data=msg)
        return {'provider_location': nfs_share}

    def manage_existing_snapshot_get_size(self, snapshot, existing_ref):
        return self._manage_existing_get_size(existing_ref)

    def unmanage_snapshot(self, snapshot):
        path = self._get_mount_point_for_share(snapshot.provider_location)

        new_name = "unmanage-" + snapshot.name

        old_path = os.path.join(path, snapshot.name)
        new_path = os.path.join(path, new_name)

        try:
            self._execute("mv", old_path, new_path,
                          run_as_root=False, check_exit_code=True)
            LOG.info(_LI("The snapshot with path %(old)s is no longer being "
                         "managed by Cinder. However, it was not deleted and "
                         "can be found in the new path %(cr)s."),
                     {'old': old_path, 'cr': new_path})

        except (OSError, ValueError):
            LOG.exception(_LE("The NFS snapshot %(old)s does not exist."),
                          {'old': old_path})
