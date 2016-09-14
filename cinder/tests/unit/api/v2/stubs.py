# Copyright 2010 OpenStack Foundation
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
import iso8601

from cinder import exception as exc
from cinder import objects
from cinder.objects import fields
from cinder.tests.unit import fake_constants as fake
from cinder.tests.unit import fake_volume
from cinder import utils


DEFAULT_VOL_NAME = "displayname"
DEFAULT_VOL_DESCRIPTION = "displaydesc"
DEFAULT_VOL_SIZE = 1
DEFAULT_VOL_TYPE = "vol_type_name"
DEFAULT_VOL_STATUS = "fakestatus"
DEFAULT_VOL_ID = fake.VOLUME_ID

# TODO(vbala): api.v1 tests use hard-coded "fakeaz" for verifying
# post-conditions. Update value to "zone1:host1" once we remove
# api.v1 tests and use it in api.v2 tests.
DEFAULT_AZ = "fakeaz"


def stub_volume(id, **kwargs):
    volume = {
        'id': id,
        'user_id': fake.USER_ID,
        'project_id': fake.PROJECT_ID,
        'host': 'fakehost',
        'size': DEFAULT_VOL_SIZE,
        'availability_zone': DEFAULT_AZ,
        'status': DEFAULT_VOL_STATUS,
        'migration_status': None,
        'attach_status': 'attached',
        'name': 'vol name',
        'display_name': DEFAULT_VOL_NAME,
        'display_description': DEFAULT_VOL_DESCRIPTION,
        'updated_at': datetime.datetime(1900, 1, 1, 1, 1, 1,
                                        tzinfo=iso8601.iso8601.Utc()),
        'created_at': datetime.datetime(1900, 1, 1, 1, 1, 1,
                                        tzinfo=iso8601.iso8601.Utc()),
        'snapshot_id': None,
        'source_volid': None,
        'volume_type_id': '3e196c20-3c06-11e2-81c1-0800200c9a66',
        'encryption_key_id': None,
        'volume_admin_metadata': [{'key': 'attached_mode', 'value': 'rw'},
                                  {'key': 'readonly', 'value': 'False'}],
        'bootable': False,
        'launched_at': datetime.datetime(1900, 1, 1, 1, 1, 1,
                                         tzinfo=iso8601.iso8601.Utc()),
        'volume_type': fake_volume.fake_db_volume_type(name=DEFAULT_VOL_TYPE),
        'replication_status': 'disabled',
        'replication_extended_status': None,
        'replication_driver_data': None,
        'volume_attachment': [],
        'multiattach': False,
    }

    volume.update(kwargs)
    if kwargs.get('volume_glance_metadata', None):
        volume['bootable'] = True
    if kwargs.get('attach_status') == 'detached':
        del volume['volume_admin_metadata'][0]
    return volume


def stub_volume_create(self, context, size, name, description, snapshot=None,
                       **param):
    vol = stub_volume(DEFAULT_VOL_ID)
    vol['size'] = size
    vol['display_name'] = name
    vol['display_description'] = description
    source_volume = param.get('source_volume') or {}
    vol['source_volid'] = source_volume.get('id')
    vol['bootable'] = False
    vol['volume_attachment'] = []
    vol['multiattach'] = utils.get_bool_param('multiattach', param)
    try:
        vol['snapshot_id'] = snapshot['id']
    except (KeyError, TypeError):
        vol['snapshot_id'] = None
    vol['availability_zone'] = param.get('availability_zone', 'fakeaz')
    return vol


def stub_volume_api_create(self, context, *args, **kwargs):
    vol = stub_volume_create(self, context, *args, **kwargs)
    return fake_volume.fake_volume_obj(context, **vol)


def stub_image_service_detail(self, context, **kwargs):
    filters = kwargs.get('filters', {'name': ''})
    if filters['name'] == "Fedora-x86_64-20-20140618-sda":
        return [{'id': "c905cedb-7281-47e4-8a62-f26bc5fc4c77"}]
    elif filters['name'] == "multi":
        return [{'id': "c905cedb-7281-47e4-8a62-f26bc5fc4c77"},
                {'id': "c905cedb-abcd-47e4-8a62-f26bc5fc4c77"}]
    return []


def stub_volume_create_from_image(self, context, size, name, description,
                                  snapshot, volume_type, metadata,
                                  availability_zone):
    vol = stub_volume(fake.VOLUME_ID)
    vol['status'] = 'creating'
    vol['size'] = size
    vol['display_name'] = name
    vol['display_description'] = description
    vol['availability_zone'] = 'cinder'
    vol['bootable'] = False
    return vol


def stub_volume_update(self, context, *args, **param):
    pass


def stub_volume_delete(self, context, *args, **param):
    pass


def stub_volume_get(self, context, volume_id, viewable_admin_meta=False):
    if viewable_admin_meta:
        return stub_volume(volume_id)
    else:
        volume = stub_volume(volume_id)
        del volume['volume_admin_metadata']
        return volume


def stub_volume_get_notfound(self, context,
                             volume_id, viewable_admin_meta=False):
    raise exc.VolumeNotFound(volume_id)


def stub_volume_get_db(context, volume_id):
    if context.is_admin:
        return stub_volume(volume_id)
    else:
        volume = stub_volume(volume_id)
        del volume['volume_admin_metadata']
        return volume


def stub_volume_api_get(self, context, volume_id, viewable_admin_meta=False):
    vol = stub_volume(volume_id)
    return fake_volume.fake_volume_obj(context, **vol)


def stub_volume_get_all(context, search_opts=None, marker=None, limit=None,
                        sort_keys=None, sort_dirs=None, filters=None,
                        viewable_admin_meta=False, offset=None):
    return [stub_volume(fake.VOLUME_ID, project_id=fake.PROJECT_ID),
            stub_volume(fake.VOLUME2_ID, project_id=fake.PROJECT2_ID),
            stub_volume(fake.VOLUME3_ID, project_id=fake.PROJECT3_ID)]


def stub_volume_get_all_by_project(self, context, marker, limit,
                                   sort_keys=None, sort_dirs=None,
                                   filters=None,
                                   viewable_admin_meta=False, offset=None):
    return [stub_volume_get(self, context, fake.VOLUME_ID,
                            viewable_admin_meta=True)]


def stub_volume_api_get_all_by_project(self, context, marker, limit,
                                       sort_keys=None, sort_dirs=None,
                                       filters=None,
                                       viewable_admin_meta=False,
                                       offset=None):
    vol = stub_volume_get(self, context, fake.VOLUME_ID,
                          viewable_admin_meta=viewable_admin_meta)
    vol_obj = fake_volume.fake_volume_obj(context, **vol)
    return objects.VolumeList(objects=[vol_obj])


def stub_snapshot(id, **kwargs):
    snapshot = {'id': id,
                'volume_id': fake.VOLUME_ID,
                'status': fields.SnapshotStatus.AVAILABLE,
                'volume_size': 100,
                'created_at': None,
                'display_name': 'Default name',
                'display_description': 'Default description',
                'project_id': fake.PROJECT_ID,
                'snapshot_metadata': []}

    snapshot.update(kwargs)
    return snapshot


def stub_snapshot_get_all(context, filters=None, marker=None, limit=None,
                          sort_keys=None, sort_dirs=None, offset=None):
    return [stub_snapshot(fake.VOLUME_ID, project_id=fake.PROJECT_ID),
            stub_snapshot(fake.VOLUME2_ID, project_id=fake.PROJECT2_ID),
            stub_snapshot(fake.VOLUME3_ID, project_id=fake.PROJECT3_ID)]


def stub_snapshot_get_all_by_project(context, project_id, filters=None,
                                     marker=None, limit=None, sort_keys=None,
                                     sort_dirs=None, offset=None):
    return [stub_snapshot(fake.SNAPSHOT_ID)]


def stub_snapshot_update(self, context, *args, **param):
    pass


def stub_service_get_all(*args, **kwargs):
    return [{'availability_zone': "zone1:host1", "disabled": 0}]


def stub_service_get_all_by_topic(context, topic, disabled=None):
    return [{'availability_zone': "zone1:host1", "disabled": 0}]


def stub_snapshot_get(self, context, snapshot_id):
    if snapshot_id == fake.WILL_NOT_BE_FOUND_ID:
        raise exc.SnapshotNotFound(snapshot_id=snapshot_id)

    return stub_snapshot(snapshot_id)


def stub_consistencygroup_get_notfound(self, context, cg_id):
    raise exc.ConsistencyGroupNotFound(consistencygroup_id=cg_id)


def stub_volume_type_get(context, id, *args, **kwargs):
    return {'id': id,
            'name': 'vol_type_name',
            'description': 'A fake volume type',
            'is_public': True,
            'projects': [],
            'extra_specs': {},
            'created_at': None,
            'deleted_at': None,
            'updated_at': None,
            'deleted': False}


def stub_volume_admin_metadata_get(context, volume_id, **kwargs):
    admin_meta = {'attached_mode': 'rw', 'readonly': 'False'}
    if kwargs.get('attach_status') == 'detached':
        del admin_meta['attached_mode']

    return admin_meta
