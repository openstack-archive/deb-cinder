# Copyright 2011 Denali Systems, Inc.
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

import mock
import webob

from cinder.api.v1 import snapshots
from cinder import context
from cinder import db
from cinder import exception
from cinder import objects
from cinder.objects import fields
from cinder import test
from cinder.tests.unit.api import fakes
from cinder.tests.unit.api.v1 import stubs
from cinder.tests.unit import fake_constants as fake
from cinder.tests.unit import fake_snapshot
from cinder.tests.unit import fake_volume
from cinder import volume


UUID = fake.SNAPSHOT_ID
INVALID_UUID = fake.WILL_NOT_BE_FOUND_ID


def _get_default_snapshot_param():
    return {'id': UUID,
            'volume_id': fake.VOLUME_ID,
            'status': fields.SnapshotStatus.AVAILABLE,
            'volume_size': 100,
            'created_at': None,
            'display_name': 'Default name',
            'display_description': 'Default description', }


def stub_snapshot_create(self, context,
                         volume_id, name,
                         description, metadata):
    snapshot = _get_default_snapshot_param()
    snapshot['volume_id'] = volume_id
    snapshot['display_name'] = name
    snapshot['display_description'] = description
    snapshot['metadata'] = metadata
    return snapshot


def stub_snapshot_delete(self, context, snapshot):
    if snapshot['id'] != UUID:
        raise exception.NotFound


def stub_snapshot_get(self, context, snapshot_id):
    if snapshot_id != UUID:
        raise exception.NotFound

    param = _get_default_snapshot_param()
    return param


def stub_snapshot_get_all(self, context, search_opts=None):
    param = _get_default_snapshot_param()
    return [param]


class SnapshotApiTest(test.TestCase):
    def setUp(self):
        super(SnapshotApiTest, self).setUp()
        self.controller = snapshots.SnapshotsController()

        self.stubs.Set(db, 'snapshot_get_all_by_project',
                       stubs.stub_snapshot_get_all_by_project)
        self.stubs.Set(db, 'snapshot_get_all',
                       stubs.stub_snapshot_get_all)

    def test_snapshot_create(self):
        self.stubs.Set(volume.api.API, "create_snapshot", stub_snapshot_create)
        self.stubs.Set(volume.api.API, 'get', stubs.stub_volume_get)
        snapshot = {"volume_id": fake.VOLUME_ID,
                    "force": False,
                    "display_name": "Snapshot Test Name",
                    "display_description": "Snapshot Test Desc"}
        body = dict(snapshot=snapshot)
        req = fakes.HTTPRequest.blank('/v1/snapshots')
        resp_dict = self.controller.create(req, body)

        self.assertIn('snapshot', resp_dict)
        self.assertEqual(snapshot['display_name'],
                         resp_dict['snapshot']['display_name'])
        self.assertEqual(snapshot['display_description'],
                         resp_dict['snapshot']['display_description'])

    def test_snapshot_create_force(self):
        self.stubs.Set(volume.api.API,
                       "create_snapshot_force",
                       stub_snapshot_create)
        self.stubs.Set(volume.api.API, 'get', stubs.stub_volume_get)
        snapshot = {"volume_id": fake.VOLUME_ID,
                    "force": True,
                    "display_name": "Snapshot Test Name",
                    "display_description": "Snapshot Test Desc"}
        body = dict(snapshot=snapshot)
        req = fakes.HTTPRequest.blank('/v1/snapshots')
        resp_dict = self.controller.create(req, body)

        self.assertIn('snapshot', resp_dict)
        self.assertEqual(snapshot['display_name'],
                         resp_dict['snapshot']['display_name'])
        self.assertEqual(snapshot['display_description'],
                         resp_dict['snapshot']['display_description'])

        snapshot = {"volume_id": fake.SNAPSHOT_ID,
                    "force": "**&&^^%%$$##@@",
                    "display_name": "Snapshot Test Name",
                    "display_description": "Snapshot Test Desc"}
        body = dict(snapshot=snapshot)
        req = fakes.HTTPRequest.blank('/v1/snapshots')
        self.assertRaises(exception.InvalidParameterValue,
                          self.controller.create,
                          req,
                          body)

    def test_snapshot_create_without_volume_id(self):
        snapshot_name = 'Snapshot Test Name'
        snapshot_description = 'Snapshot Test Desc'
        body = {
            "snapshot": {
                "force": True,
                "name": snapshot_name,
                "description": snapshot_description
            }
        }
        req = fakes.HTTPRequest.blank('/v1/snapshots')
        self.assertRaises(webob.exc.HTTPBadRequest,
                          self.controller.create, req, body)

    @mock.patch.object(volume.api.API, "update_snapshot",
                       side_effect=stubs.stub_snapshot_update)
    @mock.patch('cinder.db.snapshot_metadata_get', return_value=dict())
    @mock.patch('cinder.objects.Volume.get_by_id')
    @mock.patch('cinder.objects.Snapshot.get_by_id')
    def test_snapshot_update(self, snapshot_get_by_id, volume_get_by_id,
                             snapshot_metadata_get, update_snapshot):
        snapshot = {
            'id': UUID,
            'volume_id': fake.VOLUME_ID,
            'status': fields.SnapshotStatus.AVAILABLE,
            'volume_size': 100,
            'display_name': 'Default name',
            'display_description': 'Default description',
            'expected_attrs': ['metadata'],
        }
        ctx = context.RequestContext(fake.USER_ID, fake.PROJECT_ID, True)
        snapshot_obj = fake_snapshot.fake_snapshot_obj(ctx, **snapshot)
        fake_volume_obj = fake_volume.fake_volume_obj(ctx)
        snapshot_get_by_id.return_value = snapshot_obj
        volume_get_by_id.return_value = fake_volume_obj

        updates = {"display_name": "Updated Test Name", }
        body = {"snapshot": updates}
        req = fakes.HTTPRequest.blank('/v1/snapshots/%s' % UUID)
        res_dict = self.controller.update(req, UUID, body)
        expected = {'snapshot': {
            'id': UUID,
            'volume_id': fake.VOLUME_ID,
            'status': fields.SnapshotStatus.AVAILABLE,
            'size': 100,
            'created_at': None,
            'display_name': u'Updated Test Name',
            'display_description': u'Default description',
            'metadata': {},
        }}
        self.assertEqual(expected, res_dict)

    def test_snapshot_update_missing_body(self):
        body = {}
        req = fakes.HTTPRequest.blank('/v1/snapshots/%s' % UUID)
        self.assertRaises(webob.exc.HTTPUnprocessableEntity,
                          self.controller.update, req, UUID, body)

    def test_snapshot_update_invalid_body(self):
        body = {'display_name': 'missing top level snapshot key'}
        req = fakes.HTTPRequest.blank('/v1/snapshots/%s' % UUID)
        self.assertRaises(webob.exc.HTTPUnprocessableEntity,
                          self.controller.update, req, UUID, body)

    def test_snapshot_update_not_found(self):
        self.stubs.Set(volume.api.API, "get_snapshot", stub_snapshot_get)
        updates = {
            "display_name": "Updated Test Name",
        }
        body = {"snapshot": updates}
        req = fakes.HTTPRequest.blank('/v1/snapshots/not-the-uuid')
        self.assertRaises(exception.NotFound, self.controller.update, req,
                          'not-the-uuid', body)

    @mock.patch.object(volume.api.API, "delete_snapshot",
                       side_effect=stubs.stub_snapshot_update)
    @mock.patch('cinder.db.snapshot_metadata_get', return_value=dict())
    @mock.patch('cinder.objects.Volume.get_by_id')
    @mock.patch('cinder.objects.Snapshot.get_by_id')
    def test_snapshot_delete(self, snapshot_get_by_id, volume_get_by_id,
                             snapshot_metadata_get, delete_snapshot):
        snapshot = {
            'id': UUID,
            'volume_id': fake.VOLUME_ID,
            'status': fields.SnapshotStatus.AVAILABLE,
            'volume_size': 100,
            'display_name': 'Default name',
            'display_description': 'Default description',
            'expected_attrs': ['metadata'],
        }
        ctx = context.RequestContext(fake.USER_ID, fake.PROJECT_ID, True)
        snapshot_obj = fake_snapshot.fake_snapshot_obj(ctx, **snapshot)
        fake_volume_obj = fake_volume.fake_volume_obj(ctx)
        snapshot_get_by_id.return_value = snapshot_obj
        volume_get_by_id.return_value = fake_volume_obj

        snapshot_id = UUID
        req = fakes.HTTPRequest.blank('/v1/snapshots/%s' % snapshot_id)
        resp = self.controller.delete(req, snapshot_id)
        self.assertEqual(202, resp.status_int)

    def test_snapshot_delete_invalid_id(self):
        self.stubs.Set(volume.api.API, "delete_snapshot", stub_snapshot_delete)
        snapshot_id = INVALID_UUID
        req = fakes.HTTPRequest.blank('/v1/snapshots/%s' % snapshot_id)
        self.assertRaises(exception.SnapshotNotFound,
                          self.controller.delete,
                          req,
                          snapshot_id)

    @mock.patch('cinder.db.snapshot_metadata_get', return_value=dict())
    @mock.patch('cinder.objects.Volume.get_by_id')
    @mock.patch('cinder.objects.Snapshot.get_by_id')
    def test_snapshot_show(self, snapshot_get_by_id, volume_get_by_id,
                           snapshot_metadata_get):
        snapshot = {
            'id': UUID,
            'volume_id': fake.VOLUME_ID,
            'status': fields.SnapshotStatus.AVAILABLE,
            'volume_size': 100,
            'display_name': 'Default name',
            'display_description': 'Default description',
            'expected_attrs': ['metadata'],
        }
        ctx = context.RequestContext(fake.USER_ID, fake.PROJECT_ID, True)
        snapshot_obj = fake_snapshot.fake_snapshot_obj(ctx, **snapshot)
        fake_volume_obj = fake_volume.fake_volume_obj(ctx)
        snapshot_get_by_id.return_value = snapshot_obj
        volume_get_by_id.return_value = fake_volume_obj

        req = fakes.HTTPRequest.blank('/v1/snapshots/%s' % UUID)
        resp_dict = self.controller.show(req, UUID)

        self.assertIn('snapshot', resp_dict)
        self.assertEqual(UUID, resp_dict['snapshot']['id'])

    def test_snapshot_show_invalid_id(self):
        snapshot_id = INVALID_UUID
        req = fakes.HTTPRequest.blank('/v1/snapshots/%s' % snapshot_id)
        self.assertRaises(exception.SnapshotNotFound,
                          self.controller.show,
                          req,
                          snapshot_id)

    @mock.patch('cinder.db.snapshot_metadata_get', return_value=dict())
    @mock.patch('cinder.objects.Volume.get_by_id')
    @mock.patch('cinder.objects.Snapshot.get_by_id')
    @mock.patch('cinder.volume.api.API.get_all_snapshots')
    def test_snapshot_detail(self, get_all_snapshots, snapshot_get_by_id,
                             volume_get_by_id, snapshot_metadata_get):
        snapshot = {
            'id': UUID,
            'volume_id': fake.VOLUME_ID,
            'status': fields.SnapshotStatus.AVAILABLE,
            'volume_size': 100,
            'display_name': 'Default name',
            'display_description': 'Default description',
            'expected_attrs': ['metadata']
        }
        ctx = context.RequestContext(fake.USER_ID, fake.PROJECT_ID, True)
        snapshot_obj = fake_snapshot.fake_snapshot_obj(ctx, **snapshot)
        fake_volume_obj = fake_volume.fake_volume_obj(ctx)
        snapshot_get_by_id.return_value = snapshot_obj
        volume_get_by_id.return_value = fake_volume_obj
        snapshots = objects.SnapshotList(objects=[snapshot_obj])
        get_all_snapshots.return_value = snapshots

        req = fakes.HTTPRequest.blank('/v1/snapshots/detail')
        resp_dict = self.controller.detail(req)

        self.assertIn('snapshots', resp_dict)
        resp_snapshots = resp_dict['snapshots']
        self.assertEqual(1, len(resp_snapshots))

        resp_snapshot = resp_snapshots.pop()
        self.assertEqual(UUID, resp_snapshot['id'])

    @mock.patch('cinder.db.snapshot_metadata_get', return_value=dict())
    def test_admin_list_snapshots_limited_to_project(self,
                                                     snapshot_metadata_get):
        req = fakes.HTTPRequest.blank('/v1/%s/snapshots' % fake.PROJECT_ID,
                                      use_admin_context=True)
        res = self.controller.index(req)

        self.assertIn('snapshots', res)
        self.assertEqual(1, len(res['snapshots']))

    @mock.patch('cinder.db.snapshot_metadata_get', return_value=dict())
    def test_list_snapshots_with_limit_and_offset(self,
                                                  snapshot_metadata_get):
        def list_snapshots_with_limit_and_offset(is_admin):
            def stub_snapshot_get_all_by_project(context, project_id,
                                                 filters=None, marker=None,
                                                 limit=None, sort_keys=None,
                                                 sort_dirs=None, offset=None):
                return [
                    stubs.stub_snapshot(fake.SNAPSHOT_ID,
                                        display_name='backup1'),
                    stubs.stub_snapshot(fake.SNAPSHOT2_ID,
                                        display_name='backup2'),
                    stubs.stub_snapshot(fake.SNAPSHOT3_ID,
                                        display_name='backup3'),
                ]

            self.stubs.Set(db, 'snapshot_get_all_by_project',
                           stub_snapshot_get_all_by_project)

            req = fakes.HTTPRequest.blank('/v1/%s/snapshots?limit=1\
                                          &offset=1' % fake.PROJECT_ID,
                                          use_admin_context=is_admin)
            res = self.controller.index(req)

            self.assertIn('snapshots', res)
            self.assertEqual(1, len(res['snapshots']))
            self.assertEqual(fake.SNAPSHOT2_ID, res['snapshots'][0]['id'])

        # admin case
        list_snapshots_with_limit_and_offset(is_admin=True)
        # non_admin case
        list_snapshots_with_limit_and_offset(is_admin=False)

    @mock.patch('cinder.db.snapshot_metadata_get', return_value=dict())
    def test_admin_list_snapshots_all_tenants(self, snapshot_metadata_get):
        req = fakes.HTTPRequest.blank(
            '/v1/%s/snapshots?all_tenants=1' % fake.PROJECT_ID,
            use_admin_context=True)
        res = self.controller.index(req)
        self.assertIn('snapshots', res)
        self.assertEqual(3, len(res['snapshots']))

    @mock.patch('cinder.db.snapshot_metadata_get', return_value=dict())
    def test_all_tenants_non_admin_gets_all_tenants(self,
                                                    snapshot_metadata_get):
        req = fakes.HTTPRequest.blank(
            '/v1/%s/snapshots?all_tenants=1' % fake.PROJECT_ID)
        res = self.controller.index(req)
        self.assertIn('snapshots', res)
        self.assertEqual(1, len(res['snapshots']))

    @mock.patch('cinder.db.snapshot_metadata_get', return_value=dict())
    def test_non_admin_get_by_project(self, snapshot_metadata_get):
        req = fakes.HTTPRequest.blank('/v1/%s/snapshots' % fake.PROJECT_ID)
        res = self.controller.index(req)
        self.assertIn('snapshots', res)
        self.assertEqual(1, len(res['snapshots']))


class SnapshotsUnprocessableEntityTestCase(test.TestCase):

    """Tests of places we throw 422 Unprocessable Entity."""

    def setUp(self):
        super(SnapshotsUnprocessableEntityTestCase, self).setUp()
        self.controller = snapshots.SnapshotsController()

    def _unprocessable_snapshot_create(self, body):
        req = fakes.HTTPRequest.blank('/v1/%s/snapshots' % fake.PROJECT_ID)
        req.method = 'POST'

        self.assertRaises(webob.exc.HTTPUnprocessableEntity,
                          self.controller.create, req, body)

    def test_create_no_body(self):
        self._unprocessable_snapshot_create(body=None)

    def test_create_missing_snapshot(self):
        body = {'foo': {'a': 'b'}}
        self._unprocessable_snapshot_create(body=body)

    def test_create_malformed_entity(self):
        body = {'snapshot': 'string'}
        self._unprocessable_snapshot_create(body=body)
