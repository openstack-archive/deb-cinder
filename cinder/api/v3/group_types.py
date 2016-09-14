# Copyright (c) 2016 EMC Corporation
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

"""The group type & group type specs controller."""

from oslo_utils import strutils
import six
import webob
from webob import exc

from cinder.api import common
from cinder.api.openstack import wsgi
from cinder.api.v3.views import group_types as views_types
from cinder import exception
from cinder.i18n import _
from cinder import policy
from cinder import rpc
from cinder import utils
from cinder.volume import group_types


class GroupTypesController(wsgi.Controller):
    """The group types API controller for the OpenStack API."""

    _view_builder_class = views_types.ViewBuilder

    def _check_policy(self, context):
        target = {
            'project_id': context.project_id,
            'user_id': context.user_id,
        }
        policy.enforce(context, 'group:group_types_manage', target)

    def _notify_group_type_error(self, context, method, err,
                                 group_type=None, id=None, name=None):
        payload = dict(
            group_types=group_type, name=name, id=id, error_message=err)
        rpc.get_notifier('groupType').error(context, method, payload)

    def _notify_group_type_info(self, context, method, group_type):
        payload = dict(group_types=group_type)
        rpc.get_notifier('groupType').info(context, method, payload)

    @wsgi.Controller.api_version('3.11')
    @wsgi.response(202)
    def create(self, req, body):
        """Creates a new group type."""
        context = req.environ['cinder.context']
        self._check_policy(context)

        self.assert_valid_body(body, 'group_type')

        grp_type = body['group_type']
        name = grp_type.get('name', None)
        description = grp_type.get('description')
        specs = grp_type.get('group_specs', {})
        is_public = grp_type.get('is_public', True)

        if name is None or len(name.strip()) == 0:
            msg = _("Group type name can not be empty.")
            raise webob.exc.HTTPBadRequest(explanation=msg)

        utils.check_string_length(name, 'Type name',
                                  min_length=1, max_length=255)

        if description is not None:
            utils.check_string_length(description, 'Type description',
                                      min_length=0, max_length=255)

        try:
            group_types.create(context,
                               name,
                               specs,
                               is_public,
                               description=description)
            grp_type = group_types.get_group_type_by_name(context, name)
            req.cache_resource(grp_type, name='group_types')
            self._notify_group_type_info(
                context, 'group_type.create', grp_type)

        except exception.GroupTypeExists as err:
            self._notify_group_type_error(
                context, 'group_type.create', err, group_type=grp_type)
            raise webob.exc.HTTPConflict(explanation=six.text_type(err))
        except exception.GroupTypeNotFoundByName as err:
            self._notify_group_type_error(
                context, 'group_type.create', err, name=name)
            raise webob.exc.HTTPNotFound(explanation=err.msg)

        return self._view_builder.show(req, grp_type)

    @wsgi.Controller.api_version('3.11')
    def update(self, req, id, body):
        # Update description for a given group type.
        context = req.environ['cinder.context']
        self._check_policy(context)

        self.assert_valid_body(body, 'group_type')

        grp_type = body['group_type']
        description = grp_type.get('description')
        name = grp_type.get('name')
        is_public = grp_type.get('is_public')

        # Name and description can not be both None.
        # If name specified, name can not be empty.
        if name and len(name.strip()) == 0:
            msg = _("Group type name can not be empty.")
            raise webob.exc.HTTPBadRequest(explanation=msg)

        if name is None and description is None and is_public is None:
            msg = _("Specify group type name, description or "
                    "a combination thereof.")
            raise webob.exc.HTTPBadRequest(explanation=msg)

        if is_public is not None and not utils.is_valid_boolstr(is_public):
            msg = _("Invalid value '%s' for is_public. Accepted values: "
                    "True or False.") % is_public
            raise webob.exc.HTTPBadRequest(explanation=msg)

        if name:
            utils.check_string_length(name, 'Type name',
                                      min_length=1, max_length=255)

        if description is not None:
            utils.check_string_length(description, 'Type description',
                                      min_length=0, max_length=255)

        try:
            group_types.update(context, id, name, description,
                               is_public=is_public)
            # Get the updated
            grp_type = group_types.get_group_type(context, id)
            req.cache_resource(grp_type, name='group_types')
            self._notify_group_type_info(
                context, 'group_type.update', grp_type)

        except exception.GroupTypeNotFound as err:
            self._notify_group_type_error(
                context, 'group_type.update', err, id=id)
            raise webob.exc.HTTPNotFound(explanation=six.text_type(err))
        except exception.GroupTypeExists as err:
            self._notify_group_type_error(
                context, 'group_type.update', err, group_type=grp_type)
            raise webob.exc.HTTPConflict(explanation=six.text_type(err))
        except exception.GroupTypeUpdateFailed as err:
            self._notify_group_type_error(
                context, 'group_type.update', err, group_type=grp_type)
            raise webob.exc.HTTPInternalServerError(
                explanation=six.text_type(err))

        return self._view_builder.show(req, grp_type)

    @wsgi.Controller.api_version('3.11')
    def delete(self, req, id):
        """Deletes an existing group type."""
        context = req.environ['cinder.context']
        self._check_policy(context)

        try:
            grp_type = group_types.get_group_type(context, id)
            group_types.destroy(context, grp_type['id'])
            self._notify_group_type_info(
                context, 'group_type.delete', grp_type)
        except exception.GroupTypeInUse as err:
            self._notify_group_type_error(
                context, 'group_type.delete', err, group_type=grp_type)
            msg = _('Target group type is still in use.')
            raise webob.exc.HTTPBadRequest(explanation=msg)
        except exception.GroupTypeNotFound as err:
            self._notify_group_type_error(
                context, 'group_type.delete', err, id=id)
            raise webob.exc.HTTPNotFound(explanation=err.msg)

        return webob.Response(status_int=202)

    @wsgi.Controller.api_version('3.11')
    def index(self, req):
        """Returns the list of group types."""
        limited_types = self._get_group_types(req)
        req.cache_resource(limited_types, name='group_types')
        return self._view_builder.index(req, limited_types)

    @wsgi.Controller.api_version('3.11')
    def show(self, req, id):
        """Return a single group type item."""
        context = req.environ['cinder.context']

        # get default group type
        if id is not None and id == 'default':
            grp_type = group_types.get_default_group_type()
            if not grp_type:
                msg = _("Default group type can not be found.")
                raise exc.HTTPNotFound(explanation=msg)
            req.cache_resource(grp_type, name='group_types')
        else:
            try:
                grp_type = group_types.get_group_type(context, id)
                req.cache_resource(grp_type, name='group_types')
            except exception.GroupTypeNotFound as error:
                raise exc.HTTPNotFound(explanation=error.msg)

        return self._view_builder.show(req, grp_type)

    def _parse_is_public(self, is_public):
        """Parse is_public into something usable.

        * True: List public group types only
        * False: List private group types only
        * None: List both public and private group types
        """

        if is_public is None:
            # preserve default value of showing only public types
            return True
        elif utils.is_none_string(is_public):
            return None
        else:
            try:
                return strutils.bool_from_string(is_public, strict=True)
            except ValueError:
                msg = _('Invalid is_public filter [%s]') % is_public
                raise exc.HTTPBadRequest(explanation=msg)

    def _get_group_types(self, req):
        """Helper function that returns a list of type dicts."""
        params = req.params.copy()
        marker, limit, offset = common.get_pagination_params(params)
        sort_keys, sort_dirs = common.get_sort_params(params)
        filters = {}
        context = req.environ['cinder.context']
        if context.is_admin:
            # Only admin has query access to all group types
            filters['is_public'] = self._parse_is_public(
                req.params.get('is_public', None))
        else:
            filters['is_public'] = True
        utils.remove_invalid_filter_options(context,
                                            filters,
                                            self._get_grp_type_filter_options()
                                            )
        limited_types = group_types.get_all_group_types(context,
                                                        filters=filters,
                                                        marker=marker,
                                                        limit=limit,
                                                        sort_keys=sort_keys,
                                                        sort_dirs=sort_dirs,
                                                        offset=offset,
                                                        list_result=True)
        return limited_types

    def _get_grp_type_filter_options(self):
        """Return group type search options allowed by non-admin."""
        return ['is_public']


def create_resource():
    return wsgi.Resource(GroupTypesController())
