#    Copyright 2015 Intel Corporation
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

from oslo_config import cfg
from oslo_serialization import base64
from oslo_serialization import jsonutils
from oslo_utils import versionutils
from oslo_versionedobjects import fields

from cinder import db
from cinder import exception
from cinder.i18n import _
from cinder import objects
from cinder.objects import base
from cinder.objects import fields as c_fields


CONF = cfg.CONF


@base.CinderObjectRegistry.register
class Backup(base.CinderPersistentObject, base.CinderObject,
             base.CinderObjectDictCompat):
    # Version 1.0: Initial version
    # Version 1.1: Add new field num_dependent_backups and extra fields
    #              is_incremental and has_dependent_backups.
    # Version 1.2: Add new field snapshot_id and data_timestamp.
    # Version 1.3: Changed 'status' field to use BackupStatusField
    # Version 1.4: Add restore_volume_id
    VERSION = '1.4'

    fields = {
        'id': fields.UUIDField(),

        'user_id': fields.StringField(),
        'project_id': fields.StringField(),

        'volume_id': fields.UUIDField(),
        'host': fields.StringField(nullable=True),
        'availability_zone': fields.StringField(nullable=True),
        'container': fields.StringField(nullable=True),
        'parent_id': fields.StringField(nullable=True),
        'status': c_fields.BackupStatusField(nullable=True),
        'fail_reason': fields.StringField(nullable=True),
        'size': fields.IntegerField(nullable=True),

        'display_name': fields.StringField(nullable=True),
        'display_description': fields.StringField(nullable=True),

        # NOTE(dulek): Metadata field is used to store any strings by backup
        # drivers, that's why it can't be DictOfStringsField.
        'service_metadata': fields.StringField(nullable=True),
        'service': fields.StringField(nullable=True),

        'object_count': fields.IntegerField(nullable=True),

        'temp_volume_id': fields.StringField(nullable=True),
        'temp_snapshot_id': fields.StringField(nullable=True),
        'num_dependent_backups': fields.IntegerField(nullable=True),
        'snapshot_id': fields.StringField(nullable=True),
        'data_timestamp': fields.DateTimeField(nullable=True),
        'restore_volume_id': fields.StringField(nullable=True),
    }

    obj_extra_fields = ['name', 'is_incremental', 'has_dependent_backups']

    @property
    def name(self):
        return CONF.backup_name_template % self.id

    @property
    def is_incremental(self):
        return bool(self.parent_id)

    @property
    def has_dependent_backups(self):
        return bool(self.num_dependent_backups)

    def obj_make_compatible(self, primitive, target_version):
        """Make an object representation compatible with a target version."""
        super(Backup, self).obj_make_compatible(primitive, target_version)
        target_version = versionutils.convert_version_to_tuple(target_version)

    @staticmethod
    def _from_db_object(context, backup, db_backup):
        for name, field in backup.fields.items():
            value = db_backup.get(name)
            if isinstance(field, fields.IntegerField):
                value = value if value is not None else 0
            backup[name] = value

        backup._context = context
        backup.obj_reset_changes()
        return backup

    def create(self):
        if self.obj_attr_is_set('id'):
            raise exception.ObjectActionError(action='create',
                                              reason='already created')
        updates = self.cinder_obj_get_changes()

        db_backup = db.backup_create(self._context, updates)
        self._from_db_object(self._context, self, db_backup)

    def save(self):
        updates = self.cinder_obj_get_changes()
        if updates:
            db.backup_update(self._context, self.id, updates)

        self.obj_reset_changes()

    def destroy(self):
        with self.obj_as_admin():
            updated_values = db.backup_destroy(self._context, self.id)
        self.update(updated_values)
        self.obj_reset_changes(updated_values.keys())

    @staticmethod
    def decode_record(backup_url):
        """Deserialize backup metadata from string into a dictionary.

        :raises: InvalidInput
        """
        try:
            return jsonutils.loads(base64.decode_as_text(backup_url))
        except TypeError:
            msg = _("Can't decode backup record.")
        except ValueError:
            msg = _("Can't parse backup record.")
        raise exception.InvalidInput(reason=msg)

    def encode_record(self, **kwargs):
        """Serialize backup object, with optional extra info, into a string."""
        # We don't want to export extra fields and we want to force lazy
        # loading, so we can't use dict(self) or self.obj_to_primitive
        record = {name: field.to_primitive(self, name, getattr(self, name))
                  for name, field in self.fields.items()}
        # We must update kwargs instead of record to ensure we don't overwrite
        # "real" data from the backup
        kwargs.update(record)
        retval = jsonutils.dump_as_bytes(kwargs)
        return base64.encode_as_text(retval)


@base.CinderObjectRegistry.register
class BackupList(base.ObjectListBase, base.CinderObject):
    VERSION = '1.0'

    fields = {
        'objects': fields.ListOfObjectsField('Backup'),
    }

    @classmethod
    def get_all(cls, context, filters=None, marker=None, limit=None,
                offset=None, sort_keys=None, sort_dirs=None):
        backups = db.backup_get_all(context, filters, marker, limit, offset,
                                    sort_keys, sort_dirs)
        return base.obj_make_list(context, cls(context), objects.Backup,
                                  backups)

    @classmethod
    def get_all_by_host(cls, context, host):
        backups = db.backup_get_all_by_host(context, host)
        return base.obj_make_list(context, cls(context), objects.Backup,
                                  backups)

    @classmethod
    def get_all_by_project(cls, context, project_id, filters=None,
                           marker=None, limit=None, offset=None,
                           sort_keys=None, sort_dirs=None):
        backups = db.backup_get_all_by_project(context, project_id, filters,
                                               marker, limit, offset,
                                               sort_keys, sort_dirs)
        return base.obj_make_list(context, cls(context), objects.Backup,
                                  backups)

    @classmethod
    def get_all_by_volume(cls, context, volume_id, filters=None):
        backups = db.backup_get_all_by_volume(context, volume_id, filters)
        return base.obj_make_list(context, cls(context), objects.Backup,
                                  backups)


@base.CinderObjectRegistry.register
class BackupImport(Backup):
    """Special object for Backup Imports.

    This class should not be used for anything but Backup creation when
    importing backups to the DB.

    On creation it allows to specify the ID for the backup, since it's the
    reference used in parent_id it is imperative that this is preserved.

    Backup Import objects get promoted to standard Backups when the import is
    completed.
    """

    def create(self):
        updates = self.cinder_obj_get_changes()

        db_backup = db.backup_create(self._context, updates)
        self._from_db_object(self._context, self, db_backup)
