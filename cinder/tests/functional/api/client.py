#    Copyright (c) 2011 Justin Santa Barbara
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

from oslo_serialization import jsonutils
from oslo_utils import netutils
import requests
from six.moves import urllib

from cinder.i18n import _
from cinder.tests.unit import fake_constants as fake


class OpenStackApiException(Exception):
    message = 'Unspecified error'

    def __init__(self, response=None, msg=None):
        self.response = response
        # Give chance to override default message
        if msg:
            self.message = msg

        if response:
            self.message = _(
                '%(message)s\nStatus Code: %(_status)s\nBody: %(_body)s') % {
                '_status': response.status_code, '_body': response.text,
                'message': self.message}

        super(OpenStackApiException, self).__init__(self.message)


class OpenStackApiException401(OpenStackApiException):
    message = _("401 Unauthorized Error")


class OpenStackApiException404(OpenStackApiException):
    message = _("404 Not Found Error")


class OpenStackApiException413(OpenStackApiException):
    message = _("413 Request entity too large")


class OpenStackApiException400(OpenStackApiException):
    message = _("400 Bad Request")


class TestOpenStackClient(object):
    """Simple OpenStack API Client.

    This is a really basic OpenStack API client that is under our control,
    so we can make changes / insert hooks for testing

    """

    def __init__(self, auth_user, auth_key, auth_uri):
        super(TestOpenStackClient, self).__init__()
        self.auth_result = None
        self.auth_user = auth_user
        self.auth_key = auth_key
        self.auth_uri = auth_uri
        # default project_id
        self.project_id = fake.PROJECT_ID

    def request(self, url, method='GET', body=None, headers=None,
                ssl_verify=True, stream=False):
        _headers = {'Content-Type': 'application/json'}
        _headers.update(headers or {})

        parsed_url = urllib.parse.urlparse(url)
        port = parsed_url.port
        hostname = parsed_url.hostname
        scheme = parsed_url.scheme

        if netutils.is_valid_ipv6(hostname):
            hostname = "[%s]" % hostname

        relative_url = parsed_url.path
        if parsed_url.query:
            relative_url = relative_url + "?" + parsed_url.query

        if port:
            _url = "%s://%s:%d%s" % (scheme, hostname, int(port), relative_url)
        else:
            _url = "%s://%s%s" % (scheme, hostname, relative_url)

        response = requests.request(method, _url, data=body, headers=_headers,
                                    verify=ssl_verify, stream=stream)

        return response

    def _authenticate(self, reauthenticate=False):
        if self.auth_result and not reauthenticate:
            return self.auth_result

        auth_uri = self.auth_uri
        headers = {'X-Auth-User': self.auth_user,
                   'X-Auth-Key': self.auth_key,
                   'X-Auth-Project-Id': self.project_id}
        response = self.request(auth_uri,
                                headers=headers)

        http_status = response.status_code

        if http_status == 401:
            raise OpenStackApiException401(response=response)

        self.auth_result = response.headers
        return self.auth_result

    def update_project(self, new_project_id):
        self.project_id = new_project_id
        self._authenticate(True)

    def api_request(self, relative_uri, check_response_status=None, **kwargs):
        auth_result = self._authenticate()

        # NOTE(justinsb): httplib 'helpfully' converts headers to lower case
        base_uri = auth_result['x-server-management-url']

        full_uri = '%s/%s' % (base_uri, relative_uri)

        headers = kwargs.setdefault('headers', {})
        headers['X-Auth-Token'] = auth_result['x-auth-token']

        response = self.request(full_uri, **kwargs)

        http_status = response.status_code
        if check_response_status:
            if http_status not in check_response_status:
                message = None
                try:
                    exc = globals()["OpenStackApiException%s" % http_status]
                except KeyError:
                    exc = OpenStackApiException
                    message = _("Unexpected status code")
                raise exc(response, message)

        return response

    def _decode_json(self, response):
        body = response.text
        if body:
            return jsonutils.loads(body)
        else:
            return ""

    def api_get(self, relative_uri, **kwargs):
        kwargs.setdefault('check_response_status', [200])
        response = self.api_request(relative_uri, **kwargs)
        return self._decode_json(response)

    def api_post(self, relative_uri, body, **kwargs):
        kwargs['method'] = 'POST'
        if body:
            headers = kwargs.setdefault('headers', {})
            headers['Content-Type'] = 'application/json'
            kwargs['body'] = jsonutils.dumps(body)

        kwargs.setdefault('check_response_status', [200, 202])
        response = self.api_request(relative_uri, **kwargs)
        return self._decode_json(response)

    def api_put(self, relative_uri, body, **kwargs):
        kwargs['method'] = 'PUT'
        if body:
            headers = kwargs.setdefault('headers', {})
            headers['Content-Type'] = 'application/json'
            kwargs['body'] = jsonutils.dumps(body)

        kwargs.setdefault('check_response_status', [200, 202, 204])
        response = self.api_request(relative_uri, **kwargs)
        return self._decode_json(response)

    def api_delete(self, relative_uri, **kwargs):
        kwargs['method'] = 'DELETE'
        kwargs.setdefault('check_response_status', [200, 202, 204])
        return self.api_request(relative_uri, **kwargs)

    def get_volume(self, volume_id):
        return self.api_get('/volumes/%s' % volume_id)['volume']

    def get_volumes(self, detail=True):
        rel_url = '/volumes/detail' if detail else '/volumes'
        return self.api_get(rel_url)['volumes']

    def post_volume(self, volume):
        return self.api_post('/volumes', volume)['volume']

    def delete_volume(self, volume_id):
        return self.api_delete('/volumes/%s' % volume_id)

    def put_volume(self, volume_id, volume):
        return self.api_put('/volumes/%s' % volume_id, volume)['volume']

    def quota_set(self, project_id, quota_update):
        return self.api_put(
            'os-quota-sets/%s' % project_id,
            {'quota_set': quota_update})['quota_set']

    def quota_get(self, project_id, usage=True):

        return self.api_get('os-quota-sets/%s?usage=%s'
                            % (project_id, usage))['quota_set']

    def create_type(self, type_name, extra_specs=None):
        type = {"volume_type": {"name": type_name}}
        if extra_specs:
            type['extra_specs'] = extra_specs

        return self.api_post('/types', type)['volume_type']
