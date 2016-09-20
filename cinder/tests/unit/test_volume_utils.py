# Copyright 2011 OpenStack Foundation
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

"""Tests For miscellaneous util methods used with volume."""


import datetime
import io
import mock
import six

from oslo_concurrency import processutils
from oslo_config import cfg
from oslo_utils import units

from cinder import context
from cinder import db
from cinder.db.sqlalchemy import models
from cinder import exception
from cinder import keymgr
from cinder.objects import fields
from cinder import test
from cinder.tests.unit.backup import fake_backup
from cinder.tests.unit import fake_constants as fake
from cinder.tests.unit import fake_snapshot
from cinder.tests.unit import fake_volume
from cinder import utils
from cinder.volume import throttling
from cinder.volume import utils as volume_utils
from cinder.volume import volume_types


CONF = cfg.CONF


class NotifyUsageTestCase(test.TestCase):
    @mock.patch('cinder.volume.utils._usage_from_volume')
    @mock.patch('cinder.volume.utils.CONF')
    @mock.patch('cinder.volume.utils.rpc')
    def test_notify_about_volume_usage(self, mock_rpc, mock_conf, mock_usage):
        mock_conf.host = 'host1'
        output = volume_utils.notify_about_volume_usage(mock.sentinel.context,
                                                        mock.sentinel.volume,
                                                        'test_suffix')
        self.assertIsNone(output)
        mock_usage.assert_called_once_with(mock.sentinel.context,
                                           mock.sentinel.volume)
        mock_rpc.get_notifier.assert_called_once_with('volume', 'host1')
        mock_rpc.get_notifier.return_value.info.assert_called_once_with(
            mock.sentinel.context,
            'volume.test_suffix',
            mock_usage.return_value)

    @mock.patch('cinder.volume.utils._usage_from_volume')
    @mock.patch('cinder.volume.utils.CONF')
    @mock.patch('cinder.volume.utils.rpc')
    def test_notify_about_volume_usage_with_kwargs(self, mock_rpc, mock_conf,
                                                   mock_usage):
        mock_conf.host = 'host1'
        output = volume_utils.notify_about_volume_usage(
            mock.sentinel.context,
            mock.sentinel.volume,
            'test_suffix',
            extra_usage_info={'a': 'b', 'c': 'd'},
            host='host2')
        self.assertIsNone(output)
        mock_usage.assert_called_once_with(mock.sentinel.context,
                                           mock.sentinel.volume, a='b', c='d')
        mock_rpc.get_notifier.assert_called_once_with('volume', 'host2')
        mock_rpc.get_notifier.return_value.info.assert_called_once_with(
            mock.sentinel.context,
            'volume.test_suffix',
            mock_usage.return_value)

    @mock.patch('cinder.volume.utils._usage_from_snapshot')
    @mock.patch('cinder.volume.utils.CONF')
    @mock.patch('cinder.volume.utils.rpc')
    def test_notify_about_snapshot_usage(self, mock_rpc,
                                         mock_conf, mock_usage):
        mock_conf.host = 'host1'
        output = volume_utils.notify_about_snapshot_usage(
            mock.sentinel.context,
            mock.sentinel.snapshot,
            'test_suffix')
        self.assertIsNone(output)
        mock_usage.assert_called_once_with(mock.sentinel.snapshot)
        mock_rpc.get_notifier.assert_called_once_with('snapshot', 'host1')
        mock_rpc.get_notifier.return_value.info.assert_called_once_with(
            mock.sentinel.context,
            'snapshot.test_suffix',
            mock_usage.return_value)

    @mock.patch('cinder.volume.utils._usage_from_snapshot')
    @mock.patch('cinder.volume.utils.CONF')
    @mock.patch('cinder.volume.utils.rpc')
    def test_notify_about_snapshot_usage_with_kwargs(self, mock_rpc, mock_conf,
                                                     mock_usage):
        mock_conf.host = 'host1'
        output = volume_utils.notify_about_snapshot_usage(
            mock.sentinel.context,
            mock.sentinel.snapshot,
            'test_suffix',
            extra_usage_info={'a': 'b', 'c': 'd'},
            host='host2')
        self.assertIsNone(output)
        mock_usage.assert_called_once_with(mock.sentinel.snapshot,
                                           a='b', c='d')
        mock_rpc.get_notifier.assert_called_once_with('snapshot', 'host2')
        mock_rpc.get_notifier.return_value.info.assert_called_once_with(
            mock.sentinel.context,
            'snapshot.test_suffix',
            mock_usage.return_value)

    @mock.patch('cinder.objects.Volume.get_by_id')
    def test_usage_from_snapshot(self, volume_get_by_id):
        raw_volume = {
            'id': fake.VOLUME_ID,
            'availability_zone': 'nova'
        }
        ctxt = context.get_admin_context()
        volume_obj = fake_volume.fake_volume_obj(ctxt, **raw_volume)
        volume_get_by_id.return_value = volume_obj
        raw_snapshot = {
            'project_id': fake.PROJECT_ID,
            'user_id': fake.USER_ID,
            'volume': volume_obj,
            'volume_id': fake.VOLUME_ID,
            'volume_size': 1,
            'id': fake.SNAPSHOT_ID,
            'display_name': '11',
            'created_at': '2014-12-11T10:10:00',
            'status': fields.SnapshotStatus.ERROR,
            'deleted': '',
            'snapshot_metadata': [{'key': 'fake_snap_meta_key',
                                   'value': 'fake_snap_meta_value'}],
            'expected_attrs': ['metadata'],
        }

        snapshot_obj = fake_snapshot.fake_snapshot_obj(ctxt, **raw_snapshot)
        usage_info = volume_utils._usage_from_snapshot(snapshot_obj)
        expected_snapshot = {
            'tenant_id': fake.PROJECT_ID,
            'user_id': fake.USER_ID,
            'availability_zone': 'nova',
            'volume_id': fake.VOLUME_ID,
            'volume_size': 1,
            'snapshot_id': fake.SNAPSHOT_ID,
            'display_name': '11',
            'created_at': 'DONTCARE',
            'status': fields.SnapshotStatus.ERROR,
            'deleted': '',
            'metadata': six.text_type({'fake_snap_meta_key':
                                      u'fake_snap_meta_value'}),
        }
        self.assertDictMatch(expected_snapshot, usage_info)

    @mock.patch('cinder.db.volume_glance_metadata_get')
    @mock.patch('cinder.db.volume_attachment_get_all_by_volume_id')
    def test_usage_from_volume(self, mock_attachment, mock_image_metadata):
        mock_image_metadata.return_value = {'image_id': 'fake_image_id'}
        mock_attachment.return_value = [{'instance_uuid': 'fake_instance_id'}]
        raw_volume = {
            'project_id': '12b0330ec2584a',
            'user_id': '158cba1b8c2bb6008e',
            'host': 'fake_host',
            'availability_zone': 'nova',
            'volume_type_id': 'fake_volume_type_id',
            'id': 'fake_volume_id',
            'size': 1,
            'display_name': 'test_volume',
            'created_at': datetime.datetime(2015, 1, 1, 1, 1, 1),
            'launched_at': datetime.datetime(2015, 1, 1, 1, 1, 1),
            'snapshot_id': None,
            'replication_status': None,
            'replication_extended_status': None,
            'replication_driver_data': None,
            'status': 'available',
            'volume_metadata': {'fake_metadata_key': 'fake_metadata_value'},
        }
        usage_info = volume_utils._usage_from_volume(
            mock.sentinel.context,
            raw_volume)
        expected_volume = {
            'tenant_id': '12b0330ec2584a',
            'user_id': '158cba1b8c2bb6008e',
            'host': 'fake_host',
            'availability_zone': 'nova',
            'volume_type': 'fake_volume_type_id',
            'volume_id': 'fake_volume_id',
            'size': 1,
            'display_name': 'test_volume',
            'created_at': '2015-01-01T01:01:01',
            'launched_at': '2015-01-01T01:01:01',
            'snapshot_id': None,
            'replication_status': None,
            'replication_extended_status': None,
            'replication_driver_data': None,
            'status': 'available',
            'metadata': {'fake_metadata_key': 'fake_metadata_value'},
            'glance_metadata': {'image_id': 'fake_image_id'},
            'volume_attachment': [{'instance_uuid': 'fake_instance_id'}],
        }
        self.assertEqual(expected_volume, usage_info)

    @mock.patch('cinder.volume.utils._usage_from_consistencygroup')
    @mock.patch('cinder.volume.utils.CONF')
    @mock.patch('cinder.volume.utils.rpc')
    def test_notify_about_consistencygroup_usage(self, mock_rpc,
                                                 mock_conf, mock_usage):
        mock_conf.host = 'host1'
        output = volume_utils.notify_about_consistencygroup_usage(
            mock.sentinel.context,
            mock.sentinel.consistencygroup,
            'test_suffix')
        self.assertIsNone(output)
        mock_usage.assert_called_once_with(mock.sentinel.consistencygroup)
        mock_rpc.get_notifier.assert_called_once_with('consistencygroup',
                                                      'host1')
        mock_rpc.get_notifier.return_value.info.assert_called_once_with(
            mock.sentinel.context,
            'consistencygroup.test_suffix',
            mock_usage.return_value)

    @mock.patch('cinder.volume.utils._usage_from_consistencygroup')
    @mock.patch('cinder.volume.utils.CONF')
    @mock.patch('cinder.volume.utils.rpc')
    def test_notify_about_consistencygroup_usage_with_kwargs(self, mock_rpc,
                                                             mock_conf,
                                                             mock_usage):
        mock_conf.host = 'host1'
        output = volume_utils.notify_about_consistencygroup_usage(
            mock.sentinel.context,
            mock.sentinel.consistencygroup,
            'test_suffix',
            extra_usage_info={'a': 'b', 'c': 'd'},
            host='host2')
        self.assertIsNone(output)
        mock_usage.assert_called_once_with(mock.sentinel.consistencygroup,
                                           a='b', c='d')
        mock_rpc.get_notifier.assert_called_once_with('consistencygroup',
                                                      'host2')
        mock_rpc.get_notifier.return_value.info.assert_called_once_with(
            mock.sentinel.context,
            'consistencygroup.test_suffix',
            mock_usage.return_value)

    @mock.patch('cinder.volume.utils._usage_from_cgsnapshot')
    @mock.patch('cinder.volume.utils.CONF')
    @mock.patch('cinder.volume.utils.rpc')
    def test_notify_about_cgsnapshot_usage(self, mock_rpc,
                                           mock_conf, mock_usage):
        mock_conf.host = 'host1'
        output = volume_utils.notify_about_cgsnapshot_usage(
            mock.sentinel.context,
            mock.sentinel.cgsnapshot,
            'test_suffix')
        self.assertIsNone(output)
        mock_usage.assert_called_once_with(mock.sentinel.cgsnapshot)
        mock_rpc.get_notifier.assert_called_once_with('cgsnapshot', 'host1')
        mock_rpc.get_notifier.return_value.info.assert_called_once_with(
            mock.sentinel.context,
            'cgsnapshot.test_suffix',
            mock_usage.return_value)

    @mock.patch('cinder.volume.utils._usage_from_cgsnapshot')
    @mock.patch('cinder.volume.utils.CONF')
    @mock.patch('cinder.volume.utils.rpc')
    def test_notify_about_cgsnapshot_usage_with_kwargs(self, mock_rpc,
                                                       mock_conf, mock_usage):
        mock_conf.host = 'host1'
        output = volume_utils.notify_about_cgsnapshot_usage(
            mock.sentinel.context,
            mock.sentinel.cgsnapshot,
            'test_suffix',
            extra_usage_info={'a': 'b', 'c': 'd'},
            host='host2')
        self.assertIsNone(output)
        mock_usage.assert_called_once_with(mock.sentinel.cgsnapshot,
                                           a='b', c='d')
        mock_rpc.get_notifier.assert_called_once_with('cgsnapshot', 'host2')
        mock_rpc.get_notifier.return_value.info.assert_called_once_with(
            mock.sentinel.context,
            'cgsnapshot.test_suffix',
            mock_usage.return_value)

    def test_usage_from_backup(self):
        raw_backup = {
            'project_id': fake.PROJECT_ID,
            'user_id': fake.USER_ID,
            'availability_zone': 'nova',
            'id': fake.BACKUP_ID,
            'host': 'fake_host',
            'display_name': 'test_backup',
            'created_at': datetime.datetime(2015, 1, 1, 1, 1, 1),
            'status': 'available',
            'volume_id': fake.VOLUME_ID,
            'size': 1,
            'service_metadata': None,
            'service': 'cinder.backup.drivers.swift',
            'fail_reason': None,
            'parent_id': fake.BACKUP2_ID,
            'num_dependent_backups': 0,
            'snapshot_id': None,
        }

        ctxt = context.get_admin_context()
        backup_obj = fake_backup.fake_backup_obj(ctxt, **raw_backup)

        # Make it easier to find out differences between raw and expected.
        expected_backup = raw_backup.copy()
        expected_backup['tenant_id'] = expected_backup.pop('project_id')
        expected_backup['backup_id'] = expected_backup.pop('id')
        expected_backup['created_at'] = (
            six.text_type(expected_backup['created_at']) + '+00:00')

        usage_info = volume_utils._usage_from_backup(backup_obj)
        self.assertDictMatch(expected_backup, usage_info)


class LVMVolumeDriverTestCase(test.TestCase):
    def test_convert_blocksize_option(self):
        # Test valid volume_dd_blocksize
        bs = volume_utils._check_blocksize('10M')
        self.assertEqual('10M', bs)

        bs = volume_utils._check_blocksize('1xBBB')
        self.assertEqual('1M', bs)

        # Test 'volume_dd_blocksize' with fraction
        bs = volume_utils._check_blocksize('1.3M')
        self.assertEqual('1M', bs)

        # Test zero-size 'volume_dd_blocksize'
        bs = volume_utils._check_blocksize('0M')
        self.assertEqual('1M', bs)

        # Test negative 'volume_dd_blocksize'
        bs = volume_utils._check_blocksize('-1M')
        self.assertEqual('1M', bs)

        # Test non-digital 'volume_dd_blocksize'
        bs = volume_utils._check_blocksize('ABM')
        self.assertEqual('1M', bs)


class OdirectSupportTestCase(test.TestCase):
    @mock.patch('cinder.utils.execute')
    def test_check_for_odirect_support(self, mock_exec):
        output = volume_utils.check_for_odirect_support('/dev/abc', '/dev/def')
        self.assertTrue(output)
        mock_exec.assert_called_once_with('dd', 'count=0', 'if=/dev/abc',
                                          'of=/dev/def', 'oflag=direct',
                                          run_as_root=True)
        mock_exec.reset_mock()

        output = volume_utils.check_for_odirect_support('/dev/abc', '/dev/def',
                                                        'iflag=direct')
        self.assertTrue(output)
        mock_exec.assert_called_once_with('dd', 'count=0', 'if=/dev/abc',
                                          'of=/dev/def', 'iflag=direct',
                                          run_as_root=True)
        mock_exec.reset_mock()

        output = volume_utils.check_for_odirect_support('/dev/zero',
                                                        '/dev/def',
                                                        'iflag=direct')
        self.assertFalse(output)
        mock_exec.reset_mock()

        output = volume_utils.check_for_odirect_support('/dev/zero',
                                                        '/dev/def')
        self.assertTrue(output)
        mock_exec.assert_called_once_with('dd', 'count=0', 'if=/dev/zero',
                                          'of=/dev/def', 'oflag=direct',
                                          run_as_root=True)

    @mock.patch('cinder.utils.execute',
                side_effect=processutils.ProcessExecutionError)
    def test_check_for_odirect_support_error(self, mock_exec):
        output = volume_utils.check_for_odirect_support('/dev/abc', '/dev/def')
        self.assertFalse(output)
        mock_exec.assert_called_once_with('dd', 'count=0', 'if=/dev/abc',
                                          'of=/dev/def', 'oflag=direct',
                                          run_as_root=True)
        mock_exec.reset_mock()
        output = volume_utils.check_for_odirect_support('/dev/zero',
                                                        '/dev/def')
        self.assertFalse(output)
        mock_exec.assert_called_once_with('dd', 'count=0', 'if=/dev/zero',
                                          'of=/dev/def', 'oflag=direct',
                                          run_as_root=True)


class ClearVolumeTestCase(test.TestCase):
    @mock.patch('cinder.volume.utils.copy_volume', return_value=None)
    @mock.patch('cinder.volume.utils.CONF')
    def test_clear_volume_conf(self, mock_conf, mock_copy):
        mock_conf.volume_clear = 'zero'
        mock_conf.volume_clear_size = 0
        mock_conf.volume_dd_blocksize = '1M'
        mock_conf.volume_clear_ionice = '-c3'
        output = volume_utils.clear_volume(1024, 'volume_path')
        self.assertIsNone(output)
        mock_copy.assert_called_once_with('/dev/zero', 'volume_path', 1024,
                                          '1M', sync=True,
                                          execute=utils.execute, ionice='-c3',
                                          throttle=None, sparse=False)

    @mock.patch('cinder.volume.utils.copy_volume', return_value=None)
    @mock.patch('cinder.volume.utils.CONF')
    def test_clear_volume_args(self, mock_conf, mock_copy):
        mock_conf.volume_clear = 'shred'
        mock_conf.volume_clear_size = 0
        mock_conf.volume_dd_blocksize = '1M'
        mock_conf.volume_clear_ionice = '-c3'
        output = volume_utils.clear_volume(1024, 'volume_path', 'zero', 1,
                                           '-c0')
        self.assertIsNone(output)
        mock_copy.assert_called_once_with('/dev/zero', 'volume_path', 1,
                                          '1M', sync=True,
                                          execute=utils.execute, ionice='-c0',
                                          throttle=None, sparse=False)

    @mock.patch('cinder.utils.execute')
    @mock.patch('cinder.volume.utils.CONF')
    def test_clear_volume_shred(self, mock_conf, mock_exec):
        # 'shred' now uses 'dd'.  Remove this test when
        # support for 'volume_clear=shred' is removed.
        mock_conf.volume_clear = 'shred'
        mock_conf.volume_clear_size = 1
        mock_conf.volume_clear_ionice = None
        mock_conf.volume_dd_blocksize = '1M'
        output = volume_utils.clear_volume(1024, 'volume_path')
        self.assertIsNone(output)
        mock_exec.assert_called_with(
            'dd', 'if=/dev/zero', 'of=volume_path', 'count=1048576', 'bs=1M',
            'iflag=count_bytes', 'oflag=direct', run_as_root=True)

    @mock.patch('cinder.utils.execute')
    @mock.patch('cinder.volume.utils.CONF')
    def test_clear_volume_shred_not_clear_size(self, mock_conf, mock_exec):
        # 'shred' now uses 'dd'.  Remove this test when
        # support for 'volume_clear=shred' is removed.
        mock_conf.volume_clear = 'shred'
        mock_conf.volume_clear_size = None
        mock_conf.volume_clear_ionice = None
        mock_conf.volume_dd_blocksize = '1M'
        mock_conf.volume_clear_size = 1
        output = volume_utils.clear_volume(1024, 'volume_path')
        self.assertIsNone(output)
        mock_exec.assert_called_with(
            'dd', 'if=/dev/zero', 'of=volume_path', 'count=1048576', 'bs=1M',
            'iflag=count_bytes', 'oflag=direct', run_as_root=True)

    @mock.patch('cinder.volume.utils.CONF')
    def test_clear_volume_invalid_opt(self, mock_conf):
        mock_conf.volume_clear = 'non_existent_volume_clearer'
        mock_conf.volume_clear_size = 0
        mock_conf.volume_clear_ionice = None
        self.assertRaises(exception.InvalidConfigurationValue,
                          volume_utils.clear_volume,
                          1024, "volume_path")


class CopyVolumeTestCase(test.TestCase):
    @mock.patch('cinder.volume.utils.check_for_odirect_support',
                return_value=True)
    @mock.patch('cinder.utils.execute')
    @mock.patch('cinder.volume.utils.CONF')
    def test_copy_volume_dd_iflag_and_oflag(self, mock_conf, mock_exec,
                                            mock_support):
        fake_throttle = throttling.Throttle(['fake_throttle'])
        output = volume_utils.copy_volume('/dev/zero', '/dev/null', 1024, '3M',
                                          sync=True, execute=utils.execute,
                                          ionice=None, throttle=fake_throttle)
        self.assertIsNone(output)
        mock_exec.assert_called_once_with('fake_throttle', 'dd',
                                          'if=/dev/zero',
                                          'of=/dev/null',
                                          'count=%s' % units.Gi,
                                          'bs=3M', 'iflag=count_bytes,direct',
                                          'oflag=direct', run_as_root=True)

        mock_exec.reset_mock()

        output = volume_utils.copy_volume('/dev/zero', '/dev/null', 1024, '3M',
                                          sync=False, execute=utils.execute,
                                          ionice=None, throttle=fake_throttle)
        self.assertIsNone(output)
        mock_exec.assert_called_once_with('fake_throttle', 'dd',
                                          'if=/dev/zero',
                                          'of=/dev/null',
                                          'count=%s' % units.Gi,
                                          'bs=3M', 'iflag=count_bytes,direct',
                                          'oflag=direct', run_as_root=True)

    @mock.patch('cinder.volume.utils.check_for_odirect_support',
                return_value=False)
    @mock.patch('cinder.utils.execute')
    def test_copy_volume_dd_no_iflag_or_oflag(self, mock_exec, mock_support):
        fake_throttle = throttling.Throttle(['fake_throttle'])
        output = volume_utils.copy_volume('/dev/zero', '/dev/null', 1024, '3M',
                                          sync=True, execute=utils.execute,
                                          ionice=None, throttle=fake_throttle)
        self.assertIsNone(output)
        mock_exec.assert_called_once_with('fake_throttle', 'dd',
                                          'if=/dev/zero',
                                          'of=/dev/null',
                                          'count=%s' % units.Gi,
                                          'bs=3M', 'iflag=count_bytes',
                                          'conv=fdatasync', run_as_root=True)

        mock_exec.reset_mock()

        output = volume_utils.copy_volume('/dev/zero', '/dev/null', 1024, '3M',
                                          sync=False, execute=utils.execute,
                                          ionice=None, throttle=fake_throttle)
        self.assertIsNone(output)
        mock_exec.assert_called_once_with('fake_throttle', 'dd',
                                          'if=/dev/zero',
                                          'of=/dev/null',
                                          'count=%s' % units.Gi,
                                          'bs=3M', 'iflag=count_bytes',
                                          run_as_root=True)

    @mock.patch('cinder.volume.utils.check_for_odirect_support',
                return_value=False)
    @mock.patch('cinder.utils.execute')
    def test_copy_volume_dd_no_throttle(self, mock_exec, mock_support):
        output = volume_utils.copy_volume('/dev/zero', '/dev/null', 1024, '3M',
                                          sync=True, execute=utils.execute,
                                          ionice=None)
        self.assertIsNone(output)
        mock_exec.assert_called_once_with('dd', 'if=/dev/zero', 'of=/dev/null',
                                          'count=%s' % units.Gi, 'bs=3M',
                                          'iflag=count_bytes',
                                          'conv=fdatasync', run_as_root=True)

    @mock.patch('cinder.volume.utils.check_for_odirect_support',
                return_value=False)
    @mock.patch('cinder.utils.execute')
    def test_copy_volume_dd_with_ionice(self, mock_exec, mock_support):
        output = volume_utils.copy_volume('/dev/zero', '/dev/null', 1024, '3M',
                                          sync=True, execute=utils.execute,
                                          ionice='-c3')
        self.assertIsNone(output)
        mock_exec.assert_called_once_with('ionice', '-c3', 'dd',
                                          'if=/dev/zero', 'of=/dev/null',
                                          'count=%s' % units.Gi, 'bs=3M',
                                          'iflag=count_bytes',
                                          'conv=fdatasync', run_as_root=True)

    @mock.patch('cinder.volume.utils.check_for_odirect_support',
                return_value=False)
    @mock.patch('cinder.utils.execute')
    def test_copy_volume_dd_with_sparse(self, mock_exec, mock_support):
        output = volume_utils.copy_volume('/dev/zero', '/dev/null', 1024, '3M',
                                          sync=True, execute=utils.execute,
                                          sparse=True)
        self.assertIsNone(output)
        mock_exec.assert_called_once_with('dd', 'if=/dev/zero', 'of=/dev/null',
                                          'count=%s' % units.Gi, 'bs=3M',
                                          'iflag=count_bytes',
                                          'conv=fdatasync,sparse',
                                          run_as_root=True)

    @mock.patch('cinder.volume.utils.check_for_odirect_support',
                return_value=True)
    @mock.patch('cinder.utils.execute')
    def test_copy_volume_dd_with_sparse_iflag_and_oflag(self, mock_exec,
                                                        mock_support):
        output = volume_utils.copy_volume('/dev/zero', '/dev/null', 1024, '3M',
                                          sync=True, execute=utils.execute,
                                          sparse=True)
        self.assertIsNone(output)
        mock_exec.assert_called_once_with('dd', 'if=/dev/zero', 'of=/dev/null',
                                          'count=%s' % units.Gi, 'bs=3M',
                                          'iflag=count_bytes,direct',
                                          'oflag=direct', 'conv=sparse',
                                          run_as_root=True)

    @mock.patch('cinder.volume.utils._copy_volume_with_file')
    def test_copy_volume_handles(self, mock_copy):
        handle1 = io.RawIOBase()
        handle2 = io.RawIOBase()
        output = volume_utils.copy_volume(handle1, handle2, 1024, 1)
        self.assertIsNone(output)
        mock_copy.assert_called_once_with(handle1, handle2, 1024)

    @mock.patch('cinder.volume.utils._transfer_data')
    @mock.patch('cinder.volume.utils._open_volume_with_path')
    def test_copy_volume_handle_transfer(self, mock_open, mock_transfer):
        handle = io.RawIOBase()
        output = volume_utils.copy_volume('/foo/bar', handle, 1024, 1)
        self.assertIsNone(output)
        mock_transfer.assert_called_once_with(mock.ANY, mock.ANY,
                                              1073741824, mock.ANY)


class VolumeUtilsTestCase(test.TestCase):
    def test_null_safe_str(self):
        self.assertEqual('', volume_utils.null_safe_str(None))
        self.assertEqual('', volume_utils.null_safe_str(False))
        self.assertEqual('', volume_utils.null_safe_str(0))
        self.assertEqual('', volume_utils.null_safe_str([]))
        self.assertEqual('', volume_utils.null_safe_str(()))
        self.assertEqual('', volume_utils.null_safe_str({}))
        self.assertEqual('', volume_utils.null_safe_str(set()))
        self.assertEqual('a', volume_utils.null_safe_str('a'))
        self.assertEqual('1', volume_utils.null_safe_str(1))
        self.assertEqual('True', volume_utils.null_safe_str(True))

    @mock.patch('cinder.utils.get_root_helper')
    @mock.patch('cinder.brick.local_dev.lvm.LVM.supports_thin_provisioning')
    def test_supports_thin_provisioning(self, mock_supports_thin, mock_helper):
        self.assertEqual(mock_supports_thin.return_value,
                         volume_utils.supports_thin_provisioning())
        mock_helper.assert_called_once_with()

    @mock.patch('cinder.utils.get_root_helper')
    @mock.patch('cinder.brick.local_dev.lvm.LVM.get_all_physical_volumes')
    def test_get_all_physical_volumes(self, mock_get_vols, mock_helper):
        self.assertEqual(mock_get_vols.return_value,
                         volume_utils.get_all_physical_volumes())
        mock_helper.assert_called_once_with()

    @mock.patch('cinder.utils.get_root_helper')
    @mock.patch('cinder.brick.local_dev.lvm.LVM.get_all_volume_groups')
    def test_get_all_volume_groups(self, mock_get_groups, mock_helper):
        self.assertEqual(mock_get_groups.return_value,
                         volume_utils.get_all_volume_groups())
        mock_helper.assert_called_once_with()

    def test_generate_password(self):
        password = volume_utils.generate_password()
        self.assertTrue(any(c for c in password if c in '23456789'))
        self.assertTrue(any(c for c in password
                            if c in 'abcdefghijkmnopqrstuvwxyz'))
        self.assertTrue(any(c for c in password
                            if c in 'ABCDEFGHJKLMNPQRSTUVWXYZ'))
        self.assertEqual(16, len(password))
        self.assertEqual(10, len(volume_utils.generate_password(10)))

    @mock.patch('cinder.volume.utils.generate_password')
    def test_generate_username(self, mock_gen_pass):
        output = volume_utils.generate_username()
        self.assertEqual(mock_gen_pass.return_value, output)

    def test_extract_host(self):
        host = 'Host'
        # default level is 'backend'
        self.assertEqual(host,
                         volume_utils.extract_host(host))
        self.assertEqual(host,
                         volume_utils.extract_host(host, 'host'))
        self.assertEqual(host,
                         volume_utils.extract_host(host, 'backend'))
        # default_pool_name doesn't work for level other than 'pool'
        self.assertEqual(host,
                         volume_utils.extract_host(host, 'host', True))
        self.assertEqual(host,
                         volume_utils.extract_host(host, 'host', False))
        self.assertEqual(host,
                         volume_utils.extract_host(host, 'backend', True))
        self.assertEqual(host,
                         volume_utils.extract_host(host, 'backend', False))
        self.assertIsNone(volume_utils.extract_host(host, 'pool'))
        self.assertEqual('_pool0',
                         volume_utils.extract_host(host, 'pool', True))

        host = 'Host@Backend'
        self.assertEqual('Host@Backend',
                         volume_utils.extract_host(host))
        self.assertEqual('Host',
                         volume_utils.extract_host(host, 'host'))
        self.assertEqual(host,
                         volume_utils.extract_host(host, 'backend'))
        self.assertIsNone(volume_utils.extract_host(host, 'pool'))
        self.assertEqual('_pool0',
                         volume_utils.extract_host(host, 'pool', True))

        host = 'Host@Backend#Pool'
        pool = 'Pool'
        self.assertEqual('Host@Backend',
                         volume_utils.extract_host(host))
        self.assertEqual('Host',
                         volume_utils.extract_host(host, 'host'))
        self.assertEqual('Host@Backend',
                         volume_utils.extract_host(host, 'backend'))
        self.assertEqual(pool,
                         volume_utils.extract_host(host, 'pool'))
        self.assertEqual(pool,
                         volume_utils.extract_host(host, 'pool', True))

        host = 'Host#Pool'
        self.assertEqual('Host',
                         volume_utils.extract_host(host))
        self.assertEqual('Host',
                         volume_utils.extract_host(host, 'host'))
        self.assertEqual('Host',
                         volume_utils.extract_host(host, 'backend'))
        self.assertEqual(pool,
                         volume_utils.extract_host(host, 'pool'))
        self.assertEqual(pool,
                         volume_utils.extract_host(host, 'pool', True))

    def test_get_volume_rpc_host(self):
        host = 'Host@backend'
        # default level is 'backend'
        # check if host with backend is returned
        self.assertEqual(volume_utils.extract_host(host),
                         volume_utils.get_volume_rpc_host(host))

    def test_append_host(self):
        host = 'Host'
        pool = 'Pool'
        expected = 'Host#Pool'
        self.assertEqual(expected,
                         volume_utils.append_host(host, pool))

        pool = None
        expected = 'Host'
        self.assertEqual(expected,
                         volume_utils.append_host(host, pool))

        host = None
        pool = 'pool'
        expected = None
        self.assertEqual(expected,
                         volume_utils.append_host(host, pool))

        host = None
        pool = None
        expected = None
        self.assertEqual(expected,
                         volume_utils.append_host(host, pool))

    def test_compare_hosts(self):
        host_1 = 'fake_host@backend1'
        host_2 = 'fake_host@backend1#pool1'
        self.assertTrue(volume_utils.hosts_are_equivalent(host_1, host_2))

        host_2 = 'fake_host@backend1'
        self.assertTrue(volume_utils.hosts_are_equivalent(host_1, host_2))

        host_2 = 'fake_host2@backend1'
        self.assertFalse(volume_utils.hosts_are_equivalent(host_1, host_2))

    @mock.patch('cinder.volume.utils.CONF')
    def test_extract_id_from_volume_name_vol_id_pattern(self, conf_mock):
        conf_mock.volume_name_template = 'volume-%s'
        vol_id = 'd8cd1feb-2dcc-404d-9b15-b86fe3bec0a1'
        vol_name = conf_mock.volume_name_template % vol_id
        result = volume_utils.extract_id_from_volume_name(vol_name)
        self.assertEqual(vol_id, result)

    @mock.patch('cinder.volume.utils.CONF')
    def test_extract_id_from_volume_name_vol_id_vol_pattern(self, conf_mock):
        conf_mock.volume_name_template = 'volume-%s-volume'
        vol_id = 'd8cd1feb-2dcc-404d-9b15-b86fe3bec0a1'
        vol_name = conf_mock.volume_name_template % vol_id
        result = volume_utils.extract_id_from_volume_name(vol_name)
        self.assertEqual(vol_id, result)

    @mock.patch('cinder.volume.utils.CONF')
    def test_extract_id_from_volume_name_id_vol_pattern(self, conf_mock):
        conf_mock.volume_name_template = '%s-volume'
        vol_id = 'd8cd1feb-2dcc-404d-9b15-b86fe3bec0a1'
        vol_name = conf_mock.volume_name_template % vol_id
        result = volume_utils.extract_id_from_volume_name(vol_name)
        self.assertEqual(vol_id, result)

    @mock.patch('cinder.volume.utils.CONF')
    def test_extract_id_from_volume_name_no_match(self, conf_mock):
        conf_mock.volume_name_template = '%s-volume'
        vol_name = 'd8cd1feb-2dcc-404d-9b15-b86fe3bec0a1'
        result = volume_utils.extract_id_from_volume_name(vol_name)
        self.assertIsNone(result)
        vol_name = 'blahblahblah'
        result = volume_utils.extract_id_from_volume_name(vol_name)
        self.assertIsNone(result)

    @mock.patch('cinder.db.sqlalchemy.api.resource_exists', return_value=True)
    def test_check_managed_volume_already_managed(self, exists_mock):
        id_ = 'd8cd1feb-2dcc-404d-9b15-b86fe3bec0a1'
        result = volume_utils.check_already_managed_volume(id_)
        self.assertTrue(result)
        exists_mock.assert_called_once_with(mock.ANY, models.Volume, id_)

    @mock.patch('cinder.db.sqlalchemy.api.resource_exists', return_value=False)
    def test_check_managed_volume_not_managed_proper_uuid(self, exists_mock):
        id_ = 'd8cd1feb-2dcc-404d-9b15-b86fe3bec0a1'
        result = volume_utils.check_already_managed_volume(id_)
        self.assertFalse(result)
        exists_mock.assert_called_once_with(mock.ANY, models.Volume, id_)

    def test_check_managed_volume_not_managed_invalid_id(self):
        result = volume_utils.check_already_managed_volume(1)
        self.assertFalse(result)
        result = volume_utils.check_already_managed_volume('not-a-uuid')
        self.assertFalse(result)

    @mock.patch('cinder.volume.utils.CONF')
    def test_extract_id_from_snapshot_name(self, conf_mock):
        conf_mock.snapshot_name_template = '%s-snapshot'
        snap_id = 'd8cd1feb-2dcc-404d-9b15-b86fe3bec0a1'
        snap_name = conf_mock.snapshot_name_template % snap_id
        result = volume_utils.extract_id_from_snapshot_name(snap_name)
        self.assertEqual(snap_id, result)

    @mock.patch('cinder.volume.utils.CONF')
    def test_extract_id_from_snapshot_name_no_match(self, conf_mock):
        conf_mock.snapshot_name_template = '%s-snapshot'
        snap_name = 'd8cd1feb-2dcc-404d-9b15-b86fe3bec0a1'
        result = volume_utils.extract_id_from_snapshot_name(snap_name)
        self.assertIsNone(result)
        snap_name = 'blahblahblah'
        result = volume_utils.extract_id_from_snapshot_name(snap_name)
        self.assertIsNone(result)

    def test_paginate_entries_list_with_marker(self):
        entries = [{'reference': {'name': 'vol03'}, 'size': 1},
                   {'reference': {'name': 'vol01'}, 'size': 3},
                   {'reference': {'name': 'vol02'}, 'size': 3},
                   {'reference': {'name': 'vol04'}, 'size': 2},
                   {'reference': {'name': 'vol06'}, 'size': 3},
                   {'reference': {'name': 'vol07'}, 'size': 1},
                   {'reference': {'name': 'vol05'}, 'size': 1}]
        expected = [{'reference': {'name': 'vol04'}, 'size': 2},
                    {'reference': {'name': 'vol03'}, 'size': 1},
                    {'reference': {'name': 'vol05'}, 'size': 1}]
        res = volume_utils.paginate_entries_list(entries, {'name': 'vol02'}, 3,
                                                 1, ['size', 'reference'],
                                                 ['desc', 'asc'])
        self.assertEqual(expected, res)

    def test_paginate_entries_list_without_marker(self):
        entries = [{'reference': {'name': 'vol03'}, 'size': 1},
                   {'reference': {'name': 'vol01'}, 'size': 3},
                   {'reference': {'name': 'vol02'}, 'size': 3},
                   {'reference': {'name': 'vol04'}, 'size': 2},
                   {'reference': {'name': 'vol06'}, 'size': 3},
                   {'reference': {'name': 'vol07'}, 'size': 1},
                   {'reference': {'name': 'vol05'}, 'size': 1}]
        expected = [{'reference': {'name': 'vol07'}, 'size': 1},
                    {'reference': {'name': 'vol06'}, 'size': 3},
                    {'reference': {'name': 'vol05'}, 'size': 1}]
        res = volume_utils.paginate_entries_list(entries, None, 3, None,
                                                 ['reference'], ['desc'])
        self.assertEqual(expected, res)

    def test_paginate_entries_list_marker_not_found(self):
        entries = [{'reference': {'name': 'vol03'}, 'size': 1},
                   {'reference': {'name': 'vol01'}, 'size': 3}]
        self.assertRaises(exception.InvalidInput,
                          volume_utils.paginate_entries_list,
                          entries, {'name': 'vol02'}, 3, None,
                          ['size', 'reference'], ['desc', 'asc'])

    def test_convert_config_string_to_dict(self):
        test_string = "{'key-1'='val-1' 'key-2'='val-2' 'key-3'='val-3'}"
        expected_dict = {'key-1': 'val-1', 'key-2': 'val-2', 'key-3': 'val-3'}

        self.assertEqual(
            expected_dict,
            volume_utils.convert_config_string_to_dict(test_string))

    @mock.patch('cinder.volume.volume_types.is_encrypted', return_value=False)
    def test_create_encryption_key_unencrypted(self, is_encrypted):
        result = volume_utils.create_encryption_key(mock.ANY,
                                                    mock.ANY,
                                                    fake.VOLUME_TYPE_ID)
        self.assertIsNone(result)

    @mock.patch('cinder.volume.volume_types.is_encrypted', return_value=True)
    @mock.patch('cinder.volume.volume_types.get_volume_type_encryption')
    @mock.patch('cinder.keymgr.conf_key_mgr.ConfKeyManager.create_key')
    def test_create_encryption_key_encrypted(self, create_key,
                                             get_volume_type_encryption,
                                             is_encryption):
        enc_key = {'cipher': 'aes-xts-plain64',
                   'key_size': 256,
                   'provider': 'p1',
                   'control_location': 'front-end',
                   'encryption_id': 'uuid1'}
        ctxt = context.get_admin_context()
        type_ref1 = volume_types.create(ctxt, "type1")
        encryption = db.volume_type_encryption_create(
            ctxt, type_ref1['id'], enc_key)
        get_volume_type_encryption.return_value = encryption
        CONF.set_override(
            'api_class',
            'cinder.keymgr.conf_key_mgr.ConfKeyManager',
            group='key_manager')
        key_manager = keymgr.API()
        volume_utils.create_encryption_key(ctxt,
                                           key_manager,
                                           fake.VOLUME_TYPE_ID)
        is_encryption.assert_called_once_with(ctxt,
                                              fake.VOLUME_TYPE_ID)
        get_volume_type_encryption.assert_called_once_with(
            ctxt,
            fake.VOLUME_TYPE_ID)
        create_key.assert_called_once_with(ctxt,
                                           algorithm='aes',
                                           length=256)
