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

"""The volumes V3 api."""

from oslo_log import log as logging
from oslo_utils import uuidutils
from webob import exc

from cinder.api import common
from cinder.api.openstack import wsgi
from cinder.api.v2 import volumes as volumes_v2
from cinder.api.v3.views import volumes as volume_views_v3
from cinder import exception
from cinder import group as group_api
from cinder.i18n import _, _LI
from cinder import utils
from cinder.volume import volume_types

LOG = logging.getLogger(__name__)

SUMMARY_BASE_MICRO_VERSION = '3.12'


class VolumeController(volumes_v2.VolumeController):
    """The Volumes API controller for the OpenStack API V3."""

    _view_builder_class = volume_views_v3.ViewBuilder

    def __init__(self, ext_mgr):
        self.group_api = group_api.API()
        super(VolumeController, self).__init__(ext_mgr)

    def _get_volumes(self, req, is_detail):
        """Returns a list of volumes, transformed through view builder."""

        context = req.environ['cinder.context']
        req_version = req.api_version_request

        params = req.params.copy()
        marker, limit, offset = common.get_pagination_params(params)
        sort_keys, sort_dirs = common.get_sort_params(params)
        filters = params

        if req_version.matches(None, "3.3"):
            filters.pop('glance_metadata', None)

        if req_version.matches(None, "3.9"):
            filters.pop('group_id', None)

        utils.remove_invalid_filter_options(context, filters,
                                            self._get_volume_filter_options())
        # NOTE(thingee): v2 API allows name instead of display_name
        if 'name' in sort_keys:
            sort_keys[sort_keys.index('name')] = 'display_name'

        if 'name' in filters:
            filters['display_name'] = filters.pop('name')

        if 'group_id' in filters:
            filters['consistencygroup_id'] = filters.pop('group_id')

        strict = req.api_version_request.matches("3.2", None)
        self.volume_api.check_volume_filters(filters, strict)

        volumes = self.volume_api.get_all(context, marker, limit,
                                          sort_keys=sort_keys,
                                          sort_dirs=sort_dirs,
                                          filters=filters,
                                          viewable_admin_meta=True,
                                          offset=offset)

        for volume in volumes:
            utils.add_visible_admin_metadata(volume)

        req.cache_db_volumes(volumes.objects)

        if is_detail:
            volumes = self._view_builder.detail_list(req, volumes)
        else:
            volumes = self._view_builder.summary_list(req, volumes)
        return volumes

    @wsgi.Controller.api_version(SUMMARY_BASE_MICRO_VERSION)
    def summary(self, req):
        """Return summary of volumes."""
        view_builder_v3 = volume_views_v3.ViewBuilder()
        context = req.environ['cinder.context']
        filters = req.params.copy()

        utils.remove_invalid_filter_options(context, filters,
                                            self._get_volume_filter_options())

        volumes = self.volume_api.get_volume_summary(context, filters=filters)
        return view_builder_v3.quick_summary(volumes[0], int(volumes[1]))

    @wsgi.response(202)
    def create(self, req, body):
        """Creates a new volume.

        :param req: the request
        :param body: the request body
        :returns: dict -- the new volume dictionary
        :raises: HTTPNotFound, HTTPBadRequest
        """
        self.assert_valid_body(body, 'volume')

        LOG.debug('Create volume request body: %s', body)
        context = req.environ['cinder.context']

        req_version = req.api_version_request
        # Remove group_id from body if max version is less than 3.13.
        if req_version.matches(None, "3.12"):
            # NOTE(xyang): The group_id is from a group created with a
            # group_type. So with this group_id, we've got a group_type
            # for this volume. Also if group_id is passed in, that means
            # we already know which backend is hosting the group and the
            # volume will be created on the same backend as well. So it
            # won't go through the scheduler again if a group_id is
            # passed in.
            try:
                body.get('volume', {}).pop('group_id', None)
            except AttributeError:
                msg = (_("Invalid body provided for creating volume. "
                         "Request API version: %s.") % req_version)
                raise exc.HTTPBadRequest(explanation=msg)

        volume = body['volume']
        kwargs = {}
        self.validate_name_and_description(volume)

        # NOTE(thingee): v2 API allows name instead of display_name
        if 'name' in volume:
            volume['display_name'] = volume.pop('name')

        # NOTE(thingee): v2 API allows description instead of
        #                display_description
        if 'description' in volume:
            volume['display_description'] = volume.pop('description')

        if 'image_id' in volume:
            volume['imageRef'] = volume.pop('image_id')

        req_volume_type = volume.get('volume_type', None)
        if req_volume_type:
            # Not found exception will be handled at the wsgi level
            if not uuidutils.is_uuid_like(req_volume_type):
                kwargs['volume_type'] = (
                    volume_types.get_volume_type_by_name(
                        context, req_volume_type))
            else:
                kwargs['volume_type'] = volume_types.get_volume_type(
                    context, req_volume_type)

        kwargs['metadata'] = volume.get('metadata', None)

        snapshot_id = volume.get('snapshot_id')
        if snapshot_id is not None:
            # Not found exception will be handled at the wsgi level
            kwargs['snapshot'] = self.volume_api.get_snapshot(context,
                                                              snapshot_id)
        else:
            kwargs['snapshot'] = None

        source_volid = volume.get('source_volid')
        if source_volid is not None:
            # Not found exception will be handled at the wsgi level
            kwargs['source_volume'] = (
                self.volume_api.get_volume(context,
                                           source_volid))
        else:
            kwargs['source_volume'] = None

        source_replica = volume.get('source_replica')
        if source_replica is not None:
            # Not found exception will be handled at the wsgi level
            src_vol = self.volume_api.get_volume(context,
                                                 source_replica)
            if src_vol['replication_status'] == 'disabled':
                explanation = _('source volume id:%s is not'
                                ' replicated') % source_replica
                raise exc.HTTPBadRequest(explanation=explanation)
            kwargs['source_replica'] = src_vol
        else:
            kwargs['source_replica'] = None

        consistencygroup_id = volume.get('consistencygroup_id')
        if consistencygroup_id is not None:
            # Not found exception will be handled at the wsgi level
            kwargs['consistencygroup'] = (
                self.consistencygroup_api.get(context,
                                              consistencygroup_id))
        else:
            kwargs['consistencygroup'] = None

        # Get group_id if volume is in a group.
        group_id = volume.get('group_id')
        if group_id is not None:
            try:
                kwargs['group'] = self.group_api.get(context, group_id)
            except exception.GroupNotFound as error:
                raise exc.HTTPNotFound(explanation=error.msg)

        size = volume.get('size', None)
        if size is None and kwargs['snapshot'] is not None:
            size = kwargs['snapshot']['volume_size']
        elif size is None and kwargs['source_volume'] is not None:
            size = kwargs['source_volume']['size']
        elif size is None and kwargs['source_replica'] is not None:
            size = kwargs['source_replica']['size']

        LOG.info(_LI("Create volume of %s GB"), size)

        if self.ext_mgr.is_loaded('os-image-create'):
            image_ref = volume.get('imageRef')
            if image_ref is not None:
                image_uuid = self._image_uuid_from_ref(image_ref, context)
                kwargs['image_id'] = image_uuid

        kwargs['availability_zone'] = volume.get('availability_zone', None)
        kwargs['scheduler_hints'] = volume.get('scheduler_hints', None)
        multiattach = volume.get('multiattach', False)
        kwargs['multiattach'] = multiattach

        new_volume = self.volume_api.create(context,
                                            size,
                                            volume.get('display_name'),
                                            volume.get('display_description'),
                                            **kwargs)

        retval = self._view_builder.detail(req, new_volume)

        return retval


def create_resource(ext_mgr):
    return wsgi.Resource(VolumeController(ext_mgr))
