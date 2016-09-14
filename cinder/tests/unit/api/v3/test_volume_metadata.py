# Copyright 2016 OpenStack Foundation.
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

import uuid

import mock
from oslo_config import cfg
from oslo_serialization import jsonutils
import six

from cinder.api import extensions
from cinder.api.v3 import volume_metadata
from cinder.api.v3 import volumes
from cinder import db
from cinder import exception
from cinder import test
from cinder.tests.unit.api import fakes
from cinder.tests.unit.api.v2 import stubs
from cinder.tests.unit import fake_constants as fake
from cinder.tests.unit import fake_volume
from cinder import volume
from cinder.volume import api as volume_api


CONF = cfg.CONF


def return_create_volume_metadata_max(context, volume_id, metadata, delete):
    return stub_max_volume_metadata()


def return_create_volume_metadata(context, volume_id, metadata,
                                  delete, meta_type):
    return stub_volume_metadata()


def return_new_volume_metadata(context, volume_id, metadata,
                               delete, meta_type):
    return stub_new_volume_metadata()


def return_create_volume_metadata_insensitive(context, snapshot_id,
                                              metadata, delete,
                                              meta_type):
    return stub_volume_metadata_insensitive()


def return_volume_metadata(context, volume_id):
    return stub_volume_metadata()


def return_empty_volume_metadata(context, volume_id):
    return {}


def return_empty_container_metadata(context, volume_id, metadata,
                                    delete, meta_type):
    return {}


def stub_volume_metadata():
    metadata = {
        "key1": "value1",
        "key2": "value2",
        "key3": "value3",
    }
    return metadata


def stub_new_volume_metadata():
    metadata = {
        'key10': 'value10',
        'key99': 'value99',
        'KEY20': 'value20',
    }
    return metadata


def stub_volume_metadata_insensitive():
    metadata = {
        "key1": "value1",
        "key2": "value2",
        "key3": "value3",
        "KEY4": "value4",
    }
    return metadata


def stub_max_volume_metadata():
    metadata = {"metadata": {}}
    for num in range(CONF.quota_metadata_items):
        metadata['metadata']['key%i' % num] = "blah"
    return metadata


def get_volume(*args, **kwargs):
    vol = {'name': 'fake',
           'metadata': {}}
    return fake_volume.fake_volume_obj(args[0], **vol)


def return_volume_nonexistent(*args, **kwargs):
    raise exception.VolumeNotFound('bogus test message')


def fake_update_volume_metadata(self, context, volume, diff):
    pass


class volumeMetaDataTest(test.TestCase):

    def setUp(self):
        super(volumeMetaDataTest, self).setUp()
        self.volume_api = volume_api.API()
        self.mock_object(volume.api.API, 'get', get_volume)
        self.mock_object(db, 'volume_metadata_get',
                         return_volume_metadata)
        self.patch(
            'cinder.db.service_get_all', autospec=True,
            return_value=stubs.stub_service_get_all_by_topic(None, None))

        self.mock_object(self.volume_api, 'update_volume_metadata',
                         fake_update_volume_metadata)

        self.ext_mgr = extensions.ExtensionManager()
        self.ext_mgr.extensions = {}
        self.volume_controller = volumes.VolumeController(self.ext_mgr)
        self.controller = volume_metadata.Controller()
        self.req_id = str(uuid.uuid4())
        self.url = '/v2/%s/volumes/%s/metadata' % (
            fake.PROJECT_ID, self.req_id)

        vol = {"size": 100,
               "display_name": "Volume Test Name",
               "display_description": "Volume Test Desc",
               "availability_zone": "zone1:host1",
               "metadata": {}}
        body = {"volume": vol}
        req = fakes.HTTPRequest.blank('/v2/volumes')
        self.volume_controller.create(req, body)

    def test_index(self):
        req = fakes.HTTPRequest.blank(self.url, version="3.15")
        data = self.controller.index(req, self.req_id)

        expected = {
            'metadata': {
                'key1': 'value1',
                'key2': 'value2',
                'key3': 'value3',
            },
        }
        expected = jsonutils.dumps(expected)
        if six.PY3:
            expected = expected.encode('utf-8')
        self.assertEqual(expected, data.body)

    def test_index_nonexistent_volume(self):
        self.mock_object(db, 'volume_metadata_get',
                         return_volume_nonexistent)
        req = fakes.HTTPRequest.blank(self.url, version="3.15")
        self.assertRaises(exception.VolumeNotFound,
                          self.controller.index, req, self.url)

    def test_index_no_data(self):
        self.mock_object(db, 'volume_metadata_get',
                         return_empty_volume_metadata)
        req = fakes.HTTPRequest.blank(self.url, version="3.15")
        data = self.controller.index(req, self.req_id)
        expected = {'metadata': {}}
        expected = jsonutils.dumps(expected)
        if six.PY3:
            expected = expected.encode('utf-8')
        self.assertEqual(expected, data.body)

    def test_validate_etag_true(self):
        self.mock_object(db, 'volume_metadata_get',
                         mock.Mock(return_value={'key1': 'vanue1',
                                                 'key2': 'value2'}))
        req = fakes.HTTPRequest.blank(self.url, version="3.15")
        req.environ['cinder.context'] = mock.Mock()
        req.if_match.etags = ['d5103bf7b26ff0310200d110da3ed186']
        self.assertTrue(self.controller._validate_etag(req, self.req_id))

    @mock.patch.object(db, 'volume_metadata_update')
    def test_update_all(self, metadata_update):
        fake_volume = {'id': self.req_id, 'status': 'available'}
        fake_context = mock.Mock()
        metadata_update.side_effect = return_new_volume_metadata
        req = fakes.HTTPRequest.blank(self.url, version="3.15")
        req.method = 'PUT'
        req.content_type = "application/json"
        expected = {
            'metadata': {
                'key10': 'value10',
                'key99': 'value99',
                'KEY20': 'value20',
            },
        }
        req.body = jsonutils.dump_as_bytes(expected)
        req.environ['cinder.context'] = fake_context

        with mock.patch.object(self.controller.volume_api,
                               'get') as get_volume:
            get_volume.return_value = fake_volume
            res_dict = self.controller.update_all(req, self.req_id, expected)
            self.assertEqual(expected, res_dict)
            get_volume.assert_called_once_with(fake_context, self.req_id)

    @mock.patch.object(db, 'volume_metadata_update')
    def test_update_item(self, metadata_update):
        fake_volume = {'id': self.req_id, 'status': 'available'}
        fake_context = mock.Mock()
        metadata_update.side_effect = return_create_volume_metadata
        req = fakes.HTTPRequest.blank(self.url + '/key1', version="3.15")
        req.method = 'PUT'
        body = {"meta": {"key1": "value1"}}
        req.body = jsonutils.dump_as_bytes(body)
        req.headers["content-type"] = "application/json"
        req.environ['cinder.context'] = fake_context

        with mock.patch.object(self.controller.volume_api,
                               'get') as get_volume:
            get_volume.return_value = fake_volume
            res_dict = self.controller.update(req, self.req_id, 'key1', body)
            expected = {'meta': {'key1': 'value1'}}
            self.assertEqual(expected, res_dict)
            get_volume.assert_called_once_with(fake_context, self.req_id)
