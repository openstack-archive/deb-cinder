# Copyright (C) 2012 - 2014 EMC Corporation.
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
Tests for consistency group code.
"""

import ddt
import mock
from oslo_serialization import jsonutils
import webob

import cinder.consistencygroup
from cinder import context
from cinder import db
from cinder import exception
from cinder.i18n import _
from cinder import objects
from cinder.objects import fields
from cinder import test
from cinder.tests.unit.api import fakes
from cinder.tests.unit.api.v2 import stubs
from cinder.tests.unit import fake_constants as fake
from cinder.tests.unit import utils
from cinder.volume import api as volume_api


@ddt.ddt
class ConsistencyGroupsAPITestCase(test.TestCase):
    """Test Case for consistency groups API."""

    def setUp(self):
        super(ConsistencyGroupsAPITestCase, self).setUp()
        self.cg_api = cinder.consistencygroup.API()
        self.ctxt = context.RequestContext(fake.USER_ID, fake.PROJECT_ID,
                                           auth_token=True,
                                           is_admin=True)
        self.user_ctxt = context.RequestContext(
            fake.USER_ID, fake.PROJECT_ID, auth_token=True)

    def _create_consistencygroup(
            self,
            ctxt=None,
            name='test_consistencygroup',
            description='this is a test consistency group',
            volume_type_id=fake.VOLUME_TYPE_ID,
            availability_zone='az1',
            host='fakehost',
            status=fields.ConsistencyGroupStatus.CREATING,
            **kwargs):
        """Create a consistency group object."""
        ctxt = ctxt or self.ctxt
        consistencygroup = objects.ConsistencyGroup(ctxt)
        consistencygroup.user_id = fake.USER_ID
        consistencygroup.project_id = fake.PROJECT_ID
        consistencygroup.availability_zone = availability_zone
        consistencygroup.name = name
        consistencygroup.description = description
        consistencygroup.volume_type_id = volume_type_id
        consistencygroup.host = host
        consistencygroup.status = status
        consistencygroup.update(kwargs)
        consistencygroup.create()
        return consistencygroup

    def test_show_consistencygroup(self):
        consistencygroup = self._create_consistencygroup()
        req = webob.Request.blank('/v2/%s/consistencygroups/%s' %
                                  (fake.PROJECT_ID, consistencygroup.id))
        req.method = 'GET'
        req.headers['Content-Type'] = 'application/json'
        res = req.get_response(fakes.wsgi_app(
            fake_auth_context=self.user_ctxt))
        res_dict = jsonutils.loads(res.body)

        self.assertEqual(200, res.status_int)
        self.assertEqual('az1',
                         res_dict['consistencygroup']['availability_zone'])
        self.assertEqual('this is a test consistency group',
                         res_dict['consistencygroup']['description'])
        self.assertEqual('test_consistencygroup',
                         res_dict['consistencygroup']['name'])
        self.assertEqual('creating',
                         res_dict['consistencygroup']['status'])
        self.assertEqual([fake.VOLUME_TYPE_ID],
                         res_dict['consistencygroup']['volume_types'])

        consistencygroup.destroy()

    def test_show_consistencygroup_with_consistencygroup_NotFound(self):
        req = webob.Request.blank('/v2/%s/consistencygroups/%s' %
                                  (fake.PROJECT_ID, fake.WILL_NOT_BE_FOUND_ID))
        req.method = 'GET'
        req.headers['Content-Type'] = 'application/json'
        res = req.get_response(fakes.wsgi_app(
            fake_auth_context=self.user_ctxt))
        res_dict = jsonutils.loads(res.body)

        self.assertEqual(404, res.status_int)
        self.assertEqual(404, res_dict['itemNotFound']['code'])
        self.assertEqual('ConsistencyGroup %s could not be found.' %
                         fake.WILL_NOT_BE_FOUND_ID,
                         res_dict['itemNotFound']['message'])

    def test_show_consistencygroup_with_null_volume_type(self):
        consistencygroup = self._create_consistencygroup(volume_type_id=None)
        req = webob.Request.blank('/v2/%s/consistencygroups/%s' %
                                  (fake.PROJECT_ID, consistencygroup.id))
        req.method = 'GET'
        req.headers['Content-Type'] = 'application/json'
        res = req.get_response(fakes.wsgi_app(
            fake_auth_context=self.user_ctxt))
        res_dict = jsonutils.loads(res.body)

        self.assertEqual(200, res.status_int)
        self.assertEqual('az1',
                         res_dict['consistencygroup']['availability_zone'])
        self.assertEqual('this is a test consistency group',
                         res_dict['consistencygroup']['description'])
        self.assertEqual('test_consistencygroup',
                         res_dict['consistencygroup']['name'])
        self.assertEqual('creating',
                         res_dict['consistencygroup']['status'])
        self.assertEqual([], res_dict['consistencygroup']['volume_types'])

        consistencygroup.destroy()

    @ddt.data(2, 3)
    def test_list_consistencygroups_json(self, version):
        consistencygroup1 = self._create_consistencygroup()
        consistencygroup2 = self._create_consistencygroup()
        consistencygroup3 = self._create_consistencygroup()

        req = webob.Request.blank('/v%(version)s/%(project_id)s/'
                                  'consistencygroups'
                                  % {'version': version,
                                     'project_id': fake.PROJECT_ID})
        req.method = 'GET'
        req.headers['Content-Type'] = 'application/json'
        res = req.get_response(fakes.wsgi_app(
            fake_auth_context=self.user_ctxt))
        res_dict = jsonutils.loads(res.body)

        self.assertEqual(200, res.status_int)
        self.assertEqual(consistencygroup3.id,
                         res_dict['consistencygroups'][0]['id'])
        self.assertEqual('test_consistencygroup',
                         res_dict['consistencygroups'][0]['name'])
        self.assertEqual(consistencygroup2.id,
                         res_dict['consistencygroups'][1]['id'])
        self.assertEqual('test_consistencygroup',
                         res_dict['consistencygroups'][1]['name'])
        self.assertEqual(consistencygroup1.id,
                         res_dict['consistencygroups'][2]['id'])
        self.assertEqual('test_consistencygroup',
                         res_dict['consistencygroups'][2]['name'])

        consistencygroup1.destroy()
        consistencygroup2.destroy()
        consistencygroup3.destroy()

    @ddt.data(False, True)
    def test_list_consistencygroups_with_limit(self, is_detail):
        consistencygroup1 = self._create_consistencygroup()
        consistencygroup2 = self._create_consistencygroup()
        consistencygroup3 = self._create_consistencygroup()
        url = '/v2/%s/consistencygroups?limit=1' % fake.PROJECT_ID
        if is_detail:
            url = '/v2/%s/consistencygroups/detail?limit=1' % fake.PROJECT_ID
        req = webob.Request.blank(url)
        req.method = 'GET'
        req.headers['Content-Type'] = 'application/json'
        res = req.get_response(fakes.wsgi_app(
            fake_auth_context=self.user_ctxt))
        res_dict = jsonutils.loads(res.body)

        self.assertEqual(200, res.status_int)
        self.assertEqual(1, len(res_dict['consistencygroups']))
        self.assertEqual(consistencygroup3.id,
                         res_dict['consistencygroups'][0]['id'])
        next_link = (
            'http://localhost/v2/%s/consistencygroups?limit='
            '1&marker=%s' %
            (fake.PROJECT_ID, res_dict['consistencygroups'][0]['id']))
        self.assertEqual(next_link,
                         res_dict['consistencygroup_links'][0]['href'])
        consistencygroup1.destroy()
        consistencygroup2.destroy()
        consistencygroup3.destroy()

    @ddt.data(False, True)
    def test_list_consistencygroups_with_offset(self, is_detail):
        consistencygroup1 = self._create_consistencygroup()
        consistencygroup2 = self._create_consistencygroup()
        consistencygroup3 = self._create_consistencygroup()
        url = '/v2/%s/consistencygroups?offset=1' % fake.PROJECT_ID
        if is_detail:
            url = '/v2/%s/consistencygroups/detail?offset=1' % fake.PROJECT_ID
        req = webob.Request.blank(url)
        req.method = 'GET'
        req.headers['Content-Type'] = 'application/json'
        res = req.get_response(fakes.wsgi_app(
            fake_auth_context=self.user_ctxt))
        res_dict = jsonutils.loads(res.body)

        self.assertEqual(200, res.status_int)
        self.assertEqual(2, len(res_dict['consistencygroups']))
        self.assertEqual(consistencygroup2.id,
                         res_dict['consistencygroups'][0]['id'])
        self.assertEqual(consistencygroup1.id,
                         res_dict['consistencygroups'][1]['id'])
        consistencygroup1.destroy()
        consistencygroup2.destroy()
        consistencygroup3.destroy()

    @ddt.data(False, True)
    def test_list_consistencygroups_with_offset_out_of_range(self, is_detail):
        url = ('/v2/%s/consistencygroups?offset=234523423455454' %
               fake.PROJECT_ID)
        if is_detail:
            url = ('/v2/%s/consistencygroups/detail?offset=234523423455454' %
                   fake.PROJECT_ID)
        req = webob.Request.blank(url)
        req.method = 'GET'
        req.headers['Content-Type'] = 'application/json'
        res = req.get_response(fakes.wsgi_app(
            fake_auth_context=self.user_ctxt))
        self.assertEqual(400, res.status_int)

    @ddt.data(False, True)
    def test_list_consistencygroups_with_limit_and_offset(self, is_detail):
        consistencygroup1 = self._create_consistencygroup()
        consistencygroup2 = self._create_consistencygroup()
        consistencygroup3 = self._create_consistencygroup()
        url = '/v2/%s/consistencygroups?limit=2&offset=1' % fake.PROJECT_ID
        if is_detail:
            url = ('/v2/%s/consistencygroups/detail?limit=2&offset=1' %
                   fake.PROJECT_ID)
        req = webob.Request.blank(url)
        req.method = 'GET'
        req.headers['Content-Type'] = 'application/json'
        res = req.get_response(fakes.wsgi_app(
            fake_auth_context=self.user_ctxt))
        res_dict = jsonutils.loads(res.body)

        self.assertEqual(200, res.status_int)
        self.assertEqual(2, len(res_dict['consistencygroups']))
        self.assertEqual(consistencygroup2.id,
                         res_dict['consistencygroups'][0]['id'])
        self.assertEqual(consistencygroup1.id,
                         res_dict['consistencygroups'][1]['id'])
        consistencygroup1.destroy()
        consistencygroup2.destroy()
        consistencygroup3.destroy()

    @ddt.data(False, True)
    def test_list_consistencygroups_with_filter(self, is_detail):
        consistencygroup1 = self._create_consistencygroup()
        consistencygroup2 = self._create_consistencygroup()
        common_ctxt = context.RequestContext(fake.USER_ID, fake.PROJECT_ID,
                                             auth_token=True,
                                             is_admin=False)
        consistencygroup3 = self._create_consistencygroup(ctxt=common_ctxt)
        url = ('/v2/%s/consistencygroups?'
               'all_tenants=True&id=%s') % (fake.PROJECT_ID,
                                            consistencygroup3.id)
        if is_detail:
            url = ('/v2/%s/consistencygroups/detail?'
                   'all_tenants=True&id=%s') % (fake.PROJECT_ID,
                                                consistencygroup3.id)
        req = webob.Request.blank(url)
        req.method = 'GET'
        req.headers['Content-Type'] = 'application/json'
        res = req.get_response(fakes.wsgi_app(fake_auth_context=self.ctxt))
        res_dict = jsonutils.loads(res.body)

        self.assertEqual(200, res.status_int)
        self.assertEqual(1, len(res_dict['consistencygroups']))
        self.assertEqual(consistencygroup3.id,
                         res_dict['consistencygroups'][0]['id'])
        consistencygroup1.destroy()
        consistencygroup2.destroy()
        consistencygroup3.destroy()

    @ddt.data(False, True)
    def test_list_consistencygroups_with_sort(self, is_detail):
        consistencygroup1 = self._create_consistencygroup()
        consistencygroup2 = self._create_consistencygroup()
        consistencygroup3 = self._create_consistencygroup()
        url = '/v2/%s/consistencygroups?sort=id:asc' % fake.PROJECT_ID
        if is_detail:
            url = ('/v2/%s/consistencygroups/detail?sort=id:asc' %
                   fake.PROJECT_ID)
        req = webob.Request.blank(url)
        req.method = 'GET'
        req.headers['Content-Type'] = 'application/json'
        res = req.get_response(fakes.wsgi_app(
            fake_auth_context=self.user_ctxt))
        res_dict = jsonutils.loads(res.body)
        expect_result = [consistencygroup1.id, consistencygroup2.id,
                         consistencygroup3.id]
        expect_result.sort()

        self.assertEqual(200, res.status_int)
        self.assertEqual(3, len(res_dict['consistencygroups']))
        self.assertEqual(expect_result[0],
                         res_dict['consistencygroups'][0]['id'])
        self.assertEqual(expect_result[1],
                         res_dict['consistencygroups'][1]['id'])
        self.assertEqual(expect_result[2],
                         res_dict['consistencygroups'][2]['id'])
        consistencygroup1.destroy()
        consistencygroup2.destroy()
        consistencygroup3.destroy()

    def test_list_consistencygroups_detail_json(self):
        consistencygroup1 = self._create_consistencygroup()
        consistencygroup2 = self._create_consistencygroup()
        consistencygroup3 = self._create_consistencygroup(
            volume_type_id='%s,%s' % (fake.VOLUME_TYPE_ID,
                                      fake.VOLUME_TYPE2_ID))
        req = webob.Request.blank('/v2/%s/consistencygroups/detail' %
                                  fake.PROJECT_ID)
        req.method = 'GET'
        req.headers['Content-Type'] = 'application/json'
        req.headers['Accept'] = 'application/json'
        res = req.get_response(fakes.wsgi_app(
            fake_auth_context=self.user_ctxt))
        res_dict = jsonutils.loads(res.body)

        self.assertEqual(200, res.status_int)
        self.assertEqual('az1',
                         res_dict['consistencygroups'][0]['availability_zone'])
        self.assertEqual('this is a test consistency group',
                         res_dict['consistencygroups'][0]['description'])
        self.assertEqual('test_consistencygroup',
                         res_dict['consistencygroups'][0]['name'])
        self.assertEqual(consistencygroup3.id,
                         res_dict['consistencygroups'][0]['id'])
        self.assertEqual('creating',
                         res_dict['consistencygroups'][0]['status'])
        self.assertEqual([fake.VOLUME_TYPE_ID, fake.VOLUME_TYPE2_ID],
                         res_dict['consistencygroups'][0]['volume_types'])

        self.assertEqual('az1',
                         res_dict['consistencygroups'][1]['availability_zone'])
        self.assertEqual('this is a test consistency group',
                         res_dict['consistencygroups'][1]['description'])
        self.assertEqual('test_consistencygroup',
                         res_dict['consistencygroups'][1]['name'])
        self.assertEqual(consistencygroup2.id,
                         res_dict['consistencygroups'][1]['id'])
        self.assertEqual('creating',
                         res_dict['consistencygroups'][1]['status'])
        self.assertEqual([fake.VOLUME_TYPE_ID],
                         res_dict['consistencygroups'][1]['volume_types'])

        self.assertEqual('az1',
                         res_dict['consistencygroups'][2]['availability_zone'])
        self.assertEqual('this is a test consistency group',
                         res_dict['consistencygroups'][2]['description'])
        self.assertEqual('test_consistencygroup',
                         res_dict['consistencygroups'][2]['name'])
        self.assertEqual(consistencygroup1.id,
                         res_dict['consistencygroups'][2]['id'])
        self.assertEqual('creating',
                         res_dict['consistencygroups'][2]['status'])
        self.assertEqual([fake.VOLUME_TYPE_ID],
                         res_dict['consistencygroups'][2]['volume_types'])

        consistencygroup1.destroy()
        consistencygroup2.destroy()
        consistencygroup3.destroy()

    @mock.patch(
        'cinder.api.openstack.wsgi.Controller.validate_name_and_description')
    def test_create_consistencygroup_json(self, mock_validate):
        group_id = fake.CONSISTENCY_GROUP_ID

        # Create volume type
        vol_type = 'test'
        db.volume_type_create(self.ctxt,
                              {'name': vol_type, 'extra_specs': {}})

        body = {"consistencygroup": {"name": "cg1",
                                     "volume_types": vol_type,
                                     "description":
                                     "Consistency Group 1", }}
        req = webob.Request.blank('/v2/%s/consistencygroups' % fake.PROJECT_ID)
        req.method = 'POST'
        req.headers['Content-Type'] = 'application/json'
        req.body = jsonutils.dump_as_bytes(body)
        res = req.get_response(fakes.wsgi_app(
            fake_auth_context=self.user_ctxt))
        res_dict = jsonutils.loads(res.body)

        self.assertEqual(202, res.status_int)
        self.assertIn('id', res_dict['consistencygroup'])
        self.assertTrue(mock_validate.called)

        group_id = res_dict['consistencygroup']['id']
        cg = objects.ConsistencyGroup.get_by_id(self.ctxt,
                                                group_id)
        cg.destroy()

    def test_create_consistencygroup_with_no_body(self):
        # omit body from the request
        req = webob.Request.blank('/v2/%s/consistencygroups' % fake.PROJECT_ID)
        req.body = jsonutils.dump_as_bytes(None)
        req.method = 'POST'
        req.headers['Content-Type'] = 'application/json'
        req.headers['Accept'] = 'application/json'
        res = req.get_response(fakes.wsgi_app(
            fake_auth_context=self.user_ctxt))
        res_dict = jsonutils.loads(res.body)

        self.assertEqual(400, res.status_int)
        self.assertEqual(400, res_dict['badRequest']['code'])
        self.assertEqual("Missing required element 'consistencygroup' in "
                         "request body.",
                         res_dict['badRequest']['message'])

    def test_delete_consistencygroup_available(self):
        consistencygroup = self._create_consistencygroup(
            status=fields.ConsistencyGroupStatus.AVAILABLE)
        req = webob.Request.blank('/v2/%s/consistencygroups/%s/delete' %
                                  (fake.PROJECT_ID, consistencygroup.id))
        req.method = 'POST'
        req.headers['Content-Type'] = 'application/json'
        req.body = jsonutils.dump_as_bytes({})
        res = req.get_response(fakes.wsgi_app())

        consistencygroup = objects.ConsistencyGroup.get_by_id(
            self.ctxt, consistencygroup.id)
        self.assertEqual(202, res.status_int)
        self.assertEqual('deleting', consistencygroup.status)

        consistencygroup.destroy()

    def test_delete_consistencygroup_available_used_as_source(self):
        consistencygroup = self._create_consistencygroup(
            status=fields.ConsistencyGroupStatus.AVAILABLE)
        req = webob.Request.blank('/v2/%s/consistencygroups/%s/delete' %
                                  (fake.PROJECT_ID, consistencygroup.id))
        cg2 = self._create_consistencygroup(
            status=fields.ConsistencyGroupStatus.CREATING,
            source_cgid=consistencygroup.id)
        req.method = 'POST'
        req.headers['Content-Type'] = 'application/json'
        req.body = jsonutils.dump_as_bytes({})
        res = req.get_response(fakes.wsgi_app())

        consistencygroup = objects.ConsistencyGroup.get_by_id(
            self.ctxt, consistencygroup.id)
        self.assertEqual(400, res.status_int)
        self.assertEqual('available', consistencygroup.status)

        consistencygroup.destroy()
        cg2.destroy()

    def test_delete_consistencygroup_available_used_as_source_success(self):
        consistencygroup = self._create_consistencygroup(
            status=fields.ConsistencyGroupStatus.AVAILABLE)
        req = webob.Request.blank('/v2/%s/consistencygroups/%s/delete' %
                                  (fake.PROJECT_ID, consistencygroup.id))
        # The other CG used the first CG as source, but it's no longer in
        # creating status, so we should be able to delete it.
        cg2 = self._create_consistencygroup(
            status=fields.ConsistencyGroupStatus.AVAILABLE,
            source_cgid=consistencygroup.id)
        req.method = 'POST'
        req.headers['Content-Type'] = 'application/json'
        req.body = jsonutils.dump_as_bytes({})
        res = req.get_response(fakes.wsgi_app())

        consistencygroup = objects.ConsistencyGroup.get_by_id(
            self.ctxt, consistencygroup.id)
        self.assertEqual(202, res.status_int)
        self.assertEqual('deleting', consistencygroup.status)

        consistencygroup.destroy()
        cg2.destroy()

    def test_delete_consistencygroup_available_no_force(self):
        consistencygroup = self._create_consistencygroup(status='available')
        req = webob.Request.blank('/v2/%s/consistencygroups/%s/delete' %
                                  (fake.PROJECT_ID, consistencygroup.id))
        req.method = 'POST'
        req.headers['Content-Type'] = 'application/json'
        body = {"consistencygroup": {"force": False}}
        req.body = jsonutils.dump_as_bytes(body)
        res = req.get_response(fakes.wsgi_app(
            fake_auth_context=self.user_ctxt))

        consistencygroup = objects.ConsistencyGroup.get_by_id(
            self.ctxt, consistencygroup.id)
        self.assertEqual(202, res.status_int)
        self.assertEqual(fields.ConsistencyGroupStatus.DELETING,
                         consistencygroup.status)

        consistencygroup.destroy()

    def test_delete_consistencygroup_with_consistencygroup_NotFound(self):
        req = webob.Request.blank('/v2/%s/consistencygroups/%s/delete' %
                                  (fake.PROJECT_ID, fake.WILL_NOT_BE_FOUND_ID))
        req.method = 'POST'
        req.headers['Content-Type'] = 'application/json'
        req.body = jsonutils.dump_as_bytes(None)
        res = req.get_response(fakes.wsgi_app(
            fake_auth_context=self.user_ctxt))
        res_dict = jsonutils.loads(res.body)

        self.assertEqual(404, res.status_int)
        self.assertEqual(404, res_dict['itemNotFound']['code'])
        self.assertEqual('ConsistencyGroup %s could not be found.' %
                         fake.WILL_NOT_BE_FOUND_ID,
                         res_dict['itemNotFound']['message'])

    def test_delete_consistencygroup_with_invalid_consistencygroup(self):
        consistencygroup = self._create_consistencygroup(
            status=fields.ConsistencyGroupStatus.CREATING)
        self._assert_deleting_result_400(consistencygroup.id)
        consistencygroup.destroy()

    def test_delete_consistencygroup_invalid_force(self):
        consistencygroup = self._create_consistencygroup(
            status=fields.ConsistencyGroupStatus.CREATING)
        req = webob.Request.blank('/v2/%s/consistencygroups/%s/delete' %
                                  (fake.PROJECT_ID, consistencygroup.id))
        req.method = 'POST'
        req.headers['Content-Type'] = 'application/json'
        body = {"consistencygroup": {"force": True}}
        req.body = jsonutils.dump_as_bytes(body)
        res = req.get_response(fakes.wsgi_app())

        consistencygroup = objects.ConsistencyGroup.get_by_id(
            self.ctxt, consistencygroup.id)
        self.assertEqual(202, res.status_int)
        self.assertEqual('deleting', consistencygroup.status)

    def test_delete_consistencygroup_no_host(self):
        consistencygroup = self._create_consistencygroup(
            host=None,
            status=fields.ConsistencyGroupStatus.ERROR)
        req = webob.Request.blank('/v2/%s/consistencygroups/%s/delete' %
                                  (fake.PROJECT_ID, consistencygroup.id))
        req.method = 'POST'
        req.headers['Content-Type'] = 'application/json'
        body = {"consistencygroup": {"force": True}}
        req.body = jsonutils.dump_as_bytes(body)
        res = req.get_response(fakes.wsgi_app(
            fake_auth_context=self.user_ctxt))
        self.assertEqual(202, res.status_int)

        cg = objects.ConsistencyGroup.get_by_id(
            context.get_admin_context(read_deleted='yes'),
            consistencygroup.id)
        self.assertEqual(fields.ConsistencyGroupStatus.DELETED, cg.status)
        self.assertIsNone(cg.host)

    def test_create_delete_consistencygroup_update_quota(self):
        name = 'mycg'
        description = 'consistency group 1'
        fake_type = {'id': fake.CONSISTENCY_GROUP_ID, 'name': 'fake_type'}
        self.mock_object(db, 'volume_types_get_by_name_or_id',
                         return_value=[fake_type])
        self.mock_object(self.cg_api, '_cast_create_consistencygroup')
        self.mock_object(self.cg_api, 'update_quota')
        cg = self.cg_api.create(self.ctxt, name, description,
                                fake_type['name'])
        self.cg_api.update_quota.assert_called_once_with(
            self.ctxt, cg, 1)

        self.assertEqual(fields.ConsistencyGroupStatus.CREATING, cg.status)
        self.assertIsNone(cg.host)
        self.cg_api.update_quota.reset_mock()
        cg.status = fields.ConsistencyGroupStatus.ERROR
        self.cg_api.delete(self.ctxt, cg)

        self.cg_api.update_quota.assert_called_once_with(
            self.ctxt, cg, -1, self.ctxt.project_id)
        cg = objects.ConsistencyGroup.get_by_id(
            context.get_admin_context(read_deleted='yes'),
            cg.id)
        self.assertEqual(fields.ConsistencyGroupStatus.DELETED, cg.status)

    def test_delete_consistencygroup_with_invalid_body(self):
        consistencygroup = self._create_consistencygroup(
            status=fields.ConsistencyGroupStatus.AVAILABLE)
        req = webob.Request.blank('/v2/%s/consistencygroups/%s/delete' %
                                  (fake.PROJECT_ID, consistencygroup.id))
        req.method = 'POST'
        req.headers['Content-Type'] = 'application/json'
        body = {"invalid_request_element": {"force": False}}
        req.body = jsonutils.dump_as_bytes(body)
        res = req.get_response(fakes.wsgi_app(
            fake_auth_context=self.user_ctxt))

        self.assertEqual(400, res.status_int)

    def test_delete_consistencygroup_with_invalid_force_value_in_body(self):
        consistencygroup = self._create_consistencygroup(
            status=fields.ConsistencyGroupStatus.AVAILABLE)
        req = webob.Request.blank('/v2/%s/consistencygroups/%s/delete' %
                                  (fake.PROJECT_ID, consistencygroup.id))
        req.method = 'POST'
        req.headers['Content-Type'] = 'application/json'
        body = {"consistencygroup": {"force": "abcd"}}
        req.body = jsonutils.dump_as_bytes(body)
        res = req.get_response(fakes.wsgi_app(
            fake_auth_context=self.user_ctxt))

        self.assertEqual(400, res.status_int)

    def test_delete_consistencygroup_with_empty_force_value_in_body(self):
        consistencygroup = self._create_consistencygroup(
            status=fields.ConsistencyGroupStatus.AVAILABLE)
        req = webob.Request.blank('/v2/%s/consistencygroups/%s/delete' %
                                  (fake.PROJECT_ID, consistencygroup.id))
        req.method = 'POST'
        req.headers['Content-Type'] = 'application/json'
        body = {"consistencygroup": {"force": ""}}
        req.body = jsonutils.dump_as_bytes(body)
        res = req.get_response(fakes.wsgi_app(
            fake_auth_context=self.user_ctxt))

        self.assertEqual(400, res.status_int)

    def _assert_deleting_result_400(self, cg_id, force=False):
        req = webob.Request.blank('/v2/%s/consistencygroups/%s/delete' %
                                  (fake.PROJECT_ID, cg_id))
        req.method = 'POST'
        req.headers['Content-Type'] = 'application/json'
        body = {"consistencygroup": {"force": force}}
        req.body = jsonutils.dump_as_bytes(body)
        res = req.get_response(fakes.wsgi_app())
        self.assertEqual(400, res.status_int)

        if force:
            reason = _('Consistency group must not have attached volumes, '
                       'volumes with snapshots, or dependent cgsnapshots')
        else:
            reason = _('Consistency group status must be available or '
                       'error and must not have volumes or dependent '
                       'cgsnapshots')
        msg = (_('Invalid ConsistencyGroup: Cannot delete consistency group '
                 '%(id)s. %(reason)s, and it cannot be the source for an '
                 'ongoing CG or CG Snapshot creation.')
               % {'id': cg_id, 'reason': reason})

        res_dict = jsonutils.loads(res.body)
        self.assertEqual(400, res_dict['badRequest']['code'])
        self.assertEqual(msg, res_dict['badRequest']['message'])

    def test_delete_consistencygroup_with_volumes(self):
        consistencygroup = self._create_consistencygroup(status='available')
        utils.create_volume(self.ctxt, consistencygroup_id=consistencygroup.id,
                            testcase_instance=self)
        self._assert_deleting_result_400(consistencygroup.id)
        consistencygroup.destroy()

    def test_delete_consistencygroup_with_cgsnapshot(self):
        consistencygroup = self._create_consistencygroup(status='available')
        # If we don't add a volume to the CG the cgsnapshot creation will fail
        utils.create_volume(self.ctxt,
                            consistencygroup_id=consistencygroup.id,
                            testcase_instance=self)
        cg_snap = utils.create_cgsnapshot(self.ctxt, consistencygroup.id)
        self._assert_deleting_result_400(consistencygroup.id)
        cg_snap.destroy()
        consistencygroup.destroy()

    def test_delete_consistencygroup_with_cgsnapshot_force(self):
        consistencygroup = self._create_consistencygroup(status='available')
        # If we don't add a volume to the CG the cgsnapshot creation will fail
        utils.create_volume(self.ctxt,
                            consistencygroup_id=consistencygroup.id,
                            testcase_instance=self)
        cg_snap = utils.create_cgsnapshot(self.ctxt, consistencygroup.id)
        self._assert_deleting_result_400(consistencygroup.id, force=True)
        cg_snap.destroy()
        consistencygroup.destroy()

    def test_delete_consistencygroup_force_with_volumes(self):
        consistencygroup = self._create_consistencygroup(status='available')
        utils.create_volume(self.ctxt, consistencygroup_id=consistencygroup.id,
                            testcase_instance=self)

        req = webob.Request.blank('/v2/%s/consistencygroups/%s/delete' %
                                  (fake.PROJECT_ID, consistencygroup.id))
        req.method = 'POST'
        req.headers['Content-Type'] = 'application/json'
        body = {"consistencygroup": {"force": True}}
        req.body = jsonutils.dump_as_bytes(body)
        res = req.get_response(fakes.wsgi_app())

        consistencygroup = objects.ConsistencyGroup.get_by_id(
            self.ctxt, consistencygroup.id)
        self.assertEqual(202, res.status_int)
        self.assertEqual('deleting', consistencygroup.status)
        consistencygroup.destroy()

    def test_delete_consistencygroup_force_with_attached_volumes(self):
        consistencygroup = self._create_consistencygroup(status='available')
        utils.create_volume(self.ctxt, consistencygroup_id=consistencygroup.id,
                            testcase_instance=self, attach_status='attached')
        self._assert_deleting_result_400(consistencygroup.id, force=True)
        consistencygroup.destroy()

    def test_delete_consistencygroup_force_with_volumes_with_snapshots(self):
        consistencygroup = self._create_consistencygroup(status='available')
        vol = utils.create_volume(self.ctxt, testcase_instance=self,
                                  consistencygroup_id=consistencygroup.id)
        utils.create_snapshot(self.ctxt, vol.id)
        self._assert_deleting_result_400(consistencygroup.id, force=True)
        consistencygroup.destroy()

    def test_delete_cg_force_with_volumes_with_deleted_snapshots(self):
        consistencygroup = self._create_consistencygroup(status='available')
        vol = utils.create_volume(self.ctxt, testcase_instance=self,
                                  consistencygroup_id=consistencygroup.id)
        utils.create_snapshot(self.ctxt, vol.id, status='deleted',
                              deleted=True, testcase_instance=self)

        req = webob.Request.blank('/v2/%s/consistencygroups/%s/delete' %
                                  (fake.PROJECT_ID, consistencygroup.id))
        req.method = 'POST'
        req.headers['Content-Type'] = 'application/json'
        body = {"consistencygroup": {"force": True}}
        req.body = jsonutils.dump_as_bytes(body)
        res = req.get_response(fakes.wsgi_app())

        consistencygroup = objects.ConsistencyGroup.get_by_id(
            self.ctxt, consistencygroup.id)
        self.assertEqual(202, res.status_int)
        self.assertEqual('deleting', consistencygroup.status)
        consistencygroup.destroy()

    def test_create_consistencygroup_failed_no_volume_type(self):
        name = 'cg1'
        body = {"consistencygroup": {"name": name,
                                     "description":
                                     "Consistency Group 1", }}
        req = webob.Request.blank('/v2/%s/consistencygroups' % fake.PROJECT_ID)
        req.method = 'POST'
        req.headers['Content-Type'] = 'application/json'
        req.body = jsonutils.dump_as_bytes(body)
        res = req.get_response(fakes.wsgi_app(
            fake_auth_context=self.user_ctxt))
        res_dict = jsonutils.loads(res.body)

        self.assertEqual(400, res.status_int)
        self.assertEqual(400, res_dict['badRequest']['code'])
        msg = (_('volume_types must be provided to create '
                 'consistency group %s.') % name)
        self.assertEqual(msg, res_dict['badRequest']['message'])

    @mock.patch(
        'cinder.api.openstack.wsgi.Controller.validate_name_and_description')
    def test_update_consistencygroup_success(self, mock_validate):
        volume_type_id = fake.VOLUME_TYPE_ID
        consistencygroup = self._create_consistencygroup(
            status=fields.ConsistencyGroupStatus.AVAILABLE,
            host='test_host')

        # We create another CG from the one we are updating to confirm that
        # it will not affect the update if it is not CREATING
        cg2 = self._create_consistencygroup(
            status=fields.ConsistencyGroupStatus.AVAILABLE,
            host='test_host',
            source_cgid=consistencygroup.id)

        remove_volume_id = utils.create_volume(
            self.ctxt,
            volume_type_id=volume_type_id,
            consistencygroup_id=consistencygroup.id)['id']
        remove_volume_id2 = utils.create_volume(
            self.ctxt,
            volume_type_id=volume_type_id,
            consistencygroup_id=consistencygroup.id,
            status='error')['id']
        remove_volume_id3 = utils.create_volume(
            self.ctxt,
            volume_type_id=volume_type_id,
            consistencygroup_id=consistencygroup.id,
            status='error_deleting')['id']

        self.assertEqual(fields.ConsistencyGroupStatus.AVAILABLE,
                         consistencygroup.status)

        cg_volumes = db.volume_get_all_by_group(self.ctxt.elevated(),
                                                consistencygroup.id)
        cg_vol_ids = [cg_vol['id'] for cg_vol in cg_volumes]
        self.assertIn(remove_volume_id, cg_vol_ids)
        self.assertIn(remove_volume_id2, cg_vol_ids)
        self.assertIn(remove_volume_id3, cg_vol_ids)

        add_volume_id = utils.create_volume(
            self.ctxt,
            volume_type_id=volume_type_id)['id']
        add_volume_id2 = utils.create_volume(
            self.ctxt,
            volume_type_id=volume_type_id)['id']
        req = webob.Request.blank('/v2/%s/consistencygroups/%s/update' %
                                  (fake.PROJECT_ID, consistencygroup.id))
        req.method = 'PUT'
        req.headers['Content-Type'] = 'application/json'
        name = 'newcg'
        description = 'New Consistency Group Description'
        add_volumes = add_volume_id + "," + add_volume_id2
        remove_volumes = ','.join(
            [remove_volume_id, remove_volume_id2, remove_volume_id3])
        body = {"consistencygroup": {"name": name,
                                     "description": description,
                                     "add_volumes": add_volumes,
                                     "remove_volumes": remove_volumes, }}
        req.body = jsonutils.dump_as_bytes(body)
        res = req.get_response(fakes.wsgi_app(
            fake_auth_context=self.user_ctxt))

        consistencygroup = objects.ConsistencyGroup.get_by_id(
            self.ctxt, consistencygroup.id)
        self.assertEqual(202, res.status_int)
        self.assertTrue(mock_validate.called)
        self.assertEqual(fields.ConsistencyGroupStatus.UPDATING,
                         consistencygroup.status)

        consistencygroup.destroy()
        cg2.destroy()

    @mock.patch(
        'cinder.api.openstack.wsgi.Controller.validate_name_and_description')
    def test_update_consistencygroup_sourcing_cg(self, mock_validate):
        volume_type_id = fake.VOLUME_TYPE_ID
        consistencygroup = self._create_consistencygroup(
            status=fields.ConsistencyGroupStatus.AVAILABLE,
            host='test_host')

        cg2 = self._create_consistencygroup(
            status=fields.ConsistencyGroupStatus.CREATING,
            host='test_host',
            source_cgid=consistencygroup.id)

        remove_volume_id = utils.create_volume(
            self.ctxt,
            volume_type_id=volume_type_id,
            consistencygroup_id=consistencygroup.id)['id']
        remove_volume_id2 = utils.create_volume(
            self.ctxt,
            volume_type_id=volume_type_id,
            consistencygroup_id=consistencygroup.id)['id']

        req = webob.Request.blank('/v2/%s/consistencygroups/%s/update' %
                                  (fake.PROJECT_ID, consistencygroup.id))
        req.method = 'PUT'
        req.headers['Content-Type'] = 'application/json'
        name = 'newcg'
        description = 'New Consistency Group Description'
        remove_volumes = remove_volume_id + "," + remove_volume_id2
        body = {"consistencygroup": {"name": name,
                                     "description": description,
                                     "remove_volumes": remove_volumes, }}
        req.body = jsonutils.dump_as_bytes(body)
        res = req.get_response(fakes.wsgi_app())

        consistencygroup = objects.ConsistencyGroup.get_by_id(
            self.ctxt, consistencygroup.id)
        self.assertEqual(400, res.status_int)
        self.assertEqual(fields.ConsistencyGroupStatus.AVAILABLE,
                         consistencygroup.status)

        consistencygroup.destroy()
        cg2.destroy()

    @mock.patch(
        'cinder.api.openstack.wsgi.Controller.validate_name_and_description')
    def test_update_consistencygroup_creating_cgsnapshot(self, mock_validate):
        volume_type_id = fake.VOLUME_TYPE_ID
        consistencygroup = self._create_consistencygroup(
            status=fields.ConsistencyGroupStatus.AVAILABLE,
            host='test_host')

        # If we don't add a volume to the CG the cgsnapshot creation will fail
        utils.create_volume(self.ctxt,
                            consistencygroup_id=consistencygroup.id,
                            testcase_instance=self)

        cgsnapshot = utils.create_cgsnapshot(
            self.ctxt, consistencygroup_id=consistencygroup.id)

        add_volume_id = utils.create_volume(
            self.ctxt,
            volume_type_id=volume_type_id)['id']
        add_volume_id2 = utils.create_volume(
            self.ctxt,
            volume_type_id=volume_type_id)['id']

        req = webob.Request.blank('/v2/%s/consistencygroups/%s/update' %
                                  (fake.PROJECT_ID, consistencygroup.id))
        req.method = 'PUT'
        req.headers['Content-Type'] = 'application/json'
        name = 'newcg'
        description = 'New Consistency Group Description'
        add_volumes = add_volume_id + "," + add_volume_id2
        body = {"consistencygroup": {"name": name,
                                     "description": description,
                                     "add_volumes": add_volumes}}
        req.body = jsonutils.dump_as_bytes(body)
        res = req.get_response(fakes.wsgi_app())

        consistencygroup = objects.ConsistencyGroup.get_by_id(
            self.ctxt, consistencygroup.id)
        self.assertEqual(400, res.status_int)
        self.assertEqual(fields.ConsistencyGroupStatus.AVAILABLE,
                         consistencygroup.status)

        consistencygroup.destroy()
        cgsnapshot.destroy()

    def test_update_consistencygroup_add_volume_not_found(self):
        consistencygroup = self._create_consistencygroup(
            ctxt=self.ctxt,
            status=fields.ConsistencyGroupStatus.AVAILABLE)
        req = webob.Request.blank('/v2/%s/consistencygroups/%s/update' %
                                  (fake.PROJECT_ID, consistencygroup.id))
        req.method = 'PUT'
        req.headers['Content-Type'] = 'application/json'
        body = {"consistencygroup": {"name": None,
                                     "description": None,
                                     "add_volumes": "fake-volume-uuid",
                                     "remove_volumes": None, }}
        req.body = jsonutils.dump_as_bytes(body)
        res = req.get_response(fakes.wsgi_app(
            fake_auth_context=self.user_ctxt))
        res_dict = jsonutils.loads(res.body)

        self.assertEqual(400, res.status_int)
        self.assertEqual(400, res_dict['badRequest']['code'])
        msg = (_("Invalid volume: Cannot add volume fake-volume-uuid "
                 "to consistency group %(group_id)s because volume cannot "
                 "be found.") %
               {'group_id': consistencygroup.id})
        self.assertEqual(msg, res_dict['badRequest']['message'])

        consistencygroup.destroy()

    def test_update_consistencygroup_remove_volume_not_found(self):
        consistencygroup = self._create_consistencygroup(
            ctxt=self.ctxt,
            status=fields.ConsistencyGroupStatus.AVAILABLE)
        req = webob.Request.blank('/v2/%s/consistencygroups/%s/update' %
                                  (fake.PROJECT_ID, consistencygroup.id))
        req.method = 'PUT'
        req.headers['Content-Type'] = 'application/json'
        body = {"consistencygroup": {"name": None,
                                     "description": "new description",
                                     "add_volumes": None,
                                     "remove_volumes": "fake-volume-uuid", }}
        req.body = jsonutils.dump_as_bytes(body)
        res = req.get_response(fakes.wsgi_app(
            fake_auth_context=self.user_ctxt))
        res_dict = jsonutils.loads(res.body)

        self.assertEqual(400, res.status_int)
        self.assertEqual(400, res_dict['badRequest']['code'])
        msg = (_("Invalid volume: Cannot remove volume fake-volume-uuid "
                 "from consistency group %(group_id)s because it is not "
                 "in the group.") %
               {'group_id': consistencygroup.id})
        self.assertEqual(msg, res_dict['badRequest']['message'])

        consistencygroup.destroy()

    def test_update_consistencygroup_empty_parameters(self):
        consistencygroup = self._create_consistencygroup(
            ctxt=self.ctxt,
            status=fields.ConsistencyGroupStatus.AVAILABLE)
        req = webob.Request.blank('/v2/%s/consistencygroups/%s/update' %
                                  (fake.PROJECT_ID, consistencygroup.id))
        req.method = 'PUT'
        req.headers['Content-Type'] = 'application/json'
        body = {"consistencygroup": {"name": "",
                                     "description": "",
                                     "add_volumes": None,
                                     "remove_volumes": None, }}
        req.body = jsonutils.dump_as_bytes(body)
        res = req.get_response(fakes.wsgi_app(
            fake_auth_context=self.user_ctxt))
        res_dict = jsonutils.loads(res.body)

        self.assertEqual(400, res.status_int)
        self.assertEqual(400, res_dict['badRequest']['code'])

        consistencygroup.destroy()

    def test_update_consistencygroup_add_volume_invalid_state(self):
        volume_type_id = fake.VOLUME_TYPE_ID
        consistencygroup = self._create_consistencygroup(
            ctxt=self.ctxt,
            status=fields.ConsistencyGroupStatus.AVAILABLE)
        add_volume_id = utils.create_volume(
            self.ctxt,
            volume_type_id=volume_type_id,
            status='wrong_status')['id']
        req = webob.Request.blank('/v2/%s/consistencygroups/%s/update' %
                                  (fake.PROJECT_ID, consistencygroup.id))
        req.method = 'PUT'
        req.headers['Content-Type'] = 'application/json'
        add_volumes = add_volume_id
        body = {"consistencygroup": {"name": "cg1",
                                     "description": "",
                                     "add_volumes": add_volumes,
                                     "remove_volumes": None, }}
        req.body = jsonutils.dump_as_bytes(body)
        res = req.get_response(fakes.wsgi_app(
            fake_auth_context=self.user_ctxt))
        res_dict = jsonutils.loads(res.body)

        self.assertEqual(400, res.status_int)
        self.assertEqual(400, res_dict['badRequest']['code'])
        msg = (_("Invalid volume: Cannot add volume %(volume_id)s "
                 "to consistency group %(group_id)s because volume is in an "
                 "invalid state: %(status)s. Valid states are: ('available', "
                 "'in-use').") %
               {'volume_id': add_volume_id,
                'group_id': consistencygroup.id,
                'status': 'wrong_status'})
        self.assertEqual(msg, res_dict['badRequest']['message'])

        consistencygroup.destroy()

    def test_update_consistencygroup_add_volume_invalid_volume_type(self):
        consistencygroup = self._create_consistencygroup(
            ctxt=self.ctxt,
            status=fields.ConsistencyGroupStatus.AVAILABLE)
        wrong_type = fake.VOLUME_TYPE2_ID
        add_volume_id = utils.create_volume(
            self.ctxt,
            volume_type_id=wrong_type)['id']
        req = webob.Request.blank('/v2/%s/consistencygroups/%s/update' %
                                  (fake.PROJECT_ID, consistencygroup.id))
        req.method = 'PUT'
        req.headers['Content-Type'] = 'application/json'
        add_volumes = add_volume_id
        body = {"consistencygroup": {"name": "cg1",
                                     "description": "",
                                     "add_volumes": add_volumes,
                                     "remove_volumes": None, }}
        req.body = jsonutils.dump_as_bytes(body)
        res = req.get_response(fakes.wsgi_app(
            fake_auth_context=self.user_ctxt))
        res_dict = jsonutils.loads(res.body)

        self.assertEqual(400, res.status_int)
        self.assertEqual(400, res_dict['badRequest']['code'])
        msg = (_("Invalid volume: Cannot add volume %(volume_id)s "
                 "to consistency group %(group_id)s because volume type "
                 "%(volume_type)s is not supported by the group.") %
               {'volume_id': add_volume_id,
                'group_id': consistencygroup.id,
                'volume_type': wrong_type})
        self.assertEqual(msg, res_dict['badRequest']['message'])

        consistencygroup.destroy()

    def test_update_consistencygroup_add_volume_already_in_cg(self):
        consistencygroup = self._create_consistencygroup(
            ctxt=self.ctxt,
            status=fields.ConsistencyGroupStatus.AVAILABLE)
        add_volume_id = utils.create_volume(
            self.ctxt,
            consistencygroup_id=fake.CONSISTENCY_GROUP2_ID)['id']
        req = webob.Request.blank('/v2/%s/consistencygroups/%s/update' %
                                  (fake.PROJECT_ID, consistencygroup.id))
        req.method = 'PUT'
        req.headers['Content-Type'] = 'application/json'
        add_volumes = add_volume_id
        body = {"consistencygroup": {"name": "cg1",
                                     "description": "",
                                     "add_volumes": add_volumes,
                                     "remove_volumes": None, }}
        req.body = jsonutils.dump_as_bytes(body)
        res = req.get_response(fakes.wsgi_app(
            fake_auth_context=self.user_ctxt))
        res_dict = jsonutils.loads(res.body)

        self.assertEqual(400, res.status_int)
        self.assertEqual(400, res_dict['badRequest']['code'])
        self.assertIsNotNone(res_dict['badRequest']['message'])

        consistencygroup.destroy()

    def test_update_consistencygroup_invalid_state(self):
        consistencygroup = self._create_consistencygroup(
            status=fields.ConsistencyGroupStatus.CREATING,
            ctxt=self.ctxt)
        req = webob.Request.blank('/v2/%s/consistencygroups/%s/update' %
                                  (fake.PROJECT_ID, consistencygroup.id))
        req.method = 'PUT'
        req.headers['Content-Type'] = 'application/json'
        body = {"consistencygroup": {"name": "new name",
                                     "description": None,
                                     "add_volumes": None,
                                     "remove_volumes": None, }}
        req.body = jsonutils.dump_as_bytes(body)
        res = req.get_response(fakes.wsgi_app(
            fake_auth_context=self.user_ctxt))
        res_dict = jsonutils.loads(res.body)

        self.assertEqual(400, res.status_int)
        self.assertEqual(400, res_dict['badRequest']['code'])
        msg = (_("Invalid ConsistencyGroup: Cannot update consistency group "
                 "%s, status must be available, and it cannot be the source "
                 "for an ongoing CG or CG Snapshot creation.")
               % consistencygroup.id)
        self.assertEqual(msg, res_dict['badRequest']['message'])

        consistencygroup.destroy()

    @mock.patch(
        'cinder.api.openstack.wsgi.Controller.validate_name_and_description')
    def test_create_consistencygroup_from_src(self, mock_validate):
        self.mock_object(volume_api.API, "create", stubs.stub_volume_create)

        consistencygroup = utils.create_consistencygroup(self.ctxt)
        volume_id = utils.create_volume(
            self.ctxt,
            consistencygroup_id=consistencygroup.id)['id']
        cgsnapshot = utils.create_cgsnapshot(
            self.ctxt, consistencygroup_id=consistencygroup.id)
        snapshot = utils.create_snapshot(
            self.ctxt,
            volume_id,
            cgsnapshot_id=cgsnapshot.id,
            status=fields.SnapshotStatus.AVAILABLE)

        test_cg_name = 'test cg'
        body = {"consistencygroup-from-src": {"name": test_cg_name,
                                              "description":
                                              "Consistency Group 1",
                                              "cgsnapshot_id": cgsnapshot.id}}
        req = webob.Request.blank('/v2/%s/consistencygroups/create_from_src' %
                                  fake.PROJECT_ID)
        req.method = 'POST'
        req.headers['Content-Type'] = 'application/json'
        req.body = jsonutils.dump_as_bytes(body)
        res = req.get_response(fakes.wsgi_app(
            fake_auth_context=self.user_ctxt))
        res_dict = jsonutils.loads(res.body)

        self.assertEqual(202, res.status_int)
        self.assertIn('id', res_dict['consistencygroup'])
        self.assertEqual(test_cg_name, res_dict['consistencygroup']['name'])
        self.assertTrue(mock_validate.called)

        cg_ref = objects.ConsistencyGroup.get_by_id(
            self.ctxt.elevated(), res_dict['consistencygroup']['id'])

        cg_ref.destroy()
        snapshot.destroy()
        db.volume_destroy(self.ctxt.elevated(), volume_id)
        consistencygroup.destroy()
        cgsnapshot.destroy()

    def test_create_consistencygroup_from_src_cg(self):
        self.mock_object(volume_api.API, "create", stubs.stub_volume_create)

        source_cg = utils.create_consistencygroup(self.ctxt)
        volume_id = utils.create_volume(
            self.ctxt,
            consistencygroup_id=source_cg.id)['id']

        test_cg_name = 'test cg'
        body = {"consistencygroup-from-src": {"name": test_cg_name,
                                              "description":
                                              "Consistency Group 1",
                                              "source_cgid": source_cg.id}}
        req = webob.Request.blank('/v2/%s/consistencygroups/create_from_src' %
                                  fake.PROJECT_ID)
        req.method = 'POST'
        req.headers['Content-Type'] = 'application/json'
        req.body = jsonutils.dump_as_bytes(body)
        res = req.get_response(fakes.wsgi_app(
            fake_auth_context=self.user_ctxt))
        res_dict = jsonutils.loads(res.body)

        self.assertEqual(202, res.status_int)
        self.assertIn('id', res_dict['consistencygroup'])
        self.assertEqual(test_cg_name, res_dict['consistencygroup']['name'])

        cg = objects.ConsistencyGroup.get_by_id(
            self.ctxt, res_dict['consistencygroup']['id'])
        cg.destroy()
        db.volume_destroy(self.ctxt.elevated(), volume_id)
        source_cg.destroy()

    def test_create_consistencygroup_from_src_both_snap_cg(self):
        self.mock_object(volume_api.API, "create", stubs.stub_volume_create)

        consistencygroup = utils.create_consistencygroup(self.ctxt)
        volume_id = utils.create_volume(
            self.ctxt,
            consistencygroup_id=consistencygroup.id)['id']
        cgsnapshot_id = utils.create_cgsnapshot(
            self.ctxt,
            consistencygroup_id=consistencygroup.id)['id']
        snapshot = utils.create_snapshot(
            self.ctxt,
            volume_id,
            cgsnapshot_id=cgsnapshot_id,
            status=fields.SnapshotStatus.AVAILABLE)

        test_cg_name = 'test cg'
        body = {"consistencygroup-from-src": {"name": test_cg_name,
                                              "description":
                                              "Consistency Group 1",
                                              "cgsnapshot_id": cgsnapshot_id,
                                              "source_cgid":
                                                  consistencygroup.id}}
        req = webob.Request.blank('/v2/%s/consistencygroups/create_from_src' %
                                  fake.PROJECT_ID)
        req.method = 'POST'
        req.headers['Content-Type'] = 'application/json'
        req.body = jsonutils.dump_as_bytes(body)
        res = req.get_response(fakes.wsgi_app(
            fake_auth_context=self.user_ctxt))
        res_dict = jsonutils.loads(res.body)

        self.assertEqual(400, res.status_int)
        self.assertEqual(400, res_dict['badRequest']['code'])
        self.assertIsNotNone(res_dict['badRequest']['message'])

        snapshot.destroy()
        db.cgsnapshot_destroy(self.ctxt.elevated(), cgsnapshot_id)
        db.volume_destroy(self.ctxt.elevated(), volume_id)
        consistencygroup.destroy()

    def test_create_consistencygroup_from_src_invalid_body(self):
        name = 'cg1'
        body = {"invalid": {"name": name,
                            "description":
                            "Consistency Group 1", }}
        req = webob.Request.blank('/v2/%s/consistencygroups/create_from_src' %
                                  fake.PROJECT_ID)
        req.method = 'POST'
        req.headers['Content-Type'] = 'application/json'
        req.body = jsonutils.dump_as_bytes(body)
        res = req.get_response(fakes.wsgi_app(
            fake_auth_context=self.user_ctxt))
        res_dict = jsonutils.loads(res.body)

        self.assertEqual(400, res.status_int)
        self.assertEqual(400, res_dict['badRequest']['code'])
        # Missing 'consistencygroup-from-src' in the body.
        self.assertIsNotNone(res_dict['badRequest']['message'])

    def test_create_consistencygroup_from_src_no_source_id(self):
        name = 'cg1'
        body = {"consistencygroup-from-src": {"name": name,
                                              "description":
                                              "Consistency Group 1", }}
        req = webob.Request.blank('/v2/%s/consistencygroups/create_from_src' %
                                  fake.PROJECT_ID)
        req.method = 'POST'
        req.headers['Content-Type'] = 'application/json'
        req.body = jsonutils.dump_as_bytes(body)
        res = req.get_response(fakes.wsgi_app(
            fake_auth_context=self.user_ctxt))
        res_dict = jsonutils.loads(res.body)

        self.assertEqual(400, res.status_int)
        self.assertEqual(400, res_dict['badRequest']['code'])
        self.assertIsNotNone(res_dict['badRequest']['message'])

    def test_create_consistencygroup_from_src_no_host(self):
        consistencygroup = utils.create_consistencygroup(self.ctxt, host=None)
        volume_id = utils.create_volume(
            self.ctxt,
            consistencygroup_id=consistencygroup.id)['id']
        cgsnapshot = utils.create_cgsnapshot(
            self.ctxt, consistencygroup_id=consistencygroup.id)
        snapshot = utils.create_snapshot(
            self.ctxt,
            volume_id,
            cgsnapshot_id=cgsnapshot.id,
            status=fields.SnapshotStatus.AVAILABLE)

        test_cg_name = 'test cg'
        body = {"consistencygroup-from-src": {"name": test_cg_name,
                                              "description":
                                              "Consistency Group 1",
                                              "cgsnapshot_id": cgsnapshot.id}}
        req = webob.Request.blank('/v2/%s/consistencygroups/create_from_src' %
                                  fake.PROJECT_ID)
        req.method = 'POST'
        req.headers['Content-Type'] = 'application/json'
        req.body = jsonutils.dump_as_bytes(body)
        res = req.get_response(fakes.wsgi_app(
            fake_auth_context=self.user_ctxt))
        res_dict = jsonutils.loads(res.body)

        self.assertEqual(400, res.status_int)
        self.assertEqual(400, res_dict['badRequest']['code'])
        msg = _('Invalid ConsistencyGroup: No host to create consistency '
                'group')
        self.assertIn(msg, res_dict['badRequest']['message'])

        snapshot.destroy()
        db.volume_destroy(self.ctxt.elevated(), volume_id)
        consistencygroup.destroy()
        cgsnapshot.destroy()

    def test_create_consistencygroup_from_src_cgsnapshot_empty(self):
        consistencygroup = utils.create_consistencygroup(self.ctxt)
        volume_id = utils.create_volume(
            self.ctxt,
            consistencygroup_id=consistencygroup.id)['id']
        cgsnapshot = utils.create_cgsnapshot(
            self.ctxt,
            consistencygroup_id=consistencygroup.id)

        test_cg_name = 'test cg'
        body = {"consistencygroup-from-src": {"name": test_cg_name,
                                              "description":
                                              "Consistency Group 1",
                                              "cgsnapshot_id": cgsnapshot.id}}
        req = webob.Request.blank('/v2/%s/consistencygroups/create_from_src' %
                                  fake.PROJECT_ID)
        req.method = 'POST'
        req.headers['Content-Type'] = 'application/json'
        req.body = jsonutils.dump_as_bytes(body)
        res = req.get_response(fakes.wsgi_app(
            fake_auth_context=self.user_ctxt))
        res_dict = jsonutils.loads(res.body)

        self.assertEqual(400, res.status_int)
        self.assertEqual(400, res_dict['badRequest']['code'])
        self.assertIsNotNone(res_dict['badRequest']['message'])

        db.volume_destroy(self.ctxt.elevated(), volume_id)
        consistencygroup.destroy()
        cgsnapshot.destroy()

    def test_create_consistencygroup_from_src_source_cg_empty(self):
        source_cg = utils.create_consistencygroup(self.ctxt)

        test_cg_name = 'test cg'
        body = {"consistencygroup-from-src": {"name": test_cg_name,
                                              "description":
                                              "Consistency Group 1",
                                              "source_cgid": source_cg.id}}
        req = webob.Request.blank('/v2/%s/consistencygroups/create_from_src' %
                                  fake.PROJECT_ID)
        req.method = 'POST'
        req.headers['Content-Type'] = 'application/json'
        req.body = jsonutils.dump_as_bytes(body)
        res = req.get_response(fakes.wsgi_app(
            fake_auth_context=self.user_ctxt))
        res_dict = jsonutils.loads(res.body)

        self.assertEqual(400, res.status_int)
        self.assertEqual(400, res_dict['badRequest']['code'])
        self.assertIsNotNone(res_dict['badRequest']['message'])

        source_cg.destroy()

    def test_create_consistencygroup_from_src_cgsnapshot_notfound(self):
        consistencygroup = utils.create_consistencygroup(self.ctxt)
        volume_id = utils.create_volume(
            self.ctxt,
            consistencygroup_id=consistencygroup.id)['id']

        test_cg_name = 'test cg'
        body = {
            "consistencygroup-from-src":
            {
                "name": test_cg_name,
                "description": "Consistency Group 1",
                "source_cgid": fake.CGSNAPSHOT_ID
            }
        }
        req = webob.Request.blank('/v2/%s/consistencygroups/create_from_src' %
                                  fake.PROJECT_ID)
        req.method = 'POST'
        req.headers['Content-Type'] = 'application/json'
        req.body = jsonutils.dump_as_bytes(body)
        res = req.get_response(fakes.wsgi_app(
            fake_auth_context=self.user_ctxt))
        res_dict = jsonutils.loads(res.body)

        self.assertEqual(404, res.status_int)
        self.assertEqual(404, res_dict['itemNotFound']['code'])
        self.assertIsNotNone(res_dict['itemNotFound']['message'])

        db.volume_destroy(self.ctxt.elevated(), volume_id)
        consistencygroup.destroy()

    def test_create_consistencygroup_from_src_source_cg_notfound(self):
        test_cg_name = 'test cg'
        body = {
            "consistencygroup-from-src":
            {
                "name": test_cg_name,
                "description": "Consistency Group 1",
                "source_cgid": fake.CONSISTENCY_GROUP_ID
            }
        }
        req = webob.Request.blank('/v2/%s/consistencygroups/create_from_src' %
                                  fake.PROJECT_ID)
        req.method = 'POST'
        req.headers['Content-Type'] = 'application/json'
        req.body = jsonutils.dump_as_bytes(body)
        res = req.get_response(fakes.wsgi_app(
            fake_auth_context=self.user_ctxt))
        res_dict = jsonutils.loads(res.body)

        self.assertEqual(404, res.status_int)
        self.assertEqual(404, res_dict['itemNotFound']['code'])
        self.assertIsNotNone(res_dict['itemNotFound']['message'])

    @mock.patch.object(volume_api.API, 'create',
                       side_effect=exception.CinderException(
                           'Create volume failed.'))
    def test_create_consistencygroup_from_src_cgsnapshot_create_volume_failed(
            self, mock_create):
        consistencygroup = utils.create_consistencygroup(self.ctxt)
        volume_id = utils.create_volume(
            self.ctxt,
            consistencygroup_id=consistencygroup.id)['id']
        cgsnapshot = utils.create_cgsnapshot(
            self.ctxt, consistencygroup_id=consistencygroup.id)
        snapshot = utils.create_snapshot(
            self.ctxt,
            volume_id,
            cgsnapshot_id=cgsnapshot.id,
            status=fields.SnapshotStatus.AVAILABLE)

        test_cg_name = 'test cg'
        body = {"consistencygroup-from-src": {"name": test_cg_name,
                                              "description":
                                              "Consistency Group 1",
                                              "cgsnapshot_id": cgsnapshot.id}}
        req = webob.Request.blank('/v2/%s/consistencygroups/create_from_src' %
                                  fake.PROJECT_ID)
        req.method = 'POST'
        req.headers['Content-Type'] = 'application/json'
        req.body = jsonutils.dump_as_bytes(body)
        res = req.get_response(fakes.wsgi_app(
            fake_auth_context=self.user_ctxt))
        res_dict = jsonutils.loads(res.body)

        self.assertEqual(400, res.status_int)
        self.assertEqual(400, res_dict['badRequest']['code'])
        msg = _("Create volume failed.")
        self.assertEqual(msg, res_dict['badRequest']['message'])

        snapshot.destroy()
        db.volume_destroy(self.ctxt.elevated(), volume_id)
        consistencygroup.destroy()
        cgsnapshot.destroy()

    @mock.patch.object(volume_api.API, 'create',
                       side_effect=exception.CinderException(
                           'Create volume failed.'))
    def test_create_consistencygroup_from_src_cg_create_volume_failed(
            self, mock_create):
        source_cg = utils.create_consistencygroup(self.ctxt)
        volume_id = utils.create_volume(
            self.ctxt,
            consistencygroup_id=source_cg.id)['id']

        test_cg_name = 'test cg'
        body = {"consistencygroup-from-src": {"name": test_cg_name,
                                              "description":
                                              "Consistency Group 1",
                                              "source_cgid": source_cg.id}}
        req = webob.Request.blank('/v2/%s/consistencygroups/create_from_src' %
                                  fake.PROJECT_ID)
        req.method = 'POST'
        req.headers['Content-Type'] = 'application/json'
        req.body = jsonutils.dump_as_bytes(body)
        res = req.get_response(fakes.wsgi_app(
            fake_auth_context=self.user_ctxt))
        res_dict = jsonutils.loads(res.body)

        self.assertEqual(400, res.status_int)
        self.assertEqual(400, res_dict['badRequest']['code'])
        self.assertIsNotNone(res_dict['badRequest']['message'])

        db.volume_destroy(self.ctxt.elevated(), volume_id)
        source_cg.destroy()
