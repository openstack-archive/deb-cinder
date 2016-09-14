# Copyright (c) 2015 Parallels IP Holdings GmbH
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
import errno
import json
import os
import re

from os_brick.remotefs import remotefs
from oslo_concurrency import processutils as putils
from oslo_config import cfg
from oslo_log import log as logging
from oslo_utils import imageutils
from oslo_utils import units

from cinder import exception
from cinder.i18n import _, _LI
from cinder.image import image_utils
from cinder import interface
from cinder import utils
from cinder.volume.drivers import remotefs as remotefs_drv

VERSION = '1.0'

LOG = logging.getLogger(__name__)

vzstorage_opts = [
    cfg.StrOpt('vzstorage_shares_config',
               default='/etc/cinder/vzstorage_shares',
               help='File with the list of available vzstorage shares.'),
    cfg.BoolOpt('vzstorage_sparsed_volumes',
                default=True,
                help=('Create volumes as sparsed files which take no space '
                      'rather than regular files when using raw format, '
                      'in which case volume creation takes lot of time.')),
    cfg.FloatOpt('vzstorage_used_ratio',
                 default=0.95,
                 help=('Percent of ACTUAL usage of the underlying volume '
                       'before no new volumes can be allocated to the volume '
                       'destination.')),
    cfg.StrOpt('vzstorage_mount_point_base',
               default='$state_path/mnt',
               help=('Base dir containing mount points for '
                     'vzstorage shares.')),
    cfg.ListOpt('vzstorage_mount_options',
                help=('Mount options passed to the vzstorage client. '
                      'See section of the pstorage-mount man page '
                      'for details.')),
    cfg.StrOpt('vzstorage_default_volume_format',
               default='raw',
               help=('Default format that will be used when creating volumes '
                     'if no volume format is specified.')),
]

CONF = cfg.CONF
CONF.register_opts(vzstorage_opts)

PLOOP_BASE_DELTA_NAME = 'root.hds'
DISK_FORMAT_RAW = 'raw'
DISK_FORMAT_QCOW2 = 'qcow2'
DISK_FORMAT_PLOOP = 'parallels'


class PloopDevice(object):
    """Setup a ploop device for parallels image

    This class is for mounting ploop devices using with statement:
    with PloopDevice('/parallels/my-vm/harddisk.hdd') as dev_path:
        # do something

    :param path: A path to parallels harddisk dir
    :param snapshot_id: Snapshot id to mount
    :param execute: execute helper
    """

    def __init__(self, path, snapshot_id=None, read_only=True,
                 execute=putils.execute):
        self.path = path
        self.snapshot_id = snapshot_id
        self.read_only = read_only
        self.execute = execute

    def __enter__(self):
        self.dd_path = os.path.join(self.path, 'DiskDescriptor.xml')
        cmd = ['ploop', 'mount', self.dd_path]

        if self.snapshot_id:
            cmd.append('-u')
            cmd.append(self.snapshot_id)

        if self.read_only:
            cmd.append('-r')

        out, err = self.execute(*cmd, run_as_root=True)

        m = re.search('dev=(\S+)', out)
        if not m:
            raise Exception('Invalid output from ploop mount: %s' % out)

        self.ploop_dev = m.group(1)

        return self.ploop_dev

    def _umount(self):
        self.execute('ploop', 'umount', self.dd_path, run_as_root=True)

    def __exit__(self, type, value, traceback):
        self._umount()


@interface.volumedriver
class VZStorageDriver(remotefs_drv.RemoteFSSnapDriver):
    """Cinder driver for Virtuozzo Storage.

    Creates volumes as files on the mounted vzstorage cluster.

    Version history:
        1.0     - Initial driver.
    """
    driver_volume_type = 'vzstorage'
    driver_prefix = 'vzstorage'
    volume_backend_name = 'Virtuozzo_Storage'
    VERSION = VERSION
    # ThirdPartySystems wiki page
    CI_WIKI_NAME = "Virtuozzo_Storage_CI"

    SHARE_FORMAT_REGEX = r'(?:(\S+):\/)?([a-zA-Z0-9_-]+)(?::(\S+))?'

    def __init__(self, execute=putils.execute, *args, **kwargs):
        self._remotefsclient = None
        super(VZStorageDriver, self).__init__(*args, **kwargs)
        self.configuration.append_config_values(vzstorage_opts)
        self._execute_as_root = False
        root_helper = utils.get_root_helper()
        # base bound to instance is used in RemoteFsConnector.
        self.base = getattr(self.configuration,
                            'vzstorage_mount_point_base',
                            CONF.vzstorage_mount_point_base)
        opts = getattr(self.configuration,
                       'vzstorage_mount_options',
                       CONF.vzstorage_mount_options)

        self._remotefsclient = remotefs.RemoteFsClient(
            'vzstorage', root_helper, execute=execute,
            vzstorage_mount_point_base=self.base,
            vzstorage_mount_options=opts)

    def _qemu_img_info(self, path, volume_name):
        qemu_img_cache = path + ".qemu_img_info"
        if os.path.isdir(path):
            # 'parallels' disks stored along with metadata xml as directories
            # qemu-img should explore base data file inside
            path = os.path.join(path, PLOOP_BASE_DELTA_NAME)
        if os.path.isfile(qemu_img_cache):
            info_tm = os.stat(qemu_img_cache).st_mtime
            snap_tm = os.stat(path).st_mtime
        ret = None
        if not os.path.isfile(qemu_img_cache) or snap_tm > info_tm:
            LOG.debug("Cached qemu-img info %s not present or outdated,"
                      " refresh", qemu_img_cache)
            ret = super(VZStorageDriver, self)._qemu_img_info_base(
                path, volume_name,
                self.configuration.vzstorage_mount_point_base)
            # We need only backing_file and file_format
            d = {'file_format': ret.file_format,
                 'backing_file': ret.backing_file}
            with open(qemu_img_cache, "w") as f:
                json.dump(d, f)
        else:
            ret = imageutils.QemuImgInfo()
            with open(qemu_img_cache, "r") as f:
                ret.__dict__ = json.load(f)
        return ret

    @remotefs_drv.locked_volume_id_operation
    def initialize_connection(self, volume, connector):
        """Allow connection to connector and return connection info.

        :param volume: volume reference
        :param connector: connector reference
        """
        # Find active image
        active_file = self.get_active_image_from_info(volume)

        data = {'export': volume.provider_location,
                'format': self.get_volume_format(volume),
                'name': active_file,
                }

        return {
            'driver_volume_type': self.driver_volume_type,
            'data': data,
            'mount_point_base': self._get_mount_point_base(),
        }

    def do_setup(self, context):
        """Any initialization the volume driver does while starting."""
        super(VZStorageDriver, self).do_setup(context)

        config = self.configuration.vzstorage_shares_config
        if not os.path.exists(config):
            msg = (_("VzStorage config file at %(config)s doesn't exist.") %
                   {'config': config})
            LOG.error(msg)
            raise exception.VzStorageException(msg)

        if not os.path.isabs(self.base):
            msg = _("Invalid mount point base: %s.") % self.base
            LOG.error(msg)
            raise exception.VzStorageException(msg)

        used_ratio = self.configuration.vzstorage_used_ratio
        if not ((used_ratio > 0) and (used_ratio <= 1)):
            msg = _("VzStorage config 'vzstorage_used_ratio' invalid. "
                    "Must be > 0 and <= 1.0: %s.") % used_ratio
            LOG.error(msg)
            raise exception.VzStorageException(msg)

        self.shares = {}

        # Check if mount.fuse.pstorage is installed on this system;
        # note that we don't need to be root to see if the package
        # is installed.
        package = 'mount.fuse.pstorage'
        try:
            self._execute(package, check_exit_code=False,
                          run_as_root=False)
        except OSError as exc:
            if exc.errno == errno.ENOENT:
                msg = _('%s is not installed.') % package
                raise exception.VzStorageException(msg)
            else:
                raise

        self.configuration.nas_secure_file_operations = 'true'
        self.configuration.nas_secure_file_permissions = 'true'

    def _ensure_share_mounted(self, share):
        m = re.search(self.SHARE_FORMAT_REGEX, share)
        if not m:
            msg = (_("Invalid Virtuozzo Storage share specification: %r. "
                     "Must be: [MDS1[,MDS2],...:/]<CLUSTER NAME>[:PASSWORD].")
                   % share)
            raise exception.VzStorageException(msg)
        cluster_name = m.group(2)

        # set up logging to non-default path, so that it will
        # be possible to mount the same cluster to another mount
        # point by hand with default options.
        mnt_flags = ['-l', '/var/log/pstorage/%s-cinder.log.gz' % cluster_name]
        if self.shares.get(share) is not None:
            extra_flags = json.loads(self.shares[share])
            mnt_flags.extend(extra_flags)
        self._remotefsclient.mount(share, mnt_flags)

    def _find_share(self, volume_size_in_gib):
        """Choose VzStorage share among available ones for given volume size.

        For instances with more than one share that meets the criteria, the
        first suitable share will be selected.

        :param volume_size_in_gib: int size in GB
        """

        if not self._mounted_shares:
            raise exception.VzStorageNoSharesMounted()

        for share in self._mounted_shares:
            if self._is_share_eligible(share, volume_size_in_gib):
                break
        else:
            raise exception.VzStorageNoSuitableShareFound(
                volume_size=volume_size_in_gib)

        LOG.debug('Selected %s as target VzStorage share.', share)

        return share

    def _is_share_eligible(self, vz_share, volume_size_in_gib):
        """Verifies VzStorage share is eligible to host volume with given size.

        :param vz_share: vzstorage share
        :param volume_size_in_gib: int size in GB
        """

        used_ratio = self.configuration.vzstorage_used_ratio
        volume_size = volume_size_in_gib * units.Gi

        total_size, available, allocated = self._get_capacity_info(vz_share)

        if (allocated + volume_size) // total_size > used_ratio:
            LOG.debug('_is_share_eligible: %s is above '
                      'vzstorage_used_ratio.', vz_share)
            return False

        return True

    def choose_volume_format(self, volume):
        vol_type = volume.volume_type
        if vol_type:
            extra_specs = vol_type.extra_specs or {}
        else:
            extra_specs = {}

        extra_specs.update(volume.metadata or {})

        return (extra_specs.get('volume_format') or
                self.configuration.vzstorage_default_volume_format)

    def get_volume_format(self, volume):
        active_file = self.get_active_image_from_info(volume)
        active_file_path = os.path.join(self._local_volume_dir(volume),
                                        active_file)

        img_info = self._qemu_img_info(active_file_path, volume.name)
        return img_info.file_format

    def _create_ploop(self, volume_path, volume_size):
        os.mkdir(volume_path)
        try:
            self._execute('ploop', 'init', '-s', '%sG' % volume_size,
                          os.path.join(volume_path, PLOOP_BASE_DELTA_NAME),
                          run_as_root=True)
        except putils.ProcessExecutionError:
            os.rmdir(volume_path)
            raise

    def _do_create_volume(self, volume):
        """Create a volume on given vzstorage share.

        :param volume: volume reference
        """
        volume_format = self.choose_volume_format(volume)
        volume_path = self.local_path(volume)
        volume_size = volume.size

        LOG.debug("Creating new volume at %s.", volume_path)

        if os.path.exists(volume_path):
            msg = _('File already exists at %s.') % volume_path
            LOG.error(msg)
            raise exception.InvalidVolume(reason=msg)

        if volume_format == 'parallels':
            self._create_ploop(volume_path, volume_size)
        elif volume_format == 'qcow2':
            self._create_qcow2_file(volume_path, volume_size)
        elif self.configuration.vzstorage_sparsed_volumes:
            self._create_sparsed_file(volume_path, volume_size)
        else:
            self._create_regular_file(volume_path, volume_size)

        info_path = self._local_path_volume_info(volume)
        snap_info = {'active': os.path.basename(volume_path)}
        self._write_info_file(info_path, snap_info)

        # Query qemu-img info to cache the output
        self._qemu_img_info(volume_path, volume.name)

    def _delete(self, path):
        self._execute('rm', '-rf', path, run_as_root=True)

    @remotefs_drv.locked_volume_id_operation
    def extend_volume(self, volume, size_gb):
        LOG.info(_LI('Extending volume %s.'), volume.id)
        volume_format = self.get_volume_format(volume)
        self._extend_volume(volume, size_gb, volume_format)

    def _extend_volume(self, volume, size_gb, volume_format):
        volume_path = self.local_path(volume)

        self._check_extend_volume_support(volume, size_gb)
        LOG.info(_LI('Resizing file to %sG...'), size_gb)

        self._do_extend_volume(volume_path, size_gb, volume_format)

    def _do_extend_volume(self, volume_path, size_gb, volume_format):
        if volume_format == "parallels":
            self._execute('ploop', 'grow', '-s',
                          '%dG' % size_gb,
                          os.path.join(volume_path, 'DiskDescriptor.xml'),
                          run_as_root=True)
            volume_path = os.path.join(volume_path, PLOOP_BASE_DELTA_NAME)
        else:
            image_utils.resize_image(volume_path, size_gb)
            if not self._is_file_size_equal(volume_path, size_gb):
                raise exception.ExtendVolumeError(
                    reason='Resizing image file failed.')

    def _check_extend_volume_support(self, volume, size_gb):
        volume_path = self.local_path(volume)
        active_file = self.get_active_image_from_info(volume)
        active_file_path = os.path.join(self._local_volume_dir(volume),
                                        active_file)

        if active_file_path != volume_path:
            msg = _('Extend volume is only supported for this '
                    'driver when no snapshots exist.')
            raise exception.InvalidVolume(msg)

        extend_by = int(size_gb) - volume.size
        if not self._is_share_eligible(volume.provider_location,
                                       extend_by):
            raise exception.ExtendVolumeError(reason='Insufficient space to '
                                              'extend volume %s to %sG.'
                                              % (volume.id, size_gb))

    def _is_file_size_equal(self, path, size):
        """Checks if file size at path is equal to size."""
        data = image_utils.qemu_img_info(path)
        virt_size = data.virtual_size / units.Gi
        return virt_size == size

    def _recreate_ploop_desc(self, image_dir, image_file):
        self._delete(os.path.join(image_dir, 'DiskDescriptor.xml'))

        self._execute('ploop', 'restore-descriptor', image_dir, image_file)

    def copy_image_to_volume(self, context, volume, image_service, image_id):
        """Fetch the image from image_service and write it to the volume."""
        volume_format = self.get_volume_format(volume)
        image_path = self.local_path(volume)
        if volume_format == DISK_FORMAT_PLOOP:
            image_path = os.path.join(image_path, PLOOP_BASE_DELTA_NAME)

        image_utils.fetch_to_volume_format(
            context, image_service, image_id,
            image_path, volume_format,
            self.configuration.volume_dd_blocksize)

        if volume_format == DISK_FORMAT_PLOOP:
            self._recreate_ploop_desc(self.local_path(volume), image_path)

        self._do_extend_volume(self.local_path(volume),
                               volume.size,
                               volume_format)
        # Query qemu-img info to cache the output
        self._qemu_img_info(self.local_path(volume), volume.name)

    def _copy_volume_from_snapshot(self, snapshot, volume, volume_size):
        """Copy data from snapshot to destination volume.

        This is done with a qemu-img convert to raw/qcow2 from the snapshot
        qcow2.
        """

        info_path = self._local_path_volume_info(snapshot.volume)
        snap_info = self._read_info_file(info_path)
        vol_dir = self._local_volume_dir(snapshot.volume)
        out_format = self.choose_volume_format(volume)
        volume_format = self.get_volume_format(snapshot.volume)
        volume_path = self.local_path(volume)

        if volume_format in (DISK_FORMAT_QCOW2, DISK_FORMAT_RAW):
            forward_file = snap_info[snapshot.id]
            forward_path = os.path.join(vol_dir, forward_file)

            # Find the file which backs this file, which represents the point
            # when this snapshot was created.
            img_info = self._qemu_img_info(forward_path,
                                           snapshot.volume.name)
            path_to_snap_img = os.path.join(vol_dir, img_info.backing_file)

            LOG.debug("_copy_volume_from_snapshot: will copy "
                      "from snapshot at %s.", path_to_snap_img)

            image_utils.convert_image(path_to_snap_img,
                                      volume_path,
                                      out_format)
        elif volume_format == DISK_FORMAT_PLOOP:
            with PloopDevice(self.local_path(snapshot.volume),
                             snapshot.id,
                             execute=self._execute) as dev:
                base_file = os.path.join(volume_path, 'root.hds')
                image_utils.convert_image(dev, base_file, out_format)
        else:
            msg = _("Unsupported volume format %s") % volume_format
            raise exception.InvalidVolume(msg)

        self._extend_volume(volume, volume_size, out_format)
        # Query qemu-img info to cache the output
        img_info = self._qemu_img_info(volume_path, volume.name)

    @remotefs_drv.locked_volume_id_operation
    def delete_volume(self, volume):
        """Deletes a logical volume."""
        if not volume.provider_location:
            msg = (_('Volume %s does not have provider_location '
                     'specified, skipping.') % volume.name)
            LOG.error(msg)
            return

        self._ensure_share_mounted(volume.provider_location)
        volume_dir = self._local_volume_dir(volume)
        mounted_path = os.path.join(volume_dir,
                                    self.get_active_image_from_info(volume))
        if os.path.exists(mounted_path):
            self._delete(mounted_path)
            self._delete(mounted_path + ".qemu_img_info")
        else:
            LOG.info(_LI("Skipping deletion of volume %s "
                         "as it does not exist."), mounted_path)

        info_path = self._local_path_volume_info(volume)
        self._delete(info_path)

    def _get_desc_path(self, volume):
        return os.path.join(self.local_path(volume), 'DiskDescriptor.xml')

    def _create_snapshot_ploop(self, snapshot):
        status = snapshot.volume.status
        if status != 'available':
            msg = (_('Volume status must be available for '
                     'snapshot %(id)s. (is %(status)s)') %
                   {'id': snapshot.id, 'status': status})
            raise exception.InvalidVolume(msg)

        info_path = self._local_path_volume_info(snapshot.volume)
        snap_info = self._read_info_file(info_path)
        self._execute('ploop', 'snapshot', '-u', '{%s}' % snapshot.id,
                      self._get_desc_path(snapshot.volume),
                      run_as_root=True)
        snap_file = os.path.join('volume-%s' % snapshot.volume.id, snapshot.id)
        snap_info[snapshot.id] = snap_file
        self._write_info_file(info_path, snap_info)

    def _delete_snapshot_ploop(self, snapshot):
        status = snapshot.volume.status
        if status != 'available':
            msg = (_('Volume status must be available for '
                     'snapshot %(id)s. (is %(status)s)') %
                   {'id': snapshot.id, 'status': status})
            raise exception.InvalidVolume(msg)

        info_path = self._local_path_volume_info(snapshot.volume)
        snap_info = self._read_info_file(info_path)
        self._execute('ploop', 'snapshot-delete', '-u', '{%s}' % snapshot.id,
                      self._get_desc_path(snapshot.volume),
                      run_as_root=True)
        snap_info.pop(snapshot.id)
        self._write_info_file(info_path, snap_info)

    def create_snapshot(self, snapshot):
        volume_format = self.get_volume_format(snapshot.volume)
        if volume_format == 'parallels':
            self._create_snapshot_ploop(snapshot)
        else:
            super(VZStorageDriver, self)._create_snapshot(snapshot)

    def _do_create_snapshot(self, snapshot, backing_filename,
                            new_snap_path):
        super(VZStorageDriver, self)._do_create_snapshot(snapshot,
                                                         backing_filename,
                                                         new_snap_path)
        # Cache qemu-img info for created snapshot
        self._qemu_img_info(new_snap_path, snapshot.volume.name)

    def delete_snapshot(self, snapshot):
        info_path = self._local_path_volume_info(snapshot.volume)
        snap_info = self._read_info_file(info_path, empty_if_missing=True)
        snap_file = os.path.join(self._local_volume_dir(snapshot.volume),
                                 snap_info[snapshot.id])
        active_file = os.path.join(self._local_volume_dir(snapshot.volume),
                                   snap_info['active'])
        higher_file = self._get_higher_image_path(snapshot)
        if higher_file:
            higher_file = os.path.join(self._local_volume_dir(snapshot.volume),
                                       higher_file)
        elif active_file != snap_file:
            msg = (_("Expected higher file exists for snapshot %s") %
                   snapshot.id)
            raise exception.VzStorageException(msg)

        img_info = self._qemu_img_info(snap_file, snapshot.volume.name)
        base_file = os.path.join(self._local_volume_dir(snapshot.volume),
                                 img_info.backing_file)
        volume_format = self.get_volume_format(snapshot.volume)
        online = snapshot.volume.status == 'in-use'

        if volume_format == 'parallels':
            self._delete_snapshot_ploop(snapshot)
        else:
            super(VZStorageDriver, self)._delete_snapshot(snapshot)

        def _qemu_info_cache(fn):
            return fn + ".qemu_img_info"

        def _update_backing_file(info_src, info_dst):
            with open(info_src, 'r') as fs, open(info_dst, 'r') as fd:
                src = json.load(fs)
                dst = json.load(fd)
            dst['backing_file'] = src['backing_file']
            with open(info_dst, 'w') as fdw:
                json.dump(dst, fdw)

        if volume_format == "qcow2":
            if snap_file != active_file:
                # mv snap_file.info higher_file.info
                _update_backing_file(
                    _qemu_info_cache(snap_file),
                    _qemu_info_cache(higher_file))
                self._delete(_qemu_info_cache(snap_file))
            elif online:
                # mv base_file.info snap_file.info
                _update_backing_file(
                    _qemu_info_cache(base_file),
                    _qemu_info_cache(snap_file))
                self._delete(_qemu_info_cache(base_file))
            else:
                # rm snap_file.info
                self._delete(_qemu_info_cache(snap_file))

    def _copy_volume_to_image(self, context, volume, image_service,
                              image_meta):
        """Copy the volume to the specified image."""

        volume_format = self.get_volume_format(volume)
        if volume_format == DISK_FORMAT_PLOOP:
            with PloopDevice(self.local_path(volume),
                             execute=self._execute) as dev:
                image_utils.upload_volume(context,
                                          image_service,
                                          image_meta,
                                          dev,
                                          volume_format='raw')
        else:
            super(VZStorageDriver, self)._copy_volume_to_image(context, volume,
                                                               image_service,
                                                               image_meta)

    def _create_cloned_volume(self, volume, src_vref):
        LOG.info(_LI('Cloning volume %(src)s to volume %(dst)s'),
                 {'src': src_vref.id,
                  'dst': volume.id})

        if src_vref.status != 'available':
            msg = _("Volume status must be 'available'.")
            raise exception.InvalidVolume(msg)

        volume_name = CONF.volume_name_template % volume.id

        # Create fake snapshot object
        snap_attrs = ['volume_name', 'size', 'volume_size', 'name',
                      'volume_id', 'id', 'volume']
        Snapshot = collections.namedtuple('Snapshot', snap_attrs)

        temp_snapshot = Snapshot(id=src_vref.id,
                                 volume_name=volume_name,
                                 size=src_vref.size,
                                 volume_size=src_vref.size,
                                 name='clone-snap-%s' % src_vref.id,
                                 volume_id=src_vref.id,
                                 volume=volume)

        self._create_snapshot_ploop(temp_snapshot)
        try:
            volume.provider_location = src_vref.provider_location
            info_path = self._local_path_volume_info(volume)
            snap_info = {'active': 'volume-%s' % volume.id}
            self._write_info_file(info_path, snap_info)
            self._copy_volume_from_snapshot(temp_snapshot,
                                            volume,
                                            volume.size)

        finally:
            self.delete_snapshot(temp_snapshot)

        return {'provider_location': src_vref.provider_location}

    @remotefs_drv.locked_volume_id_operation
    def create_cloned_volume(self, volume, src_vref):
        """Creates a clone of the specified volume."""
        volume_format = self.get_volume_format(src_vref)
        if volume_format == 'parallels':
            self._create_cloned_volume(volume, src_vref)
        else:
            super(VZStorageDriver, self)._create_cloned_volume(volume,
                                                               src_vref)
