# Copyright (c) 2016 Clinton Knight
# All rights reserved.
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
import copy

import ddt
import mock

from cinder import exception
from cinder import test
from cinder.tests.unit.volume.drivers.netapp.dataontap.client import (
    fakes as fake_client)
import cinder.tests.unit.volume.drivers.netapp.dataontap.utils.fakes as fake
import cinder.tests.unit.volume.drivers.netapp.fakes as na_fakes
from cinder.volume.drivers.netapp.dataontap.utils import capabilities


@ddt.ddt
class CapabilitiesLibraryTestCase(test.TestCase):

    def setUp(self):
        super(CapabilitiesLibraryTestCase, self).setUp()

        self.zapi_client = mock.Mock()
        self.configuration = self.get_config_cmode()
        self.ssc_library = capabilities.CapabilitiesLibrary(
            'iSCSI', fake.SSC_VSERVER, self.zapi_client, self.configuration)
        self.ssc_library.ssc = fake.SSC

    def get_config_cmode(self):
        config = na_fakes.create_configuration_cmode()
        config.volume_backend_name = 'fake_backend'
        return config

    def test_check_api_permissions(self):

        mock_log = self.mock_object(capabilities.LOG, 'warning')

        self.ssc_library.check_api_permissions()

        self.zapi_client.check_cluster_api.assert_has_calls(
            [mock.call(*key) for key in capabilities.SSC_API_MAP.keys()])
        self.assertEqual(0, mock_log.call_count)

    def test_check_api_permissions_failed_ssc_apis(self):

        def check_cluster_api(object_name, operation_name, api):
            if api != 'volume-get-iter':
                return False
            return True

        self.zapi_client.check_cluster_api.side_effect = check_cluster_api
        mock_log = self.mock_object(capabilities.LOG, 'warning')

        self.ssc_library.check_api_permissions()

        self.assertEqual(1, mock_log.call_count)

    def test_check_api_permissions_failed_volume_api(self):

        def check_cluster_api(object_name, operation_name, api):
            if api == 'volume-get-iter':
                return False
            return True

        self.zapi_client.check_cluster_api.side_effect = check_cluster_api
        mock_log = self.mock_object(capabilities.LOG, 'warning')

        self.assertRaises(exception.VolumeBackendAPIException,
                          self.ssc_library.check_api_permissions)

        self.assertEqual(0, mock_log.call_count)

    def test_get_ssc(self):

        result = self.ssc_library.get_ssc()

        self.assertEqual(fake.SSC, result)
        self.assertIsNot(fake.SSC, result)

    def test_get_ssc_for_flexvol(self):

        result = self.ssc_library.get_ssc_for_flexvol(fake.SSC_VOLUMES[0])

        self.assertEqual(fake.SSC.get(fake.SSC_VOLUMES[0]), result)
        self.assertIsNot(fake.SSC.get(fake.SSC_VOLUMES[0]), result)

    def test_get_ssc_for_flexvol_not_found(self):

        result = self.ssc_library.get_ssc_for_flexvol('invalid')

        self.assertEqual({}, result)

    def test_get_ssc_aggregates(self):

        result = self.ssc_library.get_ssc_aggregates()

        self.assertEqual(list(fake.SSC_AGGREGATES), result)

    def test_update_ssc(self):

        mock_get_ssc_flexvol_info = self.mock_object(
            self.ssc_library, '_get_ssc_flexvol_info',
            mock.Mock(side_effect=[
                fake.SSC_FLEXVOL_INFO['volume1'],
                fake.SSC_FLEXVOL_INFO['volume2']
            ]))
        mock_get_ssc_dedupe_info = self.mock_object(
            self.ssc_library, '_get_ssc_dedupe_info',
            mock.Mock(side_effect=[
                fake.SSC_DEDUPE_INFO['volume1'],
                fake.SSC_DEDUPE_INFO['volume2']
            ]))
        mock_get_ssc_mirror_info = self.mock_object(
            self.ssc_library, '_get_ssc_mirror_info',
            mock.Mock(side_effect=[
                fake.SSC_MIRROR_INFO['volume1'],
                fake.SSC_MIRROR_INFO['volume2']
            ]))
        mock_get_ssc_aggregate_info = self.mock_object(
            self.ssc_library, '_get_ssc_aggregate_info',
            mock.Mock(side_effect=[
                fake.SSC_AGGREGATE_INFO['volume1'],
                fake.SSC_AGGREGATE_INFO['volume2']
            ]))
        ordered_ssc = collections.OrderedDict()
        ordered_ssc['volume1'] = fake.SSC_VOLUME_MAP['volume1']
        ordered_ssc['volume2'] = fake.SSC_VOLUME_MAP['volume2']

        result = self.ssc_library.update_ssc(ordered_ssc)

        self.assertIsNone(result)
        self.assertEqual(fake.SSC, self.ssc_library.ssc)
        mock_get_ssc_flexvol_info.assert_has_calls([
            mock.call('volume1'), mock.call('volume2')])
        mock_get_ssc_dedupe_info.assert_has_calls([
            mock.call('volume1'), mock.call('volume2')])
        mock_get_ssc_mirror_info.assert_has_calls([
            mock.call('volume1'), mock.call('volume2')])
        mock_get_ssc_aggregate_info.assert_has_calls([
            mock.call('aggr1'), mock.call('aggr2')])

    def test__update_for_failover(self):
        self.mock_object(self.ssc_library, 'update_ssc')
        flexvol_map = {'volume1': fake.SSC_VOLUME_MAP['volume1']}
        mock_client = mock.Mock(name='FAKE_ZAPI_CLIENT')

        self.ssc_library._update_for_failover(mock_client, flexvol_map)

        self.assertEqual(mock_client, self.ssc_library.zapi_client)
        self.ssc_library.update_ssc.assert_called_once_with(flexvol_map)

    @ddt.data({'lun_space_guarantee': True},
              {'lun_space_guarantee': False})
    @ddt.unpack
    def test_get_ssc_flexvol_info_thin_block(self, lun_space_guarantee):

        self.ssc_library.configuration.netapp_lun_space_reservation = \
            'enabled' if lun_space_guarantee else 'disabled'
        self.mock_object(self.ssc_library.zapi_client,
                         'get_flexvol',
                         mock.Mock(return_value=fake_client.VOLUME_INFO_SSC))

        result = self.ssc_library._get_ssc_flexvol_info(
            fake_client.VOLUME_NAMES[0])

        expected = {
            'netapp_thin_provisioned': 'true',
            'thick_provisioning_support': False,
            'thin_provisioning_support': True,
            'netapp_aggregate': 'fake_aggr1',
        }
        self.assertEqual(expected, result)
        self.zapi_client.get_flexvol.assert_called_once_with(
            flexvol_name=fake_client.VOLUME_NAMES[0])

    @ddt.data({'vol_space_guarantee': 'file', 'lun_space_guarantee': True},
              {'vol_space_guarantee': 'volume', 'lun_space_guarantee': True})
    @ddt.unpack
    def test_get_ssc_flexvol_info_thick_block(self, vol_space_guarantee,
                                              lun_space_guarantee):

        self.ssc_library.configuration.netapp_lun_space_reservation = \
            'enabled' if lun_space_guarantee else 'disabled'
        fake_volume_info_ssc = copy.deepcopy(fake_client.VOLUME_INFO_SSC)
        fake_volume_info_ssc['space-guarantee'] = vol_space_guarantee
        self.mock_object(self.ssc_library.zapi_client,
                         'get_flexvol',
                         mock.Mock(return_value=fake_volume_info_ssc))

        result = self.ssc_library._get_ssc_flexvol_info(
            fake_client.VOLUME_NAMES[0])

        expected = {
            'netapp_thin_provisioned': 'false',
            'thick_provisioning_support': lun_space_guarantee,
            'thin_provisioning_support': not lun_space_guarantee,
            'netapp_aggregate': 'fake_aggr1',
        }
        self.assertEqual(expected, result)
        self.zapi_client.get_flexvol.assert_called_once_with(
            flexvol_name=fake_client.VOLUME_NAMES[0])

    @ddt.data({'nfs_sparsed_volumes': True},
              {'nfs_sparsed_volumes': False})
    @ddt.unpack
    def test_get_ssc_flexvol_info_thin_file(self, nfs_sparsed_volumes):

        self.ssc_library.protocol = 'nfs'
        self.ssc_library.configuration.nfs_sparsed_volumes = \
            nfs_sparsed_volumes
        self.mock_object(self.ssc_library.zapi_client,
                         'get_flexvol',
                         mock.Mock(return_value=fake_client.VOLUME_INFO_SSC))

        result = self.ssc_library._get_ssc_flexvol_info(
            fake_client.VOLUME_NAMES[0])

        expected = {
            'netapp_thin_provisioned': 'true',
            'thick_provisioning_support': False,
            'thin_provisioning_support': True,
            'netapp_aggregate': 'fake_aggr1',
        }
        self.assertEqual(expected, result)
        self.zapi_client.get_flexvol.assert_called_once_with(
            flexvol_name=fake_client.VOLUME_NAMES[0])

    @ddt.data({'vol_space_guarantee': 'file', 'nfs_sparsed_volumes': True},
              {'vol_space_guarantee': 'volume', 'nfs_sparsed_volumes': False})
    @ddt.unpack
    def test_get_ssc_flexvol_info_thick_file(self, vol_space_guarantee,
                                             nfs_sparsed_volumes):

        self.ssc_library.protocol = 'nfs'
        self.ssc_library.configuration.nfs_sparsed_volumes = \
            nfs_sparsed_volumes
        fake_volume_info_ssc = copy.deepcopy(fake_client.VOLUME_INFO_SSC)
        fake_volume_info_ssc['space-guarantee'] = vol_space_guarantee
        self.mock_object(self.ssc_library.zapi_client,
                         'get_flexvol',
                         mock.Mock(return_value=fake_volume_info_ssc))

        result = self.ssc_library._get_ssc_flexvol_info(
            fake_client.VOLUME_NAMES[0])

        expected = {
            'netapp_thin_provisioned': 'false',
            'thick_provisioning_support': not nfs_sparsed_volumes,
            'thin_provisioning_support': nfs_sparsed_volumes,
            'netapp_aggregate': 'fake_aggr1',
        }
        self.assertEqual(expected, result)
        self.zapi_client.get_flexvol.assert_called_once_with(
            flexvol_name=fake_client.VOLUME_NAMES[0])

    def test_get_ssc_dedupe_info(self):

        self.mock_object(
            self.ssc_library.zapi_client, 'get_flexvol_dedupe_info',
            mock.Mock(return_value=fake_client.VOLUME_DEDUPE_INFO_SSC))

        result = self.ssc_library._get_ssc_dedupe_info(
            fake_client.VOLUME_NAMES[0])

        expected = {
            'netapp_dedup': 'true',
            'netapp_compression': 'false',
        }
        self.assertEqual(expected, result)
        self.zapi_client.get_flexvol_dedupe_info.assert_called_once_with(
            fake_client.VOLUME_NAMES[0])

    @ddt.data(True, False)
    def test_get_ssc_mirror_info(self, mirrored):

        self.mock_object(
            self.ssc_library.zapi_client, 'is_flexvol_mirrored',
            mock.Mock(return_value=mirrored))

        result = self.ssc_library._get_ssc_mirror_info(
            fake_client.VOLUME_NAMES[0])

        expected = {'netapp_mirrored': 'true' if mirrored else 'false'}
        self.assertEqual(expected, result)
        self.zapi_client.is_flexvol_mirrored.assert_called_once_with(
            fake_client.VOLUME_NAMES[0], fake.SSC_VSERVER)

    def test_get_ssc_aggregate_info(self):

        self.mock_object(
            self.ssc_library.zapi_client, 'get_aggregate',
            mock.Mock(return_value=fake_client.AGGR_INFO_SSC))
        self.mock_object(
            self.ssc_library.zapi_client, 'get_aggregate_disk_types',
            mock.Mock(return_value=fake_client.AGGREGATE_DISK_TYPES))

        result = self.ssc_library._get_ssc_aggregate_info(
            fake_client.VOLUME_AGGREGATE_NAME)

        expected = {
            'netapp_disk_type': fake_client.AGGREGATE_DISK_TYPES,
            'netapp_raid_type': fake_client.AGGREGATE_RAID_TYPE,
            'netapp_hybrid_aggregate': 'true',
        }
        self.assertEqual(expected, result)
        self.zapi_client.get_aggregate.assert_called_once_with(
            fake_client.VOLUME_AGGREGATE_NAME)
        self.zapi_client.get_aggregate_disk_types.assert_called_once_with(
            fake_client.VOLUME_AGGREGATE_NAME)

    def test_get_ssc_aggregate_info_not_found(self):

        self.mock_object(
            self.ssc_library.zapi_client, 'get_aggregate',
            mock.Mock(return_value={}))
        self.mock_object(
            self.ssc_library.zapi_client, 'get_aggregate_disk_types',
            mock.Mock(return_value=None))

        result = self.ssc_library._get_ssc_aggregate_info(
            fake_client.VOLUME_AGGREGATE_NAME)

        expected = {
            'netapp_disk_type': None,
            'netapp_raid_type': None,
            'netapp_hybrid_aggregate': None,
        }
        self.assertEqual(expected, result)

    def test_get_matching_flexvols_for_extra_specs(self):

        specs = {
            'thick_provisioning_support': '<is> False',
            'netapp_compression': 'true',
            'netapp_dedup': 'true',
            'netapp_mirrored': 'true',
            'netapp_raid_type': 'raid_dp',
            'netapp_disk_type': 'FCAL',
        }

        result = self.ssc_library.get_matching_flexvols_for_extra_specs(specs)

        self.assertEqual(['volume2'], result)

    def test_modify_extra_specs_for_comparison(self):

        specs = {
            'thick_provisioning_support': '<is> False',
            'thin_provisioning_support': '<is>  true',
            'netapp_compression': 'true',
        }

        result = self.ssc_library._modify_extra_specs_for_comparison(specs)

        expected = {
            'thick_provisioning_support': False,
            'thin_provisioning_support': True,
            'netapp_compression': 'true',
        }
        self.assertEqual(expected, result)
