# Copyright (c) 2016 Huawei Technologies Co., Ltd.
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

import six
import time

from oslo_log import log as logging
from oslo_service import loopingcall
from oslo_utils import units

from cinder import exception
from cinder.i18n import _
from cinder import objects
from cinder.volume.drivers.huawei import constants

LOG = logging.getLogger(__name__)


def encode_name(name):
    pre_name = name.split("-")[0]
    vol_encoded = six.text_type(hash(name))
    if vol_encoded.startswith('-'):
        newuuid = pre_name + vol_encoded
    else:
        newuuid = pre_name + '-' + vol_encoded
    return newuuid


def encode_host_name(name):
    if name and (len(name) > constants.MAX_HOSTNAME_LENGTH):
        name = six.text_type(hash(name))
    return name


def wait_for_condition(func, interval, timeout):
    start_time = time.time()

    def _inner():
        try:
            res = func()
        except Exception as ex:
            raise exception.VolumeBackendAPIException(data=ex)

        if res:
            raise loopingcall.LoopingCallDone()

        if int(time.time()) - start_time > timeout:
            msg = (_('wait_for_condition: %s timed out.')
                   % func.__name__)
            LOG.error(msg)
            raise exception.VolumeBackendAPIException(data=msg)

    timer = loopingcall.FixedIntervalLoopingCall(_inner)
    timer.start(interval=interval).wait()


def get_volume_size(volume):
    """Calculate the volume size.

    We should divide the given volume size by 512 for the 18000 system
    calculates volume size with sectors, which is 512 bytes.
    """
    volume_size = units.Gi / 512  # 1G
    if int(volume.size) != 0:
        volume_size = int(volume.size) * units.Gi / 512

    return volume_size


def get_volume_metadata(volume):
    if type(volume) is objects.Volume:
        return volume.metadata

    if 'volume_metadata' in volume:
        metadata = volume.get('volume_metadata')
        return {item['key']: item['value'] for item in metadata}

    return {}


def get_admin_metadata(volume):
    admin_metadata = {}
    if 'admin_metadata' in volume:
        admin_metadata = volume.admin_metadata
    elif 'volume_admin_metadata' in volume:
        metadata = volume.get('volume_admin_metadata', [])
        admin_metadata = {item['key']: item['value'] for item in metadata}

    LOG.debug("Volume ID: %(id)s, admin_metadata: %(admin_metadata)s.",
              {"id": volume.id, "admin_metadata": admin_metadata})
    return admin_metadata


def get_snapshot_metadata_value(snapshot):
    if type(snapshot) is objects.Snapshot:
        return snapshot.metadata

    if 'snapshot_metadata' in snapshot:
        metadata = snapshot.snapshot_metadata
        return {item['key']: item['value'] for item in metadata}

    return {}
