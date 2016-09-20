# Copyright 2013 OpenStack Foundation
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

import datetime

from oslo_serialization import jsonutils

import cinder
from cinder.api.openstack import wsgi
from cinder import context
from cinder import test
from cinder.tests.unit.api import fakes
from cinder.tests.unit.api.v2 import stubs
from cinder.tests.unit import fake_constants as fake


UUID = fakes.FAKE_UUID


class SchedulerHintsTestCase(test.TestCase):

    def setUp(self):
        super(SchedulerHintsTestCase, self).setUp()
        self.fake_instance = stubs.stub_volume(fake.VOLUME_ID, uuid=UUID)
        self.fake_instance['created_at'] =\
            datetime.datetime(2013, 1, 1, 1, 1, 1)
        self.fake_instance['launched_at'] =\
            datetime.datetime(2013, 1, 1, 1, 1, 1)
        self.flags(
            osapi_volume_extension=[
                'cinder.api.contrib.select_extensions'],
            osapi_volume_ext_list=['Scheduler_hints'])
        self.user_ctxt = context.RequestContext(
            fake.USER_ID, fake.PROJECT_ID, auth_token=True)
        self.app = fakes.wsgi_app(fake_auth_context=self.user_ctxt)

    def test_create_server_without_hints(self):

        @wsgi.response(202)
        def fake_create(*args, **kwargs):
            self.assertNotIn('scheduler_hints', kwargs['body'])
            return self.fake_instance

        self.mock_object(cinder.api.v2.volumes.VolumeController, 'create',
                         fake_create)

        req = fakes.HTTPRequest.blank('/v2/%s/volumes' % fake.PROJECT_ID)
        req.method = 'POST'
        req.content_type = 'application/json'
        body = {'id': UUID,
                'volume_type_id': fake.VOLUME_TYPE_ID,
                'volume_id': fake.VOLUME_ID, }
        req.body = jsonutils.dump_as_bytes(body)
        res = req.get_response(self.app)
        self.assertEqual(202, res.status_int)

    def test_create_server_with_hints(self):

        @wsgi.response(202)
        def fake_create(*args, **kwargs):
            self.assertIn('scheduler_hints', kwargs['body'])
            self.assertEqual({"a": "b"}, kwargs['body']['scheduler_hints'])
            return self.fake_instance

        self.mock_object(cinder.api.v2.volumes.VolumeController, 'create',
                         fake_create)

        req = fakes.HTTPRequest.blank('/v2/%s/volumes' % fake.PROJECT_ID)
        req.method = 'POST'
        req.content_type = 'application/json'
        body = {'id': UUID,
                'volume_type_id': fake.VOLUME_TYPE_ID,
                'volume_id': fake.VOLUME_ID,
                'scheduler_hints': {'a': 'b'}, }

        req.body = jsonutils.dump_as_bytes(body)
        res = req.get_response(self.app)
        self.assertEqual(202, res.status_int)

    def test_create_server_bad_hints(self):
        req = fakes.HTTPRequest.blank('/v2/%s/volumes' % fake.PROJECT_ID)
        req.method = 'POST'
        req.content_type = 'application/json'
        body = {'volume': {
            'id': UUID,
            'volume_type_id': fake.VOLUME_TYPE_ID,
            'volume_id': fake.VOLUME_ID,
            'scheduler_hints': 'a', }}

        req.body = jsonutils.dump_as_bytes(body)
        res = req.get_response(self.app)
        self.assertEqual(400, res.status_int)
