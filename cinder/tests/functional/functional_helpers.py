# Copyright 2011 Justin Santa Barbara
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
Provides common functionality for functional tests
"""
import os.path
import random
import string
import time
import uuid

import fixtures
import mock
from oslo_config import cfg

from cinder import service
from cinder import test  # For the flags
from cinder.tests.functional.api import client
from cinder.tests.unit import fake_constants as fake


CONF = cfg.CONF


def generate_random_alphanumeric(length):
    """Creates a random alphanumeric string of specified length."""
    return ''.join(random.choice(string.ascii_uppercase + string.digits)
                   for _x in range(length))


def generate_random_numeric(length):
    """Creates a random numeric string of specified length."""
    return ''.join(random.choice(string.digits)
                   for _x in range(length))


def generate_new_element(items, prefix, numeric=False):
    """Creates a random string with prefix, that is not in 'items' list."""
    while True:
        if numeric:
            candidate = prefix + generate_random_numeric(8)
        else:
            candidate = prefix + generate_random_alphanumeric(8)
        if candidate not in items:
            return candidate


class _FunctionalTestBase(test.TestCase):
    def setUp(self):
        super(_FunctionalTestBase, self).setUp()

        f = self._get_flags()
        self.flags(**f)
        self.flags(verbose=True)

        for var in ('http_proxy', 'HTTP_PROXY'):
            self.useFixture(fixtures.EnvironmentVariable(var))

        # set up services
        self.volume = self.start_service('volume')
        # NOTE(dulek): Mocking eventlet.sleep so test won't time out on
        # scheduler service start.
        with mock.patch('eventlet.sleep'):
            self.scheduler = self.start_service('scheduler')
        self._start_api_service()
        self.addCleanup(self.osapi.stop)

        self.api = client.TestOpenStackClient(fake.USER_ID,
                                              fake.PROJECT_ID, self.auth_url)

    def _update_project(self, new_project_id):
        self.api.update_project(new_project_id)

    def _start_api_service(self):
        default_conf = os.path.abspath(os.path.join(
            os.path.dirname(__file__), '..', '..', '..',
            'etc/cinder/api-paste.ini'))
        CONF.api_paste_config = default_conf
        self.osapi = service.WSGIService("osapi_volume")
        self.osapi.start()
        # FIXME(ja): this is not the auth url - this is the service url
        # FIXME(ja): this needs fixed in nova as well
        self.auth_url = 'http://%s:%s/v2' % (self.osapi.host, self.osapi.port)

    def _get_flags(self):
        """An opportunity to setup flags, before the services are started."""
        f = {}

        # Ensure tests only listen on localhost
        f['osapi_volume_listen'] = '127.0.0.1'

        # Auto-assign ports to allow concurrent tests
        f['osapi_volume_listen_port'] = 0

        # Use simple scheduler to avoid complications - we test schedulers
        # separately
        f['scheduler_driver'] = ('cinder.scheduler.filter_scheduler.FilterSche'
                                 'duler')

        return f

    def get_unused_server_name(self):
        servers = self.api.get_servers()
        server_names = [server['name'] for server in servers]
        return generate_new_element(server_names, 'server')

    def get_invalid_image(self):
        return str(uuid.uuid4())

    def _build_minimal_create_server_request(self):
        server = {}

        image = self.api.get_images()[0]

        if 'imageRef' in image:
            image_href = image['imageRef']
        else:
            image_href = image['id']
            image_href = 'http://fake.server/%s' % image_href

        # We now have a valid imageId
        server['imageRef'] = image_href

        # Set a valid flavorId
        flavor = self.api.get_flavors()[0]
        server['flavorRef'] = 'http://fake.server/%s' % flavor['id']

        # Set a valid server name
        server_name = self.get_unused_server_name()
        server['name'] = server_name
        return server

    def _poll_volume_while(self, volume_id, continue_states,
                           expected_end_status=None, max_retries=5):
        """Poll (briefly) while the state is in continue_states.

        Continues until the state changes from continue_states or max_retries
        are hit. If expected_end_status is specified, we assert that the end
        status of the volume is expected_end_status.
        """
        retries = 0
        while retries <= max_retries:
            try:
                found_volume = self.api.get_volume(volume_id)
            except client.OpenStackApiException404:
                return None

            self.assertEqual(volume_id, found_volume['id'])
            vol_status = found_volume['status']
            if vol_status not in continue_states:
                if expected_end_status:
                    self.assertEqual(expected_end_status, vol_status)
                return found_volume

            time.sleep(1)
            retries += 1
