# Copyright 2013 Canonical Ltd.
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
""" Tests for create_volume TaskFlow """

import ddt
import mock

from castellan.tests.unit.key_manager import mock_key_manager
from oslo_utils import imageutils

from cinder import context
from cinder import exception
from cinder import test
from cinder.tests.unit.consistencygroup import fake_consistencygroup
from cinder.tests.unit import fake_constants as fakes
from cinder.tests.unit import fake_snapshot
from cinder.tests.unit import fake_volume
from cinder.tests.unit.image import fake as fake_image
from cinder.tests.unit import utils
from cinder.tests.unit.volume.flows import fake_volume_api
from cinder.volume.flows.api import create_volume
from cinder.volume.flows.manager import create_volume as create_volume_manager


@ddt.ddt
class CreateVolumeFlowTestCase(test.TestCase):

    def time_inc(self):
        self.counter += 1
        return self.counter

    def setUp(self):
        super(CreateVolumeFlowTestCase, self).setUp()
        self.ctxt = context.get_admin_context()

        # Ensure that time.time() always returns more than the last time it was
        # called to avoid div by zero errors.
        self.counter = float(0)

    @mock.patch('cinder.objects.Volume.get_by_id')
    @mock.patch('cinder.volume.utils.extract_host')
    @mock.patch('time.time')
    @mock.patch('cinder.objects.ConsistencyGroup.get_by_id')
    def test_cast_create_volume(self, consistencygroup_get_by_id, mock_time,
                                mock_extract_host, volume_get_by_id):
        mock_time.side_effect = self.time_inc
        volume = fake_volume.fake_volume_obj(self.ctxt)
        volume_get_by_id.return_value = volume
        props = {}
        cg_obj = (fake_consistencygroup.
                  fake_consistencyobject_obj(self.ctxt, consistencygroup_id=1,
                                             host='host@backend#pool'))
        consistencygroup_get_by_id.return_value = cg_obj
        spec = {'volume_id': None,
                'volume': None,
                'source_volid': None,
                'snapshot_id': None,
                'image_id': None,
                'source_replicaid': None,
                'consistencygroup_id': None,
                'cgsnapshot_id': None,
                'group_id': None, }

        # Fake objects assert specs
        task = create_volume.VolumeCastTask(
            fake_volume_api.FakeSchedulerRpcAPI(spec, self),
            fake_volume_api.FakeVolumeAPI(spec, self),
            fake_volume_api.FakeDb())

        task._cast_create_volume(self.ctxt, spec, props)

        spec = {'volume_id': volume.id,
                'volume': volume,
                'source_volid': 2,
                'snapshot_id': 3,
                'image_id': 4,
                'source_replicaid': 5,
                'consistencygroup_id': 5,
                'cgsnapshot_id': None,
                'group_id': None, }

        # Fake objects assert specs
        task = create_volume.VolumeCastTask(
            fake_volume_api.FakeSchedulerRpcAPI(spec, self),
            fake_volume_api.FakeVolumeAPI(spec, self),
            fake_volume_api.FakeDb())

        task._cast_create_volume(self.ctxt, spec, props)
        consistencygroup_get_by_id.assert_called_once_with(self.ctxt, 5)
        mock_extract_host.assert_called_once_with('host@backend#pool')

    @mock.patch('cinder.volume.volume_types.is_encrypted')
    @mock.patch('cinder.volume.flows.api.create_volume.'
                'ExtractVolumeRequestTask.'
                '_get_volume_type_id')
    @mock.patch('cinder.volume.flows.api.create_volume.'
                'ExtractVolumeRequestTask.'
                '_get_encryption_key_id')
    @mock.patch('cinder.volume.volume_types.get_volume_type_qos_specs')
    def test_extract_volume_request_from_image_encrypted(
            self,
            fake_get_qos,
            fake_get_encryption_key,
            fake_get_volume_type_id,
            fake_is_encrypted):

        fake_image_service = fake_image.FakeImageService()
        image_id = 1
        image_meta = {}
        image_meta['id'] = image_id
        image_meta['status'] = 'active'
        image_meta['size'] = 1
        fake_image_service.create(self.ctxt, image_meta)
        fake_key_manager = mock_key_manager.MockKeyManager()

        task = create_volume.ExtractVolumeRequestTask(
            fake_image_service,
            {'nova'})

        fake_is_encrypted.return_value = True
        fake_get_volume_type_id.return_value = fakes.VOLUME_TYPE_ID
        task.execute(self.ctxt,
                     size=1,
                     snapshot=None,
                     image_id=image_id,
                     source_volume=None,
                     availability_zone='nova',
                     volume_type=None,
                     metadata=None,
                     key_manager=fake_key_manager,
                     source_replica=None,
                     consistencygroup=None,
                     cgsnapshot=None,
                     group=None)
        fake_get_encryption_key.assert_called_once_with(
            fake_key_manager, self.ctxt, fakes.VOLUME_TYPE_ID, None, None)

    @mock.patch('cinder.volume.volume_types.is_encrypted')
    @mock.patch('cinder.volume.volume_types.get_volume_type_qos_specs')
    @mock.patch('cinder.volume.flows.api.create_volume.'
                'ExtractVolumeRequestTask.'
                '_get_volume_type_id')
    def test_extract_volume_request_from_image(
            self,
            fake_get_type_id,
            fake_get_qos,
            fake_is_encrypted):

        fake_image_service = fake_image.FakeImageService()
        image_id = 2
        image_meta = {}
        image_meta['id'] = image_id
        image_meta['status'] = 'active'
        image_meta['size'] = 1
        fake_image_service.create(self.ctxt, image_meta)
        fake_key_manager = mock_key_manager.MockKeyManager()
        volume_type = 'type1'

        task = create_volume.ExtractVolumeRequestTask(
            fake_image_service,
            {'nova'})

        fake_is_encrypted.return_value = False
        fake_get_type_id.return_value = 1
        fake_get_qos.return_value = {'qos_specs': None}
        result = task.execute(self.ctxt,
                              size=1,
                              snapshot=None,
                              image_id=image_id,
                              source_volume=None,
                              availability_zone='nova',
                              volume_type=volume_type,
                              metadata=None,
                              key_manager=fake_key_manager,
                              source_replica=None,
                              consistencygroup=None,
                              cgsnapshot=None,
                              group=None)
        expected_result = {'size': 1,
                           'snapshot_id': None,
                           'source_volid': None,
                           'availability_zone': 'nova',
                           'volume_type': volume_type,
                           'volume_type_id': 1,
                           'encryption_key_id': None,
                           'qos_specs': None,
                           'source_replicaid': None,
                           'consistencygroup_id': None,
                           'cgsnapshot_id': None,
                           'group_id': None, }
        self.assertEqual(expected_result, result)

    @mock.patch('cinder.volume.volume_types.is_encrypted')
    @mock.patch('cinder.volume.volume_types.get_volume_type_qos_specs')
    @mock.patch('cinder.volume.flows.api.create_volume.'
                'ExtractVolumeRequestTask.'
                '_get_volume_type_id')
    def test_extract_availability_zone_without_fallback(
            self,
            fake_get_type_id,
            fake_get_qos,
            fake_is_encrypted):
        fake_image_service = fake_image.FakeImageService()
        image_id = 3
        image_meta = {}
        image_meta['id'] = image_id
        image_meta['status'] = 'active'
        image_meta['size'] = 1
        fake_image_service.create(self.ctxt, image_meta)
        fake_key_manager = mock_key_manager.MockKeyManager()
        volume_type = 'type1'

        task = create_volume.ExtractVolumeRequestTask(
            fake_image_service,
            {'nova'})

        fake_is_encrypted.return_value = False
        fake_get_type_id.return_value = 1
        fake_get_qos.return_value = {'qos_specs': None}
        self.assertRaises(exception.InvalidInput,
                          task.execute,
                          self.ctxt,
                          size=1,
                          snapshot=None,
                          image_id=image_id,
                          source_volume=None,
                          availability_zone='notnova',
                          volume_type=volume_type,
                          metadata=None,
                          key_manager=fake_key_manager,
                          source_replica=None,
                          consistencygroup=None,
                          cgsnapshot=None,
                          group=None)

    @mock.patch('cinder.volume.volume_types.is_encrypted')
    @mock.patch('cinder.volume.volume_types.get_volume_type_qos_specs')
    @mock.patch('cinder.volume.flows.api.create_volume.'
                'ExtractVolumeRequestTask.'
                '_get_volume_type_id')
    def test_extract_availability_zone_with_fallback(
            self,
            fake_get_type_id,
            fake_get_qos,
            fake_is_encrypted):

        self.override_config('allow_availability_zone_fallback', True)

        fake_image_service = fake_image.FakeImageService()
        image_id = 4
        image_meta = {}
        image_meta['id'] = image_id
        image_meta['status'] = 'active'
        image_meta['size'] = 1
        fake_image_service.create(self.ctxt, image_meta)
        fake_key_manager = mock_key_manager.MockKeyManager()
        volume_type = 'type1'

        task = create_volume.ExtractVolumeRequestTask(
            fake_image_service,
            {'nova'})

        fake_is_encrypted.return_value = False
        fake_get_type_id.return_value = 1
        fake_get_qos.return_value = {'qos_specs': None}
        result = task.execute(self.ctxt,
                              size=1,
                              snapshot=None,
                              image_id=image_id,
                              source_volume=None,
                              availability_zone='does_not_exist',
                              volume_type=volume_type,
                              metadata=None,
                              key_manager=fake_key_manager,
                              source_replica=None,
                              consistencygroup=None,
                              cgsnapshot=None,
                              group=None)
        expected_result = {'size': 1,
                           'snapshot_id': None,
                           'source_volid': None,
                           'availability_zone': 'nova',
                           'volume_type': volume_type,
                           'volume_type_id': 1,
                           'encryption_key_id': None,
                           'qos_specs': None,
                           'source_replicaid': None,
                           'consistencygroup_id': None,
                           'cgsnapshot_id': None,
                           'group_id': None, }
        self.assertEqual(expected_result, result)

    @mock.patch('cinder.volume.volume_types.is_encrypted')
    @mock.patch('cinder.volume.volume_types.get_volume_type_qos_specs')
    @mock.patch('cinder.volume.flows.api.create_volume.'
                'ExtractVolumeRequestTask.'
                '_get_volume_type_id')
    def test_extract_volume_request_from_image_with_qos_specs(
            self,
            fake_get_type_id,
            fake_get_qos,
            fake_is_encrypted):

        fake_image_service = fake_image.FakeImageService()
        image_id = 5
        image_meta = {}
        image_meta['id'] = image_id
        image_meta['status'] = 'active'
        image_meta['size'] = 1
        fake_image_service.create(self.ctxt, image_meta)
        fake_key_manager = mock_key_manager.MockKeyManager()
        volume_type = 'type1'

        task = create_volume.ExtractVolumeRequestTask(
            fake_image_service,
            {'nova'})

        fake_is_encrypted.return_value = False
        fake_get_type_id.return_value = 1
        fake_qos_spec = {'specs': {'fake_key': 'fake'}}
        fake_get_qos.return_value = {'qos_specs': fake_qos_spec}
        result = task.execute(self.ctxt,
                              size=1,
                              snapshot=None,
                              image_id=image_id,
                              source_volume=None,
                              availability_zone='nova',
                              volume_type=volume_type,
                              metadata=None,
                              key_manager=fake_key_manager,
                              source_replica=None,
                              consistencygroup=None,
                              cgsnapshot=None,
                              group=None)
        expected_result = {'size': 1,
                           'snapshot_id': None,
                           'source_volid': None,
                           'availability_zone': 'nova',
                           'volume_type': volume_type,
                           'volume_type_id': 1,
                           'encryption_key_id': None,
                           'qos_specs': {'fake_key': 'fake'},
                           'source_replicaid': None,
                           'consistencygroup_id': None,
                           'cgsnapshot_id': None,
                           'group_id': None, }
        self.assertEqual(expected_result, result)

    @mock.patch('cinder.volume.volume_types.is_encrypted')
    @mock.patch('cinder.volume.volume_types.get_volume_type_qos_specs')
    @mock.patch('cinder.volume.volume_types.get_default_volume_type')
    @mock.patch('cinder.volume.volume_types.get_volume_type_by_name')
    @mock.patch('cinder.volume.flows.api.create_volume.'
                'ExtractVolumeRequestTask.'
                '_get_volume_type_id')
    def test_extract_image_volume_type_from_image(
            self,
            fake_get_type_id,
            fake_get_vol_type,
            fake_get_def_vol_type,
            fake_get_qos,
            fake_is_encrypted):

        image_volume_type = 'type_from_image'
        fake_image_service = fake_image.FakeImageService()
        image_id = 6
        image_meta = {}
        image_meta['id'] = image_id
        image_meta['status'] = 'active'
        image_meta['size'] = 1
        image_meta['properties'] = {}
        image_meta['properties']['cinder_img_volume_type'] = image_volume_type
        fake_image_service.create(self.ctxt, image_meta)
        fake_key_manager = mock_key_manager.MockKeyManager()

        task = create_volume.ExtractVolumeRequestTask(
            fake_image_service,
            {'nova'})

        fake_is_encrypted.return_value = False
        fake_get_type_id.return_value = 1
        fake_get_vol_type.return_value = image_volume_type
        fake_get_def_vol_type.return_value = 'fake_vol_type'
        fake_get_qos.return_value = {'qos_specs': None}
        result = task.execute(self.ctxt,
                              size=1,
                              snapshot=None,
                              image_id=image_id,
                              source_volume=None,
                              availability_zone='nova',
                              volume_type=None,
                              metadata=None,
                              key_manager=fake_key_manager,
                              source_replica=None,
                              consistencygroup=None,
                              cgsnapshot=None,
                              group=None)
        expected_result = {'size': 1,
                           'snapshot_id': None,
                           'source_volid': None,
                           'availability_zone': 'nova',
                           'volume_type': image_volume_type,
                           'volume_type_id': 1,
                           'encryption_key_id': None,
                           'qos_specs': None,
                           'source_replicaid': None,
                           'consistencygroup_id': None,
                           'cgsnapshot_id': None,
                           'group_id': None, }
        self.assertEqual(expected_result, result)

    @mock.patch('cinder.db.volume_type_get_by_name')
    @mock.patch('cinder.volume.volume_types.is_encrypted')
    @mock.patch('cinder.volume.volume_types.get_volume_type_qos_specs')
    @mock.patch('cinder.volume.volume_types.get_default_volume_type')
    @mock.patch('cinder.volume.flows.api.create_volume.'
                'ExtractVolumeRequestTask.'
                '_get_volume_type_id')
    def test_extract_image_volume_type_from_image_invalid_type(
            self,
            fake_get_type_id,
            fake_get_def_vol_type,
            fake_get_qos,
            fake_is_encrypted,
            fake_db_get_vol_type):

        image_volume_type = 'invalid'
        fake_image_service = fake_image.FakeImageService()
        image_id = 7
        image_meta = {}
        image_meta['id'] = image_id
        image_meta['status'] = 'active'
        image_meta['size'] = 1
        image_meta['properties'] = {}
        image_meta['properties']['cinder_img_volume_type'] = image_volume_type
        fake_image_service.create(self.ctxt, image_meta)
        fake_key_manager = mock_key_manager.MockKeyManager()

        task = create_volume.ExtractVolumeRequestTask(
            fake_image_service,
            {'nova'})

        fake_is_encrypted.return_value = False
        fake_get_type_id.return_value = 1
        fake_get_def_vol_type.return_value = 'fake_vol_type'
        fake_db_get_vol_type.side_effect = (
            exception.VolumeTypeNotFoundByName(volume_type_name='invalid'))
        fake_get_qos.return_value = {'qos_specs': None}
        result = task.execute(self.ctxt,
                              size=1,
                              snapshot=None,
                              image_id=image_id,
                              source_volume=None,
                              availability_zone='nova',
                              volume_type=None,
                              metadata=None,
                              key_manager=fake_key_manager,
                              source_replica=None,
                              consistencygroup=None,
                              cgsnapshot=None,
                              group=None)
        expected_result = {'size': 1,
                           'snapshot_id': None,
                           'source_volid': None,
                           'availability_zone': 'nova',
                           'volume_type': 'fake_vol_type',
                           'volume_type_id': 1,
                           'encryption_key_id': None,
                           'qos_specs': None,
                           'source_replicaid': None,
                           'consistencygroup_id': None,
                           'cgsnapshot_id': None,
                           'group_id': None, }
        self.assertEqual(expected_result, result)

    @mock.patch('cinder.db.volume_type_get_by_name')
    @mock.patch('cinder.volume.volume_types.is_encrypted')
    @mock.patch('cinder.volume.volume_types.get_volume_type_qos_specs')
    @mock.patch('cinder.volume.volume_types.get_default_volume_type')
    @mock.patch('cinder.volume.flows.api.create_volume.'
                'ExtractVolumeRequestTask.'
                '_get_volume_type_id')
    @ddt.data((8, None), (9, {'cinder_img_volume_type': None}))
    @ddt.unpack
    def test_extract_image_volume_type_from_image_properties_error(
            self,
            image_id,
            fake_img_properties,
            fake_get_type_id,
            fake_get_def_vol_type,
            fake_get_qos,
            fake_is_encrypted,
            fake_db_get_vol_type):

        fake_image_service = fake_image.FakeImageService()
        image_meta = {}
        image_meta['id'] = image_id
        image_meta['status'] = 'active'
        image_meta['size'] = 1
        image_meta['properties'] = fake_img_properties
        fake_image_service.create(self.ctxt, image_meta)
        fake_key_manager = mock_key_manager.MockKeyManager()

        task = create_volume.ExtractVolumeRequestTask(
            fake_image_service,
            {'nova'})

        fake_is_encrypted.return_value = False
        fake_get_type_id.return_value = 1
        fake_get_def_vol_type.return_value = 'fake_vol_type'
        fake_get_qos.return_value = {'qos_specs': None}
        result = task.execute(self.ctxt,
                              size=1,
                              snapshot=None,
                              image_id=image_id,
                              source_volume=None,
                              availability_zone='nova',
                              volume_type=None,
                              metadata=None,
                              key_manager=fake_key_manager,
                              source_replica=None,
                              consistencygroup=None,
                              cgsnapshot=None,
                              group=None)
        expected_result = {'size': 1,
                           'snapshot_id': None,
                           'source_volid': None,
                           'availability_zone': 'nova',
                           'volume_type': 'fake_vol_type',
                           'volume_type_id': 1,
                           'encryption_key_id': None,
                           'qos_specs': None,
                           'source_replicaid': None,
                           'consistencygroup_id': None,
                           'cgsnapshot_id': None,
                           'group_id': None, }
        self.assertEqual(expected_result, result)

    @mock.patch('cinder.db.volume_type_get_by_name')
    @mock.patch('cinder.volume.volume_types.is_encrypted')
    @mock.patch('cinder.volume.volume_types.get_volume_type_qos_specs')
    @mock.patch('cinder.volume.volume_types.get_default_volume_type')
    @mock.patch('cinder.volume.flows.api.create_volume.'
                'ExtractVolumeRequestTask.'
                '_get_volume_type_id')
    def test_extract_image_volume_type_from_image_invalid_input(
            self,
            fake_get_type_id,
            fake_get_def_vol_type,
            fake_get_qos,
            fake_is_encrypted,
            fake_db_get_vol_type):

        fake_image_service = fake_image.FakeImageService()
        image_id = 10
        image_meta = {}
        image_meta['id'] = image_id
        image_meta['status'] = 'inactive'
        fake_image_service.create(self.ctxt, image_meta)
        fake_key_manager = mock_key_manager.MockKeyManager()

        task = create_volume.ExtractVolumeRequestTask(
            fake_image_service,
            {'nova'})

        fake_is_encrypted.return_value = False
        fake_get_type_id.return_value = 1
        fake_get_def_vol_type.return_value = 'fake_vol_type'
        fake_get_qos.return_value = {'qos_specs': None}

        self.assertRaises(exception.InvalidInput,
                          task.execute,
                          self.ctxt,
                          size=1,
                          snapshot=None,
                          image_id=image_id,
                          source_volume=None,
                          availability_zone='nova',
                          volume_type=None,
                          metadata=None,
                          key_manager=fake_key_manager,
                          source_replica=None,
                          consistencygroup=None,
                          cgsnapshot=None,
                          group=None)


@ddt.ddt
class CreateVolumeFlowManagerTestCase(test.TestCase):

    def setUp(self):
        super(CreateVolumeFlowManagerTestCase, self).setUp()
        self.ctxt = context.get_admin_context()

    @mock.patch('cinder.volume.flows.manager.create_volume.'
                'CreateVolumeFromSpecTask.'
                '_handle_bootable_volume_glance_meta')
    @mock.patch('cinder.objects.Volume.get_by_id')
    @mock.patch('cinder.objects.Snapshot.get_by_id')
    def test_create_from_snapshot(self, snapshot_get_by_id, volume_get_by_id,
                                  handle_bootable):
        fake_db = mock.MagicMock()
        fake_driver = mock.MagicMock()
        fake_volume_manager = mock.MagicMock()
        fake_manager = create_volume_manager.CreateVolumeFromSpecTask(
            fake_volume_manager, fake_db, fake_driver)
        volume_db = {'bootable': True}
        volume_obj = fake_volume.fake_volume_obj(self.ctxt, **volume_db)
        snapshot_obj = fake_snapshot.fake_snapshot_obj(self.ctxt)
        snapshot_get_by_id.return_value = snapshot_obj
        volume_get_by_id.return_value = volume_obj

        fake_manager._create_from_snapshot(self.ctxt, volume_obj,
                                           snapshot_obj.id)
        fake_driver.create_volume_from_snapshot.assert_called_once_with(
            volume_obj, snapshot_obj)
        handle_bootable.assert_called_once_with(self.ctxt, volume_obj,
                                                snapshot_id=snapshot_obj.id)

    @mock.patch('cinder.objects.Snapshot.get_by_id')
    def test_create_from_snapshot_update_failure(self, snapshot_get_by_id):
        fake_db = mock.MagicMock()
        fake_driver = mock.MagicMock()
        fake_volume_manager = mock.MagicMock()
        fake_manager = create_volume_manager.CreateVolumeFromSpecTask(
            fake_volume_manager, fake_db, fake_driver)
        volume = fake_volume.fake_db_volume()
        snapshot_obj = fake_snapshot.fake_snapshot_obj(self.ctxt)
        snapshot_get_by_id.return_value = snapshot_obj
        fake_db.volume_get.side_effect = exception.CinderException

        self.assertRaises(exception.MetadataUpdateFailure,
                          fake_manager._create_from_snapshot, self.ctxt,
                          volume, snapshot_obj.id)
        fake_driver.create_volume_from_snapshot.assert_called_once_with(
            volume, snapshot_obj)

    @mock.patch('cinder.volume.flows.manager.create_volume.'
                'CreateVolumeFromSpecTask.'
                '_handle_bootable_volume_glance_meta')
    @mock.patch('cinder.image.image_utils.TemporaryImages.fetch')
    @mock.patch('cinder.image.image_utils.qemu_img_info')
    @mock.patch('cinder.image.image_utils.check_virtual_size')
    def test_create_encrypted_volume_from_image(self,
                                                mock_check_size,
                                                mock_qemu_img,
                                                mock_fetch_img,
                                                mock_handle_bootable):
        fake_db = mock.MagicMock()
        fake_driver = mock.MagicMock()
        fake_volume_manager = mock.MagicMock()
        fake_manager = create_volume_manager.CreateVolumeFromSpecTask(
            fake_volume_manager, fake_db, fake_driver)
        volume = fake_volume.fake_volume_obj(
            self.ctxt,
            encryption_key_id=fakes.ENCRYPTION_KEY_ID)

        fake_image_service = fake_image.FakeImageService()
        image_meta = {}
        image_id = fakes.IMAGE_ID
        image_meta['id'] = image_id
        image_meta['status'] = 'active'
        image_meta['size'] = 1
        image_location = 'abc'

        fake_db.volume_update.return_value = volume
        fake_manager._create_from_image(self.ctxt, volume,
                                        image_location, image_id,
                                        image_meta, fake_image_service)

        fake_driver.create_volume.assert_called_once_with(volume)
        fake_driver.copy_image_to_encrypted_volume.assert_called_once_with(
            self.ctxt, volume, fake_image_service, image_id)
        mock_handle_bootable.assert_called_once_with(self.ctxt, volume,
                                                     image_id=image_id,
                                                     image_meta=image_meta)

    @ddt.data(True, False)
    def test__copy_image_to_volume(self, is_encrypted):
        fake_db = mock.MagicMock()
        fake_driver = mock.MagicMock()
        fake_volume_manager = mock.MagicMock()
        fake_manager = create_volume_manager.CreateVolumeFromSpecTask(
            fake_volume_manager, fake_db, fake_driver)
        key = fakes.ENCRYPTION_KEY_ID if is_encrypted else None
        volume = fake_volume.fake_volume_obj(
            self.ctxt,
            encryption_key_id=key)

        fake_image_service = fake_image.FakeImageService()
        image_id = fakes.IMAGE_ID
        image_location = 'abc'

        fake_manager._copy_image_to_volume(self.ctxt, volume, image_id,
                                           image_location, fake_image_service)
        if is_encrypted:
            fake_driver.copy_image_to_encrypted_volume.assert_called_once_with(
                self.ctxt, volume, fake_image_service, image_id)
        else:
            fake_driver.copy_image_to_volume.assert_called_once_with(
                self.ctxt, volume, fake_image_service, image_id)


class CreateVolumeFlowManagerGlanceCinderBackendCase(test.TestCase):

    def setUp(self):
        super(CreateVolumeFlowManagerGlanceCinderBackendCase, self).setUp()
        self.ctxt = context.get_admin_context()

    @mock.patch('cinder.image.image_utils.TemporaryImages.fetch')
    @mock.patch('cinder.volume.flows.manager.create_volume.'
                'CreateVolumeFromSpecTask.'
                '_handle_bootable_volume_glance_meta')
    @mock.patch('cinder.image.image_utils.qemu_img_info')
    def test_create_from_image_volume(self, mock_qemu_info, handle_bootable,
                                      mock_fetch_img, format='raw', owner=None,
                                      location=True):
        self.flags(allowed_direct_url_schemes=['cinder'])
        mock_fetch_img.return_value = mock.MagicMock(
            spec=utils.get_file_spec())
        fake_db = mock.MagicMock()
        fake_driver = mock.MagicMock()
        fake_manager = create_volume_manager.CreateVolumeFromSpecTask(
            mock.MagicMock(), fake_db, fake_driver)
        fake_image_service = mock.MagicMock()
        volume = fake_volume.fake_volume_obj(self.ctxt)
        image_volume = fake_volume.fake_volume_obj(self.ctxt,
                                                   volume_metadata={})
        image_id = fakes.IMAGE_ID
        image_info = imageutils.QemuImgInfo()
        image_info.virtual_size = '1073741824'
        mock_qemu_info.return_value = image_info

        url = 'cinder://%s' % image_volume['id']
        image_location = None
        if location:
            image_location = (url, [{'url': url, 'metadata': {}}])
        image_meta = {'id': image_id,
                      'container_format': 'bare',
                      'disk_format': format,
                      'owner': owner or self.ctxt.project_id,
                      'virtual_size': None}

        fake_driver.clone_image.return_value = (None, False)
        fake_db.volume_get_all_by_host.return_value = [image_volume]

        fake_manager._create_from_image(self.ctxt,
                                        volume,
                                        image_location,
                                        image_id,
                                        image_meta,
                                        fake_image_service)
        if format is 'raw' and not owner and location:
            fake_driver.create_cloned_volume.assert_called_once_with(
                volume, image_volume)
            handle_bootable.assert_called_once_with(self.ctxt, volume,
                                                    image_id=image_id,
                                                    image_meta=image_meta)
        else:
            self.assertFalse(fake_driver.create_cloned_volume.called)

    def test_create_from_image_volume_in_qcow2_format(self):
        self.test_create_from_image_volume(format='qcow2')

    def test_create_from_image_volume_of_other_owner(self):
        self.test_create_from_image_volume(owner='fake-owner')

    def test_create_from_image_volume_without_location(self):
        self.test_create_from_image_volume(location=False)


@mock.patch('cinder.image.image_utils.TemporaryImages.fetch')
@mock.patch('cinder.volume.flows.manager.create_volume.'
            'CreateVolumeFromSpecTask.'
            '_handle_bootable_volume_glance_meta')
@mock.patch('cinder.volume.flows.manager.create_volume.'
            'CreateVolumeFromSpecTask.'
            '_create_from_source_volume')
@mock.patch('cinder.volume.flows.manager.create_volume.'
            'CreateVolumeFromSpecTask.'
            '_create_from_image_download')
@mock.patch('cinder.context.get_internal_tenant_context')
class CreateVolumeFlowManagerImageCacheTestCase(test.TestCase):

    def setUp(self):
        super(CreateVolumeFlowManagerImageCacheTestCase, self).setUp()
        self.ctxt = context.get_admin_context()
        self.mock_db = mock.MagicMock()
        self.mock_driver = mock.MagicMock()
        self.mock_cache = mock.MagicMock()
        self.mock_image_service = mock.MagicMock()
        self.mock_volume_manager = mock.MagicMock()

        self.internal_context = self.ctxt
        self.internal_context.user_id = 'abc123'
        self.internal_context.project_id = 'def456'

    def test_create_from_image_clone_image_and_skip_cache(
            self, mock_get_internal_context, mock_create_from_img_dl,
            mock_create_from_src, mock_handle_bootable, mock_fetch_img):
        self.mock_driver.clone_image.return_value = (None, True)
        volume = fake_volume.fake_volume_obj(self.ctxt)

        image_location = 'someImageLocationStr'
        image_id = fakes.IMAGE_ID
        image_meta = {'virtual_size': '1073741824'}

        manager = create_volume_manager.CreateVolumeFromSpecTask(
            self.mock_volume_manager,
            self.mock_db,
            self.mock_driver,
            image_volume_cache=self.mock_cache
        )

        manager._create_from_image(self.ctxt,
                                   volume,
                                   image_location,
                                   image_id,
                                   image_meta,
                                   self.mock_image_service)

        # Make sure clone_image is always called even if the cache is enabled
        self.assertTrue(self.mock_driver.clone_image.called)

        # Create from source shouldn't happen if clone_image succeeds
        self.assertFalse(mock_create_from_src.called)

        # The image download should not happen if clone_image succeeds
        self.assertFalse(mock_create_from_img_dl.called)

        mock_handle_bootable.assert_called_once_with(
            self.ctxt,
            volume,
            image_id=image_id,
            image_meta=image_meta
        )

    @mock.patch('cinder.image.image_utils.qemu_img_info')
    def test_create_from_image_cannot_use_cache(
            self, mock_qemu_info, mock_get_internal_context,
            mock_create_from_img_dl, mock_create_from_src,
            mock_handle_bootable, mock_fetch_img):
        mock_get_internal_context.return_value = None
        self.mock_driver.clone_image.return_value = (None, False)
        volume = fake_volume.fake_volume_obj(self.ctxt)
        image_info = imageutils.QemuImgInfo()
        image_info.virtual_size = '1073741824'
        mock_qemu_info.return_value = image_info

        image_location = 'someImageLocationStr'
        image_id = fakes.IMAGE_ID
        image_meta = {'virtual_size': '1073741824'}

        manager = create_volume_manager.CreateVolumeFromSpecTask(
            self.mock_volume_manager,
            self.mock_db,
            self.mock_driver,
            image_volume_cache=self.mock_cache
        )

        manager._create_from_image(self.ctxt,
                                   volume,
                                   image_location,
                                   image_id,
                                   image_meta,
                                   self.mock_image_service)

        # Make sure clone_image is always called
        self.assertTrue(self.mock_driver.clone_image.called)

        # Create from source shouldn't happen if cache cannot be used.
        self.assertFalse(mock_create_from_src.called)

        # The image download should happen if clone fails and we can't use the
        # image-volume cache.
        mock_create_from_img_dl.assert_called_once_with(
            self.ctxt,
            volume,
            image_location,
            image_id,
            self.mock_image_service
        )

        # This should not attempt to use a minimal size volume
        self.assertFalse(self.mock_db.volume_update.called)

        # Make sure we didn't try and create a cache entry
        self.assertFalse(self.mock_cache.ensure_space.called)
        self.assertFalse(self.mock_cache.create_cache_entry.called)

        mock_handle_bootable.assert_called_once_with(
            self.ctxt,
            volume,
            image_id=image_id,
            image_meta=image_meta
        )

    def test_create_from_image_bigger_size(
            self, mock_get_internal_context,
            mock_create_from_img_dl, mock_create_from_src,
            mock_handle_bootable, mock_fetch_img):
        volume = fake_volume.fake_volume_obj(self.ctxt)

        image_location = 'someImageLocationStr'
        image_id = fakes.IMAGE_ID
        image_meta = {'virtual_size': '2147483648'}

        manager = create_volume_manager.CreateVolumeFromSpecTask(
            self.mock_volume_manager,
            self.mock_db,
            self.mock_driver,
            image_volume_cache=self.mock_cache
        )

        self.assertRaises(
            exception.ImageUnacceptable,
            manager._create_from_image,
            self.ctxt,
            volume,
            image_location,
            image_id,
            image_meta,
            self.mock_image_service)

    def test_create_from_image_cache_hit(
            self, mock_get_internal_context, mock_create_from_img_dl,
            mock_create_from_src, mock_handle_bootable, mock_fetch_img):
        self.mock_driver.clone_image.return_value = (None, False)
        image_volume_id = '70a599e0-31e7-49b7-b260-868f441e862b'
        self.mock_cache.get_entry.return_value = {
            'volume_id': image_volume_id
        }

        volume = fake_volume.fake_volume_obj(self.ctxt)

        image_location = 'someImageLocationStr'
        image_id = fakes.IMAGE_ID
        image_meta = {'virtual_size': None}

        manager = create_volume_manager.CreateVolumeFromSpecTask(
            self.mock_volume_manager,
            self.mock_db,
            self.mock_driver,
            image_volume_cache=self.mock_cache
        )

        manager._create_from_image(self.ctxt,
                                   volume,
                                   image_location,
                                   image_id,
                                   image_meta,
                                   self.mock_image_service)

        # Make sure clone_image is always called even if the cache is enabled
        self.assertTrue(self.mock_driver.clone_image.called)

        # For a cache hit it should only clone from the image-volume
        mock_create_from_src.assert_called_once_with(self.ctxt,
                                                     volume,
                                                     image_volume_id)

        # The image download should not happen when we get a cache hit
        self.assertFalse(mock_create_from_img_dl.called)

        mock_handle_bootable.assert_called_once_with(
            self.ctxt,
            volume,
            image_id=image_id,
            image_meta=image_meta
        )

    @mock.patch('cinder.db.volume_update')
    @mock.patch('cinder.objects.Volume.get_by_id')
    @mock.patch('cinder.image.image_utils.qemu_img_info')
    def test_create_from_image_cache_miss(
            self, mock_qemu_info, mock_volume_get, mock_volume_update,
            mock_get_internal_context, mock_create_from_img_dl,
            mock_create_from_src, mock_handle_bootable, mock_fetch_img):
        mock_get_internal_context.return_value = self.ctxt
        mock_fetch_img.return_value = mock.MagicMock(
            spec=utils.get_file_spec())
        image_info = imageutils.QemuImgInfo()
        image_info.virtual_size = '2147483648'
        mock_qemu_info.return_value = image_info
        self.mock_driver.clone_image.return_value = (None, False)
        self.mock_cache.get_entry.return_value = None

        volume = fake_volume.fake_volume_obj(self.ctxt, size=10,
                                             host='foo@bar#pool')
        mock_volume_get.return_value = volume

        image_location = 'someImageLocationStr'
        image_id = fakes.IMAGE_ID
        image_meta = mock.MagicMock()

        manager = create_volume_manager.CreateVolumeFromSpecTask(
            self.mock_volume_manager,
            self.mock_db,
            self.mock_driver,
            image_volume_cache=self.mock_cache
        )

        manager._create_from_image(self.ctxt,
                                   volume,
                                   image_location,
                                   image_id,
                                   image_meta,
                                   self.mock_image_service)

        # Make sure clone_image is always called
        self.assertTrue(self.mock_driver.clone_image.called)

        # The image download should happen if clone fails and
        # we get a cache miss
        mock_create_from_img_dl.assert_called_once_with(
            self.ctxt,
            mock.ANY,
            image_location,
            image_id,
            self.mock_image_service
        )

        # The volume size should be reduced to virtual_size and then put back
        mock_volume_update.assert_any_call(self.ctxt, volume.id, {'size': 2})
        mock_volume_update.assert_any_call(self.ctxt, volume.id, {'size': 10})

        # Make sure created a new cache entry
        (self.mock_volume_manager.
            _create_image_cache_volume_entry.assert_called_once_with(
                self.ctxt, volume, image_id, image_meta))

        mock_handle_bootable.assert_called_once_with(
            self.ctxt,
            volume,
            image_id=image_id,
            image_meta=image_meta
        )

    @mock.patch('cinder.db.volume_update')
    @mock.patch('cinder.objects.Volume.get_by_id')
    @mock.patch('cinder.image.image_utils.qemu_img_info')
    def test_create_from_image_cache_miss_error_downloading(
            self, mock_qemu_info, mock_volume_get, mock_volume_update,
            mock_get_internal_context,
            mock_create_from_img_dl, mock_create_from_src,
            mock_handle_bootable, mock_fetch_img):
        mock_fetch_img.return_value = mock.MagicMock()
        image_info = imageutils.QemuImgInfo()
        image_info.virtual_size = '2147483648'
        mock_qemu_info.return_value = image_info
        self.mock_driver.clone_image.return_value = (None, False)
        self.mock_cache.get_entry.return_value = None

        volume = fake_volume.fake_volume_obj(self.ctxt, size=10,
                                             host='foo@bar#pool')
        mock_volume_get.return_value = volume

        mock_create_from_img_dl.side_effect = exception.CinderException()

        image_location = 'someImageLocationStr'
        image_id = fakes.IMAGE_ID
        image_meta = mock.MagicMock()

        manager = create_volume_manager.CreateVolumeFromSpecTask(
            self.mock_volume_manager,
            self.mock_db,
            self.mock_driver,
            image_volume_cache=self.mock_cache
        )

        self.assertRaises(
            exception.CinderException,
            manager._create_from_image,
            self.ctxt,
            volume,
            image_location,
            image_id,
            image_meta,
            self.mock_image_service
        )

        # Make sure clone_image is always called
        self.assertTrue(self.mock_driver.clone_image.called)

        # The image download should happen if clone fails and
        # we get a cache miss
        mock_create_from_img_dl.assert_called_once_with(
            self.ctxt,
            mock.ANY,
            image_location,
            image_id,
            self.mock_image_service
        )

        # The volume size should be reduced to virtual_size and then put back,
        # especially if there is an exception while creating the volume.
        self.assertEqual(2, mock_volume_update.call_count)
        mock_volume_update.assert_any_call(self.ctxt, volume.id, {'size': 2})
        mock_volume_update.assert_any_call(self.ctxt, volume.id, {'size': 10})

        # Make sure we didn't try and create a cache entry
        self.assertFalse(self.mock_cache.ensure_space.called)
        self.assertFalse(self.mock_cache.create_cache_entry.called)

    @mock.patch('cinder.image.image_utils.qemu_img_info')
    def test_create_from_image_no_internal_context(
            self, mock_qemu_info, mock_get_internal_context,
            mock_create_from_img_dl, mock_create_from_src,
            mock_handle_bootable, mock_fetch_img):
        self.mock_driver.clone_image.return_value = (None, False)
        mock_get_internal_context.return_value = None
        volume = fake_volume.fake_volume_obj(self.ctxt)
        image_info = imageutils.QemuImgInfo()
        image_info.virtual_size = '1073741824'
        mock_qemu_info.return_value = image_info

        image_location = 'someImageLocationStr'
        image_id = fakes.IMAGE_ID
        image_meta = {'virtual_size': '1073741824'}

        manager = create_volume_manager.CreateVolumeFromSpecTask(
            self.mock_volume_manager,
            self.mock_db,
            self.mock_driver,
            image_volume_cache=self.mock_cache
        )

        manager._create_from_image(self.ctxt,
                                   volume,
                                   image_location,
                                   image_id,
                                   image_meta,
                                   self.mock_image_service)

        # Make sure clone_image is always called
        self.assertTrue(self.mock_driver.clone_image.called)

        # Create from source shouldn't happen if cache cannot be used.
        self.assertFalse(mock_create_from_src.called)

        # The image download should happen if clone fails and we can't use the
        # image-volume cache due to not having an internal context available.
        mock_create_from_img_dl.assert_called_once_with(
            self.ctxt,
            volume,
            image_location,
            image_id,
            self.mock_image_service
        )

        # This should not attempt to use a minimal size volume
        self.assertFalse(self.mock_db.volume_update.called)

        # Make sure we didn't try and create a cache entry
        self.assertFalse(self.mock_cache.ensure_space.called)
        self.assertFalse(self.mock_cache.create_cache_entry.called)

        mock_handle_bootable.assert_called_once_with(
            self.ctxt,
            volume,
            image_id=image_id,
            image_meta=image_meta
        )

    @mock.patch('cinder.image.image_utils.qemu_img_info')
    def test_create_from_image_cache_miss_error_size_invalid(
            self, mock_qemu_info, mock_get_internal_context,
            mock_create_from_img_dl, mock_create_from_src,
            mock_handle_bootable, mock_fetch_img):
        mock_fetch_img.return_value = mock.MagicMock()
        image_info = imageutils.QemuImgInfo()
        image_info.virtual_size = '2147483648'
        mock_qemu_info.return_value = image_info
        self.mock_driver.clone_image.return_value = (None, False)
        self.mock_cache.get_entry.return_value = None

        volume = fake_volume.fake_volume_obj(self.ctxt, size=1,
                                             host='foo@bar#pool')
        image_volume = fake_volume.fake_db_volume(size=2)
        self.mock_db.volume_create.return_value = image_volume

        image_location = 'someImageLocationStr'
        image_id = fakes.IMAGE_ID
        image_meta = mock.MagicMock()

        manager = create_volume_manager.CreateVolumeFromSpecTask(
            self.mock_volume_manager,
            self.mock_db,
            self.mock_driver,
            image_volume_cache=self.mock_cache
        )

        self.assertRaises(
            exception.ImageUnacceptable,
            manager._create_from_image,
            self.ctxt,
            volume,
            image_location,
            image_id,
            image_meta,
            self.mock_image_service
        )

        # The volume size should NOT be changed when in this case
        self.assertFalse(self.mock_db.volume_update.called)

        # Make sure we didn't try and create a cache entry
        self.assertFalse(self.mock_cache.ensure_space.called)
        self.assertFalse(self.mock_cache.create_cache_entry.called)
