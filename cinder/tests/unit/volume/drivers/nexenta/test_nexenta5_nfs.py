# Copyright 2016 Nexenta Systems, Inc.
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
Unit tests for OpenStack Cinder volume driver
"""

import mock
from mock import patch

from cinder import context
from cinder import db
from cinder import test
from cinder.volume import configuration as conf
from cinder.volume.drivers.nexenta.ns5 import jsonrpc
from cinder.volume.drivers.nexenta.ns5 import nfs


class TestNexentaNfsDriver(test.TestCase):
    TEST_SHARE = 'host1:/pool/share'
    TEST_SHARE2_OPTIONS = '-o intr'
    TEST_FILE_NAME = 'test.txt'
    TEST_SHARES_CONFIG_FILE = '/etc/cinder/nexenta-shares.conf'
    TEST_SNAPSHOT_NAME = 'snapshot1'
    TEST_VOLUME_NAME = 'volume1'
    TEST_VOLUME_NAME2 = 'volume2'

    TEST_VOLUME = {
        'name': TEST_VOLUME_NAME,
        'id': '1',
        'size': 1,
        'status': 'available',
        'provider_location': TEST_SHARE
    }
    TEST_VOLUME2 = {
        'name': TEST_VOLUME_NAME2,
        'size': 1,
        'id': '2',
        'status': 'in-use'
    }

    TEST_SNAPSHOT = {
        'name': TEST_SNAPSHOT_NAME,
        'volume_name': TEST_VOLUME_NAME,
        'volume_id': '1'
    }

    TEST_SHARE_SVC = 'svc:/network/nfs/server:default'

    def setUp(self):
        super(TestNexentaNfsDriver, self).setUp()
        self.ctxt = context.get_admin_context()
        self.cfg = mock.Mock(spec=conf.Configuration)
        self.cfg.nexenta_dataset_description = ''
        self.cfg.nexenta_mount_point_base = '$state_path/mnt'
        self.cfg.nexenta_sparsed_volumes = True
        self.cfg.nexenta_dataset_compression = 'on'
        self.cfg.nexenta_dataset_dedup = 'off'
        self.cfg.nfs_mount_point_base = '/mnt/test'
        self.cfg.nfs_mount_attempts = 3
        self.cfg.nas_mount_options = 'vers=4'
        self.cfg.reserved_percentage = 20
        self.cfg.nexenta_rest_protocol = 'http'
        self.cfg.nexenta_rest_port = 8080
        self.cfg.nexenta_user = 'user'
        self.cfg.nexenta_password = 'pass'
        self.cfg.max_over_subscription_ratio = 20.0
        self.cfg.nas_host = '1.1.1.1'
        self.cfg.nas_share_path = 'pool/share'
        self.nef_mock = mock.Mock()
        self.mock_object(jsonrpc, 'NexentaJSONProxy',
                         return_value=self.nef_mock)
        self.drv = nfs.NexentaNfsDriver(configuration=self.cfg)
        self.drv.db = db
        self.drv.do_setup(self.ctxt)

    def _create_volume_db_entry(self):
        vol = {
            'id': '1',
            'size': 1,
            'status': 'available',
            'provider_location': self.TEST_SHARE
        }
        return db.volume_create(self.ctxt, vol)['id']

    def test_check_for_setup_error(self):
        self.nef_mock.get.return_value = {'data': []}
        self.assertRaises(
            LookupError, lambda: self.drv.check_for_setup_error())

    def test_initialize_connection(self):
        data = {
            'export': self.TEST_VOLUME['provider_location'], 'name': 'volume'}
        self.assertEqual({
            'driver_volume_type': self.drv.driver_volume_type,
            'data': data
        }, self.drv.initialize_connection(self.TEST_VOLUME, None))

    @patch('cinder.volume.drivers.nexenta.ns5.nfs.'
           'NexentaNfsDriver._create_regular_file')
    @patch('cinder.volume.drivers.nexenta.ns5.nfs.'
           'NexentaNfsDriver._create_sparsed_file')
    @patch('cinder.volume.drivers.nexenta.ns5.nfs.'
           'NexentaNfsDriver._ensure_share_mounted')
    @patch('cinder.volume.drivers.nexenta.ns5.nfs.'
           'NexentaNfsDriver._share_folder')
    def test_do_create_volume(self, share, ensure, sparsed, regular):
        ensure.return_value = True
        share.return_value = True
        self.nef_mock.get.return_value = 'on'
        self.drv._do_create_volume(self.TEST_VOLUME)

        url = 'storage/pools/pool/filesystems'
        data = {
            'name': 'share/volume1',
            'compressionMode': 'on',
            'dedupMode': 'off',
        }
        self.nef_mock.post.assert_called_with(url, data)

    @patch('cinder.volume.drivers.nexenta.ns5.nfs.'
           'NexentaNfsDriver._ensure_share_mounted')
    def test_delete_volume(self, ensure):
        self._create_volume_db_entry()
        self.nef_mock.get.return_value = {}
        self.drv.delete_volume(self.TEST_VOLUME)
        self.nef_mock.delete.assert_called_with(
            'storage/pools/pool/filesystems/share%2Fvolume1?snapshots=true')

    def test_create_snapshot(self):
        self._create_volume_db_entry()
        self.drv.create_snapshot(self.TEST_SNAPSHOT)
        url = 'storage/pools/pool/filesystems/share%2Fvolume-1/snapshots'
        data = {'name': self.TEST_SNAPSHOT['name']}
        self.nef_mock.post.assert_called_with(url, data)

    def test_delete_snapshot(self):
        self._create_volume_db_entry()
        self.drv.delete_snapshot(self.TEST_SNAPSHOT)
        url = ('storage/pools/pool/filesystems/share%2Fvolume-1/'
               'snapshots/snapshot1')
        self.drv.delete_snapshot(self.TEST_SNAPSHOT)
        self.nef_mock.delete.assert_called_with(url)

    @patch('cinder.volume.drivers.nexenta.ns5.nfs.'
           'NexentaNfsDriver._share_folder')
    def test_create_volume_from_snapshot(self, share):
        self._create_volume_db_entry()
        url = ('storage/filesystems/pool%2Fshare%2Fvolume2/promote')

        self.drv.create_volume_from_snapshot(
            self.TEST_VOLUME2, self.TEST_SNAPSHOT)
        self.nef_mock.post.assert_called_with(url)

    def test_get_capacity_info(self):
        self.nef_mock.get.return_value = {
            'bytesAvailable': 1000,
            'bytesUsed': 100}

        self.assertEqual(
            (1000, 900, 100), self.drv._get_capacity_info('pool/share'))
