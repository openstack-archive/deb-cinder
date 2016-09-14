# Copyright (C) 2012 Hewlett-Packard Development Company, L.P.
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
Tests for Backup code.
"""

import ddt
import mock
from oslo_serialization import jsonutils
from oslo_utils import timeutils
import webob

from cinder.api.contrib import backups
# needed for stubs to work
import cinder.backup
from cinder.backup import api as backup_api
from cinder import context
from cinder import db
from cinder import exception
from cinder.i18n import _
from cinder import objects
from cinder.objects import fields
from cinder import test
from cinder.tests.unit.api import fakes
from cinder.tests.unit import fake_constants as fake
from cinder.tests.unit import utils
# needed for stubs to work
import cinder.volume

NUM_ELEMENTS_IN_BACKUP = 17


@ddt.ddt
class BackupsAPITestCase(test.TestCase):
    """Test Case for backups API."""

    def setUp(self):
        super(BackupsAPITestCase, self).setUp()
        self.volume_api = cinder.volume.API()
        self.backup_api = cinder.backup.API()
        self.context = context.get_admin_context()
        self.context.project_id = fake.PROJECT_ID
        self.context.user_id = fake.USER_ID
        self.user_context = context.RequestContext(
            fake.USER_ID, fake.PROJECT_ID, auth_token=True)
        self.controller = backups.BackupsController()
        self.patch('cinder.objects.service.Service._get_minimum_version',
                   return_value=None)

    @staticmethod
    def _create_backup(volume_id=fake.VOLUME_ID,
                       display_name='test_backup',
                       display_description='this is a test backup',
                       container='volumebackups',
                       status=fields.BackupStatus.CREATING,
                       incremental=False,
                       parent_id=None,
                       size=0, object_count=0, host='testhost',
                       num_dependent_backups=0,
                       snapshot_id=None,
                       data_timestamp=None):
        """Create a backup object."""
        backup = {}
        backup['volume_id'] = volume_id
        backup['user_id'] = fake.USER_ID
        backup['project_id'] = fake.PROJECT_ID
        backup['host'] = host
        backup['availability_zone'] = 'az1'
        backup['display_name'] = display_name
        backup['display_description'] = display_description
        backup['container'] = container
        backup['status'] = status
        backup['fail_reason'] = ''
        backup['size'] = size
        backup['object_count'] = object_count
        backup['incremental'] = incremental
        backup['parent_id'] = parent_id
        backup['num_dependent_backups'] = num_dependent_backups
        backup['snapshot_id'] = snapshot_id
        backup['data_timestamp'] = data_timestamp
        backup = db.backup_create(context.get_admin_context(), backup)
        if not snapshot_id:
            db.backup_update(context.get_admin_context(),
                             backup['id'],
                             {'data_timestamp': backup['created_at']})
        return backup['id']

    @staticmethod
    def _get_backup_attrib(backup_id, attrib_name):
        return db.backup_get(context.get_admin_context(),
                             backup_id)[attrib_name]

    @ddt.data(False, True)
    def test_show_backup(self, backup_from_snapshot):
        volume_id = utils.create_volume(self.context, size=5,
                                        status='creating').id
        snapshot = None
        snapshot_id = None
        if backup_from_snapshot:
            snapshot = utils.create_snapshot(self.context,
                                             volume_id)
            snapshot_id = snapshot.id
        backup_id = self._create_backup(volume_id,
                                        snapshot_id=snapshot_id)
        req = webob.Request.blank('/v2/%s/backups/%s' % (
                                  fake.PROJECT_ID, backup_id))
        req.method = 'GET'
        req.headers['Content-Type'] = 'application/json'
        res = req.get_response(fakes.wsgi_app(
            fake_auth_context=self.user_context))
        res_dict = jsonutils.loads(res.body)
        self.assertEqual(200, res.status_int)
        self.assertEqual('az1', res_dict['backup']['availability_zone'])
        self.assertEqual('volumebackups', res_dict['backup']['container'])
        self.assertEqual('this is a test backup',
                         res_dict['backup']['description'])
        self.assertEqual('test_backup', res_dict['backup']['name'])
        self.assertEqual(backup_id, res_dict['backup']['id'])
        self.assertEqual(0, res_dict['backup']['object_count'])
        self.assertEqual(0, res_dict['backup']['size'])
        self.assertEqual(fields.BackupStatus.CREATING,
                         res_dict['backup']['status'])
        self.assertEqual(volume_id, res_dict['backup']['volume_id'])
        self.assertFalse(res_dict['backup']['is_incremental'])
        self.assertFalse(res_dict['backup']['has_dependent_backups'])
        self.assertEqual(snapshot_id, res_dict['backup']['snapshot_id'])
        self.assertIn('updated_at', res_dict['backup'])

        if snapshot:
            snapshot.destroy()
        db.backup_destroy(context.get_admin_context(), backup_id)
        db.volume_destroy(context.get_admin_context(), volume_id)

    def test_show_backup_with_backup_NotFound(self):
        req = webob.Request.blank('/v2/%s/backups/%s' % (
            fake.PROJECT_ID, fake.WILL_NOT_BE_FOUND_ID))
        req.method = 'GET'
        req.headers['Content-Type'] = 'application/json'
        res = req.get_response(fakes.wsgi_app(
            fake_auth_context=self.user_context))
        res_dict = jsonutils.loads(res.body)

        self.assertEqual(404, res.status_int)
        self.assertEqual(404, res_dict['itemNotFound']['code'])
        self.assertEqual('Backup %s could not be found.' %
                         fake.WILL_NOT_BE_FOUND_ID,
                         res_dict['itemNotFound']['message'])

    def test_list_backups_json(self):
        backup_id1 = self._create_backup()
        backup_id2 = self._create_backup()
        backup_id3 = self._create_backup()

        req = webob.Request.blank('/v2/%s/backups' % fake.PROJECT_ID)
        req.method = 'GET'
        req.headers['Content-Type'] = 'application/json'
        res = req.get_response(fakes.wsgi_app(
            fake_auth_context=self.user_context))
        res_dict = jsonutils.loads(res.body)

        self.assertEqual(200, res.status_int)
        self.assertEqual(3, len(res_dict['backups'][0]))
        self.assertEqual(backup_id3, res_dict['backups'][0]['id'])
        self.assertEqual('test_backup', res_dict['backups'][0]['name'])
        self.assertEqual(3, len(res_dict['backups'][1]))
        self.assertEqual(backup_id2, res_dict['backups'][1]['id'])
        self.assertEqual('test_backup', res_dict['backups'][1]['name'])
        self.assertEqual(3, len(res_dict['backups'][2]))
        self.assertEqual(backup_id1, res_dict['backups'][2]['id'])
        self.assertEqual('test_backup', res_dict['backups'][2]['name'])

        db.backup_destroy(context.get_admin_context(), backup_id3)
        db.backup_destroy(context.get_admin_context(), backup_id2)
        db.backup_destroy(context.get_admin_context(), backup_id1)

    def test_list_backups_with_limit(self):
        backup_id1 = self._create_backup()
        backup_id2 = self._create_backup()
        backup_id3 = self._create_backup()

        req = webob.Request.blank('/v2/%s/backups?limit=2' % fake.PROJECT_ID)
        req.method = 'GET'
        req.headers['Content-Type'] = 'application/json'
        res = req.get_response(fakes.wsgi_app(
            fake_auth_context=self.user_context))
        res_dict = jsonutils.loads(res.body)

        self.assertEqual(200, res.status_int)
        self.assertEqual(2, len(res_dict['backups']))
        self.assertEqual(3, len(res_dict['backups'][0]))
        self.assertEqual(backup_id3, res_dict['backups'][0]['id'])
        self.assertEqual('test_backup', res_dict['backups'][0]['name'])
        self.assertEqual(3, len(res_dict['backups'][1]))
        self.assertEqual(backup_id2, res_dict['backups'][1]['id'])
        self.assertEqual('test_backup', res_dict['backups'][1]['name'])

        db.backup_destroy(context.get_admin_context(), backup_id3)
        db.backup_destroy(context.get_admin_context(), backup_id2)
        db.backup_destroy(context.get_admin_context(), backup_id1)

    def test_list_backups_with_offset_out_of_range(self):
        url = '/v2/%s/backups?offset=252452434242342434' % fake.PROJECT_ID
        req = webob.Request.blank(url)
        req.method = 'GET'
        req.headers['Content-Type'] = 'application/json'
        res = req.get_response(fakes.wsgi_app(
            fake_auth_context=self.user_context))
        self.assertEqual(400, res.status_int)

    def test_list_backups_with_marker(self):
        backup_id1 = self._create_backup()
        backup_id2 = self._create_backup()
        backup_id3 = self._create_backup()
        url = '/v2/%s/backups?marker=%s' % (fake.PROJECT_ID, backup_id3)
        req = webob.Request.blank(url)
        req.method = 'GET'
        req.headers['Content-Type'] = 'application/json'
        res = req.get_response(fakes.wsgi_app(
            fake_auth_context=self.user_context))
        res_dict = jsonutils.loads(res.body)

        self.assertEqual(200, res.status_int)
        self.assertEqual(2, len(res_dict['backups']))
        self.assertEqual(3, len(res_dict['backups'][0]))
        self.assertEqual(backup_id2, res_dict['backups'][0]['id'])
        self.assertEqual('test_backup', res_dict['backups'][0]['name'])
        self.assertEqual(3, len(res_dict['backups'][1]))
        self.assertEqual(backup_id1, res_dict['backups'][1]['id'])
        self.assertEqual('test_backup', res_dict['backups'][1]['name'])

        db.backup_destroy(context.get_admin_context(), backup_id3)
        db.backup_destroy(context.get_admin_context(), backup_id2)
        db.backup_destroy(context.get_admin_context(), backup_id1)

    def test_list_backups_with_limit_and_marker(self):
        backup_id1 = self._create_backup()
        backup_id2 = self._create_backup()
        backup_id3 = self._create_backup()

        url = ('/v2/%s/backups?limit=1&marker=%s' % (fake.PROJECT_ID,
                                                     backup_id3))
        req = webob.Request.blank(url)
        req.method = 'GET'
        req.headers['Content-Type'] = 'application/json'
        res = req.get_response(fakes.wsgi_app(
            fake_auth_context=self.user_context))
        res_dict = jsonutils.loads(res.body)

        self.assertEqual(200, res.status_int)
        self.assertEqual(1, len(res_dict['backups']))
        self.assertEqual(3, len(res_dict['backups'][0]))
        self.assertEqual(backup_id2, res_dict['backups'][0]['id'])
        self.assertEqual('test_backup', res_dict['backups'][0]['name'])

        db.backup_destroy(context.get_admin_context(), backup_id3)
        db.backup_destroy(context.get_admin_context(), backup_id2)
        db.backup_destroy(context.get_admin_context(), backup_id1)

    def test_list_backups_detail_json(self):
        backup_id1 = self._create_backup()
        backup_id2 = self._create_backup()
        backup_id3 = self._create_backup()

        req = webob.Request.blank('/v2/%s/backups/detail' % fake.PROJECT_ID)
        req.method = 'GET'
        req.headers['Content-Type'] = 'application/json'
        req.headers['Accept'] = 'application/json'
        res = req.get_response(fakes.wsgi_app(
            fake_auth_context=self.user_context))
        res_dict = jsonutils.loads(res.body)

        self.assertEqual(200, res.status_int)
        self.assertEqual(NUM_ELEMENTS_IN_BACKUP, len(res_dict['backups'][0]))
        self.assertEqual('az1', res_dict['backups'][0]['availability_zone'])
        self.assertEqual('volumebackups',
                         res_dict['backups'][0]['container'])
        self.assertEqual('this is a test backup',
                         res_dict['backups'][0]['description'])
        self.assertEqual('test_backup',
                         res_dict['backups'][0]['name'])
        self.assertEqual(backup_id3, res_dict['backups'][0]['id'])
        self.assertEqual(0, res_dict['backups'][0]['object_count'])
        self.assertEqual(0, res_dict['backups'][0]['size'])
        self.assertEqual(fields.BackupStatus.CREATING,
                         res_dict['backups'][0]['status'])
        self.assertEqual(fake.VOLUME_ID, res_dict['backups'][0]['volume_id'])
        self.assertIn('updated_at', res_dict['backups'][0])

        self.assertEqual(NUM_ELEMENTS_IN_BACKUP, len(res_dict['backups'][1]))
        self.assertEqual('az1', res_dict['backups'][1]['availability_zone'])
        self.assertEqual('volumebackups',
                         res_dict['backups'][1]['container'])
        self.assertEqual('this is a test backup',
                         res_dict['backups'][1]['description'])
        self.assertEqual('test_backup',
                         res_dict['backups'][1]['name'])
        self.assertEqual(backup_id2, res_dict['backups'][1]['id'])
        self.assertEqual(0, res_dict['backups'][1]['object_count'])
        self.assertEqual(0, res_dict['backups'][1]['size'])
        self.assertEqual(fields.BackupStatus.CREATING,
                         res_dict['backups'][1]['status'])
        self.assertEqual(fake.VOLUME_ID, res_dict['backups'][1]['volume_id'])
        self.assertIn('updated_at', res_dict['backups'][1])

        self.assertEqual(NUM_ELEMENTS_IN_BACKUP, len(res_dict['backups'][2]))
        self.assertEqual('az1', res_dict['backups'][2]['availability_zone'])
        self.assertEqual('volumebackups', res_dict['backups'][2]['container'])
        self.assertEqual('this is a test backup',
                         res_dict['backups'][2]['description'])
        self.assertEqual('test_backup',
                         res_dict['backups'][2]['name'])
        self.assertEqual(backup_id1, res_dict['backups'][2]['id'])
        self.assertEqual(0, res_dict['backups'][2]['object_count'])
        self.assertEqual(0, res_dict['backups'][2]['size'])
        self.assertEqual(fields.BackupStatus.CREATING,
                         res_dict['backups'][2]['status'])
        self.assertEqual(fake.VOLUME_ID, res_dict['backups'][2]['volume_id'])
        self.assertIn('updated_at', res_dict['backups'][2])

        db.backup_destroy(context.get_admin_context(), backup_id3)
        db.backup_destroy(context.get_admin_context(), backup_id2)
        db.backup_destroy(context.get_admin_context(), backup_id1)

    def test_list_backups_detail_using_filters(self):
        backup_id1 = self._create_backup(display_name='test2')
        backup_id2 = self._create_backup(status=fields.BackupStatus.AVAILABLE)
        backup_id3 = self._create_backup(volume_id=fake.VOLUME3_ID)

        req = webob.Request.blank('/v2/%s/backups/detail?name=test2' %
                                  fake.PROJECT_ID)
        req.method = 'GET'
        req.headers['Content-Type'] = 'application/json'
        req.headers['Accept'] = 'application/json'
        res = req.get_response(fakes.wsgi_app(
            fake_auth_context=self.user_context))
        res_dict = jsonutils.loads(res.body)

        self.assertEqual(1, len(res_dict['backups']))
        self.assertEqual(200, res.status_int)
        self.assertEqual(backup_id1, res_dict['backups'][0]['id'])

        req = webob.Request.blank('/v2/%s/backups/detail?status=available' %
                                  fake.PROJECT_ID)
        req.method = 'GET'
        req.headers['Content-Type'] = 'application/json'
        req.headers['Accept'] = 'application/json'
        res = req.get_response(fakes.wsgi_app(
            fake_auth_context=self.user_context))
        res_dict = jsonutils.loads(res.body)

        self.assertEqual(1, len(res_dict['backups']))
        self.assertEqual(200, res.status_int)
        self.assertEqual(backup_id2, res_dict['backups'][0]['id'])

        req = webob.Request.blank('/v2/%s/backups/detail?volume_id=%s' % (
            fake.PROJECT_ID, fake.VOLUME3_ID))
        req.method = 'GET'
        req.headers['Content-Type'] = 'application/json'
        req.headers['Accept'] = 'application/json'
        res = req.get_response(fakes.wsgi_app(
            fake_auth_context=self.user_context))
        res_dict = jsonutils.loads(res.body)

        self.assertEqual(1, len(res_dict['backups']))
        self.assertEqual(200, res.status_int)
        self.assertEqual(backup_id3, res_dict['backups'][0]['id'])

        db.backup_destroy(context.get_admin_context(), backup_id3)
        db.backup_destroy(context.get_admin_context(), backup_id2)
        db.backup_destroy(context.get_admin_context(), backup_id1)

    def test_list_backups_detail_with_limit_and_sort_args(self):
        backup_id1 = self._create_backup()
        backup_id2 = self._create_backup()
        backup_id3 = self._create_backup()
        url = ('/v2/%s/backups/detail?limit=2&sort_key=created_at'
               '&sort_dir=desc' % fake.PROJECT_ID)
        req = webob.Request.blank(url)
        req.method = 'GET'
        req.headers['Content-Type'] = 'application/json'
        res = req.get_response(fakes.wsgi_app(
            fake_auth_context=self.user_context))
        res_dict = jsonutils.loads(res.body)

        self.assertEqual(200, res.status_int)
        self.assertEqual(2, len(res_dict['backups']))
        self.assertEqual(NUM_ELEMENTS_IN_BACKUP, len(res_dict['backups'][0]))
        self.assertEqual(backup_id3, res_dict['backups'][0]['id'])
        self.assertEqual(NUM_ELEMENTS_IN_BACKUP, len(res_dict['backups'][1]))
        self.assertEqual(backup_id2, res_dict['backups'][1]['id'])

        db.backup_destroy(context.get_admin_context(), backup_id3)
        db.backup_destroy(context.get_admin_context(), backup_id2)
        db.backup_destroy(context.get_admin_context(), backup_id1)

    def test_list_backups_detail_with_marker(self):
        backup_id1 = self._create_backup()
        backup_id2 = self._create_backup()
        backup_id3 = self._create_backup()

        url = ('/v2/%s/backups/detail?marker=%s' % (
            fake.PROJECT_ID, backup_id3))
        req = webob.Request.blank(url)
        req.method = 'GET'
        req.headers['Content-Type'] = 'application/json'
        res = req.get_response(fakes.wsgi_app(
            fake_auth_context=self.user_context))
        res_dict = jsonutils.loads(res.body)

        self.assertEqual(200, res.status_int)
        self.assertEqual(2, len(res_dict['backups']))
        self.assertEqual(NUM_ELEMENTS_IN_BACKUP, len(res_dict['backups'][0]))
        self.assertEqual(backup_id2, res_dict['backups'][0]['id'])
        self.assertEqual(NUM_ELEMENTS_IN_BACKUP, len(res_dict['backups'][1]))
        self.assertEqual(backup_id1, res_dict['backups'][1]['id'])

        db.backup_destroy(context.get_admin_context(), backup_id3)
        db.backup_destroy(context.get_admin_context(), backup_id2)
        db.backup_destroy(context.get_admin_context(), backup_id1)

    def test_list_backups_detail_with_limit_and_marker(self):
        backup_id1 = self._create_backup()
        backup_id2 = self._create_backup()
        backup_id3 = self._create_backup()

        url = ('/v2/%s/backups/detail?limit=1&marker=%s' % (
            fake.PROJECT_ID, backup_id3))
        req = webob.Request.blank(url)
        req.method = 'GET'
        req.headers['Content-Type'] = 'application/json'
        res = req.get_response(fakes.wsgi_app(
            fake_auth_context=self.user_context))
        res_dict = jsonutils.loads(res.body)

        self.assertEqual(200, res.status_int)
        self.assertEqual(1, len(res_dict['backups']))
        self.assertEqual(NUM_ELEMENTS_IN_BACKUP, len(res_dict['backups'][0]))
        self.assertEqual(backup_id2, res_dict['backups'][0]['id'])

        db.backup_destroy(context.get_admin_context(), backup_id3)
        db.backup_destroy(context.get_admin_context(), backup_id2)
        db.backup_destroy(context.get_admin_context(), backup_id1)

    def test_list_backups_detail_with_offset_out_of_range(self):
        url = ('/v2/%s/backups/detail?offset=234534543657634523' %
               fake.PROJECT_ID)
        req = webob.Request.blank(url)
        req.method = 'GET'
        req.headers['Content-Type'] = 'application/json'
        res = req.get_response(fakes.wsgi_app(
            fake_auth_context=self.user_context))
        self.assertEqual(400, res.status_int)

    @mock.patch('cinder.db.service_get_all')
    @mock.patch(
        'cinder.api.openstack.wsgi.Controller.validate_name_and_description')
    def test_create_backup_json(self, mock_validate,
                                _mock_service_get_all):
        _mock_service_get_all.return_value = [
            {'availability_zone': 'fake_az', 'host': 'testhost',
             'disabled': 0, 'updated_at': timeutils.utcnow()}]

        volume_id = utils.create_volume(self.context, size=5).id

        body = {"backup": {"display_name": "nightly001",
                           "display_description":
                           "Nightly Backup 03-Sep-2012",
                           "volume_id": volume_id,
                           "container": "nightlybackups",
                           }
                }
        req = webob.Request.blank('/v2/%s/backups' % fake.PROJECT_ID)
        req.method = 'POST'
        req.headers['Content-Type'] = 'application/json'
        req.body = jsonutils.dump_as_bytes(body)
        res = req.get_response(fakes.wsgi_app(
            fake_auth_context=self.user_context))

        res_dict = jsonutils.loads(res.body)

        self.assertEqual(202, res.status_int)
        self.assertIn('id', res_dict['backup'])
        _mock_service_get_all.assert_called_once_with(mock.ANY,
                                                      disabled=False,
                                                      topic='cinder-backup')
        self.assertTrue(mock_validate.called)

        db.volume_destroy(context.get_admin_context(), volume_id)

    @mock.patch('cinder.db.service_get_all')
    def test_create_backup_inuse_no_force(self,
                                          _mock_service_get_all):
        _mock_service_get_all.return_value = [
            {'availability_zone': 'fake_az', 'host': 'testhost',
             'disabled': 0, 'updated_at': timeutils.utcnow()}]

        volume_id = utils.create_volume(self.context, size=5,
                                        status='in-use').id

        body = {"backup": {"display_name": "nightly001",
                           "display_description":
                           "Nightly Backup 03-Sep-2012",
                           "volume_id": volume_id,
                           "container": "nightlybackups",
                           }
                }
        req = webob.Request.blank('/v2/%s/backups' % fake.PROJECT_ID)
        req.method = 'POST'
        req.headers['Content-Type'] = 'application/json'
        req.body = jsonutils.dump_as_bytes(body)
        res = req.get_response(fakes.wsgi_app(
            fake_auth_context=self.user_context))

        res_dict = jsonutils.loads(res.body)

        self.assertEqual(400, res.status_int)
        self.assertEqual(400, res_dict['badRequest']['code'])
        self.assertIsNotNone(res_dict['badRequest']['message'])

        db.volume_destroy(context.get_admin_context(), volume_id)

    @mock.patch('cinder.db.service_get_all')
    def test_create_backup_inuse_force(self, _mock_service_get_all):
        _mock_service_get_all.return_value = [
            {'availability_zone': 'fake_az', 'host': 'testhost',
             'disabled': 0, 'updated_at': timeutils.utcnow()}]

        volume_id = utils.create_volume(self.context, size=5,
                                        status='in-use').id
        backup_id = self._create_backup(volume_id,
                                        status=fields.BackupStatus.AVAILABLE)
        body = {"backup": {"display_name": "nightly001",
                           "display_description":
                           "Nightly Backup 03-Sep-2012",
                           "volume_id": volume_id,
                           "container": "nightlybackups",
                           "force": True,
                           }
                }
        req = webob.Request.blank('/v2/%s/backups' % fake.PROJECT_ID)
        req.method = 'POST'
        req.headers['Content-Type'] = 'application/json'
        req.body = jsonutils.dump_as_bytes(body)
        res = req.get_response(fakes.wsgi_app(
            fake_auth_context=self.user_context))

        res_dict = jsonutils.loads(res.body)

        self.assertEqual(202, res.status_int)
        self.assertIn('id', res_dict['backup'])
        _mock_service_get_all.assert_called_once_with(mock.ANY,
                                                      disabled=False,
                                                      topic='cinder-backup')

        db.backup_destroy(context.get_admin_context(), backup_id)
        db.volume_destroy(context.get_admin_context(), volume_id)

    @mock.patch('cinder.db.service_get_all')
    @mock.patch(
        'cinder.api.openstack.wsgi.Controller.validate_name_and_description')
    def test_create_backup_snapshot_json(self, mock_validate,
                                         _mock_service_get_all):
        _mock_service_get_all.return_value = [
            {'availability_zone': 'fake_az', 'host': 'testhost',
             'disabled': 0, 'updated_at': timeutils.utcnow()}]

        volume_id = utils.create_volume(self.context, size=5,
                                        status='available').id

        body = {"backup": {"display_name": "nightly001",
                           "display_description":
                           "Nightly Backup 03-Sep-2012",
                           "volume_id": volume_id,
                           "container": "nightlybackups",
                           }
                }
        req = webob.Request.blank('/v2/%s/backups' % fake.PROJECT_ID)
        req.method = 'POST'
        req.headers['Content-Type'] = 'application/json'
        req.body = jsonutils.dump_as_bytes(body)
        res = req.get_response(fakes.wsgi_app(
            fake_auth_context=self.user_context))

        res_dict = jsonutils.loads(res.body)
        self.assertEqual(202, res.status_int)
        self.assertIn('id', res_dict['backup'])
        _mock_service_get_all.assert_called_once_with(mock.ANY,
                                                      disabled=False,
                                                      topic='cinder-backup')
        self.assertTrue(mock_validate.called)

        db.volume_destroy(context.get_admin_context(), volume_id)

    def test_create_backup_snapshot_with_inconsistent_volume(self):
        volume_id = utils.create_volume(self.context, size=5,
                                        status='available').id
        volume_id2 = utils.create_volume(self.context, size=5,
                                         status='available').id
        snapshot_id = utils.create_snapshot(self.context,
                                            volume_id,
                                            status='available')['id']

        self.addCleanup(db.volume_destroy,
                        self.context.elevated(),
                        volume_id)
        self.addCleanup(db.volume_destroy,
                        self.context.elevated(),
                        volume_id2)
        self.addCleanup(db.snapshot_destroy,
                        self.context.elevated(),
                        snapshot_id)
        body = {"backup": {"display_name": "nightly001",
                           "display_description":
                           "Nightly Backup 03-Sep-2012",
                           "volume_id": volume_id2,
                           "snapshot_id": snapshot_id,
                           "container": "nightlybackups",
                           }
                }
        req = webob.Request.blank('/v2/%s/backups' % fake.PROJECT_ID)
        req.method = 'POST'
        req.headers['Content-Type'] = 'application/json'
        req.body = jsonutils.dump_as_bytes(body)
        res = req.get_response(fakes.wsgi_app(
            fake_auth_context=self.user_context))

        res_dict = jsonutils.loads(res.body)
        self.assertEqual(400, res.status_int)
        self.assertIsNotNone(res_dict['badRequest']['message'])

    def test_create_backup_with_invalid_snapshot(self):
        volume_id = utils.create_volume(self.context, size=5,
                                        status='available').id
        snapshot_id = utils.create_snapshot(self.context, volume_id,
                                            status='error')['id']
        body = {"backup": {"display_name": "nightly001",
                           "display_description":
                           "Nightly Backup 03-Sep-2012",
                           "snapshot_id": snapshot_id,
                           "volume_id": volume_id,
                           }
                }
        self.addCleanup(db.volume_destroy,
                        self.context.elevated(),
                        volume_id)
        self.addCleanup(db.snapshot_destroy,
                        self.context.elevated(),
                        snapshot_id)
        req = webob.Request.blank('/v2/%s/backups' % fake.PROJECT_ID)
        req.method = 'POST'
        req.headers['Content-Type'] = 'application/json'
        req.body = jsonutils.dump_as_bytes(body)
        res = req.get_response(fakes.wsgi_app(
            fake_auth_context=self.user_context))

        res_dict = jsonutils.loads(res.body)
        self.assertEqual(400, res.status_int)
        self.assertEqual(400, res_dict['badRequest']['code'])
        self.assertIsNotNone(res_dict['badRequest']['message'])

    def test_create_backup_with_non_existent_snapshot(self):
        volume_id = utils.create_volume(self.context, size=5,
                                        status='restoring').id
        body = {"backup": {"display_name": "nightly001",
                           "display_description":
                           "Nightly Backup 03-Sep-2012",
                           "snapshot_id": fake.SNAPSHOT_ID,
                           "volume_id": volume_id,
                           }
                }
        self.addCleanup(db.volume_destroy,
                        self.context.elevated(),
                        volume_id)
        req = webob.Request.blank('/v2/%s/backups' % fake.PROJECT_ID)
        req.method = 'POST'
        req.headers['Content-Type'] = 'application/json'
        req.body = jsonutils.dump_as_bytes(body)
        res = req.get_response(fakes.wsgi_app(
            fake_auth_context=self.user_context))

        res_dict = jsonutils.loads(res.body)
        self.assertEqual(404, res.status_int)
        self.assertEqual(404, res_dict['itemNotFound']['code'])
        self.assertIsNotNone(res_dict['itemNotFound']['message'])

    def test_create_backup_with_invalid_container(self):
        volume_id = utils.create_volume(self.context, size=5,
                                        status='available').id
        body = {"backup": {"display_name": "nightly001",
                           "display_description": "Nightly Backup 03-Sep-2012",
                           "volume_id": volume_id,
                           "container": "a" * 256
                           }
                }
        req = webob.Request.blank('/v2/%s/backups' % fake.PROJECT_ID)
        req.method = 'POST'
        req.environ['cinder.context'] = self.context
        self.assertRaises(exception.InvalidInput,
                          self.controller.create,
                          req,
                          body)

    @mock.patch('cinder.db.service_get_all')
    @mock.patch(
        'cinder.api.openstack.wsgi.Controller.validate_name_and_description')
    @ddt.data(False, True)
    def test_create_backup_delta(self, backup_from_snapshot,
                                 mock_validate,
                                 _mock_service_get_all):
        _mock_service_get_all.return_value = [
            {'availability_zone': 'fake_az', 'host': 'testhost',
             'disabled': 0, 'updated_at': timeutils.utcnow()}]

        volume_id = utils.create_volume(self.context, size=5).id
        snapshot = None
        snapshot_id = None
        if backup_from_snapshot:
            snapshot = utils.create_snapshot(self.context,
                                             volume_id,
                                             status=
                                             fields.SnapshotStatus.AVAILABLE)
            snapshot_id = snapshot.id
        backup_id = self._create_backup(volume_id,
                                        status=fields.BackupStatus.AVAILABLE)
        body = {"backup": {"display_name": "nightly001",
                           "display_description":
                           "Nightly Backup 03-Sep-2012",
                           "volume_id": volume_id,
                           "container": "nightlybackups",
                           "incremental": True,
                           "snapshot_id": snapshot_id,
                           }
                }
        req = webob.Request.blank('/v2/%s/backups' % fake.PROJECT_ID)
        req.method = 'POST'
        req.headers['Content-Type'] = 'application/json'
        req.body = jsonutils.dump_as_bytes(body)
        res = req.get_response(fakes.wsgi_app(
            fake_auth_context=self.user_context))
        res_dict = jsonutils.loads(res.body)

        self.assertEqual(202, res.status_int)
        self.assertIn('id', res_dict['backup'])
        _mock_service_get_all.assert_called_once_with(mock.ANY,
                                                      disabled=False,
                                                      topic='cinder-backup')
        self.assertTrue(mock_validate.called)

        db.backup_destroy(context.get_admin_context(), backup_id)
        if snapshot:
            snapshot.destroy()
        db.volume_destroy(context.get_admin_context(), volume_id)

    @mock.patch('cinder.db.service_get_all')
    def test_create_incremental_backup_invalid_status(
            self, _mock_service_get_all):
        _mock_service_get_all.return_value = [
            {'availability_zone': 'fake_az', 'host': 'testhost',
             'disabled': 0, 'updated_at': timeutils.utcnow()}]

        volume_id = utils.create_volume(self.context, size=5).id

        backup_id = self._create_backup(volume_id)
        body = {"backup": {"display_name": "nightly001",
                           "display_description":
                           "Nightly Backup 03-Sep-2012",
                           "volume_id": volume_id,
                           "container": "nightlybackups",
                           "incremental": True,
                           }
                }
        req = webob.Request.blank('/v2/%s/backups' % fake.PROJECT_ID)
        req.method = 'POST'
        req.headers['Content-Type'] = 'application/json'
        req.body = jsonutils.dump_as_bytes(body)
        res = req.get_response(fakes.wsgi_app(
            fake_auth_context=self.user_context))
        res_dict = jsonutils.loads(res.body)

        self.assertEqual(400, res_dict['badRequest']['code'])
        self.assertEqual('Invalid backup: The parent backup must be '
                         'available for incremental backup.',
                         res_dict['badRequest']['message'])

        db.backup_destroy(context.get_admin_context(), backup_id)
        db.volume_destroy(context.get_admin_context(), volume_id)

    def test_create_backup_with_no_body(self):
        # omit body from the request
        req = webob.Request.blank('/v2/%s/backups' % fake.PROJECT_ID)
        req.body = jsonutils.dump_as_bytes(None)
        req.method = 'POST'
        req.headers['Content-Type'] = 'application/json'
        req.headers['Accept'] = 'application/json'
        res = req.get_response(fakes.wsgi_app(
            fake_auth_context=self.user_context))
        res_dict = jsonutils.loads(res.body)

        self.assertEqual(400, res.status_int)
        self.assertEqual(400, res_dict['badRequest']['code'])
        self.assertEqual("Missing required element 'backup' in request body.",
                         res_dict['badRequest']['message'])

    def test_create_backup_with_body_KeyError(self):
        # omit volume_id from body
        body = {"backup": {"display_name": "nightly001",
                           "display_description":
                           "Nightly Backup 03-Sep-2012",
                           "container": "nightlybackups",
                           }
                }
        req = webob.Request.blank('/v2/%s/backups' % fake.PROJECT_ID)
        req.method = 'POST'
        req.headers['Content-Type'] = 'application/json'
        req.body = jsonutils.dump_as_bytes(body)
        res = req.get_response(fakes.wsgi_app(
            fake_auth_context=self.user_context))
        res_dict = jsonutils.loads(res.body)

        self.assertEqual(400, res.status_int)
        self.assertEqual(400, res_dict['badRequest']['code'])
        self.assertEqual('Incorrect request body format',
                         res_dict['badRequest']['message'])

    def test_create_backup_with_VolumeNotFound(self):
        body = {"backup": {"display_name": "nightly001",
                           "display_description":
                           "Nightly Backup 03-Sep-2012",
                           "volume_id": fake.WILL_NOT_BE_FOUND_ID,
                           "container": "nightlybackups",
                           }
                }
        req = webob.Request.blank('/v2/%s/backups' % fake.PROJECT_ID)
        req.method = 'POST'
        req.headers['Content-Type'] = 'application/json'
        req.body = jsonutils.dump_as_bytes(body)
        res = req.get_response(fakes.wsgi_app(
            fake_auth_context=self.user_context))
        res_dict = jsonutils.loads(res.body)

        self.assertEqual(404, res.status_int)
        self.assertEqual(404, res_dict['itemNotFound']['code'])
        self.assertEqual('Volume %s could not be found.' %
                         fake.WILL_NOT_BE_FOUND_ID,
                         res_dict['itemNotFound']['message'])

    def test_create_backup_with_InvalidVolume(self):
        # need to create the volume referenced below first
        volume_id = utils.create_volume(self.context, size=5,
                                        status='restoring').id
        body = {"backup": {"display_name": "nightly001",
                           "display_description":
                           "Nightly Backup 03-Sep-2012",
                           "volume_id": volume_id,
                           "container": "nightlybackups",
                           }
                }
        req = webob.Request.blank('/v2/%s/backups' % fake.PROJECT_ID)
        req.method = 'POST'
        req.headers['Content-Type'] = 'application/json'
        req.body = jsonutils.dump_as_bytes(body)
        res = req.get_response(fakes.wsgi_app(
            fake_auth_context=self.user_context))
        res_dict = jsonutils.loads(res.body)

        self.assertEqual(400, res.status_int)
        self.assertEqual(400, res_dict['badRequest']['code'])

    @mock.patch('cinder.db.service_get_all')
    def test_create_backup_WithOUT_enabled_backup_service(
            self,
            _mock_service_get_all):
        # need an enabled backup service available
        _mock_service_get_all.return_value = []

        volume_id = utils.create_volume(self.context, size=2).id
        req = webob.Request.blank('/v2/%s/backups' % fake.PROJECT_ID)
        body = {"backup": {"display_name": "nightly001",
                           "display_description":
                           "Nightly Backup 03-Sep-2012",
                           "volume_id": volume_id,
                           "container": "nightlybackups",
                           }
                }
        req.method = 'POST'
        req.headers['Content-Type'] = 'application/json'
        req.headers['Accept'] = 'application/json'
        req.body = jsonutils.dump_as_bytes(body)

        res = req.get_response(fakes.wsgi_app(
            fake_auth_context=self.user_context))
        res_dict = jsonutils.loads(res.body)
        self.assertEqual(500, res.status_int)
        self.assertEqual(500, res_dict['computeFault']['code'])
        self.assertEqual('Service cinder-backup could not be found.',
                         res_dict['computeFault']['message'])

        volume = self.volume_api.get(context.get_admin_context(), volume_id)
        self.assertEqual('available', volume['status'])

    @mock.patch('cinder.db.service_get_all')
    def test_create_incremental_backup_invalid_no_full(
            self, _mock_service_get_all):
        _mock_service_get_all.return_value = [
            {'availability_zone': 'fake_az', 'host': 'testhost',
             'disabled': 0, 'updated_at': timeutils.utcnow()}]

        volume_id = utils.create_volume(self.context, size=5,
                                        status='available').id

        body = {"backup": {"display_name": "nightly001",
                           "display_description":
                           "Nightly Backup 03-Sep-2012",
                           "volume_id": volume_id,
                           "container": "nightlybackups",
                           "incremental": True,
                           }
                }
        req = webob.Request.blank('/v2/%s/backups' % fake.PROJECT_ID)
        req.method = 'POST'
        req.headers['Content-Type'] = 'application/json'
        req.body = jsonutils.dump_as_bytes(body)
        res = req.get_response(fakes.wsgi_app(
            fake_auth_context=self.user_context))
        res_dict = jsonutils.loads(res.body)

        self.assertEqual(400, res_dict['badRequest']['code'])
        self.assertEqual('Invalid backup: No backups available to do '
                         'an incremental backup.',
                         res_dict['badRequest']['message'])

        db.volume_destroy(context.get_admin_context(), volume_id)

    @mock.patch('cinder.db.service_get_all')
    def test_is_backup_service_enabled(self, _mock_service_get_all):

        testhost = 'test_host'
        alt_host = 'strange_host'
        empty_service = []
        # service host not match with volume's host
        host_not_match = [{'availability_zone': 'fake_az', 'host': alt_host,
                           'disabled': 0, 'updated_at': timeutils.utcnow()}]
        # service az not match with volume's az
        az_not_match = [{'availability_zone': 'strange_az', 'host': testhost,
                         'disabled': 0, 'updated_at': timeutils.utcnow()}]
        # service disabled
        disabled_service = []

        # dead service that last reported at 20th century
        dead_service = [{'availability_zone': 'fake_az', 'host': alt_host,
                         'disabled': 0, 'updated_at': '1989-04-16 02:55:44'}]

        # first service's host not match but second one works.
        multi_services = [{'availability_zone': 'fake_az', 'host': alt_host,
                           'disabled': 0, 'updated_at': timeutils.utcnow()},
                          {'availability_zone': 'fake_az', 'host': testhost,
                           'disabled': 0, 'updated_at': timeutils.utcnow()}]

        # Setup mock to run through the following service cases
        _mock_service_get_all.side_effect = [empty_service,
                                             host_not_match,
                                             az_not_match,
                                             disabled_service,
                                             dead_service,
                                             multi_services]

        volume_id = utils.create_volume(self.context, size=2,
                                        host=testhost).id
        volume = self.volume_api.get(context.get_admin_context(), volume_id)

        # test empty service
        self.assertEqual(False,
                         self.backup_api._is_backup_service_enabled(
                             volume['availability_zone'],
                             testhost))

        # test host not match service
        self.assertEqual(False,
                         self.backup_api._is_backup_service_enabled(
                             volume['availability_zone'],
                             testhost))

        # test az not match service
        self.assertEqual(False,
                         self.backup_api._is_backup_service_enabled(
                             volume['availability_zone'],
                             testhost))

        # test disabled service
        self.assertEqual(False,
                         self.backup_api._is_backup_service_enabled(
                             volume['availability_zone'],
                             testhost))

        # test dead service
        self.assertEqual(False,
                         self.backup_api._is_backup_service_enabled(
                             volume['availability_zone'],
                             testhost))

        # test multi services and the last service matches
        self.assertTrue(self.backup_api._is_backup_service_enabled(
                        volume['availability_zone'],
                        testhost))

    @mock.patch('cinder.db.service_get_all')
    def test_get_available_backup_service(self, _mock_service_get_all):
        _mock_service_get_all.return_value = [
            {'availability_zone': 'az1', 'host': 'testhost1',
             'disabled': 0, 'updated_at': timeutils.utcnow()},
            {'availability_zone': 'az2', 'host': 'testhost2',
             'disabled': 0, 'updated_at': timeutils.utcnow()},
            {'availability_zone': 'az2', 'host': 'testhost3',
             'disabled': 0, 'updated_at': timeutils.utcnow()}, ]
        actual_host = self.backup_api._get_available_backup_service_host(
            None, 'az1')
        self.assertEqual('testhost1', actual_host)
        actual_host = self.backup_api._get_available_backup_service_host(
            'testhost2', 'az2')
        self.assertIn(actual_host, ['testhost2', 'testhost3'])
        actual_host = self.backup_api._get_available_backup_service_host(
            'testhost4', 'az1')
        self.assertEqual('testhost1', actual_host)

    @mock.patch('cinder.db.service_get_all')
    def test_get_available_backup_service_with_same_host(
            self, _mock_service_get_all):
        _mock_service_get_all.return_value = [
            {'availability_zone': 'az1', 'host': 'testhost1',
             'disabled': 0, 'updated_at': timeutils.utcnow()},
            {'availability_zone': 'az2', 'host': 'testhost2',
             'disabled': 0, 'updated_at': timeutils.utcnow()}, ]
        self.override_config('backup_use_same_host', True)
        actual_host = self.backup_api._get_available_backup_service_host(
            None, 'az1')
        self.assertEqual('testhost1', actual_host)
        actual_host = self.backup_api._get_available_backup_service_host(
            'testhost2', 'az2')
        self.assertEqual('testhost2', actual_host)
        self.assertRaises(exception.ServiceNotFound,
                          self.backup_api._get_available_backup_service_host,
                          'testhost4', 'az1')

    @mock.patch('cinder.db.service_get_all')
    def test_delete_backup_available(self, _mock_service_get_all):
        _mock_service_get_all.return_value = [
            {'availability_zone': 'az1', 'host': 'testhost',
             'disabled': 0, 'updated_at': timeutils.utcnow()}]
        backup_id = self._create_backup(status=fields.BackupStatus.AVAILABLE)
        req = webob.Request.blank('/v2/%s/backups/%s' % (
                                  fake.PROJECT_ID, backup_id))
        req.method = 'DELETE'
        req.headers['Content-Type'] = 'application/json'
        res = req.get_response(fakes.wsgi_app(
            fake_auth_context=self.user_context))

        self.assertEqual(202, res.status_int)
        self.assertEqual(fields.BackupStatus.DELETING,
                         self._get_backup_attrib(backup_id, 'status'))

        db.backup_destroy(context.get_admin_context(), backup_id)

    @mock.patch('cinder.db.service_get_all')
    def test_delete_delta_backup(self,
                                 _mock_service_get_all):
        _mock_service_get_all.return_value = [
            {'availability_zone': 'az1', 'host': 'testhost',
             'disabled': 0, 'updated_at': timeutils.utcnow()}]
        backup_id = self._create_backup(status=fields.BackupStatus.AVAILABLE)
        delta_id = self._create_backup(status=fields.BackupStatus.AVAILABLE,
                                       incremental=True)
        req = webob.Request.blank('/v2/%s/backups/%s' % (
                                  fake.PROJECT_ID, delta_id))
        req.method = 'DELETE'
        req.headers['Content-Type'] = 'application/json'
        res = req.get_response(fakes.wsgi_app(
            fake_auth_context=self.user_context))

        self.assertEqual(202, res.status_int)
        self.assertEqual(fields.BackupStatus.DELETING,
                         self._get_backup_attrib(delta_id, 'status'))

        db.backup_destroy(context.get_admin_context(), delta_id)
        db.backup_destroy(context.get_admin_context(), backup_id)

    @mock.patch('cinder.db.service_get_all')
    def test_delete_backup_error(self,
                                 _mock_service_get_all):
        _mock_service_get_all.return_value = [
            {'availability_zone': 'az1', 'host': 'testhost',
             'disabled': 0, 'updated_at': timeutils.utcnow()}]
        backup_id = self._create_backup(status=fields.BackupStatus.ERROR)
        req = webob.Request.blank('/v2/%s/backups/%s' % (
                                  fake.PROJECT_ID, backup_id))
        req.method = 'DELETE'
        req.headers['Content-Type'] = 'application/json'
        res = req.get_response(fakes.wsgi_app(
            fake_auth_context=self.user_context))

        self.assertEqual(202, res.status_int)
        self.assertEqual(fields.BackupStatus.DELETING,
                         self._get_backup_attrib(backup_id, 'status'))

        db.backup_destroy(context.get_admin_context(), backup_id)

    def test_delete_backup_with_backup_NotFound(self):
        req = webob.Request.blank('/v2/%s/backups/%s' % (
                                  fake.PROJECT_ID, fake.WILL_NOT_BE_FOUND_ID))
        req.method = 'DELETE'
        req.headers['Content-Type'] = 'application/json'
        res = req.get_response(fakes.wsgi_app(
            fake_auth_context=self.user_context))
        res_dict = jsonutils.loads(res.body)

        self.assertEqual(404, res.status_int)
        self.assertEqual(404, res_dict['itemNotFound']['code'])
        self.assertEqual('Backup %s could not be found.' %
                         fake.WILL_NOT_BE_FOUND_ID,
                         res_dict['itemNotFound']['message'])

    def test_delete_backup_with_InvalidBackup(self):
        backup_id = self._create_backup()
        req = webob.Request.blank('/v2/%s/backups/%s' % (
                                  fake.PROJECT_ID, backup_id))
        req.method = 'DELETE'
        req.headers['Content-Type'] = 'application/json'
        res = req.get_response(fakes.wsgi_app(
            fake_auth_context=self.user_context))
        res_dict = jsonutils.loads(res.body)

        self.assertEqual(400, res.status_int)
        self.assertEqual(400, res_dict['badRequest']['code'])
        self.assertEqual('Invalid backup: Backup status must be '
                         'available or error',
                         res_dict['badRequest']['message'])

        db.backup_destroy(context.get_admin_context(), backup_id)

    @mock.patch('cinder.db.service_get_all')
    def test_delete_backup_with_InvalidBackup2(self,
                                               _mock_service_get_all):
        _mock_service_get_all.return_value = [
            {'availability_zone': 'az1', 'host': 'testhost',
             'disabled': 0, 'updated_at': timeutils.utcnow()}]
        volume_id = utils.create_volume(self.context, size=5).id
        backup_id = self._create_backup(volume_id,
                                        status=fields.BackupStatus.AVAILABLE)
        delta_backup_id = self._create_backup(
            status=fields.BackupStatus.AVAILABLE, incremental=True,
            parent_id=backup_id)

        req = webob.Request.blank('/v2/%s/backups/%s' % (
                                  fake.PROJECT_ID, backup_id))
        req.method = 'DELETE'
        req.headers['Content-Type'] = 'application/json'
        res = req.get_response(fakes.wsgi_app(
            fake_auth_context=self.user_context))
        res_dict = jsonutils.loads(res.body)
        self.assertEqual(400, res.status_int)
        self.assertEqual(400, res_dict['badRequest']['code'])
        self.assertEqual('Invalid backup: Incremental backups '
                         'exist for this backup.',
                         res_dict['badRequest']['message'])

        db.backup_destroy(context.get_admin_context(), delta_backup_id)
        db.backup_destroy(context.get_admin_context(), backup_id)

    @mock.patch('cinder.db.service_get_all')
    def test_delete_backup_service_down(self,
                                        _mock_service_get_all):
        _mock_service_get_all.return_value = [
            {'availability_zone': 'az1', 'host': 'testhost',
             'disabled': 0, 'updated_at': '1775-04-19 05:00:00'}]
        backup_id = self._create_backup(status='available')
        req = webob.Request.blank('/v2/%s/backups/%s' % (
                                  fake.PROJECT_ID, backup_id))
        req.method = 'DELETE'
        req.headers['Content-Type'] = 'application/json'
        res = req.get_response(fakes.wsgi_app(
            fake_auth_context=self.user_context))

        self.assertEqual(404, res.status_int)

        db.backup_destroy(context.get_admin_context(), backup_id)

    @mock.patch('cinder.backup.api.API._get_available_backup_service_host')
    def test_restore_backup_volume_id_specified_json(
            self, _mock_get_backup_host):
        _mock_get_backup_host.return_value = 'testhost'
        backup_id = self._create_backup(status=fields.BackupStatus.AVAILABLE)
        # need to create the volume referenced below first
        volume_name = 'test1'
        volume_id = utils.create_volume(self.context,
                                        size=5,
                                        display_name = volume_name).id

        body = {"restore": {"volume_id": volume_id, }}
        req = webob.Request.blank('/v2/%s/backups/%s/restore' % (
                                  fake.PROJECT_ID, backup_id))
        req.method = 'POST'
        req.headers['Content-Type'] = 'application/json'
        req.body = jsonutils.dump_as_bytes(body)
        res = req.get_response(fakes.wsgi_app(
            fake_auth_context=self.user_context))
        res_dict = jsonutils.loads(res.body)

        self.assertEqual(202, res.status_int)
        self.assertEqual(backup_id, res_dict['restore']['backup_id'])
        self.assertEqual(volume_id, res_dict['restore']['volume_id'])
        self.assertEqual(volume_name, res_dict['restore']['volume_name'])

    def test_restore_backup_with_no_body(self):
        # omit body from the request
        backup_id = self._create_backup(status=fields.BackupStatus.AVAILABLE)

        req = webob.Request.blank('/v2/%s/backups/%s/restore' % (
                                  fake.PROJECT_ID, backup_id))
        req.body = jsonutils.dump_as_bytes(None)
        req.method = 'POST'
        req.headers['Content-Type'] = 'application/json'
        req.headers['Accept'] = 'application/json'
        res = req.get_response(fakes.wsgi_app(
            fake_auth_context=self.user_context))
        res_dict = jsonutils.loads(res.body)

        self.assertEqual(400, res.status_int)
        self.assertEqual(400, res_dict['badRequest']['code'])
        self.assertEqual("Missing required element 'restore' in request body.",
                         res_dict['badRequest']['message'])

        db.backup_destroy(context.get_admin_context(), backup_id)

    def test_restore_backup_with_body_KeyError(self):
        # omit restore from body
        backup_id = self._create_backup(status=fields.BackupStatus.AVAILABLE)

        req = webob.Request.blank('/v2/%s/backups/%s/restore' % (
            fake.PROJECT_ID, backup_id))
        body = {"": {}}
        req.method = 'POST'
        req.headers['Content-Type'] = 'application/json'
        req.headers['Accept'] = 'application/json'
        req.body = jsonutils.dump_as_bytes(body)
        res = req.get_response(fakes.wsgi_app(
            fake_auth_context=self.user_context))

        res_dict = jsonutils.loads(res.body)

        self.assertEqual(400, res.status_int)
        self.assertEqual(400, res_dict['badRequest']['code'])
        self.assertEqual("Missing required element 'restore' in request body.",
                         res_dict['badRequest']['message'])

    @mock.patch('cinder.db.service_get_all')
    @mock.patch('cinder.volume.api.API.create')
    def test_restore_backup_volume_id_unspecified(
            self, _mock_volume_api_create, _mock_service_get_all):
        # intercept volume creation to ensure created volume
        # has status of available
        def fake_volume_api_create(context, size, name, description):
            volume_id = utils.create_volume(self.context, size=size).id
            return db.volume_get(context, volume_id)

        _mock_service_get_all.return_value = [
            {'availability_zone': 'az1', 'host': 'testhost',
             'disabled': 0, 'updated_at': timeutils.utcnow()}]
        _mock_volume_api_create.side_effect = fake_volume_api_create

        backup_id = self._create_backup(size=5,
                                        status=fields.BackupStatus.AVAILABLE)

        body = {"restore": {}}
        req = webob.Request.blank('/v2/%s/backups/%s/restore' % (
                                  fake.PROJECT_ID, backup_id))
        req.method = 'POST'
        req.headers['Content-Type'] = 'application/json'
        req.body = jsonutils.dump_as_bytes(body)
        res = req.get_response(fakes.wsgi_app(
            fake_auth_context=self.user_context))
        res_dict = jsonutils.loads(res.body)

        self.assertEqual(202, res.status_int)
        self.assertEqual(backup_id, res_dict['restore']['backup_id'])

    @mock.patch('cinder.db.service_get_all')
    @mock.patch('cinder.volume.api.API.create')
    def test_restore_backup_name_specified(self,
                                           _mock_volume_api_create,
                                           _mock_service_get_all):
        # Intercept volume creation to ensure created volume
        # has status of available
        def fake_volume_api_create(context, size, name, description):
            volume_id = utils.create_volume(self.context, size=size,
                                            display_name=name).id
            return db.volume_get(context, volume_id)

        _mock_volume_api_create.side_effect = fake_volume_api_create
        _mock_service_get_all.return_value = [
            {'availability_zone': 'az1', 'host': 'testhost',
             'disabled': 0, 'updated_at': timeutils.utcnow()}]

        backup_id = self._create_backup(size=5,
                                        status=fields.BackupStatus.AVAILABLE)

        body = {"restore": {'name': 'vol-01'}}
        req = webob.Request.blank('/v2/%s/backups/%s/restore' %
                                  (fake.PROJECT_ID, backup_id))
        req.method = 'POST'
        req.headers['Content-Type'] = 'application/json'
        req.body = jsonutils.dump_as_bytes(body)
        res = req.get_response(fakes.wsgi_app(
            fake_auth_context=self.user_context))
        res_dict = jsonutils.loads(res.body)

        description = 'auto-created_from_restore_from_backup'
        # Assert that we have indeed passed on the name parameter
        _mock_volume_api_create.assert_called_once_with(
            mock.ANY,
            5,
            body['restore']['name'],
            description)

        self.assertEqual(202, res.status_int)
        self.assertEqual(backup_id, res_dict['restore']['backup_id'])

    @mock.patch('cinder.backup.api.API._get_available_backup_service_host')
    def test_restore_backup_name_volume_id_specified(
            self, _mock_get_backup_host):
        _mock_get_backup_host.return_value = 'testhost'
        backup_id = self._create_backup(size=5,
                                        status=fields.BackupStatus.AVAILABLE)
        orig_vol_name = "vol-00"
        volume_id = utils.create_volume(self.context, size=5,
                                        display_name=orig_vol_name).id
        body = {"restore": {'name': 'vol-01', 'volume_id': volume_id}}
        req = webob.Request.blank('/v2/%s/backups/%s/restore' % (
                                  fake.PROJECT_ID, backup_id))
        req.method = 'POST'
        req.headers['Content-Type'] = 'application/json'
        req.body = jsonutils.dump_as_bytes(body)
        res = req.get_response(fakes.wsgi_app(
            fake_auth_context=self.user_context))
        res_dict = jsonutils.loads(res.body)

        self.assertEqual(202, res.status_int)
        self.assertEqual(backup_id, res_dict['restore']['backup_id'])
        self.assertEqual(volume_id, res_dict['restore']['volume_id'])
        restored_vol = db.volume_get(self.context,
                                     res_dict['restore']['volume_id'])
        # Ensure that the original volume name wasn't overridden
        self.assertEqual(orig_vol_name, restored_vol['display_name'])

    @mock.patch('cinder.backup.API.restore')
    def test_restore_backup_with_InvalidInput(self,
                                              _mock_volume_api_restore):

        msg = _("Invalid input")
        _mock_volume_api_restore.side_effect = \
            exception.InvalidInput(reason=msg)

        backup_id = self._create_backup(status=fields.BackupStatus.AVAILABLE)
        # need to create the volume referenced below first
        volume_id = utils.create_volume(self.context, size=0).id
        body = {"restore": {"volume_id": volume_id, }}
        req = webob.Request.blank('/v2/%s/backups/%s/restore' % (
                                  fake.PROJECT_ID, backup_id))

        req.method = 'POST'
        req.headers['Content-Type'] = 'application/json'
        req.body = jsonutils.dump_as_bytes(body)
        res = req.get_response(fakes.wsgi_app(
            fake_auth_context=self.user_context))
        res_dict = jsonutils.loads(res.body)

        self.assertEqual(400, res.status_int)
        self.assertEqual(400, res_dict['badRequest']['code'])
        self.assertEqual('Invalid input received: Invalid input',
                         res_dict['badRequest']['message'])

    def test_restore_backup_with_InvalidVolume(self):
        backup_id = self._create_backup(status=fields.BackupStatus.AVAILABLE)
        # need to create the volume referenced below first
        volume_id = utils.create_volume(self.context, size=5,
                                        status='attaching').id

        body = {"restore": {"volume_id": volume_id, }}
        req = webob.Request.blank('/v2/%s/backups/%s/restore' % (
                                  fake.PROJECT_ID, backup_id))
        req.method = 'POST'
        req.headers['Content-Type'] = 'application/json'
        req.body = jsonutils.dump_as_bytes(body)
        res = req.get_response(fakes.wsgi_app(
            fake_auth_context=self.user_context))
        res_dict = jsonutils.loads(res.body)

        self.assertEqual(400, res.status_int)
        self.assertEqual(400, res_dict['badRequest']['code'])
        self.assertEqual('Invalid volume: Volume to be restored to must '
                         'be available',
                         res_dict['badRequest']['message'])

        db.volume_destroy(context.get_admin_context(), volume_id)
        db.backup_destroy(context.get_admin_context(), backup_id)

    def test_restore_backup_with_InvalidBackup(self):
        backup_id = self._create_backup(status=fields.BackupStatus.RESTORING)
        # need to create the volume referenced below first
        volume_id = utils.create_volume(self.context, size=5).id

        body = {"restore": {"volume_id": volume_id, }}
        req = webob.Request.blank('/v2/%s/backups/%s/restore' % (
                                  fake.PROJECT_ID, backup_id))
        req.method = 'POST'
        req.headers['Content-Type'] = 'application/json'
        req.body = jsonutils.dump_as_bytes(body)
        res = req.get_response(fakes.wsgi_app(
            fake_auth_context=self.user_context))
        res_dict = jsonutils.loads(res.body)

        self.assertEqual(400, res.status_int)
        self.assertEqual(400, res_dict['badRequest']['code'])
        self.assertEqual('Invalid backup: Backup status must be available',
                         res_dict['badRequest']['message'])

        db.volume_destroy(context.get_admin_context(), volume_id)
        db.backup_destroy(context.get_admin_context(), backup_id)

    def test_restore_backup_with_BackupNotFound(self):
        # need to create the volume referenced below first
        volume_id = utils.create_volume(self.context, size=5).id

        body = {"restore": {"volume_id": volume_id, }}
        req = webob.Request.blank('/v2/%s/backups/%s/restore' %
                                  (fake.PROJECT_ID, fake.WILL_NOT_BE_FOUND_ID))
        req.method = 'POST'
        req.headers['Content-Type'] = 'application/json'
        req.body = jsonutils.dump_as_bytes(body)
        res = req.get_response(fakes.wsgi_app(
            fake_auth_context=self.user_context))
        res_dict = jsonutils.loads(res.body)

        self.assertEqual(404, res.status_int)
        self.assertEqual(404, res_dict['itemNotFound']['code'])
        self.assertEqual('Backup %s could not be found.' %
                         fake.WILL_NOT_BE_FOUND_ID,
                         res_dict['itemNotFound']['message'])

        db.volume_destroy(context.get_admin_context(), volume_id)

    def test_restore_backup_with_VolumeNotFound(self):
        backup_id = self._create_backup(status=fields.BackupStatus.AVAILABLE)

        body = {"restore": {"volume_id": fake.WILL_NOT_BE_FOUND_ID, }}
        req = webob.Request.blank('/v2/%s/backups/%s/restore' % (
                                  fake.PROJECT_ID, backup_id))
        req.method = 'POST'
        req.headers['Content-Type'] = 'application/json'
        req.body = jsonutils.dump_as_bytes(body)
        res = req.get_response(fakes.wsgi_app(
            fake_auth_context=self.user_context))
        res_dict = jsonutils.loads(res.body)

        self.assertEqual(404, res.status_int)
        self.assertEqual(404, res_dict['itemNotFound']['code'])
        self.assertEqual('Volume %s could not be found.' %
                         fake.WILL_NOT_BE_FOUND_ID,
                         res_dict['itemNotFound']['message'])

        db.backup_destroy(context.get_admin_context(), backup_id)

    @mock.patch('cinder.backup.API.restore')
    def test_restore_backup_with_VolumeSizeExceedsAvailableQuota(
            self,
            _mock_backup_restore):

        _mock_backup_restore.side_effect = \
            exception.VolumeSizeExceedsAvailableQuota(requested='2',
                                                      consumed='2',
                                                      quota='3')

        backup_id = self._create_backup(status=fields.BackupStatus.AVAILABLE)
        # need to create the volume referenced below first
        volume_id = utils.create_volume(self.context, size=5).id

        body = {"restore": {"volume_id": volume_id, }}
        req = webob.Request.blank('/v2/%s/backups/%s/restore' % (
                                  fake.PROJECT_ID, backup_id))

        req.method = 'POST'
        req.headers['Content-Type'] = 'application/json'
        req.body = jsonutils.dump_as_bytes(body)
        res = req.get_response(fakes.wsgi_app(
            fake_auth_context=self.user_context))
        res_dict = jsonutils.loads(res.body)

        self.assertEqual(413, res.status_int)
        self.assertEqual(413, res_dict['overLimit']['code'])
        self.assertEqual('Requested volume or snapshot exceeds allowed '
                         'gigabytes quota. Requested 2G, quota is 3G and '
                         '2G has been consumed.',
                         res_dict['overLimit']['message'])

    @mock.patch('cinder.backup.API.restore')
    def test_restore_backup_with_VolumeLimitExceeded(self,
                                                     _mock_backup_restore):

        _mock_backup_restore.side_effect = \
            exception.VolumeLimitExceeded(allowed=1)

        backup_id = self._create_backup(status=fields.BackupStatus.AVAILABLE)
        # need to create the volume referenced below first
        volume_id = utils.create_volume(self.context, size=5).id

        body = {"restore": {"volume_id": volume_id, }}
        req = webob.Request.blank('/v2/%s/backups/%s/restore' % (
                                  fake.PROJECT_ID, backup_id))

        req.method = 'POST'
        req.headers['Content-Type'] = 'application/json'
        req.body = jsonutils.dump_as_bytes(body)
        res = req.get_response(fakes.wsgi_app(
            fake_auth_context=self.user_context))
        res_dict = jsonutils.loads(res.body)

        self.assertEqual(413, res.status_int)
        self.assertEqual(413, res_dict['overLimit']['code'])
        self.assertEqual("Maximum number of volumes allowed (1) exceeded for"
                         " quota 'volumes'.", res_dict['overLimit']['message'])

    def test_restore_backup_to_undersized_volume(self):
        backup_size = 10
        backup_id = self._create_backup(status=fields.BackupStatus.AVAILABLE,
                                        size=backup_size)
        # need to create the volume referenced below first
        volume_size = 5
        volume_id = utils.create_volume(self.context, size=volume_size).id

        body = {"restore": {"volume_id": volume_id, }}
        req = webob.Request.blank('/v2/%s/backups/%s/restore' % (
                                  fake.PROJECT_ID, backup_id))
        req.method = 'POST'
        req.headers['Content-Type'] = 'application/json'
        req.body = jsonutils.dump_as_bytes(body)
        res = req.get_response(fakes.wsgi_app(
            fake_auth_context=self.user_context))
        res_dict = jsonutils.loads(res.body)

        self.assertEqual(400, res.status_int)
        self.assertEqual(400, res_dict['badRequest']['code'])
        self.assertEqual('Invalid volume: volume size %d is too '
                         'small to restore backup of size %d.'
                         % (volume_size, backup_size),
                         res_dict['badRequest']['message'])

        db.volume_destroy(context.get_admin_context(), volume_id)
        db.backup_destroy(context.get_admin_context(), backup_id)

    @mock.patch('cinder.backup.api.API._get_available_backup_service_host')
    def test_restore_backup_to_oversized_volume(self, _mock_get_backup_host):
        backup_id = self._create_backup(status=fields.BackupStatus.AVAILABLE,
                                        size=10)
        _mock_get_backup_host.return_value = 'testhost'
        # need to create the volume referenced below first
        volume_name = 'test1'
        volume_id = utils.create_volume(self.context,
                                        size=15,
                                        display_name = volume_name).id

        body = {"restore": {"volume_id": volume_id, }}
        req = webob.Request.blank('/v2/%s/backups/%s/restore' % (
                                  fake.PROJECT_ID, backup_id))
        req.method = 'POST'
        req.headers['Content-Type'] = 'application/json'
        req.body = jsonutils.dump_as_bytes(body)
        res = req.get_response(fakes.wsgi_app(
            fake_auth_context=self.user_context))
        res_dict = jsonutils.loads(res.body)

        self.assertEqual(202, res.status_int)
        self.assertEqual(backup_id, res_dict['restore']['backup_id'])
        self.assertEqual(volume_id, res_dict['restore']['volume_id'])
        self.assertEqual(volume_name, res_dict['restore']['volume_name'])

        db.volume_destroy(context.get_admin_context(), volume_id)
        db.backup_destroy(context.get_admin_context(), backup_id)

    @mock.patch('cinder.backup.rpcapi.BackupAPI.restore_backup')
    @mock.patch('cinder.backup.api.API._get_available_backup_service_host')
    def test_restore_backup_with_different_host(self, _mock_get_backup_host,
                                                mock_restore_backup):
        volume_name = 'test1'
        backup_id = self._create_backup(status=fields.BackupStatus.AVAILABLE,
                                        size=10, host='HostA')
        volume_id = utils.create_volume(self.context, size=10,
                                        host='HostB@BackendB#PoolB',
                                        display_name=volume_name).id

        _mock_get_backup_host.return_value = 'testhost'
        body = {"restore": {"volume_id": volume_id, }}
        req = webob.Request.blank('/v2/%s/backups/%s/restore' % (
                                  fake.PROJECT_ID, backup_id))
        req.method = 'POST'
        req.headers['Content-Type'] = 'application/json'
        req.body = jsonutils.dump_as_bytes(body)
        res = req.get_response(fakes.wsgi_app(
            fake_auth_context=self.user_context))
        res_dict = jsonutils.loads(res.body)

        self.assertEqual(202, res.status_int)
        self.assertEqual(backup_id, res_dict['restore']['backup_id'])
        self.assertEqual(volume_id, res_dict['restore']['volume_id'])
        self.assertEqual(volume_name, res_dict['restore']['volume_name'])
        mock_restore_backup.assert_called_once_with(mock.ANY, u'testhost',
                                                    mock.ANY, volume_id)
        # Manually check if restore_backup was called with appropriate backup.
        self.assertEqual(backup_id, mock_restore_backup.call_args[0][2].id)

        db.volume_destroy(context.get_admin_context(), volume_id)
        db.backup_destroy(context.get_admin_context(), backup_id)

    def test_export_record_as_non_admin(self):
        backup_id = self._create_backup(status=fields.BackupStatus.AVAILABLE,
                                        size=10)
        req = webob.Request.blank('/v2/%s/backups/%s/export_record' % (
                                  fake.PROJECT_ID, backup_id))
        req.method = 'GET'
        req.headers['content-type'] = 'application/json'

        res = req.get_response(fakes.wsgi_app(
            fake_auth_context=self.user_context))
        # request is not authorized
        self.assertEqual(403, res.status_int)

    @mock.patch('cinder.backup.api.API._get_available_backup_service_host')
    @mock.patch('cinder.backup.rpcapi.BackupAPI.export_record')
    def test_export_backup_record_id_specified_json(self,
                                                    _mock_export_record_rpc,
                                                    _mock_get_backup_host):
        backup_id = self._create_backup(status=fields.BackupStatus.AVAILABLE,
                                        size=10)
        ctx = context.RequestContext(fake.USER_ID, fake.PROJECT_ID,
                                     is_admin=True)
        backup_service = 'fake'
        backup_url = 'fake'
        _mock_export_record_rpc.return_value = \
            {'backup_service': backup_service,
             'backup_url': backup_url}
        _mock_get_backup_host.return_value = 'testhost'
        req = webob.Request.blank('/v2/%s/backups/%s/export_record' % (
                                  fake.PROJECT_ID, backup_id))
        req.method = 'GET'
        req.headers['content-type'] = 'application/json'

        res = req.get_response(fakes.wsgi_app(fake_auth_context=ctx))
        res_dict = jsonutils.loads(res.body)
        # verify that request is successful
        self.assertEqual(200, res.status_int)
        self.assertEqual(backup_service,
                         res_dict['backup-record']['backup_service'])
        self.assertEqual(backup_url,
                         res_dict['backup-record']['backup_url'])
        db.backup_destroy(context.get_admin_context(), backup_id)

    def test_export_record_with_bad_backup_id(self):

        ctx = context.RequestContext(fake.USER_ID, fake.PROJECT_ID,
                                     is_admin=True)
        backup_id = fake.WILL_NOT_BE_FOUND_ID
        req = webob.Request.blank('/v2/%s/backups/%s/export_record' %
                                  (fake.PROJECT_ID, backup_id))
        req.method = 'GET'
        req.headers['content-type'] = 'application/json'

        res = req.get_response(fakes.wsgi_app(fake_auth_context=ctx))
        res_dict = jsonutils.loads(res.body)
        self.assertEqual(404, res.status_int)
        self.assertEqual(404, res_dict['itemNotFound']['code'])
        self.assertEqual('Backup %s could not be found.' % backup_id,
                         res_dict['itemNotFound']['message'])

    def test_export_record_for_unavailable_backup(self):

        backup_id = self._create_backup(status=fields.BackupStatus.RESTORING)
        ctx = context.RequestContext(fake.USER_ID, fake.PROJECT_ID,
                                     is_admin=True)
        req = webob.Request.blank('/v2/%s/backups/%s/export_record' %
                                  (fake.PROJECT_ID, backup_id))
        req.method = 'GET'
        req.headers['content-type'] = 'application/json'

        res = req.get_response(fakes.wsgi_app(fake_auth_context=ctx))
        res_dict = jsonutils.loads(res.body)
        self.assertEqual(400, res.status_int)
        self.assertEqual(400, res_dict['badRequest']['code'])
        self.assertEqual('Invalid backup: Backup status must be available '
                         'and not restoring.',
                         res_dict['badRequest']['message'])
        db.backup_destroy(context.get_admin_context(), backup_id)

    @mock.patch('cinder.backup.api.API._get_available_backup_service_host')
    @mock.patch('cinder.backup.rpcapi.BackupAPI.export_record')
    def test_export_record_with_unavailable_service(self,
                                                    _mock_export_record_rpc,
                                                    _mock_get_backup_host):
        msg = 'fake unavailable service'
        _mock_export_record_rpc.side_effect = \
            exception.InvalidBackup(reason=msg)
        _mock_get_backup_host.return_value = 'testhost'
        backup_id = self._create_backup(status=fields.BackupStatus.AVAILABLE)
        ctx = context.RequestContext(fake.USER_ID, fake.PROJECT_ID,
                                     is_admin=True)
        req = webob.Request.blank('/v2/%s/backups/%s/export_record' %
                                  (fake.PROJECT_ID, backup_id))
        req.method = 'GET'
        req.headers['content-type'] = 'application/json'

        res = req.get_response(fakes.wsgi_app(fake_auth_context=ctx))
        res_dict = jsonutils.loads(res.body)

        self.assertEqual(400, res.status_int)
        self.assertEqual(400, res_dict['badRequest']['code'])
        self.assertEqual('Invalid backup: %s' % msg,
                         res_dict['badRequest']['message'])
        db.backup_destroy(context.get_admin_context(), backup_id)

    def test_import_record_as_non_admin(self):
        backup_service = 'fake'
        backup_url = 'fake'
        req = webob.Request.blank('/v2/%s/backups/import_record' %
                                  fake.PROJECT_ID)
        body = {'backup-record': {'backup_service': backup_service,
                                  'backup_url': backup_url}}
        req.body = jsonutils.dump_as_bytes(body)
        req.method = 'POST'
        req.headers['content-type'] = 'application/json'

        res = req.get_response(fakes.wsgi_app(
            fake_auth_context=self.user_context))
        # request is not authorized
        self.assertEqual(403, res.status_int)

    @mock.patch('cinder.backup.api.API._list_backup_hosts')
    @mock.patch('cinder.backup.rpcapi.BackupAPI.import_record')
    def test_import_record_volume_id_specified_json(self,
                                                    _mock_import_record_rpc,
                                                    _mock_list_services):
        utils.replace_obj_loader(self, objects.Backup)
        project_id = fake.PROJECT_ID
        backup_service = 'fake'
        ctx = context.RequestContext(fake.USER_ID, project_id, is_admin=True)
        backup = objects.Backup(ctx, id=fake.BACKUP_ID, user_id=fake.USER_ID,
                                project_id=project_id,
                                status=fields.BackupStatus.AVAILABLE)
        backup_url = backup.encode_record()
        _mock_import_record_rpc.return_value = None
        _mock_list_services.return_value = [backup_service]

        req = webob.Request.blank('/v2/%s/backups/import_record' %
                                  fake.PROJECT_ID)
        body = {'backup-record': {'backup_service': backup_service,
                                  'backup_url': backup_url}}
        req.body = jsonutils.dump_as_bytes(body)
        req.method = 'POST'
        req.headers['content-type'] = 'application/json'

        res = req.get_response(fakes.wsgi_app(fake_auth_context=ctx))
        res_dict = jsonutils.loads(res.body)

        # verify that request is successful
        self.assertEqual(201, res.status_int)
        self.assertIn('id', res_dict['backup'])
        self.assertEqual(fake.BACKUP_ID, res_dict['backup']['id'])

        # Verify that entry in DB is as expected
        db_backup = objects.Backup.get_by_id(ctx, fake.BACKUP_ID)
        self.assertEqual(ctx.project_id, db_backup.project_id)
        self.assertEqual(ctx.user_id, db_backup.user_id)
        self.assertEqual(backup_api.IMPORT_VOLUME_ID, db_backup.volume_id)
        self.assertEqual(fields.BackupStatus.CREATING, db_backup.status)

    @mock.patch('cinder.backup.api.API._list_backup_hosts')
    @mock.patch('cinder.backup.rpcapi.BackupAPI.import_record')
    def test_import_record_volume_id_exists_deleted(self,
                                                    _mock_import_record_rpc,
                                                    _mock_list_services):
        ctx = context.RequestContext(fake.USER_ID, fake.PROJECT_ID,
                                     is_admin=True)
        utils.replace_obj_loader(self, objects.Backup)

        # Original backup belonged to a different user_id and project_id
        backup = objects.Backup(ctx, id=fake.BACKUP_ID, user_id=fake.USER2_ID,
                                project_id=fake.PROJECT2_ID,
                                status=fields.BackupStatus.AVAILABLE)
        backup_url = backup.encode_record()

        # Deleted DB entry has project_id and user_id set to fake
        backup_id = self._create_backup(fake.VOLUME_ID,
                                        status=fields.BackupStatus.DELETED)
        backup_service = 'fake'
        _mock_import_record_rpc.return_value = None
        _mock_list_services.return_value = [backup_service]

        req = webob.Request.blank('/v2/%s/backups/import_record' %
                                  fake.PROJECT_ID)
        body = {'backup-record': {'backup_service': backup_service,
                                  'backup_url': backup_url}}
        req.body = jsonutils.dump_as_bytes(body)
        req.method = 'POST'
        req.headers['content-type'] = 'application/json'

        res = req.get_response(fakes.wsgi_app(fake_auth_context=ctx))
        res_dict = jsonutils.loads(res.body)

        # verify that request is successful
        self.assertEqual(201, res.status_int)
        self.assertIn('id', res_dict['backup'])
        self.assertEqual(fake.BACKUP_ID, res_dict['backup']['id'])

        # Verify that entry in DB is as expected, with new project and user_id
        db_backup = objects.Backup.get_by_id(ctx, fake.BACKUP_ID)
        self.assertEqual(ctx.project_id, db_backup.project_id)
        self.assertEqual(ctx.user_id, db_backup.user_id)
        self.assertEqual(backup_api.IMPORT_VOLUME_ID, db_backup.volume_id)
        self.assertEqual(fields.BackupStatus.CREATING, db_backup.status)

        db.backup_destroy(context.get_admin_context(), backup_id)

    @mock.patch('cinder.backup.api.API._list_backup_hosts')
    def test_import_record_with_no_backup_services(self,
                                                   _mock_list_services):
        ctx = context.RequestContext(fake.USER_ID, fake.PROJECT_ID,
                                     is_admin=True)
        backup_service = 'fake'
        backup_url = 'fake'
        _mock_list_services.return_value = []

        req = webob.Request.blank('/v2/%s/backups/import_record' %
                                  fake.PROJECT_ID)
        body = {'backup-record': {'backup_service': backup_service,
                                  'backup_url': backup_url}}
        req.body = jsonutils.dump_as_bytes(body)
        req.method = 'POST'
        req.headers['content-type'] = 'application/json'

        res = req.get_response(fakes.wsgi_app(fake_auth_context=ctx))
        res_dict = jsonutils.loads(res.body)
        self.assertEqual(500, res.status_int)
        self.assertEqual(500, res_dict['computeFault']['code'])
        self.assertEqual('Service %s could not be found.'
                         % backup_service,
                         res_dict['computeFault']['message'])

    @mock.patch('cinder.backup.api.API._list_backup_hosts')
    def test_import_backup_with_wrong_backup_url(self, _mock_list_services):
        ctx = context.RequestContext(fake.USER_ID, fake.PROJECT_ID,
                                     is_admin=True)
        backup_service = 'fake'
        backup_url = 'fake'
        _mock_list_services.return_value = ['no-match1', 'no-match2']
        req = webob.Request.blank('/v2/%s/backups/import_record' %
                                  fake.PROJECT_ID)
        body = {'backup-record': {'backup_service': backup_service,
                                  'backup_url': backup_url}}
        req.body = jsonutils.dump_as_bytes(body)
        req.method = 'POST'
        req.headers['content-type'] = 'application/json'

        res = req.get_response(fakes.wsgi_app(fake_auth_context=ctx))
        res_dict = jsonutils.loads(res.body)
        self.assertEqual(400, res.status_int)
        self.assertEqual(400, res_dict['badRequest']['code'])
        self.assertEqual("Invalid input received: Can't parse backup record.",
                         res_dict['badRequest']['message'])

    @mock.patch('cinder.backup.api.API._list_backup_hosts')
    def test_import_backup_with_existing_backup_record(self,
                                                       _mock_list_services):
        ctx = context.RequestContext(fake.USER_ID, fake.PROJECT_ID,
                                     is_admin=True)
        backup_id = self._create_backup(fake.VOLUME_ID)
        backup_service = 'fake'
        backup = objects.Backup.get_by_id(ctx, backup_id)
        backup_url = backup.encode_record()
        _mock_list_services.return_value = ['no-match1', 'no-match2']
        req = webob.Request.blank('/v2/%s/backups/import_record' %
                                  fake.PROJECT_ID)
        body = {'backup-record': {'backup_service': backup_service,
                                  'backup_url': backup_url}}
        req.body = jsonutils.dump_as_bytes(body)
        req.method = 'POST'
        req.headers['content-type'] = 'application/json'

        res = req.get_response(fakes.wsgi_app(fake_auth_context=ctx))
        res_dict = jsonutils.loads(res.body)
        self.assertEqual(400, res.status_int)
        self.assertEqual(400, res_dict['badRequest']['code'])
        self.assertEqual('Invalid backup: Backup already exists in database.',
                         res_dict['badRequest']['message'])

        db.backup_destroy(context.get_admin_context(), backup_id)

    @mock.patch('cinder.backup.api.API._list_backup_hosts')
    @mock.patch('cinder.backup.rpcapi.BackupAPI.import_record')
    def test_import_backup_with_missing_backup_services(self,
                                                        _mock_import_record,
                                                        _mock_list_services):
        ctx = context.RequestContext(fake.USER_ID, fake.PROJECT_ID,
                                     is_admin=True)
        backup_id = self._create_backup(fake.VOLUME_ID,
                                        status=fields.BackupStatus.DELETED)
        backup_service = 'fake'
        backup = objects.Backup.get_by_id(ctx, backup_id)
        backup_url = backup.encode_record()
        _mock_list_services.return_value = ['no-match1', 'no-match2']
        _mock_import_record.side_effect = \
            exception.ServiceNotFound(service_id='fake')
        req = webob.Request.blank('/v2/%s/backups/import_record' %
                                  fake.PROJECT_ID)
        body = {'backup-record': {'backup_service': backup_service,
                                  'backup_url': backup_url}}
        req.body = jsonutils.dump_as_bytes(body)
        req.method = 'POST'
        req.headers['content-type'] = 'application/json'

        res = req.get_response(fakes.wsgi_app(fake_auth_context=ctx))
        res_dict = jsonutils.loads(res.body)
        self.assertEqual(500, res.status_int)
        self.assertEqual(500, res_dict['computeFault']['code'])
        self.assertEqual('Service %s could not be found.' % backup_service,
                         res_dict['computeFault']['message'])

        db.backup_destroy(context.get_admin_context(), backup_id)

    def test_import_record_with_missing_body_elements(self):
        ctx = context.RequestContext(fake.USER_ID, fake.PROJECT_ID,
                                     is_admin=True)
        backup_service = 'fake'
        backup_url = 'fake'

        # test with no backup_service
        req = webob.Request.blank('/v2/%s/backups/import_record' %
                                  fake.PROJECT_ID)
        body = {'backup-record': {'backup_url': backup_url}}
        req.body = jsonutils.dump_as_bytes(body)
        req.method = 'POST'
        req.headers['content-type'] = 'application/json'
        res = req.get_response(fakes.wsgi_app(fake_auth_context=ctx))
        res_dict = jsonutils.loads(res.body)
        self.assertEqual(400, res.status_int)
        self.assertEqual(400, res_dict['badRequest']['code'])
        self.assertEqual('Incorrect request body format.',
                         res_dict['badRequest']['message'])

        # test with no backup_url
        req = webob.Request.blank('/v2/%s/backups/import_record' %
                                  fake.PROJECT_ID)
        body = {'backup-record': {'backup_service': backup_service}}
        req.body = jsonutils.dump_as_bytes(body)
        req.method = 'POST'
        req.headers['content-type'] = 'application/json'

        res = req.get_response(fakes.wsgi_app(fake_auth_context=ctx))
        res_dict = jsonutils.loads(res.body)
        self.assertEqual(400, res.status_int)
        self.assertEqual(400, res_dict['badRequest']['code'])
        self.assertEqual('Incorrect request body format.',
                         res_dict['badRequest']['message'])

        # test with no backup_url and backup_url
        req = webob.Request.blank('/v2/%s/backups/import_record' %
                                  fake.PROJECT_ID)
        body = {'backup-record': {}}
        req.body = jsonutils.dump_as_bytes(body)
        req.method = 'POST'
        req.headers['content-type'] = 'application/json'

        res = req.get_response(fakes.wsgi_app(fake_auth_context=ctx))
        res_dict = jsonutils.loads(res.body)
        self.assertEqual(400, res.status_int)
        self.assertEqual(400, res_dict['badRequest']['code'])
        self.assertEqual('Incorrect request body format.',
                         res_dict['badRequest']['message'])

    def test_import_record_with_no_body(self):
        ctx = context.RequestContext(fake.USER_ID, fake.PROJECT_ID,
                                     is_admin=True)

        req = webob.Request.blank('/v2/%s/backups/import_record' %
                                  fake.PROJECT_ID)
        req.body = jsonutils.dump_as_bytes(None)
        req.method = 'POST'
        req.headers['content-type'] = 'application/json'

        res = req.get_response(fakes.wsgi_app(fake_auth_context=ctx))
        res_dict = jsonutils.loads(res.body)
        # verify that request is successful
        self.assertEqual(400, res.status_int)
        self.assertEqual(400, res_dict['badRequest']['code'])
        self.assertEqual("Missing required element 'backup-record' in "
                         "request body.",
                         res_dict['badRequest']['message'])

    @mock.patch('cinder.backup.rpcapi.BackupAPI.check_support_to_force_delete',
                return_value=False)
    def test_force_delete_with_not_supported_operation(self,
                                                       mock_check_support):
        backup_id = self._create_backup(status=fields.BackupStatus.AVAILABLE)
        backup = self.backup_api.get(self.context, backup_id)
        self.assertRaises(exception.NotSupportedOperation,
                          self.backup_api.delete, self.context, backup, True)

    @ddt.data(False, True)
    def test_show_incremental_backup(self, backup_from_snapshot):
        volume_id = utils.create_volume(self.context, size=5).id
        parent_backup_id = self._create_backup(
            volume_id, status=fields.BackupStatus.AVAILABLE,
            num_dependent_backups=1)
        backup_id = self._create_backup(volume_id,
                                        status=fields.BackupStatus.AVAILABLE,
                                        incremental=True,
                                        parent_id=parent_backup_id,
                                        num_dependent_backups=1)
        snapshot = None
        snapshot_id = None
        if backup_from_snapshot:
            snapshot = utils.create_snapshot(self.context,
                                             volume_id)
            snapshot_id = snapshot.id
        child_backup_id = self._create_backup(
            volume_id, status=fields.BackupStatus.AVAILABLE, incremental=True,
            parent_id=backup_id, snapshot_id=snapshot_id)

        req = webob.Request.blank('/v2/%s/backups/%s' % (
                                  fake.PROJECT_ID, backup_id))
        req.method = 'GET'
        req.headers['Content-Type'] = 'application/json'
        res = req.get_response(fakes.wsgi_app(
            fake_auth_context=self.user_context))
        res_dict = jsonutils.loads(res.body)

        self.assertEqual(200, res.status_int)
        self.assertTrue(res_dict['backup']['is_incremental'])
        self.assertTrue(res_dict['backup']['has_dependent_backups'])
        self.assertIsNone(res_dict['backup']['snapshot_id'])

        req = webob.Request.blank('/v2/%s/backups/%s' % (
                                  fake.PROJECT_ID, parent_backup_id))
        req.method = 'GET'
        req.headers['Content-Type'] = 'application/json'
        res = req.get_response(fakes.wsgi_app(
            fake_auth_context=self.user_context))
        res_dict = jsonutils.loads(res.body)

        self.assertEqual(200, res.status_int)
        self.assertFalse(res_dict['backup']['is_incremental'])
        self.assertTrue(res_dict['backup']['has_dependent_backups'])
        self.assertIsNone(res_dict['backup']['snapshot_id'])

        req = webob.Request.blank('/v2/%s/backups/%s' % (
                                  fake.PROJECT_ID, child_backup_id))
        req.method = 'GET'
        req.headers['Content-Type'] = 'application/json'
        res = req.get_response(fakes.wsgi_app(
            fake_auth_context=self.user_context))
        res_dict = jsonutils.loads(res.body)

        self.assertEqual(200, res.status_int)
        self.assertTrue(res_dict['backup']['is_incremental'])
        self.assertFalse(res_dict['backup']['has_dependent_backups'])
        self.assertEqual(snapshot_id, res_dict['backup']['snapshot_id'])

        db.backup_destroy(context.get_admin_context(), child_backup_id)
        db.backup_destroy(context.get_admin_context(), backup_id)
        db.backup_destroy(context.get_admin_context(), parent_backup_id)
        if snapshot:
            snapshot.destroy()
        db.volume_destroy(context.get_admin_context(), volume_id)
