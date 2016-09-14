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

"""The consistencygroups api."""

from oslo_log import log as logging
from oslo_utils import strutils
import webob
from webob import exc

from cinder.api import common
from cinder.api import extensions
from cinder.api.openstack import wsgi
from cinder.api.views import consistencygroups as consistencygroup_views
from cinder import consistencygroup as consistencygroupAPI
from cinder import exception
from cinder.i18n import _, _LI

LOG = logging.getLogger(__name__)


class ConsistencyGroupsController(wsgi.Controller):
    """The ConsistencyGroups API controller for the OpenStack API."""

    _view_builder_class = consistencygroup_views.ViewBuilder

    def __init__(self):
        self.consistencygroup_api = consistencygroupAPI.API()
        super(ConsistencyGroupsController, self).__init__()

    def show(self, req, id):
        """Return data about the given consistency group."""
        LOG.debug('show called for member %s', id)
        context = req.environ['cinder.context']

        # Not found exception will be handled at the wsgi level
        consistencygroup = self.consistencygroup_api.get(
            context,
            group_id=id)

        return self._view_builder.detail(req, consistencygroup)

    def delete(self, req, id, body):
        """Delete a consistency group."""
        LOG.debug('delete called for member %s', id)
        context = req.environ['cinder.context']
        force = False
        if body:
            if not self.is_valid_body(body, 'consistencygroup'):
                msg = _("Missing required element 'consistencygroup' in "
                        "request body.")
                raise exc.HTTPBadRequest(explanation=msg)

            cg_body = body['consistencygroup']
            try:
                force = strutils.bool_from_string(cg_body.get('force', False),
                                                  strict=True)
            except ValueError:
                msg = _("Invalid value '%s' for force.") % force
                raise exc.HTTPBadRequest(explanation=msg)

        LOG.info(_LI('Delete consistency group with id: %s'), id)

        try:
            group = self.consistencygroup_api.get(context, id)
            self.consistencygroup_api.delete(context, group, force)
        # Not found exception will be handled at the wsgi level
        except exception.InvalidConsistencyGroup as error:
            raise exc.HTTPBadRequest(explanation=error.msg)

        return webob.Response(status_int=202)

    def index(self, req):
        """Returns a summary list of consistency groups."""
        return self._get_consistencygroups(req, is_detail=False)

    def detail(self, req):
        """Returns a detailed list of consistency groups."""
        return self._get_consistencygroups(req, is_detail=True)

    def _get_consistencygroups(self, req, is_detail):
        """Returns a list of consistency groups through view builder."""
        context = req.environ['cinder.context']
        filters = req.params.copy()
        marker, limit, offset = common.get_pagination_params(filters)
        sort_keys, sort_dirs = common.get_sort_params(filters)

        consistencygroups = self.consistencygroup_api.get_all(
            context, filters=filters, marker=marker, limit=limit,
            offset=offset, sort_keys=sort_keys, sort_dirs=sort_dirs)

        if is_detail:
            consistencygroups = self._view_builder.detail_list(
                req, consistencygroups)
        else:
            consistencygroups = self._view_builder.summary_list(
                req, consistencygroups)
        return consistencygroups

    @wsgi.response(202)
    def create(self, req, body):
        """Create a new consistency group."""
        LOG.debug('Creating new consistency group %s', body)
        self.assert_valid_body(body, 'consistencygroup')

        context = req.environ['cinder.context']
        consistencygroup = body['consistencygroup']
        self.validate_name_and_description(consistencygroup)
        name = consistencygroup.get('name', None)
        description = consistencygroup.get('description', None)
        volume_types = consistencygroup.get('volume_types', None)
        if not volume_types:
            msg = _("volume_types must be provided to create "
                    "consistency group %(name)s.") % {'name': name}
            raise exc.HTTPBadRequest(explanation=msg)
        availability_zone = consistencygroup.get('availability_zone', None)

        LOG.info(_LI("Creating consistency group %(name)s."),
                 {'name': name})

        try:
            new_consistencygroup = self.consistencygroup_api.create(
                context, name, description, volume_types,
                availability_zone=availability_zone)
        # Not found exception will be handled at the wsgi level
        except exception.InvalidConsistencyGroup as error:
            raise exc.HTTPBadRequest(explanation=error.msg)
        except exception.InvalidVolumeType as error:
            raise exc.HTTPBadRequest(explanation=error.msg)

        retval = self._view_builder.summary(req, new_consistencygroup)
        return retval

    @wsgi.response(202)
    def create_from_src(self, req, body):
        """Create a new consistency group from a source.

        The source can be a CG snapshot or a CG. Note that
        this does not require volume_types as the "create"
        API above.
        """
        LOG.debug('Creating new consistency group %s.', body)
        self.assert_valid_body(body, 'consistencygroup-from-src')

        context = req.environ['cinder.context']
        consistencygroup = body['consistencygroup-from-src']
        self.validate_name_and_description(consistencygroup)
        name = consistencygroup.get('name', None)
        description = consistencygroup.get('description', None)
        cgsnapshot_id = consistencygroup.get('cgsnapshot_id', None)
        source_cgid = consistencygroup.get('source_cgid', None)
        if not cgsnapshot_id and not source_cgid:
            msg = _("Either 'cgsnapshot_id' or 'source_cgid' must be "
                    "provided to create consistency group %(name)s "
                    "from source.") % {'name': name}
            raise exc.HTTPBadRequest(explanation=msg)

        if cgsnapshot_id and source_cgid:
            msg = _("Cannot provide both 'cgsnapshot_id' and 'source_cgid' "
                    "to create consistency group %(name)s from "
                    "source.") % {'name': name}
            raise exc.HTTPBadRequest(explanation=msg)

        if cgsnapshot_id:
            LOG.info(_LI("Creating consistency group %(name)s from "
                         "cgsnapshot %(snap)s."),
                     {'name': name, 'snap': cgsnapshot_id})
        elif source_cgid:
            LOG.info(_LI("Creating consistency group %(name)s from "
                         "source consistency group %(source_cgid)s."),
                     {'name': name, 'source_cgid': source_cgid})

        try:
            new_consistencygroup = self.consistencygroup_api.create_from_src(
                context, name, description, cgsnapshot_id, source_cgid)
        except exception.InvalidConsistencyGroup as error:
            raise exc.HTTPBadRequest(explanation=error.msg)
        except exception.NotFound:
            # Not found exception will be handled at the wsgi level
            raise
        except exception.CinderException as error:
            raise exc.HTTPBadRequest(explanation=error.msg)

        retval = self._view_builder.summary(req, new_consistencygroup)
        return retval

    def _check_update_parameters(self, name, description, add_volumes,
                                 remove_volumes):
        if not (name or description or add_volumes or remove_volumes):
            msg = _("Name, description, add_volumes, and remove_volumes "
                    "can not be all empty in the request body.")
            raise exc.HTTPBadRequest(explanation=msg)

    def _update(self, context, id, name, description, add_volumes,
                remove_volumes,
                allow_empty=False):
        LOG.info(_LI("Updating consistency group %(id)s with name %(name)s "
                     "description: %(description)s add_volumes: "
                     "%(add_volumes)s remove_volumes: %(remove_volumes)s."),
                 {'id': id,
                  'name': name,
                  'description': description,
                  'add_volumes': add_volumes,
                  'remove_volumes': remove_volumes})

        # Handle relevant exceptions at wsgi level
        group = self.consistencygroup_api.get(context, id)
        self.consistencygroup_api.update(context, group, name, description,
                                         add_volumes, remove_volumes,
                                         allow_empty)

    def update(self, req, id, body):
        """Update the consistency group.

        Expected format of the input parameter 'body':

        .. code-block:: json

            {
                "consistencygroup":
                {
                    "name": "my_cg",
                    "description": "My consistency group",
                    "add_volumes": "volume-uuid-1,volume-uuid-2,...",
                    "remove_volumes": "volume-uuid-8,volume-uuid-9,..."
                }
            }

        """
        LOG.debug('Update called for consistency group %s.', id)
        if not body:
            msg = _("Missing request body.")
            raise exc.HTTPBadRequest(explanation=msg)

        self.assert_valid_body(body, 'consistencygroup')
        context = req.environ['cinder.context']
        consistencygroup = body.get('consistencygroup', None)
        self.validate_name_and_description(consistencygroup)
        name = consistencygroup.get('name', None)
        description = consistencygroup.get('description', None)
        add_volumes = consistencygroup.get('add_volumes', None)
        remove_volumes = consistencygroup.get('remove_volumes', None)

        self._check_update_parameters(name, description, add_volumes,
                                      remove_volumes)
        self._update(context, id, name, description, add_volumes,
                     remove_volumes)
        return webob.Response(status_int=202)


class Consistencygroups(extensions.ExtensionDescriptor):
    """consistency groups support."""

    name = 'Consistencygroups'
    alias = 'consistencygroups'
    updated = '2014-08-18T00:00:00+00:00'

    def get_resources(self):
        resources = []
        res = extensions.ResourceExtension(
            Consistencygroups.alias, ConsistencyGroupsController(),
            collection_actions={'detail': 'GET', 'create_from_src': 'POST'},
            member_actions={'delete': 'POST', 'update': 'PUT'})
        resources.append(res)
        return resources
