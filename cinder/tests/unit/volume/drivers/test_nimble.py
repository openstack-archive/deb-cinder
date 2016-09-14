# Nimble Storage, Inc. (c) 2013-2014
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

import sys

import mock
from oslo_utils import units

from cinder import context
from cinder import exception
from cinder.objects import volume as obj_volume
from cinder import test
from cinder.tests.unit import fake_constants as fake
from cinder.volume.drivers import nimble
from cinder.volume import volume_types


NIMBLE_CLIENT = 'cinder.volume.drivers.nimble.client'
NIMBLE_URLLIB2 = 'six.moves.urllib.request'
NIMBLE_RANDOM = 'cinder.volume.drivers.nimble.random'
NIMBLE_ISCSI_DRIVER = 'cinder.volume.drivers.nimble.NimbleISCSIDriver'
DRIVER_VERSION = '3.0.0'

FAKE_ENUM_STRING = """
    <simpleType name="SmErrorType">
        <restriction base="xsd:string">
            <enumeration value="SM-ok"/><!-- enum const = 0 -->
            <enumeration value="SM-eperm"/><!-- enum const = 1 -->
            <enumeration value="SM-enoent"/><!-- enum const = 2 -->
            <enumeration value="SM-eaccess"/><!-- enum const = 13 -->
            <enumeration value="SM-eexist"/><!-- enum const = 17 -->
        </restriction>
    </simpleType>"""

FAKE_POSITIVE_LOGIN_RESPONSE_1 = {'err-list': {'err-list':
                                               [{'code': 0}]},
                                  'authInfo': {'sid': "a9b9aba7"}}

FAKE_POSITIVE_LOGIN_RESPONSE_2 = {'err-list': {'err-list':
                                               [{'code': 0}]},
                                  'authInfo': {'sid': "a9f3eba7"}}

FAKE_POSITIVE_NETCONFIG_RESPONSE = {
    'config': {'subnet-list': [{'label': "data1",
                               'subnet-id': {'type': 3},
                                'discovery-ip': "172.18.108.21"},
                               {'label': "mgmt-data",
                                'subnet-id':
                                {'type': 4},
                                'discovery-ip': "10.18.108.55"}]},
    'err-list': {'err-list': [{'code': 0}]}}

FAKE_NEGATIVE_NETCONFIG_RESPONSE = {'err-list': {'err-list':
                                                 [{'code': 13}]}}

FAKE_CREATE_VOLUME_POSITIVE_RESPONSE = {'err-list': {'err-list':
                                        [{'code': 0}]},
                                        'name': "openstack-test11"}

FAKE_CREATE_VOLUME_POSITIVE_RESPONSE_ENCRYPTION = {'err-list': {'err-list':
                                                   [{'code': 0}]},
                                                   'name':
                                                   "openstack-test-encryption"}

FAKE_CREATE_VOLUME_POSITIVE_RESPONSE_PERFPOLICY = {'err-list': {'err-list':
                                                   [{'code': 0}]},
                                                   'name':
                                                   "openstack-test-perfpolicy"}

FAKE_CREATE_VOLUME_NEGATIVE_RESPONSE = {'err-list': {'err-list':
                                                     [{'code': 17}]},
                                        'name': "openstack-test11"}

FAKE_CREATE_VOLUME_NEGATIVE_RESPONSE_ENCRYPTION = {'err-list': {'err-list':
                                                   [{'code': 17}]},
                                                   'name':
                                                   "openstack-test-encryption"}

FAKE_CREATE_VOLUME_NEGATIVE_RESPONSE_PERFPOLICY = {'err-list': {'err-list':
                                                   [{'code': 17}]},
                                                   'name':
                                                   "openstack-test-perfpolicy"}

FAKE_GENERIC_POSITIVE_RESPONSE = {'err-list': {'err-list':
                                               [{'code': 0}]}}

FAKE_POSITIVE_GROUP_CONFIG_RESPONSE = {
    'err-list': {'err-list': [{'code': 0}]},
    'info': {'usableCapacity': 8016883089408,
             'volUsageCompressed': 2938311843,
             'snapUsageCompressed': 36189,
             'unusedReserve': 0,
             'spaceInfoValid': True}}

FAKE_IGROUP_LIST_RESPONSE = {
    'err-list': {'err-list': [{'code': 0}]},
    'initiatorgrp-list': [
        {'initiator-list': [{'name': 'test-initiator1'},
                            {'name': 'test-initiator2'}],
         'name': 'test-igrp1'},
        {'initiator-list': [{'name': 'test-initiator1'}],
         'name': 'test-igrp2'}]}

FAKE_GET_VOL_INFO_RESPONSE = {
    'err-list': {'err-list': [{'code': 0}]},
    'vol': {'target-name': 'iqn.test',
            'name': 'test_vol',
            'agent-type': 1,
            'online': False}}

FAKE_GET_VOL_INFO_BACKUP_RESPONSE = {
    'err-list': {'err-list': [{'code': 0}]},
    'vol': {'target-name': 'iqn.test',
            'name': 'test_vol',
            'agent-type': 1,
            'clone': 1,
            'base-snap': 'test-backup-snap',
            'parent-vol': 'volume-' + fake.VOLUME2_ID,
            'online': False}}

FAKE_GET_SNAP_INFO_BACKUP_RESPONSE = {
    'err-list': {'err-list': [{'code': 0}]},
    'snap': {'description': "backup-vol-" + fake.VOLUME2_ID,
             'name': 'test-backup-snap',
             'vol': 'volume-' + fake.VOLUME_ID}
}

FAKE_GET_VOL_INFO_ONLINE = {
    'err-list': {'err-list': [{'code': 0}]},
    'vol': {'target-name': 'iqn.test',
            'name': 'test_vol',
            'agent-type': 1,
            'size': int(1.75 * units.Gi),
            'online': True}}

FAKE_GET_VOL_INFO_ERROR = {
    'err-list': {'err-list': [{'code': 2}]},
    'vol': {'target-name': 'iqn.test'}}

FAKE_GET_VOL_INFO_RESPONSE_WITH_SET_AGENT_TYPE = {
    'err-list': {'err-list': [{'code': 0}]},
    'vol': {'target-name': 'iqn.test',
            'name': 'test_vol',
            'agent-type': 5}}

FAKE_TYPE_ID = fake.VOLUME_TYPE_ID


def create_configuration(username, password, ip_address,
                         pool_name=None, subnet_label=None,
                         thin_provision=True):
    configuration = mock.Mock()
    configuration.san_login = username
    configuration.san_password = password
    configuration.san_ip = ip_address
    configuration.san_thin_provision = thin_provision
    configuration.nimble_pool_name = pool_name
    configuration.nimble_subnet_label = subnet_label
    configuration.safe_get.return_value = 'NIMBLE'
    return configuration


class NimbleDriverBaseTestCase(test.TestCase):

    """Base Class for the NimbleDriver Tests."""

    def setUp(self):
        super(NimbleDriverBaseTestCase, self).setUp()
        self.mock_client_service = None
        self.mock_client_class = None
        self.driver = None

    @staticmethod
    def client_mock_decorator(configuration):
        def client_mock_wrapper(func):
            def inner_client_mock(
                    self, mock_client_class, mock_urllib2, *args, **kwargs):
                self.mock_client_class = mock_client_class
                self.mock_client_service = mock.MagicMock(name='Client')
                self.mock_client_class.Client.return_value = \
                    self.mock_client_service
                mock_wsdl = mock_urllib2.urlopen.return_value
                mock_wsdl.read = mock.MagicMock()
                mock_wsdl.read.return_value = FAKE_ENUM_STRING
                self.driver = nimble.NimbleISCSIDriver(
                    configuration=configuration)
                self.mock_client_service.service.login.return_value = \
                    FAKE_POSITIVE_LOGIN_RESPONSE_1
                self.driver.do_setup(context.get_admin_context())
                func(self, *args, **kwargs)
            return inner_client_mock
        return client_mock_wrapper

    def tearDown(self):
        super(NimbleDriverBaseTestCase, self).tearDown()


class NimbleDriverLoginTestCase(NimbleDriverBaseTestCase):

    """Tests do_setup api."""

    @mock.patch(NIMBLE_URLLIB2)
    @mock.patch(NIMBLE_CLIENT)
    @mock.patch.object(obj_volume.VolumeList, 'get_all_by_host',
                       mock.Mock(return_value=[]))
    @NimbleDriverBaseTestCase.client_mock_decorator(create_configuration(
        'nimble', 'nimble_pass', '10.18.108.55', 'default', '*'))
    def test_do_setup_positive(self):
        expected_call_list = [
            mock.call.Client(
                'https://10.18.108.55/wsdl/NsGroupManagement.wsdl',
                username='nimble',
                password='nimble_pass')]
        self.assertEqual(self.mock_client_class.method_calls,
                         expected_call_list)
        expected_call_list = [mock.call.set_options(
            location='https://10.18.108.55:5391/soap'),
            mock.call.service.login(
                req={'username': 'nimble', 'password': 'nimble_pass'})]
        self.assertEqual(
            self.mock_client_service.method_calls,
            expected_call_list)

    @mock.patch(NIMBLE_URLLIB2)
    @mock.patch(NIMBLE_CLIENT)
    @mock.patch.object(obj_volume.VolumeList, 'get_all_by_host',
                       mock.Mock(return_value=[]))
    @NimbleDriverBaseTestCase.client_mock_decorator(create_configuration(
        'nimble', 'nimble_pass', '10.18.108.55', 'default', '*'))
    def test_expire_session_id(self):
        self.mock_client_service.service.login.return_value = \
            FAKE_POSITIVE_LOGIN_RESPONSE_2
        self.mock_client_service.service.getNetConfig = mock.MagicMock(
            side_effect=[
                FAKE_NEGATIVE_NETCONFIG_RESPONSE,
                FAKE_POSITIVE_NETCONFIG_RESPONSE])
        self.driver.APIExecutor.get_netconfig("active")
        expected_call_list = [mock.call.set_options(
            location='https://10.18.108.55:5391/soap'),
            mock.call.service.login(
                req={
                    'username': 'nimble', 'password': 'nimble_pass'}),
            mock.call.service.getNetConfig(
                request={'name': 'active',
                         'sid': 'a9b9aba7'}),
            mock.call.service.login(
                req={'username': 'nimble',
                     'password': 'nimble_pass'}),
            mock.call.service.getNetConfig(
                request={'name': 'active', 'sid': 'a9f3eba7'})]
        self.assertEqual(
            self.mock_client_service.method_calls,
            expected_call_list)


class NimbleDriverVolumeTestCase(NimbleDriverBaseTestCase):

    """Tests volume related api's."""

    @mock.patch(NIMBLE_URLLIB2)
    @mock.patch(NIMBLE_CLIENT)
    @mock.patch.object(obj_volume.VolumeList, 'get_all_by_host',
                       mock.Mock(return_value=[]))
    @mock.patch.object(volume_types, 'get_volume_type_extra_specs',
                       mock.Mock(type_id=FAKE_TYPE_ID, return_value={
                                 'nimble:perfpol-name': 'default',
                                 'nimble:encryption': 'yes'}))
    @NimbleDriverBaseTestCase.client_mock_decorator(create_configuration(
        'nimble', 'nimble_pass', '10.18.108.55', 'default', '*'))
    def test_create_volume_positive(self):
        self.mock_client_service.service.createVol.return_value = \
            FAKE_CREATE_VOLUME_POSITIVE_RESPONSE
        self.mock_client_service.service.getVolInfo.return_value = \
            FAKE_GET_VOL_INFO_RESPONSE
        self.mock_client_service.service.getNetConfig.return_value = \
            FAKE_POSITIVE_NETCONFIG_RESPONSE
        self.assertEqual({
            'provider_location': '172.18.108.21:3260 iqn.test 0',
            'provider_auth': None},
            self.driver.create_volume({'name': 'testvolume',
                                       'size': 1,
                                       'volume_type_id': None,
                                       'display_name': '',
                                       'display_description': ''}))
        self.mock_client_service.service.createVol.assert_called_once_with(
            request={
                'attr': {'snap-quota': sys.maxsize,
                         'warn-level': 858993459,
                         'name': 'testvolume', 'reserve': 0,
                         'online': True, 'pool-name': 'default',
                         'size': 1073741824, 'quota': 1073741824,
                         'perfpol-name': 'default', 'description': '',
                         'agent-type': 5, 'encryptionAttr': {'cipher': 3},
                         'multi-initiator': 'false'},
                'sid': 'a9b9aba7'})

    @mock.patch(NIMBLE_URLLIB2)
    @mock.patch(NIMBLE_CLIENT)
    @mock.patch.object(obj_volume.VolumeList, 'get_all_by_host',
                       mock.Mock(return_value=[]))
    @mock.patch.object(volume_types, 'get_volume_type_extra_specs',
                       mock.Mock(type_id=FAKE_TYPE_ID, return_value={
                           'nimble:perfpol-name': 'default',
                           'nimble:encryption': 'yes',
                           'nimble:multi-initiator': 'false'}))
    @NimbleDriverBaseTestCase.client_mock_decorator(create_configuration(
        'nimble', 'nimble_pass', '10.18.108.55', 'default', '*'))
    def test_create_volume_encryption_positive(self):
        self.mock_client_service.service.createVol.return_value = \
            FAKE_CREATE_VOLUME_POSITIVE_RESPONSE_ENCRYPTION
        self.mock_client_service.service.getVolInfo.return_value = \
            FAKE_GET_VOL_INFO_RESPONSE
        self.mock_client_service.service.getNetConfig.return_value = \
            FAKE_POSITIVE_NETCONFIG_RESPONSE

        self.assertEqual({
            'provider_location': '172.18.108.21:3260 iqn.test 0',
            'provider_auth': None},
            self.driver.create_volume({'name': 'testvolume-encryption',
                                       'size': 1,
                                       'volume_type_id': FAKE_TYPE_ID,
                                       'display_name': '',
                                       'display_description': ''}))

        mock_volume_type = volume_types.get_volume_type_extra_specs
        mock_volume_type.assert_called_once_with(FAKE_TYPE_ID)

        self.mock_client_service.service.createVol.assert_called_once_with(
            request={
                'attr': {'snap-quota': sys.maxsize,
                         'warn-level': 858993459,
                         'name': 'testvolume-encryption', 'reserve': 0,
                         'online': True, 'pool-name': 'default',
                         'size': 1073741824, 'quota': 1073741824,
                         'perfpol-name': 'default', 'description': '',
                         'agent-type': 5, 'encryptionAttr': {'cipher': 2},
                         'multi-initiator': 'false'},
                'sid': 'a9b9aba7'})

    @mock.patch(NIMBLE_URLLIB2)
    @mock.patch(NIMBLE_CLIENT)
    @mock.patch.object(obj_volume.VolumeList, 'get_all_by_host',
                       mock.Mock(return_value=[]))
    @mock.patch.object(volume_types, 'get_volume_type_extra_specs',
                       mock.Mock(type_id=FAKE_TYPE_ID, return_value={
                           'nimble:perfpol-name': 'VMware ESX',
                           'nimble:encryption': 'no',
                           'nimble:multi-initiator': 'false'}))
    @NimbleDriverBaseTestCase.client_mock_decorator(create_configuration(
        'nimble', 'nimble_pass', '10.18.108.55', 'default', '*'))
    def test_create_volume_perfpolicy_positive(self):
        self.mock_client_service.service.createVol.return_value = \
            FAKE_CREATE_VOLUME_POSITIVE_RESPONSE_PERFPOLICY
        self.mock_client_service.service.getVolInfo.return_value = \
            FAKE_GET_VOL_INFO_RESPONSE
        self.mock_client_service.service.getNetConfig.return_value = \
            FAKE_POSITIVE_NETCONFIG_RESPONSE

        self.assertEqual(
            {'provider_location': '172.18.108.21:3260 iqn.test 0',
             'provider_auth': None},
            self.driver.create_volume({'name': 'testvolume-perfpolicy',
                                       'size': 1,
                                       'volume_type_id': FAKE_TYPE_ID,
                                       'display_name': '',
                                       'display_description': ''}))

        mock_volume_type = volume_types.get_volume_type_extra_specs
        mock_volume_type.assert_called_once_with(FAKE_TYPE_ID)

        self.mock_client_service.service.createVol.assert_called_once_with(
            request={
                'attr': {'snap-quota': sys.maxsize,
                         'warn-level': 858993459,
                         'name': 'testvolume-perfpolicy', 'reserve': 0,
                         'online': True, 'pool-name': 'default',
                         'size': 1073741824, 'quota': 1073741824,
                         'perfpol-name': 'VMware ESX', 'description': '',
                         'agent-type': 5, 'encryptionAttr': {'cipher': 3},
                         'multi-initiator': 'false'},
                'sid': 'a9b9aba7'})

    @mock.patch(NIMBLE_URLLIB2)
    @mock.patch(NIMBLE_CLIENT)
    @mock.patch.object(obj_volume.VolumeList, 'get_all_by_host',
                       mock.Mock(return_value=[]))
    @mock.patch.object(volume_types, 'get_volume_type_extra_specs',
                       mock.Mock(type_id=FAKE_TYPE_ID, return_value={
                           'nimble:perfpol-name': 'default',
                           'nimble:encryption': 'no',
                           'nimble:multi-initiator': 'true'}))
    @NimbleDriverBaseTestCase.client_mock_decorator(create_configuration(
        'nimble', 'nimble_pass', '10.18.108.55', 'default', '*'))
    def test_create_volume_multi_initiator_positive(self):
        self.mock_client_service.service.createVol.return_value = \
            FAKE_CREATE_VOLUME_POSITIVE_RESPONSE_PERFPOLICY
        self.mock_client_service.service.getVolInfo.return_value = \
            FAKE_GET_VOL_INFO_RESPONSE
        self.mock_client_service.service.getNetConfig.return_value = \
            FAKE_POSITIVE_NETCONFIG_RESPONSE

        self.assertEqual(
            {'provider_location': '172.18.108.21:3260 iqn.test 0',
             'provider_auth': None},
            self.driver.create_volume({'name': 'testvolume-perfpolicy',
                                       'size': 1,
                                       'volume_type_id': FAKE_TYPE_ID,
                                       'display_name': '',
                                       'display_description': ''}))

        mock_volume_type = volume_types.get_volume_type_extra_specs
        mock_volume_type.assert_called_once_with(FAKE_TYPE_ID)

        self.mock_client_service.service.createVol.assert_called_once_with(
            request={
                'attr': {'snap-quota': sys.maxsize,
                         'warn-level': 858993459,
                         'name': 'testvolume-perfpolicy', 'reserve': 0,
                         'online': True, 'pool-name': 'default',
                         'size': 1073741824, 'quota': 1073741824,
                         'perfpol-name': 'default', 'description': '',
                         'agent-type': 5, 'encryptionAttr': {'cipher': 3},
                         'multi-initiator': 'true'},
                'sid': 'a9b9aba7'})

    @mock.patch(NIMBLE_URLLIB2)
    @mock.patch(NIMBLE_CLIENT)
    @mock.patch.object(obj_volume.VolumeList, 'get_all_by_host',
                       mock.Mock(return_value=[]))
    @NimbleDriverBaseTestCase.client_mock_decorator(create_configuration(
        'nimble', 'nimble_pass', '10.18.108.55', 'default', '*'))
    def test_create_volume_negative(self):
        self.mock_client_service.service.createVol.return_value = \
            FAKE_CREATE_VOLUME_NEGATIVE_RESPONSE
        self.assertRaises(
            exception.VolumeBackendAPIException,
            self.driver.create_volume,
            {'name': 'testvolume',
             'size': 1,
             'volume_type_id': None,
             'display_name': '',
             'display_description': ''})

    @mock.patch(NIMBLE_URLLIB2)
    @mock.patch(NIMBLE_CLIENT)
    @mock.patch.object(obj_volume.VolumeList, 'get_all_by_host',
                       mock.Mock(return_value=[]))
    @NimbleDriverBaseTestCase.client_mock_decorator(create_configuration(
        'nimble', 'nimble_pass', '10.18.108.55', 'default', '*'))
    def test_create_volume_encryption_negative(self):
        self.mock_client_service.service.createVol.return_value = \
            FAKE_CREATE_VOLUME_NEGATIVE_RESPONSE_ENCRYPTION
        self.assertRaises(
            exception.VolumeBackendAPIException,
            self.driver.create_volume,
            {'name': 'testvolume-encryption',
             'size': 1,
             'volume_type_id': None,
             'display_name': '',
             'display_description': ''})

    @mock.patch(NIMBLE_URLLIB2)
    @mock.patch(NIMBLE_CLIENT)
    @mock.patch.object(obj_volume.VolumeList, 'get_all_by_host',
                       mock.Mock(return_value=[]))
    @NimbleDriverBaseTestCase.client_mock_decorator(create_configuration(
        'nimble', 'nimble_pass', '10.18.108.55', 'default', '*'))
    def test_create_volume_perfpolicy_negative(self):
        self.mock_client_service.service.createVol.return_value = \
            FAKE_CREATE_VOLUME_NEGATIVE_RESPONSE_PERFPOLICY
        self.assertRaises(
            exception.VolumeBackendAPIException,
            self.driver.create_volume,
            {'name': 'testvolume-perfpolicy',
             'size': 1,
             'volume_type_id': None,
             'display_name': '',
             'display_description': ''})

    @mock.patch(NIMBLE_URLLIB2)
    @mock.patch(NIMBLE_CLIENT)
    @mock.patch.object(obj_volume.VolumeList, 'get_all_by_host',
                       mock.Mock(return_value=[]))
    @NimbleDriverBaseTestCase.client_mock_decorator(create_configuration(
        'nimble', 'nimble_pass', '10.18.108.55', 'default', '*'))
    @mock.patch(NIMBLE_ISCSI_DRIVER + ".is_volume_backup_clone", mock.Mock(
        return_value = ['', '']))
    def test_delete_volume(self):
        self.mock_client_service.service.onlineVol.return_value = \
            FAKE_GENERIC_POSITIVE_RESPONSE
        self.mock_client_service.service.deleteVol.return_value = \
            FAKE_GENERIC_POSITIVE_RESPONSE
        self.mock_client_service.service.dissocProtPol.return_value = \
            FAKE_GENERIC_POSITIVE_RESPONSE
        self.driver.delete_volume({'name': 'testvolume'})
        expected_calls = [mock.call.service.onlineVol(
            request={
                'online': False, 'name': 'testvolume', 'sid': 'a9b9aba7'}),
            mock.call.service.dissocProtPol(
                request={'vol-name': 'testvolume', 'sid': 'a9b9aba7'}),
            mock.call.service.deleteVol(
                request={'name': 'testvolume', 'sid': 'a9b9aba7'})]
        self.mock_client_service.assert_has_calls(expected_calls)

    @mock.patch(NIMBLE_URLLIB2)
    @mock.patch(NIMBLE_CLIENT)
    @NimbleDriverBaseTestCase.client_mock_decorator(create_configuration(
        'nimble', 'nimble_pass', '10.18.108.55', 'default', '*'))
    @mock.patch(NIMBLE_ISCSI_DRIVER + ".is_volume_backup_clone", mock.Mock(
        return_value=['test-backup-snap', 'volume-' + fake.VOLUME_ID]))
    @mock.patch.object(obj_volume.VolumeList, 'get_all_by_host')
    def test_delete_volume_with_backup(self, mock_volume_list):
        mock_volume_list.return_value = []
        self.mock_client_service.service.onlineVol.return_value = \
            FAKE_GENERIC_POSITIVE_RESPONSE
        self.mock_client_service.service.deleteVol.return_value = \
            FAKE_GENERIC_POSITIVE_RESPONSE
        self.mock_client_service.service.dissocProtPol.return_value = \
            FAKE_GENERIC_POSITIVE_RESPONSE
        self.mock_client_service.service.onlineSnap.return_value = \
            FAKE_GENERIC_POSITIVE_RESPONSE
        self.mock_client_service.service.deleteSnap.return_value = \
            FAKE_GENERIC_POSITIVE_RESPONSE

        self.driver.delete_volume({'name': 'testvolume'})
        expected_calls = [mock.call.service.onlineVol(
            request={
                'online': False, 'name': 'testvolume', 'sid': 'a9b9aba7'}),
            mock.call.service.dissocProtPol(
                request={'vol-name': 'testvolume', 'sid': 'a9b9aba7'}),
            mock.call.service.deleteVol(
                request={'name': 'testvolume', 'sid': 'a9b9aba7'}),
            mock.call.service.onlineSnap(
                request={'vol': 'volume-' + fake.VOLUME_ID,
                         'name': 'test-backup-snap',
                         'online': False,
                         'sid': 'a9b9aba7'}),
            mock.call.service.deleteSnap(
                request={'vol': 'volume-' + fake.VOLUME_ID,
                         'name': 'test-backup-snap',
                         'sid': 'a9b9aba7'})]

        self.mock_client_service.assert_has_calls(expected_calls)

    @mock.patch(NIMBLE_URLLIB2)
    @mock.patch(NIMBLE_CLIENT)
    @mock.patch.object(obj_volume.VolumeList, 'get_all_by_host',
                       mock.Mock(return_value=[]))
    @NimbleDriverBaseTestCase.client_mock_decorator(create_configuration(
        'nimble', 'nimble_pass', '10.18.108.55', 'default', '*'))
    def test_extend_volume(self):
        self.mock_client_service.service.editVol.return_value = \
            FAKE_CREATE_VOLUME_POSITIVE_RESPONSE
        self.driver.extend_volume({'name': 'testvolume'}, 5)
        self.mock_client_service.service.editVol.assert_called_once_with(
            request={'attr': {'size': 5368709120,
                              'snap-quota': sys.maxsize,
                              'warn-level': 4294967296,
                              'reserve': 0,
                              'quota': 5368709120},
                     'mask': 884,
                     'name': 'testvolume',
                     'sid': 'a9b9aba7'})

    @mock.patch(NIMBLE_URLLIB2)
    @mock.patch(NIMBLE_CLIENT)
    @mock.patch.object(volume_types, 'get_volume_type_extra_specs',
                       mock.Mock(type_id=FAKE_TYPE_ID,
                                 return_value=
                                 {'nimble:perfpol-name': 'default',
                                  'nimble:encryption': 'yes',
                                  'nimble:multi-initiator': 'false'}))
    @NimbleDriverBaseTestCase.client_mock_decorator(create_configuration(
        'nimble', 'nimble_pass', '10.18.108.55', 'default', '*', False))
    @mock.patch.object(obj_volume.VolumeList, 'get_all_by_host')
    @mock.patch(NIMBLE_RANDOM)
    def test_create_cloned_volume(self, mock_random, mock_volume_list):
        mock_random.sample.return_value = fake.VOLUME_ID
        mock_volume_list.return_value = []
        self.mock_client_service.service.snapVol.return_value = \
            FAKE_GENERIC_POSITIVE_RESPONSE
        self.mock_client_service.service.cloneVol.return_value = \
            FAKE_GENERIC_POSITIVE_RESPONSE
        self.mock_client_service.service.getVolInfo.return_value = \
            FAKE_GET_VOL_INFO_RESPONSE
        self.mock_client_service.service.getNetConfig.return_value = \
            FAKE_POSITIVE_NETCONFIG_RESPONSE

        volume = obj_volume.Volume(context.get_admin_context(),
                                   id=fake.VOLUME_ID,
                                   size=5.0,
                                   _name_id=None,
                                   display_name='',
                                   volume_type_id=FAKE_TYPE_ID
                                   )
        src_volume = obj_volume.Volume(context.get_admin_context(),
                                       id=fake.VOLUME2_ID,
                                       _name_id=None,
                                       size=5.0)
        self.assertEqual({
            'provider_location': '172.18.108.21:3260 iqn.test 0',
            'provider_auth': None},
            self.driver.create_cloned_volume(volume, src_volume))
        expected_calls = [mock.call.service.snapVol(
            request={
                'vol': "volume-" + fake.VOLUME2_ID,
                'snapAttr': {'name': 'openstack-clone-volume-' +
                                     fake.VOLUME_ID +
                             "-" + fake.VOLUME_ID,
                             'description': ''},
                'sid': 'a9b9aba7'}),
            mock.call.service.cloneVol(
                request={
                    'snap-name': 'openstack-clone-volume-' + fake.VOLUME_ID +
                                 "-" + fake.VOLUME_ID,
                    'attr': {'snap-quota': sys.maxsize,
                             'name': 'volume-' + fake.VOLUME_ID,
                             'quota': 5368709120,
                             'reserve': 5368709120,
                             'online': True,
                             'warn-level': 4294967296,
                             'encryptionAttr': {'cipher': 2},
                             'multi-initiator': 'false',
                             'perfpol-name': 'default',
                             'agent-type': 5},
                    'name': 'volume-' + fake.VOLUME2_ID,
                    'sid': 'a9b9aba7'})]
        self.mock_client_service.assert_has_calls(expected_calls)

    @mock.patch(NIMBLE_URLLIB2)
    @mock.patch(NIMBLE_CLIENT)
    @mock.patch.object(obj_volume.VolumeList, 'get_all_by_host',
                       mock.Mock(return_value=[]))
    @NimbleDriverBaseTestCase.client_mock_decorator(create_configuration(
        'nimble', 'nimble_pass', '10.18.108.55', 'default', '*'))
    def test_manage_volume_positive(self):
        self.mock_client_service.service.getNetConfig.return_value = (
            FAKE_POSITIVE_NETCONFIG_RESPONSE)
        self.mock_client_service.service.getVolInfo.return_value = (
            FAKE_GET_VOL_INFO_RESPONSE)
        self.mock_client_service.service.onlineVol.return_value = (
            FAKE_GENERIC_POSITIVE_RESPONSE)
        self.mock_client_service.service.editVol.return_value = (
            FAKE_CREATE_VOLUME_POSITIVE_RESPONSE)
        self.assertEqual({
            'provider_location': '172.18.108.21:3260 iqn.test 0',
            'provider_auth': None},
            self.driver.manage_existing({'name': 'volume-abcdef'},
                                        {'source-name': 'test-vol'}))
        expected_calls = [
            mock.call.service.editVol(
                request={
                    'attr': {
                        'name': 'volume-abcdef', 'agent-type': 5},
                    'mask': 262145,
                    'name': 'test-vol',
                    'sid': 'a9b9aba7'}),
            mock.call.service.onlineVol(
                request={'online': True,
                         'name': 'volume-abcdef',
                         'sid': 'a9b9aba7'}
            )
        ]
        self.mock_client_service.assert_has_calls(expected_calls)

    @mock.patch(NIMBLE_URLLIB2)
    @mock.patch(NIMBLE_CLIENT)
    @mock.patch.object(obj_volume.VolumeList, 'get_all_by_host',
                       mock.Mock(return_value=[]))
    @NimbleDriverBaseTestCase.client_mock_decorator(create_configuration(
        'nimble', 'nimble_pass', '10.18.108.55', 'default', '*'))
    def test_manage_volume_which_is_online(self):
        self.mock_client_service.service.getNetConfig.return_value = (
            FAKE_POSITIVE_NETCONFIG_RESPONSE)
        self.mock_client_service.service.getVolInfo.return_value = (
            FAKE_GET_VOL_INFO_ONLINE)
        self.assertRaises(
            exception.InvalidVolume,
            self.driver.manage_existing,
            {'name': 'volume-abcdef'},
            {'source-name': 'test-vol'})

    @mock.patch(NIMBLE_URLLIB2)
    @mock.patch(NIMBLE_CLIENT)
    @mock.patch.object(obj_volume.VolumeList, 'get_all_by_host',
                       mock.Mock(return_value=[]))
    @NimbleDriverBaseTestCase.client_mock_decorator(create_configuration(
        'nimble', 'nimble_pass', '10.18.108.55', 'default', '*'))
    def test_manage_volume_get_size(self):
        self.mock_client_service.service.getNetConfig.return_value = (
            FAKE_POSITIVE_NETCONFIG_RESPONSE)
        self.mock_client_service.service.getVolInfo.return_value = (
            FAKE_GET_VOL_INFO_ONLINE)
        size = self.driver.manage_existing_get_size(
            {'name': 'volume-abcdef'}, {'source-name': 'test-vol'})
        self.assertEqual(2, size)

    @mock.patch(NIMBLE_URLLIB2)
    @mock.patch(NIMBLE_CLIENT)
    @mock.patch.object(obj_volume.VolumeList, 'get_all',
                       mock.Mock(return_value=[]))
    @NimbleDriverBaseTestCase.client_mock_decorator(create_configuration(
        'nimble', 'nimble_pass', '10.18.108.55', 'default', '*'))
    def test_manage_volume_with_improper_ref(self):
        self.assertRaises(
            exception.ManageExistingInvalidReference,
            self.driver.manage_existing,
            {'name': 'volume-abcdef'},
            {'source-id': 'test-vol'})

    @mock.patch(NIMBLE_URLLIB2)
    @mock.patch(NIMBLE_CLIENT)
    @mock.patch.object(obj_volume.VolumeList, 'get_all_by_host',
                       mock.Mock(return_value=[]))
    @NimbleDriverBaseTestCase.client_mock_decorator(create_configuration(
        'nimble', 'nimble_pass', '10.18.108.55', 'default', '*'))
    def test_manage_volume_with_nonexistant_volume(self):
        self.mock_client_service.service.getVolInfo.return_value = (
            FAKE_GET_VOL_INFO_ERROR)
        self.assertRaises(
            exception.VolumeBackendAPIException,
            self.driver.manage_existing,
            {'name': 'volume-abcdef'},
            {'source-name': 'test-vol'})

    @mock.patch(NIMBLE_URLLIB2)
    @mock.patch(NIMBLE_CLIENT)
    @mock.patch.object(obj_volume.VolumeList, 'get_all_by_host',
                       mock.Mock(return_value=[]))
    @NimbleDriverBaseTestCase.client_mock_decorator(create_configuration(
        'nimble', 'nimble_pass', '10.18.108.55', 'default', '*'))
    def test_manage_volume_with_wrong_agent_type(self):
        self.mock_client_service.service.getVolInfo.return_value = (
            FAKE_GET_VOL_INFO_RESPONSE_WITH_SET_AGENT_TYPE)
        self.assertRaises(
            exception.ManageExistingAlreadyManaged,
            self.driver.manage_existing,
            {'id': 'abcdef', 'name': 'volume-abcdef'},
            {'source-name': 'test-vol'})

    @mock.patch(NIMBLE_URLLIB2)
    @mock.patch(NIMBLE_CLIENT)
    @mock.patch.object(obj_volume.VolumeList, 'get_all_by_host',
                       mock.Mock(return_value=[]))
    @NimbleDriverBaseTestCase.client_mock_decorator(create_configuration(
        'nimble', 'nimble_pass', '10.18.108.55', 'default', '*'))
    def test_unmanage_volume_positive(self):
        self.mock_client_service.service.getVolInfo.return_value = (
            FAKE_GET_VOL_INFO_RESPONSE_WITH_SET_AGENT_TYPE)
        self.mock_client_service.service.editVol.return_value = (
            FAKE_CREATE_VOLUME_POSITIVE_RESPONSE)
        self.driver.unmanage({'name': 'volume-abcdef'})
        expected_calls = [
            mock.call.service.editVol(
                request={'attr': {'agent-type': 1},
                         'mask': 262144,
                         'name': 'volume-abcdef',
                         'sid': 'a9b9aba7'}),
            mock.call.service.onlineVol(
                request={'online': False,
                         'name': 'volume-abcdef',
                         'sid': 'a9b9aba7'}
            )
        ]
        self.mock_client_service.assert_has_calls(expected_calls)

    @mock.patch(NIMBLE_URLLIB2)
    @mock.patch(NIMBLE_CLIENT)
    @mock.patch.object(obj_volume.VolumeList, 'get_all_by_host',
                       mock.Mock(return_value=[]))
    @NimbleDriverBaseTestCase.client_mock_decorator(create_configuration(
        'nimble', 'nimble_pass', '10.18.108.55', 'default', '*'))
    def test_unmanage_with_invalid_volume(self):
        self.mock_client_service.service.getVolInfo.return_value = (
            FAKE_GET_VOL_INFO_ERROR)
        self.assertRaises(
            exception.VolumeBackendAPIException,
            self.driver.unmanage,
            {'name': 'volume-abcdef'}
        )

    @mock.patch(NIMBLE_URLLIB2)
    @mock.patch(NIMBLE_CLIENT)
    @mock.patch.object(obj_volume.VolumeList, 'get_all_by_host',
                       mock.Mock(return_value=[]))
    @NimbleDriverBaseTestCase.client_mock_decorator(create_configuration(
        'nimble', 'nimble_pass', '10.18.108.55', 'default', '*'))
    def test_unmanage_with_invalid_agent_type(self):
        self.mock_client_service.service.getVolInfo.return_value = (
            FAKE_GET_VOL_INFO_RESPONSE)
        self.assertRaises(
            exception.InvalidVolume,
            self.driver.unmanage,
            {'name': 'volume-abcdef'}
        )

    @mock.patch(NIMBLE_URLLIB2)
    @mock.patch(NIMBLE_CLIENT)
    @mock.patch.object(obj_volume.VolumeList, 'get_all_by_host',
                       mock.Mock(return_value=[]))
    @NimbleDriverBaseTestCase.client_mock_decorator(create_configuration(
        'nimble', 'nimble_pass', '10.18.108.55', 'default', '*'))
    def test_get_volume_stats(self):
        self.mock_client_service.service.getGroupConfig.return_value = \
            FAKE_POSITIVE_GROUP_CONFIG_RESPONSE
        expected_res = {'driver_version': DRIVER_VERSION,
                        'vendor_name': 'Nimble',
                        'volume_backend_name': 'NIMBLE',
                        'storage_protocol': 'iSCSI',
                        'pools': [{'pool_name': 'NIMBLE',
                                   'total_capacity_gb': 7466.30419921875,
                                   'free_capacity_gb': 7463.567649364471,
                                   'reserved_percentage': 0,
                                   'QoS_support': False}]}
        self.assertEqual(
            expected_res,
            self.driver.get_volume_stats(refresh=True))

    @mock.patch(NIMBLE_URLLIB2)
    @mock.patch(NIMBLE_CLIENT)
    @mock.patch.object(obj_volume.VolumeList, 'get_all_by_host',
                       mock.Mock(return_value=[]))
    @NimbleDriverBaseTestCase.client_mock_decorator(create_configuration(
        'nimble', 'nimble_pass', '10.18.108.55', 'default', '*'))
    def test_is_volume_backup_clone(self):
        self.mock_client_service.service.getVolInfo.return_value = \
            FAKE_GET_VOL_INFO_BACKUP_RESPONSE
        self.mock_client_service.service.getSnapInfo.return_value = \
            FAKE_GET_SNAP_INFO_BACKUP_RESPONSE
        volume = obj_volume.Volume(context.get_admin_context(),
                                   id=fake.VOLUME_ID,
                                   _name_id=None)
        self.assertEqual(("test-backup-snap", "volume-" + fake.VOLUME_ID),
                         self.driver.is_volume_backup_clone(volume))
        expected_calls = [
            mock.call.service.getVolInfo(
                request={'name': 'volume-' + fake.VOLUME_ID,
                         'sid': 'a9b9aba7'}),
            mock.call.service.getSnapInfo(
                request={'sid': 'a9b9aba7',
                         'vol': 'volume-' + fake.VOLUME2_ID,
                         'name': 'test-backup-snap'})
        ]
        self.mock_client_service.assert_has_calls(expected_calls)


class NimbleDriverSnapshotTestCase(NimbleDriverBaseTestCase):

    """Tests snapshot related api's."""

    @mock.patch(NIMBLE_URLLIB2)
    @mock.patch(NIMBLE_CLIENT)
    @mock.patch.object(obj_volume.VolumeList, 'get_all_by_host',
                       mock.Mock(return_value=[]))
    @NimbleDriverBaseTestCase.client_mock_decorator(create_configuration(
        'nimble', 'nimble_pass', '10.18.108.55', 'default', '*'))
    def test_create_snapshot(self):
        self.mock_client_service.service.snapVol.return_value = \
            FAKE_GENERIC_POSITIVE_RESPONSE
        self.driver.create_snapshot(
            {'volume_name': 'testvolume',
             'name': 'testvolume-snap1',
             'display_name': '',
             'display_description': ''})
        self.mock_client_service.service.snapVol.assert_called_once_with(
            request={'vol': 'testvolume',
                     'snapAttr': {'name': 'testvolume-snap1',
                                  'description':
                                  ''
                                  },
                     'sid': 'a9b9aba7'})

    @mock.patch(NIMBLE_URLLIB2)
    @mock.patch(NIMBLE_CLIENT)
    @mock.patch.object(obj_volume.VolumeList, 'get_all_by_host',
                       mock.Mock(return_value=[]))
    @NimbleDriverBaseTestCase.client_mock_decorator(create_configuration(
        'nimble', 'nimble_pass', '10.18.108.55', 'default', '*'))
    def test_delete_snapshot(self):
        self.mock_client_service.service.onlineSnap.return_value = \
            FAKE_GENERIC_POSITIVE_RESPONSE
        self.mock_client_service.service.deleteSnap.return_value = \
            FAKE_GENERIC_POSITIVE_RESPONSE
        self.driver.delete_snapshot(
            {'volume_name': 'testvolume',
             'name': 'testvolume-snap1'})
        expected_calls = [mock.call.service.onlineSnap(
            request={
                'vol': 'testvolume',
                'online': False,
                'name': 'testvolume-snap1',
                'sid': 'a9b9aba7'}),
            mock.call.service.deleteSnap(request={'vol': 'testvolume',
                                                  'name': 'testvolume-snap1',
                                                  'sid': 'a9b9aba7'})]
        self.mock_client_service.assert_has_calls(expected_calls)

    @mock.patch(NIMBLE_URLLIB2)
    @mock.patch(NIMBLE_CLIENT)
    @mock.patch.object(obj_volume.VolumeList, 'get_all_by_host',
                       mock.Mock(return_value=[]))
    @mock.patch.object(volume_types, 'get_volume_type_extra_specs',
                       mock.Mock(type_id=FAKE_TYPE_ID, return_value={
                                 'nimble:perfpol-name': 'default',
                                 'nimble:encryption': 'yes',
                                 'nimble:multi-initiator': 'false'}))
    @NimbleDriverBaseTestCase.client_mock_decorator(create_configuration(
        'nimble', 'nimble_pass', '10.18.108.55', 'default', '*'))
    def test_create_volume_from_snapshot(self):
        self.mock_client_service.service.cloneVol.return_value = \
            FAKE_GENERIC_POSITIVE_RESPONSE
        self.mock_client_service.service.getVolInfo.return_value = \
            FAKE_GET_VOL_INFO_RESPONSE
        self.mock_client_service.service.getNetConfig.return_value = \
            FAKE_POSITIVE_NETCONFIG_RESPONSE
        self.assertEqual({
            'provider_location': '172.18.108.21:3260 iqn.test 0',
            'provider_auth': None},
            self.driver.create_volume_from_snapshot(
                {'name': 'clone-testvolume',
                 'size': 2,
                 'volume_type_id': FAKE_TYPE_ID},
                {'volume_name': 'testvolume',
                 'name': 'testvolume-snap1',
                 'volume_size': 1}))
        expected_calls = [
            mock.call.service.cloneVol(
                request={'snap-name': 'testvolume-snap1',
                         'attr': {'snap-quota': sys.maxsize,
                                  'name': 'clone-testvolume',
                                  'quota': 1073741824,
                                  'online': True,
                                  'reserve': 0,
                                  'warn-level': 858993459,
                                  'perfpol-name': 'default',
                                  'encryptionAttr': {'cipher': 2},
                                  'multi-initiator': 'false',
                                  'agent-type': 5},
                         'name': 'testvolume',
                         'sid': 'a9b9aba7'}),
            mock.call.service.editVol(
                request={'attr': {'size': 2147483648,
                                  'snap-quota': sys.maxsize,
                                  'warn-level': 1717986918,
                                  'reserve': 0,
                                  'quota': 2147483648},
                         'mask': 884,
                         'name': 'clone-testvolume',
                         'sid': 'a9b9aba7'})]
        self.mock_client_service.assert_has_calls(expected_calls)


class NimbleDriverConnectionTestCase(NimbleDriverBaseTestCase):

    """Tests Connection related api's."""

    @mock.patch(NIMBLE_URLLIB2)
    @mock.patch(NIMBLE_CLIENT)
    @mock.patch.object(obj_volume.VolumeList, 'get_all_by_host',
                       mock.Mock(return_value=[]))
    @NimbleDriverBaseTestCase.client_mock_decorator(create_configuration(
        'nimble', 'nimble_pass', '10.18.108.55', 'default', '*'))
    def test_initialize_connection_igroup_exist(self):
        self.mock_client_service.service.getInitiatorGrpList.return_value = \
            FAKE_IGROUP_LIST_RESPONSE
        expected_res = {
            'driver_volume_type': 'iscsi',
            'data': {
                'target_lun': 14,
                'volume_id': 12,
                'target_iqn': '13',
                'target_discovered': False,
                'target_portal': '12'}}
        self.assertEqual(
            expected_res,
            self.driver.initialize_connection(
                {'name': 'test-volume',
                 'provider_location': '12 13 14',
                 'id': 12},
                {'initiator': 'test-initiator1'}))
        expected_call_list = [mock.call.set_options(
            location='https://10.18.108.55:5391/soap'),
            mock.call.service.login(
                req={
                    'username': 'nimble', 'password': 'nimble_pass'}),
            mock.call.service.getInitiatorGrpList(
                request={'sid': 'a9b9aba7'}),
            mock.call.service.addVolAcl(
                request={'volname': 'test-volume',
                         'apply-to': 3,
                         'chapuser': '*',
                         'initiatorgrp': 'test-igrp2',
                         'sid': 'a9b9aba7'})]
        self.assertEqual(
            self.mock_client_service.method_calls,
            expected_call_list)

    @mock.patch(NIMBLE_URLLIB2)
    @mock.patch(NIMBLE_CLIENT)
    @mock.patch.object(obj_volume.VolumeList, 'get_all_by_host',
                       mock.Mock(return_value=[]))
    @NimbleDriverBaseTestCase.client_mock_decorator(create_configuration(
        'nimble', 'nimble_pass', '10.18.108.55', 'default', '*'))
    @mock.patch(NIMBLE_RANDOM)
    def test_initialize_connection_igroup_not_exist(self, mock_random):
        mock_random.sample.return_value = 'abcdefghijkl'
        self.mock_client_service.service.getInitiatorGrpList.return_value = \
            FAKE_IGROUP_LIST_RESPONSE
        expected_res = {
            'driver_volume_type': 'iscsi',
            'data': {
                'target_lun': 14,
                'volume_id': 12,
                'target_iqn': '13',
                'target_discovered': False,
                'target_portal': '12'}}
        self.assertEqual(
            expected_res,
            self.driver.initialize_connection(
                {'name': 'test-volume',
                 'provider_location': '12 13 14',
                 'id': 12},
                {'initiator': 'test-initiator3'}))
        expected_calls = [
            mock.call.service.getInitiatorGrpList(
                request={'sid': 'a9b9aba7'}),
            mock.call.service.createInitiatorGrp(
                request={
                    'attr': {'initiator-list': [{'name': 'test-initiator3',
                                                 'label': 'test-initiator3'}],
                             'name': 'openstack-abcdefghijkl'},
                    'sid': 'a9b9aba7'}),
            mock.call.service.addVolAcl(
                request={'volname': 'test-volume', 'apply-to': 3,
                         'chapuser': '*',
                         'initiatorgrp': 'openstack-abcdefghijkl',
                         'sid': 'a9b9aba7'})]
        self.mock_client_service.assert_has_calls(
            self.mock_client_service.method_calls,
            expected_calls)

    @mock.patch(NIMBLE_URLLIB2)
    @mock.patch(NIMBLE_CLIENT)
    @mock.patch.object(obj_volume.VolumeList, 'get_all_by_host',
                       mock.Mock(return_value=[]))
    @NimbleDriverBaseTestCase.client_mock_decorator(create_configuration(
        'nimble', 'nimble_pass', '10.18.108.55', 'default', '*'))
    def test_terminate_connection_positive(self):
        self.mock_client_service.service.getInitiatorGrpList.return_value = \
            FAKE_IGROUP_LIST_RESPONSE
        self.driver.terminate_connection(
            {'name': 'test-volume',
             'provider_location': '12 13 14',
             'id': 12},
            {'initiator': 'test-initiator1'})
        expected_calls = [mock.call.service.getInitiatorGrpList(
            request={'sid': 'a9b9aba7'}),
            mock.call.service.removeVolAcl(
                request={'volname': 'test-volume',
                         'apply-to': 3,
                         'chapuser': '*',
                         'initiatorgrp': {'initiator-list':
                                          [{'name': 'test-initiator1'}]},
                         'sid': 'a9b9aba7'})]
        self.mock_client_service.assert_has_calls(
            self.mock_client_service.method_calls,
            expected_calls)

    @mock.patch(NIMBLE_URLLIB2)
    @mock.patch(NIMBLE_CLIENT)
    @mock.patch.object(obj_volume.VolumeList, 'get_all_by_host',
                       mock.Mock(return_value=[]))
    @NimbleDriverBaseTestCase.client_mock_decorator(create_configuration(
        'nimble', 'nimble_pass', '10.18.108.55', 'default', '*'))
    def test_terminate_connection_negative(self):
        self.mock_client_service.service.getInitiatorGrpList.return_value = \
            FAKE_IGROUP_LIST_RESPONSE
        self.assertRaises(
            exception.VolumeDriverException,
            self.driver.terminate_connection, {
                'name': 'test-volume',
                'provider_location': '12 13 14', 'id': 12},
            {'initiator': 'test-initiator3'})
