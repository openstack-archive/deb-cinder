# Copyright (c) 2016 by Kaminario Technologies, Ltd.
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
"""Volume driver for Kaminario K2 all-flash arrays."""
import six

from oslo_log import log as logging

from cinder import coordination
from cinder import exception
from cinder.i18n import _, _LE
from cinder import interface
from cinder.objects import fields
from cinder.volume.drivers.kaminario import kaminario_common as common

ISCSI_TCP_PORT = "3260"
K2_REP_FAILED_OVER = fields.ReplicationStatus.FAILED_OVER
LOG = logging.getLogger(__name__)
kaminario_logger = common.kaminario_logger


@interface.volumedriver
class KaminarioISCSIDriver(common.KaminarioCinderDriver):
    """Kaminario K2 iSCSI Volume Driver.

    Version history:
        1.0 - Initial driver
        1.1 - Added manage/unmanage and extra-specs support for nodedup
        1.2 - Added replication support
        1.3 - Added retype support
        1.4 - Added replication failback support
    """

    VERSION = '1.4'

    # ThirdPartySystems wiki page name
    CI_WIKI_NAME = "Kaminario_K2_CI"

    @kaminario_logger
    def __init__(self, *args, **kwargs):
        super(KaminarioISCSIDriver, self).__init__(*args, **kwargs)
        self._protocol = 'iSCSI'

    @kaminario_logger
    @coordination.synchronized('{self.k2_lock_name}')
    def initialize_connection(self, volume, connector):
        """Attach K2 volume to host."""
        # To support replication failback
        temp_client = None
        if (hasattr(volume, 'replication_status') and
                volume.replication_status == K2_REP_FAILED_OVER):
            temp_client = self.client
            self.client = self.target
        # Get target_portal and target iqn.
        iscsi_portal, target_iqn = self.get_target_info(volume)
        # Map volume.
        lun = self.k2_initialize_connection(volume, connector)
        # To support replication failback
        if temp_client:
            self.client = temp_client
        # Return target volume information.
        return {"driver_volume_type": "iscsi",
                "data": {"target_iqn": target_iqn,
                         "target_portal": iscsi_portal,
                         "target_lun": lun,
                         "target_discovered": True}}

    @kaminario_logger
    @coordination.synchronized('{self.k2_lock_name}')
    def terminate_connection(self, volume, connector, **kwargs):
        # To support replication failback
        temp_client = None
        if (hasattr(volume, 'replication_status') and
                volume.replication_status == K2_REP_FAILED_OVER):
            temp_client = self.client
            self.client = self.target
        super(KaminarioISCSIDriver, self).terminate_connection(volume,
                                                               connector)
        # To support replication failback
        if temp_client:
            self.client = temp_client

    def get_target_info(self, volume):
        LOG.debug("Searching first iscsi port ip without wan in K2.")
        iscsi_ip_rs = self.client.search("system/net_ips", wan_port="")
        iscsi_ip = target_iqn = None
        if hasattr(iscsi_ip_rs, 'hits') and iscsi_ip_rs.total != 0:
            iscsi_ip = iscsi_ip_rs.hits[0].ip_address
        if not iscsi_ip:
            msg = _("Unable to get ISCSI IP address from K2.")
            LOG.error(msg)
            raise exception.KaminarioCinderDriverException(reason=msg)
        iscsi_portal = "{0}:{1}".format(iscsi_ip, ISCSI_TCP_PORT)
        LOG.debug("Searching system state for target iqn in K2.")
        sys_state_rs = self.client.search("system/state")

        if hasattr(sys_state_rs, 'hits') and sys_state_rs.total != 0:
            target_iqn = sys_state_rs.hits[0].iscsi_qualified_target_name

        if not target_iqn:
            msg = _("Unable to get target iqn from K2.")
            LOG.error(msg)
            raise exception.KaminarioCinderDriverException(reason=msg)
        return iscsi_portal, target_iqn

    @kaminario_logger
    def _get_host_object(self, connector):
        host_name = self.get_initiator_host_name(connector)
        LOG.debug("Searching initiator hostname: %s in K2.", host_name)
        host_rs = self.client.search("hosts", name=host_name)
        """Create a host if not exists."""
        if host_rs.total == 0:
            try:
                LOG.debug("Creating initiator hostname: %s in K2.", host_name)
                host = self.client.new("hosts", name=host_name,
                                       type="Linux").save()
                LOG.debug("Adding iqn: %(iqn)s to host: %(host)s in K2.",
                          {'iqn': connector['initiator'], 'host': host_name})
                iqn = self.client.new("host_iqns", iqn=connector['initiator'],
                                      host=host)
                iqn.save()
            except Exception as ex:
                self._delete_host_by_name(host_name)
                LOG.exception(_LE("Unable to create host: %s in K2."),
                              host_name)
                raise exception.KaminarioCinderDriverException(
                    reason=six.text_type(ex.message))
        else:
            LOG.debug("Use existing initiator hostname: %s in K2.", host_name)
            host = host_rs.hits[0]
        return host, host_rs, host_name
