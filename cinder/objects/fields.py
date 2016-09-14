#    Copyright 2015 IBM Corp.
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

"""Custom fields for Cinder objects."""

from oslo_versionedobjects import fields


BaseEnumField = fields.BaseEnumField
Enum = fields.Enum
Field = fields.Field
FieldType = fields.FieldType


class BaseCinderEnum(Enum):
    def __init__(self):
        super(BaseCinderEnum, self).__init__(valid_values=self.__class__.ALL)


class BackupStatus(BaseCinderEnum):
    ERROR = 'error'
    ERROR_DELETING = 'error_deleting'
    CREATING = 'creating'
    AVAILABLE = 'available'
    DELETING = 'deleting'
    DELETED = 'deleted'
    RESTORING = 'restoring'

    ALL = (ERROR, ERROR_DELETING, CREATING, AVAILABLE, DELETING, DELETED,
           RESTORING)


class BackupStatusField(BaseEnumField):
    AUTO_TYPE = BackupStatus()


class ConsistencyGroupStatus(BaseCinderEnum):
    ERROR = 'error'
    AVAILABLE = 'available'
    CREATING = 'creating'
    DELETING = 'deleting'
    DELETED = 'deleted'
    UPDATING = 'updating'
    ERROR_DELETING = 'error_deleting'

    ALL = (ERROR, AVAILABLE, CREATING, DELETING, DELETED,
           UPDATING, ERROR_DELETING)


class ConsistencyGroupStatusField(BaseEnumField):
    AUTO_TYPE = ConsistencyGroupStatus()


class GroupStatus(BaseCinderEnum):
    ERROR = 'error'
    AVAILABLE = 'available'
    CREATING = 'creating'
    DELETING = 'deleting'
    DELETED = 'deleted'
    UPDATING = 'updating'
    IN_USE = 'in-use'
    ERROR_DELETING = 'error_deleting'

    ALL = (ERROR, AVAILABLE, CREATING, DELETING, DELETED,
           UPDATING, IN_USE, ERROR_DELETING)


class GroupStatusField(BaseEnumField):
    AUTO_TYPE = GroupStatus()


class ReplicationStatus(BaseCinderEnum):
    ERROR = 'error'
    ENABLED = 'enabled'
    DISABLED = 'disabled'
    NOT_CAPABLE = 'not-capable'
    FAILING_OVER = 'failing-over'
    FAILOVER_ERROR = 'failover-error'
    FAILED_OVER = 'failed-over'

    ALL = (ERROR, ENABLED, DISABLED, NOT_CAPABLE, FAILOVER_ERROR, FAILING_OVER,
           FAILED_OVER)


class ReplicationStatusField(BaseEnumField):
    AUTO_TYPE = ReplicationStatus()


class SnapshotStatus(BaseCinderEnum):
    ERROR = 'error'
    AVAILABLE = 'available'
    CREATING = 'creating'
    DELETING = 'deleting'
    DELETED = 'deleted'
    UPDATING = 'updating'
    ERROR_DELETING = 'error_deleting'

    ALL = (ERROR, AVAILABLE, CREATING, DELETING, DELETED,
           UPDATING, ERROR_DELETING)


class SnapshotStatusField(BaseEnumField):
    AUTO_TYPE = SnapshotStatus()


class QoSConsumerValues(BaseCinderEnum):
    BACK_END = 'back-end'
    FRONT_END = 'front-end'
    BOTH = 'both'

    ALL = (BACK_END, FRONT_END, BOTH)


class QoSConsumerField(BaseEnumField):
    AUTO_TYPE = QoSConsumerValues()
