#   Copyright (c) 2015 Huawei Technologies Co., Ltd.
#   Copyright (c) 2016 Stratoscale, Ltd.
#
#   Licensed under the Apache License, Version 2.0 (the "License"); you may
#   not use this file except in compliance with the License. You may obtain
#   a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#   WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#   License for the specific language governing permissions and limitations
#   under the License.

import mock
from oslo_config import cfg
from oslo_serialization import jsonutils
try:
    from urllib import urlencode
except ImportError:
    from urllib.parse import urlencode
import webob

from cinder import context
from cinder import exception
from cinder import objects
from cinder import test
from cinder.tests.unit.api import fakes
from cinder.tests.unit import fake_constants as fake
from cinder.tests.unit import fake_service

CONF = cfg.CONF


def app():
    # no auth, just let environ['cinder.context'] pass through
    api = fakes.router.APIRouter()
    mapper = fakes.urlmap.URLMap()
    mapper['/v2'] = api
    return mapper


def volume_get(self, context, volume_id, viewable_admin_meta=False):
    if volume_id == fake.VOLUME_ID:
        return objects.Volume(context, id=fake.VOLUME_ID,
                              _name_id=fake.VOLUME2_ID,
                              host='fake_host')
    raise exception.VolumeNotFound(volume_id=volume_id)


def api_get_manageable_snapshots(*args, **kwargs):
    """Replacement for cinder.volume.api.API.get_manageable_snapshots."""
    snap_id = 'ffffffff-0000-ffff-0000-ffffffffffff'
    snaps = [
        {'reference': {'source-name': 'snapshot-%s' % snap_id},
         'size': 4,
         'extra_info': 'qos_setting:high',
         'safe_to_manage': False,
         'reason_not_safe': 'snapshot in use',
         'cinder_id': snap_id,
         'source_reference': {'source-name':
                              'volume-00000000-ffff-0000-ffff-000000'}},
        {'reference': {'source-name': 'mysnap'},
         'size': 5,
         'extra_info': 'qos_setting:low',
         'safe_to_manage': True,
         'reason_not_safe': None,
         'cinder_id': None,
         'source_reference': {'source-name': 'myvol'}}]
    return snaps


@mock.patch('cinder.volume.api.API.get', volume_get)
class SnapshotManageTest(test.TestCase):
    """Test cases for cinder/api/contrib/snapshot_manage.py

    The API extension adds a POST /os-snapshot-manage API that is passed a
    cinder volume id, and a driver-specific reference parameter.
    If everything is passed correctly,
    then the cinder.volume.api.API.manage_existing_snapshot method
    is invoked to manage an existing storage object on the host.

    In this set of test cases, we are ensuring that the code correctly parses
    the request structure and raises the correct exceptions when things are not
    right, and calls down into cinder.volume.api.API.manage_existing_snapshot
    with the correct arguments.
    """

    def setUp(self):
        super(SnapshotManageTest, self).setUp()
        self._admin_ctxt = context.RequestContext(fake.USER_ID,
                                                  fake.PROJECT_ID,
                                                  is_admin=True)
        self._non_admin_ctxt = context.RequestContext(fake.USER_ID,
                                                      fake.PROJECT_ID,
                                                      is_admin=False)

    def _get_resp_post(self, body):
        """Helper to execute an os-snapshot-manage API call."""
        req = webob.Request.blank('/v2/%s/os-snapshot-manage' %
                                  fake.PROJECT_ID)
        req.method = 'POST'
        req.headers['Content-Type'] = 'application/json'
        req.environ['cinder.context'] = self._admin_ctxt
        req.body = jsonutils.dump_as_bytes(body)
        res = req.get_response(app())
        return res

    @mock.patch('cinder.volume.rpcapi.VolumeAPI.manage_existing_snapshot')
    @mock.patch('cinder.volume.api.API.create_snapshot_in_db')
    @mock.patch('cinder.db.service_get')
    def test_manage_snapshot_ok(self, mock_db,
                                mock_create_snapshot, mock_rpcapi):
        """Test successful manage snapshot execution.

        Tests for correct operation when valid arguments are passed in the
        request body. We ensure that cinder.volume.api.API.manage_existing got
        called with the correct arguments, and that we return the correct HTTP
        code to the caller.
        """
        mock_db.return_value = fake_service.fake_service_obj(
            self._admin_ctxt,
            binary='cinder-volume')
        body = {'snapshot': {'volume_id': fake.VOLUME_ID, 'ref': 'fake_ref'}}
        res = self._get_resp_post(body)
        self.assertEqual(202, res.status_int, res)

        # Check the db.service_get was called with correct arguments.
        mock_db.assert_called_once_with(
            mock.ANY, host='fake_host', binary='cinder-volume')

        # Check the create_snapshot_in_db was called with correct arguments.
        self.assertEqual(1, mock_create_snapshot.call_count)
        args = mock_create_snapshot.call_args[0]
        named_args = mock_create_snapshot.call_args[1]
        self.assertEqual(fake.VOLUME_ID, args[1].get('id'))
        # We should commit quota in cinder-volume layer for this operation.
        self.assertFalse(named_args['commit_quota'])

        # Check the volume_rpcapi.manage_existing_snapshot was called with
        # correct arguments.
        self.assertEqual(1, mock_rpcapi.call_count)
        args = mock_rpcapi.call_args[0]
        self.assertEqual('fake_ref', args[2])

    @mock.patch('cinder.utils.service_is_up', return_value=True)
    @mock.patch('cinder.volume.rpcapi.VolumeAPI.manage_existing_snapshot')
    @mock.patch('cinder.volume.api.API.create_snapshot_in_db')
    @mock.patch('cinder.db.service_get')
    def test_manage_snapshot_disabled(self, mock_db, mock_create_snapshot,
                                      mock_rpcapi, mock_is_up):
        """Test manage snapshot failure due to disabled service."""
        mock_db.return_value = fake_service.fake_service_obj(self._admin_ctxt,
                                                             disabled=True)
        body = {'snapshot': {'volume_id': fake.VOLUME_ID, 'ref': 'fake_ref'}}
        res = self._get_resp_post(body)
        self.assertEqual(400, res.status_int, res)
        self.assertEqual(exception.ServiceUnavailable.message,
                         res.json['badRequest']['message'])
        mock_create_snapshot.assert_not_called()
        mock_rpcapi.assert_not_called()
        mock_is_up.assert_not_called()

    @mock.patch('cinder.utils.service_is_up', return_value=False)
    @mock.patch('cinder.volume.rpcapi.VolumeAPI.manage_existing_snapshot')
    @mock.patch('cinder.volume.api.API.create_snapshot_in_db')
    @mock.patch('cinder.db.service_get')
    def test_manage_snapshot_is_down(self, mock_db, mock_create_snapshot,
                                     mock_rpcapi, mock_is_up):
        """Test manage snapshot failure due to down service."""
        mock_db.return_value = fake_service.fake_service_obj(self._admin_ctxt)
        body = {'snapshot': {'volume_id': fake.VOLUME_ID, 'ref': 'fake_ref'}}
        res = self._get_resp_post(body)
        self.assertEqual(400, res.status_int, res)
        self.assertEqual(exception.ServiceUnavailable.message,
                         res.json['badRequest']['message'])
        mock_create_snapshot.assert_not_called()
        mock_rpcapi.assert_not_called()
        self.assertTrue(mock_is_up.called)

    def test_manage_snapshot_missing_volume_id(self):
        """Test correct failure when volume_id is not specified."""
        body = {'snapshot': {'ref': 'fake_ref'}}
        res = self._get_resp_post(body)
        self.assertEqual(400, res.status_int)

    def test_manage_snapshot_missing_ref(self):
        """Test correct failure when the ref is not specified."""
        body = {'snapshot': {'volume_id': fake.VOLUME_ID}}
        res = self._get_resp_post(body)
        self.assertEqual(400, res.status_int)

    def test_manage_snapshot_error_body(self):
        """Test correct failure when body is invaild."""
        body = {'error_snapshot': {'volume_id': fake.VOLUME_ID}}
        res = self._get_resp_post(body)
        self.assertEqual(400, res.status_int)

    def test_manage_snapshot_error_volume_id(self):
        """Test correct failure when volume can't be found."""
        body = {'snapshot': {'volume_id': 'error_volume_id',
                             'ref': 'fake_ref'}}
        res = self._get_resp_post(body)
        self.assertEqual(404, res.status_int)

    def _get_resp_get(self, host, detailed, paging, admin=True):
        """Helper to execute a GET os-snapshot-manage API call."""
        params = {'host': host}
        if paging:
            params.update({'marker': '1234', 'limit': 10,
                           'offset': 4, 'sort': 'reference:asc'})
        query_string = "?%s" % urlencode(params)
        detail = ""
        if detailed:
            detail = "/detail"
        url = "/v2/%s/os-snapshot-manage%s%s" % (fake.PROJECT_ID, detail,
                                                 query_string)
        req = webob.Request.blank(url)
        req.method = 'GET'
        req.headers['Content-Type'] = 'application/json'
        req.environ['cinder.context'] = (self._admin_ctxt if admin
                                         else self._non_admin_ctxt)
        res = req.get_response(app())
        return res

    @mock.patch('cinder.volume.api.API.get_manageable_snapshots',
                wraps=api_get_manageable_snapshots)
    def test_get_manageable_snapshots_non_admin(self, mock_api_manageable):
        res = self._get_resp_get('fakehost', False, False, admin=False)
        self.assertEqual(403, res.status_int)
        self.assertEqual(False, mock_api_manageable.called)
        res = self._get_resp_get('fakehost', True, False, admin=False)
        self.assertEqual(403, res.status_int)
        self.assertEqual(False, mock_api_manageable.called)

    @mock.patch('cinder.volume.api.API.get_manageable_snapshots',
                wraps=api_get_manageable_snapshots)
    def test_get_manageable_snapshots_ok(self, mock_api_manageable):
        res = self._get_resp_get('fakehost', False, False)
        snap_name = 'snapshot-ffffffff-0000-ffff-0000-ffffffffffff'
        exp = {'manageable-snapshots':
               [{'reference': {'source-name': snap_name}, 'size': 4,
                 'safe_to_manage': False,
                 'source_reference':
                 {'source-name': 'volume-00000000-ffff-0000-ffff-000000'}},
                {'reference': {'source-name': 'mysnap'}, 'size': 5,
                 'safe_to_manage': True,
                 'source_reference': {'source-name': 'myvol'}}]}
        self.assertEqual(200, res.status_int)
        self.assertEqual(jsonutils.loads(res.body), exp)
        mock_api_manageable.assert_called_once_with(
            self._admin_ctxt, 'fakehost', limit=CONF.osapi_max_limit,
            marker=None, offset=0, sort_dirs=['desc'],
            sort_keys=['reference'])

    @mock.patch('cinder.volume.api.API.get_manageable_snapshots',
                wraps=api_get_manageable_snapshots)
    def test_get_manageable_snapshots_detailed_ok(self, mock_api_manageable):
        res = self._get_resp_get('fakehost', True, True)
        snap_id = 'ffffffff-0000-ffff-0000-ffffffffffff'
        exp = {'manageable-snapshots':
               [{'reference': {'source-name': 'snapshot-%s' % snap_id},
                 'size': 4, 'safe_to_manage': False, 'cinder_id': snap_id,
                 'reason_not_safe': 'snapshot in use',
                 'extra_info': 'qos_setting:high',
                 'source_reference':
                 {'source-name': 'volume-00000000-ffff-0000-ffff-000000'}},
                {'reference': {'source-name': 'mysnap'}, 'size': 5,
                 'cinder_id': None, 'safe_to_manage': True,
                 'reason_not_safe': None, 'extra_info': 'qos_setting:low',
                 'source_reference': {'source-name': 'myvol'}}]}
        self.assertEqual(200, res.status_int)
        self.assertEqual(jsonutils.loads(res.body), exp)
        mock_api_manageable.assert_called_once_with(
            self._admin_ctxt, 'fakehost', limit=10, marker='1234', offset=4,
            sort_dirs=['asc'], sort_keys=['reference'])

    @mock.patch('cinder.utils.service_is_up', return_value=True)
    @mock.patch('cinder.db.service_get')
    def test_get_manageable_snapshots_disabled(self, mock_db, mock_is_up):
        mock_db.return_value = fake_service.fake_service_obj(self._admin_ctxt,
                                                             disabled=True)
        res = self._get_resp_get('host_ok', False, True)
        self.assertEqual(400, res.status_int, res)
        self.assertEqual(exception.ServiceUnavailable.message,
                         res.json['badRequest']['message'])
        mock_is_up.assert_not_called()

    @mock.patch('cinder.utils.service_is_up', return_value=False)
    @mock.patch('cinder.db.service_get')
    def test_get_manageable_snapshots_is_down(self, mock_db, mock_is_up):
        mock_db.return_value = fake_service.fake_service_obj(self._admin_ctxt)
        res = self._get_resp_get('host_ok', False, True)
        self.assertEqual(400, res.status_int, res)
        self.assertEqual(exception.ServiceUnavailable.message,
                         res.json['badRequest']['message'])
        self.assertTrue(mock_is_up.called)
