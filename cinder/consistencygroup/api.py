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
Handles all requests relating to consistency groups.
"""


import functools

from oslo_config import cfg
from oslo_log import log as logging
from oslo_utils import excutils
from oslo_utils import timeutils

from cinder.common import constants
from cinder import db
from cinder.db import base
from cinder import exception
from cinder.i18n import _, _LE, _LW
from cinder import objects
from cinder.objects import fields as c_fields
import cinder.policy
from cinder import quota
from cinder.scheduler import rpcapi as scheduler_rpcapi
from cinder.volume import api as volume_api
from cinder.volume import rpcapi as volume_rpcapi
from cinder.volume import utils as vol_utils
from cinder.volume import volume_types


CONF = cfg.CONF

LOG = logging.getLogger(__name__)
CGQUOTAS = quota.CGQUOTAS
VALID_REMOVE_VOL_FROM_CG_STATUS = (
    'available',
    'in-use',
    'error',
    'error_deleting')
VALID_ADD_VOL_TO_CG_STATUS = (
    'available',
    'in-use')


def wrap_check_policy(func):
    """Check policy corresponding to the wrapped methods prior to execution.

    This decorator requires the first 3 args of the wrapped function
    to be (self, context, consistencygroup)
    """
    @functools.wraps(func)
    def wrapped(self, context, target_obj, *args, **kwargs):
        check_policy(context, func.__name__, target_obj)
        return func(self, context, target_obj, *args, **kwargs)

    return wrapped


def check_policy(context, action, target_obj=None):
    target = {
        'project_id': context.project_id,
        'user_id': context.user_id,
    }
    target_obj = target_obj.fields if target_obj else {}
    target.update(target_obj)
    _action = 'consistencygroup:%s' % action
    cinder.policy.enforce(context, _action, target)


class API(base.Base):
    """API for interacting with the volume manager for consistency groups."""

    def __init__(self, db_driver=None):
        self.scheduler_rpcapi = scheduler_rpcapi.SchedulerAPI()
        self.volume_rpcapi = volume_rpcapi.VolumeAPI()
        self.availability_zone_names = ()
        self.volume_api = volume_api.API()

        super(API, self).__init__(db_driver)

    def _valid_availability_zone(self, availability_zone):
        if availability_zone in self.availability_zone_names:
            return True
        if CONF.storage_availability_zone == availability_zone:
            return True
        azs = self.volume_api.list_availability_zones()
        self.availability_zone_names = [az['name'] for az in azs]
        return availability_zone in self.availability_zone_names

    def _extract_availability_zone(self, availability_zone):
        if availability_zone is None:
            if CONF.default_availability_zone:
                availability_zone = CONF.default_availability_zone
            else:
                # For backwards compatibility use the storage_availability_zone
                availability_zone = CONF.storage_availability_zone

        valid = self._valid_availability_zone(availability_zone)
        if not valid:
            msg = _LW(
                "Availability zone '%s' is invalid") % (availability_zone)
            LOG.warning(msg)
            raise exception.InvalidInput(reason=msg)

        return availability_zone

    def create(self, context, name, description,
               cg_volume_types, availability_zone=None):
        check_policy(context, 'create')

        volume_type_list = None
        volume_type_list = cg_volume_types.split(',')

        req_volume_types = []
        # NOTE: Admin context is required to get extra_specs of volume_types.
        req_volume_types = (self.db.volume_types_get_by_name_or_id(
            context.elevated(), volume_type_list))

        req_volume_type_ids = ""
        for voltype in req_volume_types:
            req_volume_type_ids = (
                req_volume_type_ids + voltype.get('id') + ",")
        if len(req_volume_type_ids) == 0:
            req_volume_type_ids = None

        availability_zone = self._extract_availability_zone(availability_zone)
        kwargs = {'user_id': context.user_id,
                  'project_id': context.project_id,
                  'availability_zone': availability_zone,
                  'status': c_fields.ConsistencyGroupStatus.CREATING,
                  'name': name,
                  'description': description,
                  'volume_type_id': req_volume_type_ids}
        group = None
        try:
            group = objects.ConsistencyGroup(context=context, **kwargs)
            group.create()
        except Exception:
            with excutils.save_and_reraise_exception():
                LOG.error(_LE("Error occurred when creating consistency group"
                              " %s."), name)

        request_spec_list = []
        filter_properties_list = []
        for req_volume_type in req_volume_types:
            request_spec = {'volume_type': req_volume_type.copy(),
                            'consistencygroup_id': group.id}
            filter_properties = {}
            request_spec_list.append(request_spec)
            filter_properties_list.append(filter_properties)

        # Update quota for consistencygroups
        self.update_quota(context, group, 1)

        self._cast_create_consistencygroup(context, group,
                                           request_spec_list,
                                           filter_properties_list)

        return group

    def create_from_src(self, context, name, description=None,
                        cgsnapshot_id=None, source_cgid=None):
        check_policy(context, 'create')

        kwargs = {
            'user_id': context.user_id,
            'project_id': context.project_id,
            'status': c_fields.ConsistencyGroupStatus.CREATING,
            'name': name,
            'description': description,
            'cgsnapshot_id': cgsnapshot_id,
            'source_cgid': source_cgid,
        }

        group = None
        try:
            group = objects.ConsistencyGroup(context=context, **kwargs)
            group.create(cg_snap_id=cgsnapshot_id, cg_id=source_cgid)
        except exception.ConsistencyGroupNotFound:
            with excutils.save_and_reraise_exception():
                LOG.error(_LE("Source CG %(source_cg)s not found when "
                              "creating consistency group %(cg)s from "
                              "source."),
                          {'cg': name, 'source_cg': source_cgid})
        except exception.CgSnapshotNotFound:
            with excutils.save_and_reraise_exception():
                LOG.error(_LE("CG snapshot %(cgsnap)s not found when creating "
                              "consistency group %(cg)s from source."),
                          {'cg': name, 'cgsnap': cgsnapshot_id})
        except Exception:
            with excutils.save_and_reraise_exception():
                LOG.error(_LE("Error occurred when creating consistency group"
                              " %(cg)s from cgsnapshot %(cgsnap)s."),
                          {'cg': name, 'cgsnap': cgsnapshot_id})

        # Update quota for consistencygroups
        self.update_quota(context, group, 1)

        if not group.host:
            msg = _("No host to create consistency group %s.") % group.id
            LOG.error(msg)
            raise exception.InvalidConsistencyGroup(reason=msg)

        if cgsnapshot_id:
            self._create_cg_from_cgsnapshot(context, group, cgsnapshot_id)
        elif source_cgid:
            self._create_cg_from_source_cg(context, group, source_cgid)

        return group

    def _create_cg_from_cgsnapshot(self, context, group, cgsnapshot_id):
        try:
            cgsnapshot = objects.CGSnapshot.get_by_id(context, cgsnapshot_id)
            snapshots = objects.SnapshotList.get_all_for_cgsnapshot(
                context, cgsnapshot.id)

            if not snapshots:
                msg = _("Cgsnahost is empty. No consistency group "
                        "will be created.")
                raise exception.InvalidConsistencyGroup(reason=msg)

            for snapshot in snapshots:
                kwargs = {}
                kwargs['availability_zone'] = group.availability_zone
                kwargs['cgsnapshot'] = cgsnapshot
                kwargs['consistencygroup'] = group
                kwargs['snapshot'] = snapshot
                volume_type_id = snapshot.volume_type_id
                if volume_type_id:
                    kwargs['volume_type'] = volume_types.get_volume_type(
                        context, volume_type_id)

                # Since cgsnapshot is passed in, the following call will
                # create a db entry for the volume, but will not call the
                # volume manager to create a real volume in the backend yet.
                # If error happens, taskflow will handle rollback of quota
                # and removal of volume entry in the db.
                try:
                    self.volume_api.create(context,
                                           snapshot.volume_size,
                                           None,
                                           None,
                                           **kwargs)
                except exception.CinderException:
                    with excutils.save_and_reraise_exception():
                        LOG.error(_LE("Error occurred when creating volume "
                                      "entry from snapshot in the process of "
                                      "creating consistency group %(group)s "
                                      "from cgsnapshot %(cgsnap)s."),
                                  {'group': group.id,
                                   'cgsnap': cgsnapshot.id})
        except Exception:
            with excutils.save_and_reraise_exception():
                try:
                    group.destroy()
                finally:
                    LOG.error(_LE("Error occurred when creating consistency "
                                  "group %(group)s from cgsnapshot "
                                  "%(cgsnap)s."),
                              {'group': group.id,
                               'cgsnap': cgsnapshot.id})

        volumes = self.db.volume_get_all_by_group(context,
                                                  group.id)
        for vol in volumes:
            # Update the host field for the volume.
            self.db.volume_update(context, vol['id'],
                                  {'host': group.get('host')})

        self.volume_rpcapi.create_consistencygroup_from_src(
            context, group, cgsnapshot)

    def _create_cg_from_source_cg(self, context, group, source_cgid):
        try:
            source_cg = objects.ConsistencyGroup.get_by_id(context,
                                                           source_cgid)
            source_vols = self.db.volume_get_all_by_group(context,
                                                          source_cg.id)

            if not source_vols:
                msg = _("Source CG is empty. No consistency group "
                        "will be created.")
                raise exception.InvalidConsistencyGroup(reason=msg)

            for source_vol in source_vols:
                kwargs = {}
                kwargs['availability_zone'] = group.availability_zone
                kwargs['source_cg'] = source_cg
                kwargs['consistencygroup'] = group
                kwargs['source_volume'] = source_vol
                volume_type_id = source_vol.get('volume_type_id')
                if volume_type_id:
                    kwargs['volume_type'] = volume_types.get_volume_type(
                        context, volume_type_id)

                # Since source_cg is passed in, the following call will
                # create a db entry for the volume, but will not call the
                # volume manager to create a real volume in the backend yet.
                # If error happens, taskflow will handle rollback of quota
                # and removal of volume entry in the db.
                try:
                    self.volume_api.create(context,
                                           source_vol['size'],
                                           None,
                                           None,
                                           **kwargs)
                except exception.CinderException:
                    with excutils.save_and_reraise_exception():
                        LOG.error(_LE("Error occurred when creating cloned "
                                      "volume in the process of creating "
                                      "consistency group %(group)s from "
                                      "source CG %(source_cg)s."),
                                  {'group': group.id,
                                   'source_cg': source_cg.id})
        except Exception:
            with excutils.save_and_reraise_exception():
                try:
                    group.destroy()
                finally:
                    LOG.error(_LE("Error occurred when creating consistency "
                                  "group %(group)s from source CG "
                                  "%(source_cg)s."),
                              {'group': group.id,
                               'source_cg': source_cg.id})

        volumes = self.db.volume_get_all_by_group(context,
                                                  group.id)
        for vol in volumes:
            # Update the host field for the volume.
            self.db.volume_update(context, vol['id'],
                                  {'host': group.host})

        self.volume_rpcapi.create_consistencygroup_from_src(context, group,
                                                            None, source_cg)

    def _cast_create_consistencygroup(self, context, group,
                                      request_spec_list,
                                      filter_properties_list):

        try:
            for request_spec in request_spec_list:
                volume_type = request_spec.get('volume_type', None)
                volume_type_id = None
                if volume_type:
                    volume_type_id = volume_type.get('id', None)

                specs = {}
                if volume_type_id:
                    qos_specs = volume_types.get_volume_type_qos_specs(
                        volume_type_id)
                    specs = qos_specs['qos_specs']
                if not specs:
                    # to make sure we don't pass empty dict
                    specs = None

                volume_properties = {
                    'size': 0,  # Need to populate size for the scheduler
                    'user_id': context.user_id,
                    'project_id': context.project_id,
                    'status': 'creating',
                    'attach_status': 'detached',
                    'encryption_key_id': request_spec.get('encryption_key_id',
                                                          None),
                    'display_description': request_spec.get('description',
                                                            None),
                    'display_name': request_spec.get('name', None),
                    'volume_type_id': volume_type_id,
                }

                request_spec['volume_properties'] = volume_properties
                request_spec['qos_specs'] = specs

        except Exception:
            with excutils.save_and_reraise_exception():
                try:
                    group.destroy()
                finally:
                    LOG.error(_LE("Error occurred when building "
                                  "request spec list for consistency group "
                                  "%s."), group.id)

        # Cast to the scheduler and let it handle whatever is needed
        # to select the target host for this group.
        self.scheduler_rpcapi.create_consistencygroup(
            context,
            constants.VOLUME_TOPIC,
            group,
            request_spec_list=request_spec_list,
            filter_properties_list=filter_properties_list)

    def update_quota(self, context, group, num, project_id=None):
        reserve_opts = {'consistencygroups': num}
        try:
            reservations = CGQUOTAS.reserve(context,
                                            project_id=project_id,
                                            **reserve_opts)
            if reservations:
                CGQUOTAS.commit(context, reservations)
        except Exception:
            with excutils.save_and_reraise_exception():
                try:
                    group.destroy()
                finally:
                    LOG.error(_LE("Failed to update quota for "
                                  "consistency group %s."), group.id)

    @wrap_check_policy
    def delete(self, context, group, force=False):
        if not group.host:
            self.update_quota(context, group, -1, group.project_id)

            LOG.debug("No host for consistency group %s. Deleting from "
                      "the database.", group.id)
            group.destroy()

            return

        if force:
            expected = {}
        else:
            expected = {'status': (c_fields.ConsistencyGroupStatus.AVAILABLE,
                                   c_fields.ConsistencyGroupStatus.ERROR)}
        filters = [~db.cg_has_cgsnapshot_filter(),
                   ~db.cg_has_volumes_filter(attached_or_with_snapshots=force),
                   ~db.cg_creating_from_src(cg_id=group.id)]
        values = {'status': c_fields.ConsistencyGroupStatus.DELETING}

        if not group.conditional_update(values, expected, filters):
            if force:
                reason = _('Consistency group must not have attached volumes, '
                           'volumes with snapshots, or dependent cgsnapshots')
            else:
                reason = _('Consistency group status must be available or '
                           'error and must not have volumes or dependent '
                           'cgsnapshots')
            msg = (_('Cannot delete consistency group %(id)s. %(reason)s, and '
                     'it cannot be the source for an ongoing CG or CG '
                     'Snapshot creation.')
                   % {'id': group.id, 'reason': reason})
            raise exception.InvalidConsistencyGroup(reason=msg)
        self.volume_rpcapi.delete_consistencygroup(context, group)

    def _check_update(self, group, name, description, add_volumes,
                      remove_volumes, allow_empty=False):
        if allow_empty:
            if (name is None and description is None
                    and not add_volumes and not remove_volumes):
                msg = (_("Cannot update consistency group %(group_id)s "
                         "because no valid name, description, add_volumes, "
                         "or remove_volumes were provided.") %
                       {'group_id': group.id})
                raise exception.InvalidConsistencyGroup(reason=msg)
        else:
            if not (name or description or add_volumes or remove_volumes):
                msg = (_("Cannot update consistency group %(group_id)s "
                         "because no valid name, description, add_volumes, "
                         "or remove_volumes were provided.") %
                       {'group_id': group.id})
                raise exception.InvalidConsistencyGroup(reason=msg)

    def update(self, context, group, name, description,
               add_volumes, remove_volumes, allow_empty=False):
        """Update consistency group."""
        add_volumes_list = []
        remove_volumes_list = []
        if add_volumes:
            add_volumes = add_volumes.strip(',')
            add_volumes_list = add_volumes.split(',')
        if remove_volumes:
            remove_volumes = remove_volumes.strip(',')
            remove_volumes_list = remove_volumes.split(',')

        invalid_uuids = []
        for uuid in add_volumes_list:
            if uuid in remove_volumes_list:
                invalid_uuids.append(uuid)
        if invalid_uuids:
            msg = _("UUIDs %s are in both add and remove volume "
                    "list.") % invalid_uuids
            raise exception.InvalidVolume(reason=msg)

        # Validate name.
        if name == group.name:
            name = None

        # Validate description.
        if description == group.description:
            description = None
        self._check_update(group, name, description, add_volumes,
                           remove_volumes, allow_empty)

        fields = {'updated_at': timeutils.utcnow()}

        # Update name and description in db now. No need to
        # to send them over through an RPC call.
        if allow_empty:
            if name is not None:
                fields['name'] = name
            if description is not None:
                fields['description'] = description
        else:
            if name:
                fields['name'] = name
            if description:
                fields['description'] = description

        # NOTE(geguileo): We will use the updating status in the CG as a lock
        # mechanism to prevent volume add/remove races with other API, while we
        # figure out if we really need to add or remove volumes.
        if add_volumes or remove_volumes:
            fields['status'] = c_fields.ConsistencyGroupStatus.UPDATING

            # We cannot modify the members of this CG if the CG is being used
            # to create another CG or a CGsnapshot is being created
            filters = [~db.cg_creating_from_src(cg_id=group.id),
                       ~db.cgsnapshot_creating_from_src()]
        else:
            filters = []

        expected = {'status': c_fields.ConsistencyGroupStatus.AVAILABLE}
        if not group.conditional_update(fields, expected, filters):
            msg = _("Cannot update consistency group %s, status must be "
                    "available, and it cannot be the source for an ongoing "
                    "CG or CG Snapshot creation.") % group.id
            raise exception.InvalidConsistencyGroup(reason=msg)

        # Now the CG is "locked" for updating
        try:
            # Validate volumes in add_volumes and remove_volumes.
            add_volumes_new = self._validate_add_volumes(
                context, group.volumes, add_volumes_list, group)
            remove_volumes_new = self._validate_remove_volumes(
                group.volumes, remove_volumes_list, group)

            self._check_update(group, name, description, add_volumes_new,
                               remove_volumes_new, allow_empty)
        except Exception:
            # If we have an error on the volume_lists we must return status to
            # available as we were doing before removing API races
            with excutils.save_and_reraise_exception():
                group.status = c_fields.ConsistencyGroupStatus.AVAILABLE
                group.save()

        # Do an RPC call only if the update request includes
        # adding/removing volumes. add_volumes_new and remove_volumes_new
        # are strings of volume UUIDs separated by commas with no spaces
        # in between.
        if add_volumes_new or remove_volumes_new:
            self.volume_rpcapi.update_consistencygroup(
                context, group,
                add_volumes=add_volumes_new,
                remove_volumes=remove_volumes_new)
        # If there are no new volumes to add or remove and we had changed
        # the status to updating, turn it back to available
        elif group.status == c_fields.ConsistencyGroupStatus.UPDATING:
            group.status = c_fields.ConsistencyGroupStatus.AVAILABLE
            group.save()

    def _validate_remove_volumes(self, volumes, remove_volumes_list, group):
        # Validate volumes in remove_volumes.
        if not remove_volumes_list:
            return None
        remove_volumes_new = ""
        for volume in volumes:
            if volume['id'] in remove_volumes_list:
                if volume['status'] not in VALID_REMOVE_VOL_FROM_CG_STATUS:
                    msg = (_("Cannot remove volume %(volume_id)s from "
                             "consistency group %(group_id)s because volume "
                             "is in an invalid state: %(status)s. Valid "
                             "states are: %(valid)s.") %
                           {'volume_id': volume['id'],
                            'group_id': group.id,
                            'status': volume['status'],
                            'valid': VALID_REMOVE_VOL_FROM_CG_STATUS})
                    raise exception.InvalidVolume(reason=msg)
                # Volume currently in CG. It will be removed from CG.
                if remove_volumes_new:
                    remove_volumes_new += ","
                remove_volumes_new += volume['id']

        for rem_vol in remove_volumes_list:
            if rem_vol not in remove_volumes_new:
                msg = (_("Cannot remove volume %(volume_id)s from "
                         "consistency group %(group_id)s because it "
                         "is not in the group.") %
                       {'volume_id': rem_vol,
                        'group_id': group.id})
                raise exception.InvalidVolume(reason=msg)

        return remove_volumes_new

    def _validate_add_volumes(self, context, volumes, add_volumes_list, group):
        if not add_volumes_list:
            return None
        add_volumes_new = ""
        for volume in volumes:
            if volume['id'] in add_volumes_list:
                # Volume already in CG. Remove from add_volumes.
                add_volumes_list.remove(volume['id'])

        for add_vol in add_volumes_list:
            try:
                add_vol_ref = self.db.volume_get(context, add_vol)
            except exception.VolumeNotFound:
                msg = (_("Cannot add volume %(volume_id)s to consistency "
                         "group %(group_id)s because volume cannot be "
                         "found.") %
                       {'volume_id': add_vol,
                        'group_id': group.id})
                raise exception.InvalidVolume(reason=msg)
            orig_group = add_vol_ref.get('consistencygroup_id', None)
            if orig_group:
                # If volume to be added is already in the group to be updated,
                # it should have been removed from the add_volumes_list in the
                # beginning of this function. If we are here, it means it is
                # in a different group.
                msg = (_("Cannot add volume %(volume_id)s to consistency "
                         "group %(group_id)s because it is already in "
                         "consistency group %(orig_group)s.") %
                       {'volume_id': add_vol_ref['id'],
                        'group_id': group.id,
                        'orig_group': orig_group})
                raise exception.InvalidVolume(reason=msg)
            if add_vol_ref:
                add_vol_type_id = add_vol_ref.get('volume_type_id', None)
                if not add_vol_type_id:
                    msg = (_("Cannot add volume %(volume_id)s to consistency "
                             "group %(group_id)s because it has no volume "
                             "type.") %
                           {'volume_id': add_vol_ref['id'],
                            'group_id': group.id})
                    raise exception.InvalidVolume(reason=msg)
                if add_vol_type_id not in group.volume_type_id:
                    msg = (_("Cannot add volume %(volume_id)s to consistency "
                             "group %(group_id)s because volume type "
                             "%(volume_type)s is not supported by the "
                             "group.") %
                           {'volume_id': add_vol_ref['id'],
                            'group_id': group.id,
                            'volume_type': add_vol_type_id})
                    raise exception.InvalidVolume(reason=msg)
                if (add_vol_ref['status'] not in
                        VALID_ADD_VOL_TO_CG_STATUS):
                    msg = (_("Cannot add volume %(volume_id)s to consistency "
                             "group %(group_id)s because volume is in an "
                             "invalid state: %(status)s. Valid states are: "
                             "%(valid)s.") %
                           {'volume_id': add_vol_ref['id'],
                            'group_id': group.id,
                            'status': add_vol_ref['status'],
                            'valid': VALID_ADD_VOL_TO_CG_STATUS})
                    raise exception.InvalidVolume(reason=msg)

                # group.host and add_vol_ref['host'] are in this format:
                # 'host@backend#pool'. Extract host (host@backend) before
                # doing comparison.
                vol_host = vol_utils.extract_host(add_vol_ref['host'])
                group_host = vol_utils.extract_host(group.host)
                if group_host != vol_host:
                    raise exception.InvalidVolume(
                        reason=_("Volume is not local to this node."))

                # Volume exists. It will be added to CG.
                if add_volumes_new:
                    add_volumes_new += ","
                add_volumes_new += add_vol_ref['id']

            else:
                msg = (_("Cannot add volume %(volume_id)s to consistency "
                         "group %(group_id)s because volume does not exist.") %
                       {'volume_id': add_vol_ref['id'],
                        'group_id': group.id})
                raise exception.InvalidVolume(reason=msg)

        return add_volumes_new

    def get(self, context, group_id):
        group = objects.ConsistencyGroup.get_by_id(context, group_id)
        check_policy(context, 'get', group)
        return group

    def get_all(self, context, filters=None, marker=None, limit=None,
                offset=None, sort_keys=None, sort_dirs=None):
        check_policy(context, 'get_all')
        if filters is None:
            filters = {}

        if filters:
            LOG.debug("Searching by: %s", filters)

        if (context.is_admin and 'all_tenants' in filters):
            del filters['all_tenants']
            groups = objects.ConsistencyGroupList.get_all(
                context, filters=filters, marker=marker, limit=limit,
                offset=offset, sort_keys=sort_keys, sort_dirs=sort_dirs)
        else:
            groups = objects.ConsistencyGroupList.get_all_by_project(
                context, context.project_id, filters=filters, marker=marker,
                limit=limit, offset=offset, sort_keys=sort_keys,
                sort_dirs=sort_dirs)
        return groups

    def create_cgsnapshot(self, context, group, name, description):
        options = {'consistencygroup_id': group.id,
                   'user_id': context.user_id,
                   'project_id': context.project_id,
                   'status': "creating",
                   'name': name,
                   'description': description}

        cgsnapshot = None
        cgsnapshot_id = None
        try:
            cgsnapshot = objects.CGSnapshot(context, **options)
            cgsnapshot.create()
            cgsnapshot_id = cgsnapshot.id

            snap_name = cgsnapshot.name
            snap_desc = cgsnapshot.description
            with group.obj_as_admin():
                self.volume_api.create_snapshots_in_db(
                    context, group.volumes, snap_name, snap_desc,
                    cgsnapshot_id)

        except Exception:
            with excutils.save_and_reraise_exception():
                try:
                    # If the cgsnapshot has been created
                    if cgsnapshot.obj_attr_is_set('id'):
                        cgsnapshot.destroy()
                finally:
                    LOG.error(_LE("Error occurred when creating cgsnapshot"
                                  " %s."), cgsnapshot_id)

        self.volume_rpcapi.create_cgsnapshot(context, cgsnapshot)

        return cgsnapshot

    def delete_cgsnapshot(self, context, cgsnapshot, force=False):
        values = {'status': 'deleting'}
        expected = {'status': ('available', 'error')}
        filters = [~db.cg_creating_from_src(cgsnapshot_id=cgsnapshot.id)]
        res = cgsnapshot.conditional_update(values, expected, filters)

        if not res:
            msg = _('CgSnapshot status must be available or error, and no CG '
                    'can be currently using it as source for its creation.')
            raise exception.InvalidCgSnapshot(reason=msg)
        self.volume_rpcapi.delete_cgsnapshot(context.elevated(), cgsnapshot)

    def update_cgsnapshot(self, context, cgsnapshot, fields):
        cgsnapshot.update(fields)
        cgsnapshot.save()

    def get_cgsnapshot(self, context, cgsnapshot_id):
        check_policy(context, 'get_cgsnapshot')
        cgsnapshots = objects.CGSnapshot.get_by_id(context, cgsnapshot_id)
        return cgsnapshots

    def get_all_cgsnapshots(self, context, search_opts=None):
        check_policy(context, 'get_all_cgsnapshots')

        search_opts = search_opts or {}

        if context.is_admin and 'all_tenants' in search_opts:
            # Need to remove all_tenants to pass the filtering below.
            del search_opts['all_tenants']
            cgsnapshots = objects.CGSnapshotList.get_all(context, search_opts)
        else:
            cgsnapshots = objects.CGSnapshotList.get_all_by_project(
                context.elevated(), context.project_id, search_opts)
        return cgsnapshots
