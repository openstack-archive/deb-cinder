#    Copyright 2016 Dell Inc.
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
"""Interface for interacting with the Dell Storage Center array."""

import json
import os.path

import eventlet
from oslo_log import log as logging
import requests
from simplejson import scanner
import six

from cinder import exception
from cinder.i18n import _, _LE, _LI, _LW
from cinder import utils

LOG = logging.getLogger(__name__)


class PayloadFilter(object):
    """PayloadFilter

    Simple class for creating filters for interacting with the Dell
    Storage API 15.3 and later.
    """

    def __init__(self, filtertype='AND'):
        self.payload = {}
        self.payload['filter'] = {'filterType': filtertype,
                                  'filters': []}

    def append(self, name, val, filtertype='Equals'):
        if val is not None:
            apifilter = {}
            apifilter['attributeName'] = name
            apifilter['attributeValue'] = val
            apifilter['filterType'] = filtertype
            self.payload['filter']['filters'].append(apifilter)


class LegacyPayloadFilter(object):
    """LegacyPayloadFilter

    Simple class for creating filters for interacting with the Dell
    Storage API 15.1 and 15.2.
    """

    def __init__(self, filter_type='AND'):
        self.payload = {'filterType': filter_type,
                        'filters': []}

    def append(self, name, val, filtertype='Equals'):
        if val is not None:
            apifilter = {}
            apifilter['attributeName'] = name
            apifilter['attributeValue'] = val
            apifilter['filterType'] = filtertype
            self.payload['filters'].append(apifilter)


class HttpClient(object):
    """HttpClient

    Helper for making the REST calls.
    """

    def __init__(self, host, port, user, password, verify, apiversion):
        """HttpClient handles the REST requests.

        :param host: IP address of the Dell Data Collector.
        :param port: Port the Data Collector is listening on.
        :param user: User account to login with.
        :param password: Password.
        :param verify: Boolean indicating whether certificate verification
                       should be turned on or not.
        :param apiversion: Dell API version.
        """
        self.baseUrl = 'https://%s:%s/' % (host, port)

        self.session = requests.Session()
        self.session.auth = (user, password)

        self.header = {}
        self.header['Content-Type'] = 'application/json; charset=utf-8'
        self.header['Accept'] = 'application/json'
        self.header['x-dell-api-version'] = apiversion
        self.verify = verify

        # Verify is a configurable option.  So if this is false do not
        # spam the c-vol log.
        if not verify:
            requests.packages.urllib3.disable_warnings()

    def __enter__(self):
        return self

    def __exit__(self, type, value, traceback):
        self.session.close()

    def __formatUrl(self, url):
        baseurl = self.baseUrl
        # Some url sources have api/rest and some don't. Handle.
        if 'api/rest' not in url:
            baseurl += 'api/rest/'
        return '%s%s' % (baseurl, url if url[0] != '/' else url[1:])

    def _get_header(self, async):
        if async:
            header = self.header.copy()
            header['async'] = True
            return header
        return self.header

    def _get_async_url(self, asyncTask):
        """Handle a bug in SC API that gives a full url."""
        try:
            # strip off the https.
            url = asyncTask.get('returnValue').split(
                'https://')[1].split('/', 1)[1]
        except IndexError:
            url = asyncTask.get('returnValue')
        # Check for incomplete url error case.
        if url.endswith('/'):
            # Try to fix.
            id = asyncTask.get('instanceId')
            if id:
                # We have an id so note the error and add the id.
                LOG.debug('_get_async_url: url format error. (%s)', asyncTask)
                url = url + id
            else:
                # No hope.
                LOG.error(_LE('_get_async_url: Bogus return url %s'), url)
                raise exception.VolumeBackendAPIException(
                    message=_('_get_async_url: Invalid URL.'))
        return url

    def _wait_for_async_complete(self, asyncTask):
        url = self._get_async_url(asyncTask)
        while True and url:
            try:
                r = self.get(url)
                # We can leave this loop for a variety of reasons.
                # Nothing returned.
                # r.content blanks.
                # Object returned switches to one without objectType or with
                # a different objectType.
                if not StorageCenterApi._check_result(r):
                    LOG.debug('Async error: status_code: %s', r.status_code)
                else:
                    # In theory we have a good run.
                    if r.content:
                        content = r.json()
                        if content.get('objectType') == 'AsyncTask':
                            url = self._get_async_url(content)
                            eventlet.sleep(1)
                            continue
                    else:
                        LOG.debug('Async debug: r.content is None')
                return r
            except Exception:
                methodname = asyncTask.get('methodName')
                objectTypeName = asyncTask.get('objectTypeName')
                msg = (_('Async error: Unable to retrieve %(obj)s '
                         'method %(method)s result')
                       % {'obj': objectTypeName, 'method': methodname})
                raise exception.VolumeBackendAPIException(message=msg)
        return None

    def _rest_ret(self, rest_response, async):
        # If we made an async call and it was accepted
        # we wait for our response.
        if async:
            if rest_response.status_code == 202:
                asyncTask = rest_response.json()
                return self._wait_for_async_complete(asyncTask)
            else:
                LOG.debug('REST Async error command not accepted:\n'
                          '\tUrl:    %(url)s\n'
                          '\tCode:   %(code)d\n'
                          '\tReason: %(reason)s\n',
                          {'url': rest_response.url,
                           'code': rest_response.status_code,
                           'reason': rest_response.reason})
                msg = _('REST Async Error: Command not accepted.')
                raise exception.VolumeBackendAPIException(message=msg)
        return rest_response

    @utils.retry(exceptions=(requests.ConnectionError,
                             exception.DellDriverRetryableException))
    def get(self, url, async=False):
        LOG.debug('get: %(url)s', {'url': url})
        rest_response = self._rest_ret(self.session.get(
            self.__formatUrl(url), headers=self._get_header(async),
            verify=self.verify), async)
        if rest_response and rest_response.status_code == 400 and (
                'Unhandled Exception' in rest_response.text):
            raise exception.DellDriverRetryableException()
        return rest_response

    @utils.retry(exceptions=(requests.ConnectionError,))
    def post(self, url, payload, async=False):
        LOG.debug('post: %(url)s data: %(payload)s',
                  {'url': url,
                   'payload': payload})
        return self._rest_ret(self.session.post(
            self.__formatUrl(url),
            data=json.dumps(payload,
                            ensure_ascii=False).encode('utf-8'),
            headers=self._get_header(async),
            verify=self.verify), async)

    @utils.retry(exceptions=(requests.ConnectionError,))
    def put(self, url, payload, async=False):
        LOG.debug('put: %(url)s data: %(payload)s',
                  {'url': url,
                   'payload': payload})
        return self._rest_ret(self.session.put(
            self.__formatUrl(url),
            data=json.dumps(payload,
                            ensure_ascii=False).encode('utf-8'),
            headers=self._get_header(async),
            verify=self.verify), async)

    @utils.retry(exceptions=(requests.ConnectionError,))
    def delete(self, url, payload=None, async=False):
        LOG.debug('delete: %(url)s data: %(payload)s',
                  {'url': url, 'payload': payload})
        if payload:
            return self._rest_ret(
                self.session.delete(self.__formatUrl(url),
                                    data=json.dumps(payload,
                                                    ensure_ascii=False
                                                    ).encode('utf-8'),
                                    headers=self._get_header(async),
                                    verify=self.verify), async)
        return self._rest_ret(
            self.session.delete(self.__formatUrl(url),
                                headers=self._get_header(async),
                                verify=self.verify), async)


class StorageCenterApiHelper(object):
    """StorageCenterApiHelper

    Helper class for API access.  Handles opening and closing the
    connection to the Dell REST API.
    """

    def __init__(self, config, active_backend_id, storage_protocol):
        self.config = config
        # Now that active_backend_id is set on failover.
        # Use that if set.  Mark the backend as failed over.
        self.active_backend_id = active_backend_id
        self.primaryssn = self.config.dell_sc_ssn
        self.storage_protocol = storage_protocol
        self.apiversion = '2.0'

    def open_connection(self):
        """Creates the StorageCenterApi object.

        :return: StorageCenterApi object.
        :raises: VolumeBackendAPIException
        """
        connection = None
        LOG.info(_LI('open_connection to %(ssn)s at %(ip)s'),
                 {'ssn': self.primaryssn,
                  'ip': self.config.san_ip})
        if self.primaryssn:
            """Open connection to REST API."""
            connection = StorageCenterApi(self.config.san_ip,
                                          self.config.dell_sc_api_port,
                                          self.config.san_login,
                                          self.config.san_password,
                                          self.config.dell_sc_verify_cert,
                                          self.apiversion)
            # This instance is for a single backend.  That backend has a
            # few items of information we should save rather than passing them
            # about.
            connection.vfname = self.config.dell_sc_volume_folder
            connection.sfname = self.config.dell_sc_server_folder
            # Our primary SSN doesn't change
            connection.primaryssn = self.primaryssn
            if self.storage_protocol == 'FC':
                connection.protocol = 'FibreChannel'
            # Set appropriate ssn and failover state.
            if self.active_backend_id:
                # active_backend_id is a string.  Convert to int.
                connection.ssn = int(self.active_backend_id)
            else:
                connection.ssn = self.primaryssn
            # Open connection.
            connection.open_connection()
            # Save our api version for next time.
            if self.apiversion != connection.apiversion:
                LOG.info(_LI('open_connection: Updating API version to %s'),
                         connection.apiversion)
                self.apiversion = connection.apiversion

        else:
            raise exception.VolumeBackendAPIException(
                data=_('Configuration error: dell_sc_ssn not set.'))
        return connection


class StorageCenterApi(object):
    """StorageCenterApi

    Handles calls to Dell SC and EM via the REST API interface.

    Version history:
        1.0.0 - Initial driver
        1.1.0 - Added extra spec support for Storage Profile selection
        1.2.0 - Added consistency group support.
        2.0.0 - Switched to inheriting functional objects rather than volume
        driver.
        2.1.0 - Added support for ManageableVD.
        2.2.0 - Added API 2.2 support.
        2.3.0 - Added Legacy Port Mode Support
        2.3.1 - Updated error handling.
        2.4.0 - Added Replication V2 support.
        2.4.1 - Updated Replication support to V2.1.
        2.5.0 - ManageableSnapshotsVD implemented.
        3.0.0 - ProviderID utilized.
        3.1.0 - Failback Supported.
    """

    APIDRIVERVERSION = '3.1.0'

    def __init__(self, host, port, user, password, verify, apiversion):
        """This creates a connection to Dell SC or EM.

        :param host: IP address of the REST interface..
        :param port: Port the REST interface is listening on.
        :param user: User account to login with.
        :param password: Password.
        :param verify: Boolean indicating whether certificate verification
                       should be turned on or not.
        :param apiversion: Version used on login.
        """
        self.notes = 'Created by Dell Cinder Driver'
        self.repl_prefix = 'Cinder repl of '
        self.ssn = None
        # primaryssn is the ssn of the SC we are configured to use. This
        # doesn't change in the case of a failover.
        self.primaryssn = None
        self.failed_over = False
        self.vfname = 'openstack'
        self.sfname = 'openstack'
        self.legacypayloadfilters = False
        self.consisgroups = True
        self.protocol = 'Iscsi'
        self.apiversion = apiversion
        # Nothing other than Replication should care if we are direct connect
        # or not.
        self.is_direct_connect = False
        self.client = HttpClient(host, port, user, password,
                                 verify, apiversion)

    def __enter__(self):
        return self

    def __exit__(self, type, value, traceback):
        self.close_connection()

    @staticmethod
    def _check_result(rest_response):
        """Checks and logs API responses.

        :param rest_response: The result from a REST API call.
        :returns: ``True`` if success, ``False`` otherwise.
        """
        if rest_response:
            if 200 <= rest_response.status_code < 300:
                # API call was a normal success
                return True

            # Some versions return this as a dict.
            try:
                response_json = rest_response.json()
                response_text = response_json.text['result']
            except Exception:
                # We do not care why that failed. Just use the text.
                response_text = rest_response.text

            LOG.debug('REST call result:\n'
                      '\tUrl:    %(url)s\n'
                      '\tCode:   %(code)d\n'
                      '\tReason: %(reason)s\n'
                      '\tText:   %(text)s',
                      {'url': rest_response.url,
                       'code': rest_response.status_code,
                       'reason': rest_response.reason,
                       'text': response_text})
        else:
            LOG.warning(_LW('Failed to get REST call result.'))
        return False

    @staticmethod
    def _path_to_array(path):
        """Breaks a path into a reversed string array.

        :param path: Path to a folder on the Storage Center.
        :return: A reversed array of each path element.
        """
        array = []
        while True:
            (path, tail) = os.path.split(path)
            if tail == '':
                array.reverse()
                return array
            array.append(tail)

    def _first_result(self, blob):
        """Get the first result from the JSON return value.

        :param blob: Full return from a REST call.
        :return: The JSON encoded dict or the first item in a JSON encoded
                 list.
        """
        return self._get_result(blob, None, None)

    def _get_result(self, blob, attribute, value):
        """Find the result specified by attribute and value.

        If the JSON blob is a list then it will be searched for the attribute
        and value combination.  If attribute and value are not specified then
        the first item is returned.  If the JSON blob is a dict then it
        will be returned so long as the dict matches the attribute and value
        combination or attribute is None.

        :param blob: The REST call's JSON response.  Can be a list or dict.
        :param attribute: The attribute we are looking for.  If it is None
                          the first item in the list, or the dict, is returned.
        :param value: The attribute value we are looking for.  If the attribute
                      is None this value is ignored.
        :returns: The JSON content in blob, the dict specified by matching the
                  attribute and value or None.
        """
        rsp = None
        content = self._get_json(blob)
        if content is not None:
            # We can get a list or a dict or nothing
            if isinstance(content, list):
                for r in content:
                    if attribute is None or r.get(attribute) == value:
                        rsp = r
                        break
            elif isinstance(content, dict):
                if attribute is None or content.get(attribute) == value:
                    rsp = content
            elif attribute is None:
                rsp = content

        if rsp is None:
            LOG.debug('Unable to find result where %(attr)s is %(val)s',
                      {'attr': attribute,
                       'val': value})
            LOG.debug('Blob was %(blob)s', {'blob': blob.text})
        return rsp

    def _get_json(self, blob):
        """Returns a dict from the JSON of a REST response.

        :param blob: The response from a REST call.
        :returns: JSON or None on error.
        """
        try:
            return blob.json()
        except AttributeError:
            LOG.error(_LE('Error invalid json: %s'),
                      blob)
        except TypeError as ex:
            LOG.error(_LE('Error TypeError. %s'), ex)
        except scanner.JSONDecodeError as ex:
            LOG.error(_LE('Error JSONDecodeError. %s'), ex)
        # We are here so this went poorly. Log our blob.
        LOG.debug('_get_json blob %s', blob)
        return None

    def _get_id(self, blob):
        """Returns the instanceId from a Dell REST object.

        :param blob: A Dell SC REST call's response.
        :returns: The instanceId from the Dell SC object or None on error.
        """
        try:
            if isinstance(blob, dict):
                return blob.get('instanceId')
        except AttributeError:
            LOG.error(_LE('Invalid API object: %s'),
                      blob)
        except TypeError as ex:
            LOG.error(_LE('Error TypeError. %s'), ex)
        except scanner.JSONDecodeError as ex:
            LOG.error(_LE('Error JSONDecodeError. %s'), ex)
        LOG.debug('_get_id failed: blob %s', blob)
        return None

    def _get_payload_filter(self, filterType='AND'):
        # 2.1 or earlier and we are talking LegacyPayloadFilters.
        if self.legacypayloadfilters:
            return LegacyPayloadFilter(filterType)
        return PayloadFilter(filterType)

    def _check_version_fail(self, payload, response):
        try:
            # Is it even our error?
            result = self._get_json(response).get('result')
            if result and result.startswith(
                    'Invalid API version specified, '
                    'the version must be in the range ['):
                # We're looking for something very specific. The except
                # will catch any errors.
                # Update our version and update our header.
                self.apiversion = response.text.split('[')[1].split(',')[0]
                self.client.header['x-dell-api-version'] = self.apiversion
                LOG.debug('API version updated to %s', self.apiversion)
                # Give login another go.
                r = self.client.post('ApiConnection/Login', payload)
                return r
        except Exception:
            # We don't care what failed. The clues are already in the logs.
            # Just log a parsing error and move on.
            LOG.error(_LE('_check_version_fail: Parsing error.'))
        # Just eat this if it isn't a version error.
        return response

    def open_connection(self):
        """Authenticate with Dell REST interface.

        :raises: VolumeBackendAPIException.
        """
        # Set our fo state.
        self.failed_over = (self.primaryssn != self.ssn)

        # Login
        payload = {}
        payload['Application'] = 'Cinder REST Driver'
        payload['ApplicationVersion'] = self.APIDRIVERVERSION
        r = self.client.post('ApiConnection/Login', payload)
        if not self._check_result(r):
            # SC requires a specific version. See if we can get it.
            r = self._check_version_fail(payload, r)
            # Either we tried to login and have a new result or we are
            # just checking the same result. Either way raise on fail.
            if not self._check_result(r):
                raise exception.VolumeBackendAPIException(
                    data=_('Failed to connect to Dell REST API'))

        # We should be logged in.  Try to grab the api version out of the
        # response.
        try:
            apidict = self._get_json(r)
            version = apidict['apiVersion']
            self.is_direct_connect = apidict['provider'] == 'StorageCenter'
            splitver = version.split('.')
            if splitver[0] == '2':
                if splitver[1] == '0':
                    self.consisgroups = False
                    self.legacypayloadfilters = True

                elif splitver[1] == '1':
                    self.legacypayloadfilters = True
            return

        except Exception:
            # Good return but not the login response we were expecting.
            # Log it and error out.
            LOG.error(_LE('Unrecognized Login Response: %s'), r)

    def close_connection(self):
        """Logout of Dell REST API."""
        r = self.client.post('ApiConnection/Logout', {})
        # 204 expected.
        self._check_result(r)
        self.client = None

    def _use_provider_id(self, provider_id):
        """See if our provider_id points at our current backend.

        provider_id is instanceId. The instanceId contains the ssn of the
        StorageCenter it is hosted on. This must equal our current ssn or
        it isn't valid.

        :param provider_id: Provider_id from an volume or snapshot object.
        :returns: True/False
        """
        ret = False
        if provider_id:
            try:
                if provider_id.split('.')[0] == six.text_type(self.ssn):
                    ret = True
            except Exception:
                LOG.error(_LE('_use_provider_id: provider_id %s is invalid!'),
                          provider_id)
        return ret

    def find_sc(self, ssn=-1):
        """Check that the SC is there and being managed by EM.

        :returns: The SC SSN.
        :raises: VolumeBackendAPIException
        """
        # We might be looking for another ssn.  If not then
        # look for our default.
        if ssn == -1:
            ssn = self.ssn

        r = self.client.get('StorageCenter/StorageCenter')
        result = self._get_result(r, 'scSerialNumber', ssn)
        if result is None:
            LOG.error(_LE('Failed to find %(s)s.  Result %(r)s'),
                      {'s': ssn,
                       'r': r})
            raise exception.VolumeBackendAPIException(
                data=_('Failed to find Storage Center'))

        return self._get_id(result)

    # Folder functions

    def _create_folder(self, url, parent, folder):
        """Creates folder under parent.

        This can create both to server and volume folders.  The REST url
        sent in defines the folder type being created on the Dell Storage
        Center backend.

        :param url: This is the Dell SC rest url for creating the specific
                    (server or volume) folder type.
        :param parent: The instance ID of this folder's parent folder.
        :param folder: The folder name to be created.  This is one level deep.
        :returns: The REST folder object.
        """
        scfolder = None
        payload = {}
        payload['Name'] = folder
        payload['StorageCenter'] = self.ssn
        if parent != '':
            payload['Parent'] = parent
        payload['Notes'] = self.notes

        r = self.client.post(url, payload, True)
        if self._check_result(r):
            scfolder = self._first_result(r)
        return scfolder

    def _create_folder_path(self, url, foldername):
        """Creates a folder path from a fully qualified name.

        The REST url sent in defines the folder type being created on the Dell
        Storage Center backend.  Thus this is generic to server and volume
        folders.

        :param url: This is the Dell SC REST url for creating the specific
                    (server or volume) folder type.
        :param foldername: The full folder name with path.
        :returns: The REST folder object.
        """
        path = self._path_to_array(foldername)
        folderpath = ''
        instanceId = ''
        # Technically the first folder is the root so that is already created.
        found = True
        scfolder = None
        for folder in path:
            folderpath = folderpath + folder
            # If the last was found see if this part of the path exists too
            if found:
                listurl = url + '/GetList'
                scfolder = self._find_folder(listurl, folderpath)
                if scfolder is None:
                    found = False
            # We didn't find it so create it
            if found is False:
                scfolder = self._create_folder(url, instanceId, folder)
            # If we haven't found a folder or created it then leave
            if scfolder is None:
                LOG.error(_LE('Unable to create folder path %s'), folderpath)
                break
            # Next part of the path will need this
            instanceId = self._get_id(scfolder)
            folderpath = folderpath + '/'
        return scfolder

    def _find_folder(self, url, foldername):
        """Find a folder on the SC using the specified url.

        Most of the time the folder will already have been created so
        we look for the end folder and check that the rest of the path is
        right.

        The REST url sent in defines the folder type being created on the Dell
        Storage Center backend.  Thus this is generic to server and volume
        folders.

        :param url: The portion of the url after the base url (see http class)
                    to use for this operation.  (Can be for Server or Volume
                    folders.)
        :param foldername: Full path to the folder we are looking for.
        :returns: Dell folder object.
        """
        pf = self._get_payload_filter()
        pf.append('scSerialNumber', self.ssn)
        basename = os.path.basename(foldername)
        pf.append('Name', basename)
        # If we have any kind of path we throw it into the filters.
        folderpath = os.path.dirname(foldername)
        if folderpath != '':
            # SC convention is to end with a '/' so make sure we do.
            folderpath += '/'
            pf.append('folderPath', folderpath)
        folder = None
        r = self.client.post(url, pf.payload)
        if self._check_result(r):
            folder = self._get_result(r, 'folderPath', folderpath)
        return folder

    def _find_volume_folder(self, create=False):
        """Looks for the volume folder where backend volumes will be created.

        Volume folder is specified in the cindef.conf.  See __init.

        :param create: If True will create the folder if not found.
        :returns: Folder object.
        """
        folder = self._find_folder('StorageCenter/ScVolumeFolder/GetList',
                                   self.vfname)
        # Doesn't exist?  make it
        if folder is None and create is True:
            folder = self._create_folder_path('StorageCenter/ScVolumeFolder',
                                              self.vfname)
        return folder

    def _init_volume(self, scvolume):
        """Initializes the volume.

        Maps the volume to a random server and immediately unmaps
        it.  This initializes the volume.

        Don't wig out if this fails.
        :param scvolume: Dell Volume object.
        """
        pf = self._get_payload_filter()
        pf.append('scSerialNumber', scvolume.get('scSerialNumber'))
        r = self.client.post('StorageCenter/ScServer/GetList', pf.payload)
        if self._check_result(r):
            scservers = self._get_json(r)
            # Sort through the servers looking for one with connectivity.
            for scserver in scservers:
                # This needs to be either a physical or virtual server.
                # Outside of tempest tests this should not matter as we only
                # "init" a volume to allow snapshotting of an empty volume.
                if scserver.get('status', '').lower() != 'down':
                    # Map to actually create the volume
                    self.map_volume(scvolume, scserver)
                    # We have changed the volume so grab a new copy of it.
                    scvolume = self.get_volume(self._get_id(scvolume))
                    self.unmap_volume(scvolume, scserver)
                    return
        # We didn't map/unmap the volume.  So no initialization done.
        # Warn the user before we leave.  Note that this is almost certainly
        # a tempest test failure we are trying to catch here.  A snapshot
        # has likely been attempted before the volume has been instantiated
        # on the Storage Center.  In the real world no one will snapshot
        # a volume without first putting some data in that volume.
        LOG.warning(_LW('Volume %s initialization failure.'), scvolume['name'])

    def _find_storage_profile(self, storage_profile):
        """Looks for a Storage Profile on the array.

        Storage Profiles determine tiering settings. If not specified a volume
        will use the Default storage profile.

        :param storage_profile: The Storage Profile name to find with any
                                spaces stripped.
        :returns: The Storage Profile object or None.
        """
        if not storage_profile:
            return None

        # Since we are stripping out spaces for convenience we are not
        # able to just filter on name. Need to get all Storage Profiles
        # and look through for the one we want. Never many profiles, so
        # this doesn't cause as much overhead as it might seem.
        storage_profile = storage_profile.replace(' ', '').lower()
        pf = self._get_payload_filter()
        pf.append('scSerialNumber', self.ssn)
        r = self.client.post('StorageCenter/ScStorageProfile/GetList',
                             pf.payload)
        if self._check_result(r):
            profiles = self._get_json(r)
            for profile in profiles:
                # Look for the stripped, case insensitive match
                name = profile.get('name', '').replace(' ', '').lower()
                if name == storage_profile:
                    return profile
        return None

    def _find_user_replay_profiles(self):
        """Find user default profiles.

        Note that this only deals with standard and not cg profiles.

        :return: List of replay profiles.
        """
        user_prefs = self._get_user_preferences()
        if user_prefs:
            profileids = [profile['instanceId'] for profile in
                          user_prefs['replayProfileList']]
            return profileids
        return []

    def _find_daily_replay_profile(self):
        """Find the system replay profile named "Daily".

        :return: Profile instanceId or None.
        """
        pf = self._get_payload_filter()
        pf.append('scSerialNumber', self.ssn)
        pf.append('instanceName', 'Daily')
        r = self.client.post('StorageCenter/ScReplayProfile/GetList',
                             pf.payload)
        if self._check_result(r):
            profiles = self._get_json(r)
            if profiles:
                return profiles[0]['instanceId']
        return None

    def _find_replay_profiles(self, replay_profile_string):
        """Find our replay profiles.

        Note that if called on volume creation the removeids list can be safely
        ignored.

        :param replay_profile_string: Comma separated list of profile names.
        :return: List replication profiles to use, List to remove.
        :raises VolumeBackendAPIException: If we can't find our profiles.
        """
        addids = []
        removeids = []
        replay_profiles = []
        if replay_profile_string:
            replay_profiles = replay_profile_string.split(',')
        # Most of the time they will not specify this so don't call anything.
        if replay_profiles:
            pf = self._get_payload_filter()
            pf.append('scSerialNumber', self.ssn)
            pf.append('type', 'Standard')
            r = self.client.post('StorageCenter/ScReplayProfile/GetList',
                                 pf.payload)
            if self._check_result(r):
                profiles = self._get_json(r)
                for profile in profiles:
                    if replay_profiles.count(profile['name']) > 0:
                        addids.append(profile['instanceId'])
                    else:
                        # in the volume.
                        removeids.append(profile['instanceId'])
        # Check that we've found what we are looking for if anything
        if len(addids) != len(replay_profiles):
            msg = (_('Unable to locate specified replay profiles %s ') %
                   replay_profile_string)
            raise exception.VolumeBackendAPIException(data=msg)

        return addids, removeids

    def update_replay_profiles(self, scvolume, replay_profile_string):
        """Update our replay profiles.

        If the replay_profile_string is empty we look for the user's default
        profiles. If those aren't found we look for the Daily profile.

        Note that this is in addition to the CG profiles which we do not touch.

        :param scvolume: SC Volume object.
        :param replay_profile_string: Comma separated string of replay profile
                                      names.
        :return: True/False.
        """
        # Find our replay_profiles.
        addids, removeids = self._find_replay_profiles(replay_profile_string)
        # We either found what we were looking for.
        # If we are clearing out our ids then find a default.
        if not addids:
            # if no replay profiles specified we must be clearing out.
            addids = self._find_user_replay_profiles()
            if not addids:
                addids = [self._find_daily_replay_profile()]
        # Do any removals first.
        for id in removeids:
            # We might have added to the addids list after creating removeids.
            # User preferences or the daily profile could have been added.
            # If our id is in both lists just skip it and remove it from
            # The add list.
            if addids.count(id):
                addids.remove(id)
            elif not self._update_volume_profiles(
                    scvolume, addid=None, removeid=id):
                return False
        # Add anything new.
        for id in addids:
            if not self._update_volume_profiles(
                    scvolume, addid=id, removeid=None):
                return False
        return True

    def create_volume(self, name, size, storage_profile=None,
                      replay_profile_string=None):
        """Creates a new volume on the Storage Center.

        It will create it in a folder called self.vfname.  If self.vfname
        does not exist it will create it.  If it cannot create it
        the volume will be created in the root.

        :param name: Name of the volume to be created on the Dell SC backend.
                     This is the cinder volume ID.
        :param size: The size of the volume to be created in GB.
        :param storage_profile: Optional storage profile to set for the volume.
        :param replay_profile_string: Optional replay profile to set for
                                      the volume.
        :returns: Dell Volume object or None.
        """
        LOG.debug('create_volume: %(name)s %(ssn)s %(folder)s %(profile)s',
                  {'name': name,
                   'ssn': self.ssn,
                   'folder': self.vfname,
                   'profile': storage_profile,
                   'replay': replay_profile_string
                   })

        # Find our folder
        folder = self._find_volume_folder(True)

        # If we actually have a place to put our volume create it
        if folder is None:
            LOG.warning(_LW('Unable to create folder %s'), self.vfname)

        # See if we need a storage profile
        profile = self._find_storage_profile(storage_profile)
        if storage_profile and profile is None:
            msg = _('Storage Profile %s not found.') % storage_profile
            raise exception.VolumeBackendAPIException(data=msg)

        # Find our replay_profiles.
        addids, removeids = self._find_replay_profiles(replay_profile_string)

        # Init our return.
        scvolume = None

        # Create the volume
        payload = {}
        payload['Name'] = name
        payload['Notes'] = self.notes
        payload['Size'] = '%d GB' % size
        payload['StorageCenter'] = self.ssn
        if folder is not None:
            payload['VolumeFolder'] = self._get_id(folder)
        if profile:
            payload['StorageProfile'] = self._get_id(profile)
        # This is a new volume so there is nothing to remove.
        if addids:
            payload['ReplayProfileList'] = addids
        r = self.client.post('StorageCenter/ScVolume', payload, True)
        if self._check_result(r):
            # Our volume should be in the return.
            scvolume = self._get_json(r)
            if scvolume:
                LOG.info(_LI('Created volume %(instanceId)s: %(name)s'),
                         {'instanceId': scvolume['instanceId'],
                          'name': scvolume['name']})
            else:
                LOG.error(_LE('ScVolume returned success with empty payload.'
                              '  Attempting to locate volume'))
                # In theory it is there since success was returned.
                # Try one last time to find it before returning.
                scvolume = self._search_for_volume(name)
        else:
            LOG.error(_LE('Unable to create volume on SC: %s'), name)

        return scvolume

    def _get_volume_list(self, name, deviceid, filterbyvfname=True, ssn=-1):
        """Return the specified list of volumes.

        :param name: Volume name.
        :param deviceid: Volume device ID on the SC backend.
        :param filterbyvfname:  If set to true then this filters by the preset
                                folder name.
        :param ssn: SSN to search on.
        :return: Returns the scvolume list or None.
        """
        if ssn == -1:
            ssn = self.ssn
        result = None
        # We need a name or a device ID to find a volume.
        if name or deviceid:
            pf = self._get_payload_filter()
            pf.append('scSerialNumber', ssn)
            if name is not None:
                pf.append('Name', name)
            if deviceid is not None:
                pf.append('DeviceId', deviceid)
            # set folderPath
            if filterbyvfname:
                vfname = (self.vfname if self.vfname.endswith('/')
                          else self.vfname + '/')
                pf.append('volumeFolderPath', vfname)
            r = self.client.post('StorageCenter/ScVolume/GetList', pf.payload)
            if self._check_result(r):
                result = self._get_json(r)
        # We return None if there was an error and a list if the command
        # succeeded. It might be an empty list.
        return result

    def find_volume(self, name, provider_id):
        """Find the volume by name or instanceId.

        We check if we can use provider_id before using it. If so then
        we expect to find it by provider_id.

        We also conclude our failover at this point. If we are failed over we
        run _import_one to rename the volume.

        :param name: Volume name.
        :param provider_id: instanceId of the volume if known.
        :return: sc volume object or None.
        :raises VolumeBackendAPIException: if unable to import.
        """
        scvolume = None
        # If we have a provided_id just go get it.
        if self._use_provider_id(provider_id):
            scvolume = self.get_volume(provider_id)
            # if we are failed over we need to check if we
            # need to import the failed over volume.
            if self.failed_over:
                if scvolume['name'] == self._repl_name(name):
                    scvolume = self._import_one(scvolume, name)
                    if not scvolume:
                        msg = _('Unable to complete failover of %s.') % name
                        raise exception.VolumeBackendAPIException(data=msg)
                    LOG.info(_LI('Imported %(fail)s to %(guid)s.'),
                             {'fail': self._repl_name(name),
                              'guid': name})
        else:
            # No? Then search for it.
            scvolume = self._search_for_volume(name)
        return scvolume

    def _search_for_volume(self, name):
        """Search self.ssn for volume of name.

        This searches the folder self.vfname (specified in the cinder.conf)
        for the volume first.  If not found it searches the entire array for
        the volume.

        :param name: Name of the volume to search for.  This is the cinder
                     volume ID.
        :returns: Dell Volume object or None if not found.
        :raises VolumeBackendAPIException: If multiple copies are found.
        """
        LOG.debug('Searching %(sn)s for %(name)s',
                  {'sn': self.ssn,
                   'name': name})

        # Cannot find a volume without the name.
        if name is None:
            return None

        # Look for our volume in our folder.
        vollist = self._get_volume_list(name, None, True)
        # If an empty list was returned they probably moved the volumes or
        # changed the folder name so try again without the folder.
        if not vollist:
            LOG.debug('Cannot find volume %(n)s in %(v)s.  Searching SC.',
                      {'n': name,
                       'v': self.vfname})
            vollist = self._get_volume_list(name, None, False)

        # If multiple volumes of the same name are found we need to error.
        if len(vollist) > 1:
            # blow up
            msg = _('Multiple copies of volume %s found.') % name
            raise exception.VolumeBackendAPIException(data=msg)

        # We made it and should have a valid volume.
        return None if not vollist else vollist[0]

    def get_volume(self, provider_id):
        """Returns the scvolume associated with provider_id.

        :param provider_id: This is the instanceId
        :return: Dell SCVolume object.
        """
        result = None
        if provider_id:
            r = self.client.get('StorageCenter/ScVolume/%s' % provider_id)
            if self._check_result(r):
                result = self._get_json(r)
        return result

    def delete_volume(self, name, provider_id=None):
        """Deletes the volume from the SC backend array.

        If the volume cannot be found we claim success.

        :param name: Name of the volume to search for.  This is the cinder
                     volume ID.
        :param provider_id: This is the instanceId
        :returns: Boolean indicating success or failure.
        """
        # No provider id? Then do a search.
        if not provider_id:
            vol = self._search_for_volume(name)
            if vol:
                provider_id = self._get_id(vol)

        # If we have an id then delete the volume.
        if provider_id:
            r = self.client.delete('StorageCenter/ScVolume/%s' % provider_id,
                                   async=True)
            if not self._check_result(r):
                msg = _('Error deleting volume %(ssn)s: %(volume)s') % {
                    'ssn': self.ssn,
                    'volume': provider_id}
                raise exception.VolumeBackendAPIException(data=msg)

            # json return should be true or false
            return self._get_json(r)

        # If we can't find the volume then it is effectively gone.
        LOG.warning(_LW('delete_volume: unable to find volume '
                        'provider_id: %s'), provider_id)
        return True

    def _find_server_folder(self, create=False):
        """Looks for the server folder on the Dell Storage Center.

         This is the folder where a server objects for mapping volumes will be
         created.  Server folder is specified in cinder.conf.  See __init.

        :param create: If True will create the folder if not found.
        :return: Folder object.
        """
        folder = self._find_folder('StorageCenter/ScServerFolder/GetList',
                                   self.sfname)
        if folder is None and create is True:
            folder = self._create_folder_path('StorageCenter/ScServerFolder',
                                              self.sfname)
        return folder

    def _add_hba(self, scserver, wwnoriscsiname):
        """This adds a server HBA to the Dell server object.

        The HBA is taken from the connector provided in initialize_connection.
        The Dell server object is largely a container object for the list of
        HBAs associated with a single server (or vm or cluster) for the
        purposes of mapping volumes.

        :param scserver: Dell server object.
        :param wwnoriscsiname: The WWN or IQN to add to this server.
        :returns: Boolean indicating success or failure.
        """
        payload = {}
        payload['HbaPortType'] = self.protocol
        payload['WwnOrIscsiName'] = wwnoriscsiname
        payload['AllowManual'] = True
        r = self.client.post('StorageCenter/ScPhysicalServer/%s/AddHba'
                             % self._get_id(scserver), payload, True)
        if not self._check_result(r):
            LOG.error(_LE('_add_hba error: %(wwn)s to %(srvname)s'),
                      {'wwn': wwnoriscsiname,
                       'srvname': scserver['name']})
            return False
        return True

    def _find_serveros(self, osname='Red Hat Linux 6.x'):
        """Returns the serveros instance id of the specified osname.

        Required to create a Dell server object.

        We do not know that we are Red Hat Linux 6.x but that works
        best for Red Hat and Ubuntu.  So we use that.

        :param osname: The name of the OS to look for.
        :returns: InstanceId of the ScServerOperatingSystem object.
        """
        pf = self._get_payload_filter()
        pf.append('scSerialNumber', self.ssn)
        r = self.client.post('StorageCenter/ScServerOperatingSystem/GetList',
                             pf.payload)
        if self._check_result(r):
            oslist = self._get_json(r)
            for srvos in oslist:
                name = srvos.get('name', 'nope')
                if name.lower() == osname.lower():
                    # Found it return the id
                    return self._get_id(srvos)

        LOG.warning(_LW('Unable to find appropriate OS %s'), osname)

        return None

    def create_server_multiple_hbas(self, wwns):
        """Creates a server with multiple WWNS associated with it.

        Same as create_server except it can take a list of HBAs.

        :param wwns: A list of FC WWNs or iSCSI IQNs associated with this
                     server.
        :returns: Dell server object.
        """
        scserver = None
        # Our instance names
        for wwn in wwns:
            if scserver is None:
                # Use the fist wwn to create the server.
                scserver = self.create_server(wwn)
            else:
                # Add the wwn to our server
                self._add_hba(scserver, wwn)
        return scserver

    def create_server(self, wwnoriscsiname):
        """Creates a Dell server object on the the Storage Center.

        Adds the first HBA identified by wwnoriscsiname to it.

        :param wwnoriscsiname: FC WWN or iSCSI IQN associated with
                               this Dell server object.
        :returns: Dell server object.
        """

        LOG.info(_LI('Creating server %s'), wwnoriscsiname)

        scserver = None
        payload = {}
        payload['Name'] = 'Server_' + wwnoriscsiname
        payload['StorageCenter'] = self.ssn
        payload['Notes'] = self.notes
        # We pick Red Hat Linux 6.x because it supports multipath and
        # will attach luns to paths as they are found.
        scserveros = self._find_serveros('Red Hat Linux 6.x')
        if scserveros is not None:
            payload['OperatingSystem'] = scserveros

        # Find our folder or make it
        folder = self._find_server_folder(True)

        # At this point it doesn't matter if the folder was created or not.
        # We just attempt to create the server.  Let it be in the root if
        # the folder creation fails.
        if folder is not None:
            payload['ServerFolder'] = self._get_id(folder)

        # create our server
        r = self.client.post('StorageCenter/ScPhysicalServer', payload, True)
        if self._check_result(r):
            # Server was created
            scserver = self._first_result(r)
            LOG.info(_LI('SC server created %s'), scserver)

            # Add hba to our server
            if scserver is not None:
                if not self._add_hba(scserver, wwnoriscsiname):
                    LOG.error(_LE('Error adding HBA to server'))
                    # Can't have a server without an HBA
                    self._delete_server(scserver)
                    scserver = None

        # Success or failure is determined by the caller
        return scserver

    def find_server(self, instance_name):
        """Hunts for a server on the Dell backend by instance_name.

        The instance_name is the same as the server's HBA.  This is the  IQN or
        WWN listed in the connector.  If found, the server the HBA is attached
        to, if any, is returned.

        :param instance_name: instance_name is a FC WWN or iSCSI IQN from
                              the connector.  In cinder a server is identified
                              by its HBA.
        :returns: Dell server object or None.
        """
        scserver = None
        # We search for our server by first finding our HBA
        hba = self._find_serverhba(instance_name)
        # Once created hbas stay in the system.  So it isn't enough
        # that we found one it actually has to be attached to a
        # server.
        if hba is not None and hba.get('server') is not None:
            pf = self._get_payload_filter()
            pf.append('scSerialNumber', self.ssn)
            pf.append('instanceId', self._get_id(hba['server']))
            r = self.client.post('StorageCenter/ScServer/GetList', pf.payload)
            if self._check_result(r):
                scserver = self._first_result(r)

        if scserver is None:
            LOG.debug('Server (%s) not found.', instance_name)
        return scserver

    def _find_serverhba(self, instance_name):
        """Hunts for a server HBA on the Dell backend by instance_name.

        Instance_name is the same as the IQN or WWN specified in the
        connector.

        :param instance_name: Instance_name is a FC WWN or iSCSI IQN from
                              the connector.
        :returns: Dell server HBA object.
        """
        scserverhba = None
        # We search for our server by first finding our HBA
        pf = self._get_payload_filter()
        pf.append('scSerialNumber', self.ssn)
        pf.append('instanceName', instance_name)
        r = self.client.post('StorageCenter/ScServerHba/GetList', pf.payload)
        if self._check_result(r):
            scserverhba = self._first_result(r)
        return scserverhba

    def _find_domains(self, cportid):
        """Find the list of Dell domain objects associated with the cportid.

        :param cportid: The Instance ID of the Dell controller port.
        :returns: List of fault domains associated with this controller port.
        """
        r = self.client.get('StorageCenter/ScControllerPort/%s/FaultDomainList'
                            % cportid)
        if self._check_result(r):
            domains = self._get_json(r)
            return domains

        LOG.error(_LE('Error getting FaultDomainList for %s'), cportid)
        return None

    def _find_initiators(self, scserver):
        """Returns a list of WWNs associated with the specified Dell server.

        :param scserver: The Dell backend server object.
        :returns: A list of WWNs associated with this server.
        """
        initiators = []
        r = self.client.get('StorageCenter/ScServer/%s/HbaList'
                            % self._get_id(scserver))
        if self._check_result(r):
            hbas = self._get_json(r)
            for hba in hbas:
                wwn = hba.get('instanceName')
                if (hba.get('portType') == self.protocol and
                        wwn is not None):
                    initiators.append(wwn)
        else:
            LOG.error(_LE('Unable to find initiators'))
        LOG.debug('_find_initiators: %s', initiators)
        return initiators

    def get_volume_count(self, scserver):
        """Returns the number of volumes attached to specified Dell server.

        :param scserver: The Dell backend server object.
        :returns: Mapping count.  -1 if there was an error.
        """
        r = self.client.get('StorageCenter/ScServer/%s/MappingList'
                            % self._get_id(scserver))
        if self._check_result(r):
            mappings = self._get_json(r)
            return len(mappings)
        # Panic mildly but do not return 0.
        return -1

    def _find_mappings(self, scvolume):
        """Find the Dell volume object mappings.

        :param scvolume: Dell volume object.
        :returns: A list of Dell mappings objects.
        """
        mappings = []
        if scvolume.get('active', False):
            r = self.client.get('StorageCenter/ScVolume/%s/MappingList'
                                % self._get_id(scvolume))
            if self._check_result(r):
                mappings = self._get_json(r)
        else:
            LOG.error(_LE('_find_mappings: volume is not active'))
        LOG.info(_LI('Volume mappings for %(name)s: %(mappings)s'),
                 {'name': scvolume.get('name'),
                  'mappings': mappings})
        return mappings

    def _find_mapping_profiles(self, scvolume):
        """Find the Dell volume object mapping profiles.

        :param scvolume: Dell volume object.
        :returns: A list of Dell mapping profile objects.
        """
        mapping_profiles = []
        r = self.client.get('StorageCenter/ScVolume/%s/MappingProfileList'
                            % self._get_id(scvolume))
        if self._check_result(r):
            mapping_profiles = self._get_json(r)
        else:
            LOG.error(_LE('Unable to find mapping profiles: %s'),
                      scvolume.get('name'))
        LOG.debug(mapping_profiles)
        return mapping_profiles

    def _find_controller_port(self, cportid):
        """Finds the SC controller port object for the specified cportid.

        :param cportid: The instanceID of the Dell backend controller port.
        :returns: The controller port object.
        """
        controllerport = None
        r = self.client.get('StorageCenter/ScControllerPort/%s' % cportid)
        if self._check_result(r):
            controllerport = self._first_result(r)
        LOG.debug('_find_controller_port: %s', controllerport)
        return controllerport

    def find_wwns(self, scvolume, scserver):
        """Finds the lun and wwns of the mapped volume.

        :param scvolume: Storage Center volume object.
        :param scserver: Storage Center server opbject.
        :returns: Lun, wwns, initiator target map
        """
        lun = None  # our lun.  We return the first lun.
        wwns = []  # list of targets
        itmap = {}  # dict of initiators and the associated targets

        # Make sure we know our server's initiators.  Only return
        # mappings that contain HBA for this server.
        initiators = self._find_initiators(scserver)
        # Get our volume mappings
        mappings = self._find_mappings(scvolume)
        # We check each of our mappings.  We want to return
        # the mapping we have been configured to use.
        for mapping in mappings:
            # Find the controller port for this mapping
            cport = mapping.get('controllerPort')
            controllerport = self._find_controller_port(self._get_id(cport))
            if controllerport is not None:
                # This changed case at one point or another.
                # Look for both keys.
                wwn = controllerport.get('wwn', controllerport.get('WWN'))
                if wwn:
                    serverhba = mapping.get('serverHba')
                    if serverhba:
                        hbaname = serverhba.get('instanceName')
                        if hbaname in initiators:
                            if itmap.get(hbaname) is None:
                                itmap[hbaname] = []
                            itmap[hbaname].append(wwn)
                            wwns.append(wwn)
                            mappinglun = mapping.get('lun')
                            if lun is None:
                                lun = mappinglun
                            elif lun != mappinglun:
                                LOG.warning(_LW('Inconsistent Luns.'))
                        else:
                            LOG.debug('%s not found in initiator list',
                                      hbaname)
                    else:
                        LOG.warning(_LW('_find_wwn: serverhba is None.'))
                else:
                    LOG.warning(_LW('_find_wwn: Unable to find port wwn.'))
            else:
                LOG.warning(_LW('_find_wwn: controllerport is None.'))
        LOG.info(_LI('_find_wwns-lun: %(lun)s wwns: %(wwn)s itmap: %(map)s'),
                 {'lun': lun,
                  'wwn': wwns,
                  'map': itmap})
        return lun, wwns, itmap

    def _find_active_controller(self, scvolume):
        """Finds the controller on which the Dell volume is active.

        There can be more than one Dell backend controller per Storage center
        but a given volume can only be active on one of them at a time.

        :param scvolume: Dell backend volume object.
        :returns: Active controller ID.
        """
        actvctrl = None
        volconfig = self._get_volume_configuration(scvolume)
        if volconfig:
            controller = volconfig.get('controller')
            actvctrl = self._get_id(controller)
        else:
            LOG.error(_LE('Unable to retrieve VolumeConfiguration: %s'),
                      self._get_id(scvolume))
        LOG.debug('_find_active_controller: %s', actvctrl)
        return actvctrl

    def _get_controller_id(self, mapping):
        # The mapping lists the associated controller.
        return self._get_id(mapping.get('controller'))

    def _get_domains(self, mapping):
        # Return a list of domains associated with this controller port.
        return self._find_domains(self._get_id(mapping.get('controllerPort')))

    def _get_iqn(self, mapping):
        # Get our iqn from the controller port listed in our our mapping.
        iqn = None
        cportid = self._get_id(mapping.get('controllerPort'))
        controllerport = self._find_controller_port(cportid)
        if controllerport:
            iqn = controllerport.get('iscsiName')
        LOG.debug('_get_iqn: %s', iqn)
        return iqn

    def _is_virtualport_mode(self):
        isvpmode = False
        r = self.client.get('StorageCenter/ScConfiguration/%s' % self.ssn)
        if self._check_result(r):
            scconfig = self._get_json(r)
            if scconfig and scconfig['iscsiTransportMode'] == 'VirtualPort':
                isvpmode = True
        return isvpmode

    def _find_controller_port_iscsi_config(self, cportid):
        """Finds the SC controller port object for the specified cportid.

        :param cportid: The instanceID of the Dell backend controller port.
        :returns: The controller port object.
        """
        controllerport = None
        r = self.client.get(
            'StorageCenter/ScControllerPortIscsiConfiguration/%s' % cportid)
        if self._check_result(r):
            controllerport = self._first_result(r)
        else:
            LOG.error(_LE('_find_controller_port_iscsi_config: '
                          'Error finding configuration: %s'), cportid)
        return controllerport

    def find_iscsi_properties(self, scvolume, ip=None, port=None):
        """Finds target information for a given Dell scvolume object mapping.

        The data coming back is both the preferred path and all the paths.

        :param scvolume: The dell sc volume object.
        :param ip: The preferred target portal ip.
        :param port: The preferred target portal port.
        :returns: iSCSI property dictionary.
        :raises: VolumeBackendAPIException
        """
        LOG.debug('find_iscsi_properties: scvolume: %s', scvolume)
        # Our mutable process object.
        pdata = {'active': -1,
                 'up': -1,
                 'ip': ip,
                 'port': port}
        # Our output lists.
        portals = []
        luns = []
        iqns = []

        # Process just looks for the best port to return.
        def process(lun, iqn, address, port, status, active):
            """Process this mapping information.

            :param lun: SCSI Lun.
            :param iqn: iSCSI IQN address.
            :param address: IP address.
            :param port: IP Port number
            :param readonly: Boolean indicating mapping is readonly.
            :param status: String indicating mapping status.  (Up is what we
                           are looking for.)
            :param active: Boolean indicating whether this is on the active
                           controller or not.
            :return: Nothing
            """
            portals.append(address + ':' + six.text_type(port))
            iqns.append(iqn)
            luns.append(lun)

            # We've all the information.  We need to find
            # the best single portal to return.  So check
            # this one if it is on the right IP, port and
            # if the access and status are correct.
            if ((pdata['ip'] is None or pdata['ip'] == address) and
                    (pdata['port'] is None or pdata['port'] == port)):

                # We need to point to the best link.
                # So state active and status up is preferred
                # but we don't actually need the state to be
                # up at this point.
                if pdata['up'] == -1:
                    if active:
                        pdata['active'] = len(iqns) - 1
                        if status == 'Up':
                            pdata['up'] = pdata['active']

        # Start by getting our mappings.
        mappings = self._find_mappings(scvolume)

        # We should have mappings at the time of this call but do check.
        if len(mappings) > 0:
            # In multipath (per Liberty) we will return all paths.  But
            # if multipath is not set (ip and port are None) then we need
            # to return a mapping from the controller on which the volume
            # is active.  So find that controller.
            actvctrl = self._find_active_controller(scvolume)
            # Two different methods are used to find our luns and portals
            # depending on whether we are in virtual or legacy port mode.
            isvpmode = self._is_virtualport_mode()
            # Trundle through our mappings.
            for mapping in mappings:
                # The lun, ro mode and status are in the mapping.
                LOG.debug('find_iscsi_properties: mapping: %s', mapping)
                lun = mapping.get('lun')
                status = mapping.get('status')
                # Get our IQN from our mapping.
                iqn = self._get_iqn(mapping)
                # Check if our controller ID matches our active controller ID.
                isactive = True if (self._get_controller_id(mapping) ==
                                    actvctrl) else False
                # If we have an IQN and are in virtual port mode.
                if isvpmode and iqn:
                    domains = self._get_domains(mapping)
                    if domains:
                        for dom in domains:
                            LOG.debug('find_iscsi_properties: domain: %s', dom)
                            ipaddress = dom.get('targetIpv4Address',
                                                dom.get('wellKnownIpAddress'))
                            portnumber = dom.get('portNumber')
                            # We have all our information. Process this portal.
                            process(lun, iqn, ipaddress, portnumber,
                                    status, isactive)
                # Else we are in legacy mode.
                elif iqn:
                    # Need to get individual ports
                    cportid = self._get_id(mapping.get('controllerPort'))
                    # Legacy mode stuff is in the ISCSI configuration object.
                    cpconfig = self._find_controller_port_iscsi_config(cportid)
                    # This should really never fail. Things happen so if it
                    # does just keep moving. Return what we can.
                    if cpconfig:
                        ipaddress = cpconfig.get('ipAddress')
                        portnumber = cpconfig.get('portNumber')
                        # We have all our information.  Process this portal.
                        process(lun, iqn, ipaddress, portnumber,
                                status, isactive)

        # We've gone through all our mappings.
        # Make sure we found something to return.
        if len(luns) == 0:
            # Since we just mapped this and can't find that mapping the world
            # is wrong so we raise exception.
            raise exception.VolumeBackendAPIException(
                data=_('Unable to find iSCSI mappings.'))

        # Make sure we point to the best portal we can.  This means it is
        # on the active controller and, preferably, up.  If it isn't return
        # what we have.
        if pdata['up'] != -1:
            # We found a connection that is already up.  Return that.
            pdata['active'] = pdata['up']
        elif pdata['active'] == -1:
            # This shouldn't be able to happen.  Maybe a controller went
            # down in the middle of this so just return the first one and
            # hope the ports are up by the time the connection is attempted.
            LOG.debug('find_iscsi_properties: '
                      'Volume is not yet active on any controller.')
            pdata['active'] = 0

        data = {'target_discovered': False,
                'target_iqn': iqns[pdata['active']],
                'target_iqns': iqns,
                'target_portal': portals[pdata['active']],
                'target_portals': portals,
                'target_lun': luns[pdata['active']],
                'target_luns': luns,
                }
        LOG.debug('find_iscsi_properties: %s', data)
        return data

    def map_volume(self, scvolume, scserver):
        """Maps the Dell backend volume object to the Dell server object.

        The check for the Dell server object existence is elsewhere;  does not
        create the Dell server object.

        :param scvolume: Storage Center volume object.
        :param scserver: Storage Center server object.
        :returns: SC mapping profile or None
        """
        # Make sure we have what we think we have
        serverid = self._get_id(scserver)
        volumeid = self._get_id(scvolume)
        if serverid is not None and volumeid is not None:
            # If we have a mapping to our server return it here.
            mprofiles = self._find_mapping_profiles(scvolume)
            for mprofile in mprofiles:
                if self._get_id(mprofile.get('server')) == serverid:
                    LOG.info(_LI('Volume %(vol)s already mapped to %(srv)s'),
                             {'vol': scvolume['name'],
                              'srv': scserver['name']})
                    return mprofile
            # No?  Then map it up.
            payload = {}
            payload['server'] = serverid
            payload['Advanced'] = {'MapToDownServerHbas': True}
            r = self.client.post('StorageCenter/ScVolume/%s/MapToServer'
                                 % volumeid, payload, True)
            if self._check_result(r):
                # We just return our mapping
                LOG.info(_LI('Volume %(vol)s mapped to %(srv)s'),
                         {'vol': scvolume['name'],
                          'srv': scserver['name']})
                return self._first_result(r)

        # Error out
        LOG.error(_LE('Unable to map %(vol)s to %(srv)s'),
                  {'vol': scvolume['name'],
                   'srv': scserver['name']})
        return None

    def unmap_volume(self, scvolume, scserver):
        """Unmaps the Dell volume object from the Dell server object.

        Deletes all mappings to a Dell server object, not just the ones on
        the path defined in cinder.conf.

        :param scvolume: Storage Center volume object.
        :param scserver: Storage Center server object.
        :returns: True or False.
        """
        rtn = True
        serverid = self._get_id(scserver)
        volumeid = self._get_id(scvolume)
        if serverid is not None and volumeid is not None:
            profiles = self._find_mapping_profiles(scvolume)
            for profile in profiles:
                prosrv = profile.get('server')
                if prosrv is not None and self._get_id(prosrv) == serverid:
                    r = self.client.delete('StorageCenter/ScMappingProfile/%s'
                                           % self._get_id(profile),
                                           async=True)
                    if self._check_result(r):
                        # Check our result in the json.
                        result = self._get_json(r)
                        # EM 15.1 and 15.2 return a boolean directly.
                        # 15.3 on up return it in a dict under 'result'.
                        if result is True or (type(result) is dict and
                                              result.get('result')):
                            LOG.info(
                                _LI('Volume %(vol)s unmapped from %(srv)s'),
                                {'vol': scvolume['name'],
                                 'srv': scserver['name']})
                            continue

                    LOG.error(_LE('Unable to unmap %(vol)s from %(srv)s'),
                              {'vol': scvolume['name'],
                               'srv': scserver['name']})
                    # 1 failed unmap is as good as 100.
                    # Fail it and leave
                    rtn = False
                    break
        # return true/false.
        return rtn

    def get_storage_usage(self):
        """Gets the storage usage object from the Dell backend.

        This contains capacity and usage information for the SC.

        :returns: The SC storageusage object.
        """
        storageusage = None
        if self.ssn is not None:
            r = self.client.get(
                'StorageCenter/StorageCenter/%s/StorageUsage' % self.ssn)
            if self._check_result(r):
                storageusage = self._get_json(r)
        return storageusage

    def create_replay(self, scvolume, replayid, expire):
        """Takes a snapshot of a volume.

        One could snap a volume before it has been activated, so activate
        by mapping and unmapping to a random server and let them.  This
        should be a fail but the Tempest tests require it.

        :param scvolume: Volume to snapshot.
        :param replayid: Name to use for the snapshot.  This is a portion of
                         the snapshot ID as we do not have space for the
                         entire GUID in the replay description.
        :param expire: Time in minutes before the replay expires.  For most
                       snapshots this will be 0 (never expire) but if we are
                       cloning a volume we will snap it right before creating
                       the clone.
        :returns: The Dell replay object or None.
        """
        replay = None
        if scvolume is not None:
            if (scvolume.get('active') is not True or
                    scvolume.get('replayAllowed') is not True):
                self._init_volume(scvolume)
            payload = {}
            payload['description'] = replayid
            payload['expireTime'] = expire
            r = self.client.post('StorageCenter/ScVolume/%s/CreateReplay'
                                 % self._get_id(scvolume), payload, True)
            if self._check_result(r):
                replay = self._first_result(r)

        # Quick double check.
        if replay is None:
            LOG.warning(_LW('Unable to create snapshot %s'), replayid)
        # Return replay or None.
        return replay

    def find_replay(self, scvolume, replayid):
        """Searches for the replay by replayid.

        replayid is stored in the replay's description attribute.

        :param scvolume: Dell volume object.
        :param replayid: Name to search for.  This is a portion of the
                         snapshot ID as we do not have space for the entire
                         GUID in the replay description.
        :returns: Dell replay object or None.
        """
        r = self.client.get('StorageCenter/ScVolume/%s/ReplayList'
                            % self._get_id(scvolume))
        try:
            replays = self._get_json(r)
            # This will be a list.  If it isn't bail
            if isinstance(replays, list):
                for replay in replays:
                    # The only place to save our information with the public
                    # api is the description field which isn't quite long
                    # enough.  So we check that our description is pretty much
                    # the max length and we compare that to the start of
                    # the snapshot id.
                    description = replay.get('description')
                    if (len(description) >= 30 and
                            replayid.startswith(description) is True and
                            replay.get('markedForExpiration') is not True):
                        # We found our replay so return it.
                        return replay
        except Exception:
            LOG.error(_LE('Invalid ReplayList return: %s'),
                      r)
        # If we are here then we didn't find the replay so warn and leave.
        LOG.warning(_LW('Unable to find snapshot %s'),
                    replayid)

        return None

    def manage_replay(self, screplay, replayid):
        """Basically renames the screplay and sets it to never expire.

        :param screplay: DellSC object.
        :param replayid: New name for replay.
        :return: True on success.  False on fail.
        """
        if screplay and replayid:
            payload = {}
            payload['description'] = replayid
            payload['expireTime'] = 0
            r = self.client.put('StorageCenter/ScReplay/%s' %
                                self._get_id(screplay), payload, True)
            if self._check_result(r):
                return True
            LOG.error(_LE('Error managing replay %s'),
                      screplay.get('description'))
        return False

    def unmanage_replay(self, screplay):
        """Basically sets the expireTime

        :param screplay: DellSC object.
        :return: True on success.  False on fail.
        """
        if screplay:
            payload = {}
            payload['expireTime'] = 1440
            r = self.client.put('StorageCenter/ScReplay/%s' %
                                self._get_id(screplay), payload, True)
            if self._check_result(r):
                return True
            LOG.error(_LE('Error unmanaging replay %s'),
                      screplay.get('description'))
        return False

    def delete_replay(self, scvolume, replayid):
        """Finds a Dell replay by replayid string and expires it.

        Once marked for expiration we do not return the replay as a snapshot
        even though it might still exist.  (Backend requirements.)

        :param scvolume: Dell volume object.
        :param replayid: Name to search for.  This is a portion of the snapshot
                         ID as we do not have space for the entire GUID in the
                         replay description.
        :returns: Boolean for success or failure.
        """
        ret = True
        LOG.debug('Expiring replay %s', replayid)
        # if we do not have the instanceid then we have to find the replay.
        replay = self.find_replay(scvolume, replayid)
        if replay is not None:
            # expire our replay.
            r = self.client.post('StorageCenter/ScReplay/%s/Expire' %
                                 self._get_id(replay), {}, True)
            ret = self._check_result(r)
        # If we couldn't find it we call that a success.
        return ret

    def create_view_volume(self, volname, screplay, replay_profile_string):
        """Creates a new volume named volname from the screplay.

        :param volname: Name of new volume.  This is the cinder volume ID.
        :param screplay: Dell replay object from which to make a new volume.
        :param replay_profile_string: Profiles to be applied to the volume
        :returns: Dell volume object or None.
        """
        folder = self._find_volume_folder(True)

        # Find our replay_profiles.
        addids, removeids = self._find_replay_profiles(replay_profile_string)

        # payload is just the volume name and folder if we have one.
        payload = {}
        payload['Name'] = volname
        payload['Notes'] = self.notes
        if folder is not None:
            payload['VolumeFolder'] = self._get_id(folder)
        if addids:
            payload['ReplayProfileList'] = addids
        r = self.client.post('StorageCenter/ScReplay/%s/CreateView'
                             % self._get_id(screplay), payload, True)
        volume = None
        if self._check_result(r):
            volume = self._first_result(r)

        if volume is None:
            LOG.error(_LE('Unable to create volume %s from replay'),
                      volname)

        return volume

    def create_cloned_volume(self, volumename, scvolume, replay_profile_list):
        """Creates a volume named volumename from a copy of scvolume.

        This is done by creating a replay and then a view volume from
        that replay.  The replay is set to expire after an hour.  It is only
        needed long enough to create the volume.  (1 minute should be enough
        but we set an hour in case the world has gone mad.)


        :param volumename: Name of new volume.  This is the cinder volume ID.
        :param scvolume: Dell volume object.
        :param replay_profile_list: List of snapshot profiles.
        :returns: The new volume's Dell volume object.
        """
        replay = self.create_replay(scvolume, 'Cinder Clone Replay', 60)
        if replay is not None:
            return self.create_view_volume(volumename, replay,
                                           replay_profile_list)
        LOG.error(_LE('Error: unable to snap replay'))
        return None

    def expand_volume(self, scvolume, newsize):
        """Expands scvolume to newsize GBs.

        :param scvolume: Dell volume object to be expanded.
        :param newsize: The new size of the volume object.
        :returns: The updated Dell volume object on success or None on failure.
        """
        vol = None
        payload = {}
        payload['NewSize'] = '%d GB' % newsize
        r = self.client.post('StorageCenter/ScVolume/%s/ExpandToSize'
                             % self._get_id(scvolume), payload, True)
        if self._check_result(r):
            vol = self._get_json(r)
        # More info might be good.
        if vol is not None:
            LOG.debug('Volume expanded: %(name)s %(size)s',
                      {'name': vol['name'],
                       'size': vol['configuredSize']})
        else:
            LOG.error(_LE('Error expanding volume %s.'), scvolume['name'])
        return vol

    def rename_volume(self, scvolume, name):
        """Rename scvolume to name.

        This is mostly used by update_migrated_volume.

        :param scvolume: The Dell volume object to be renamed.
        :param name: The new volume name.
        :returns: Boolean indicating success or failure.
        """
        payload = {}
        payload['Name'] = name
        r = self.client.put('StorageCenter/ScVolume/%s'
                            % self._get_id(scvolume),
                            payload, True)
        if self._check_result(r):
            return True

        LOG.error(_LE('Error renaming volume %(original)s to %(name)s'),
                  {'original': scvolume['name'],
                   'name': name})
        return False

    def update_storage_profile(self, scvolume, storage_profile):
        """Update a volume's Storage Profile.

        Changes the volume setting to use a different Storage Profile. If
        storage_profile is None, will reset to the default profile for the
        cinder user account.

        :param scvolume: The Storage Center volume to be updated.
        :param storage_profile: The requested Storage Profile name.
        :returns: True if successful, False otherwise.
        """
        prefs = self._get_user_preferences()
        if not prefs:
            return False

        if not prefs.get('allowStorageProfileSelection'):
            LOG.error(_LE('User does not have permission to change '
                          'Storage Profile selection.'))
            return False

        profile = self._find_storage_profile(storage_profile)
        if storage_profile:
            if not profile:
                LOG.error(_LE('Storage Profile %s was not found.'),
                          storage_profile)
                return False
        else:
            # Going from specific profile to the user default
            profile = prefs.get('storageProfile')
            if not profile:
                LOG.error(_LE('Default Storage Profile was not found.'))
                return False

        LOG.info(_LI('Switching volume %(vol)s to profile %(prof)s.'),
                 {'vol': scvolume['name'],
                  'prof': profile.get('name')})
        payload = {}
        payload['StorageProfile'] = self._get_id(profile)
        r = self.client.put('StorageCenter/ScVolumeConfiguration/%s'
                            % self._get_id(scvolume), payload, True)
        if self._check_result(r):
            return True

        LOG.error(_LE('Error changing Storage Profile for volume '
                      '%(original)s to %(name)s'),
                  {'original': scvolume['name'],
                   'name': storage_profile})
        return False

    def _get_user_preferences(self):
        """Gets the preferences and defaults for this user.

        There are a set of preferences and defaults for each user on the
        Storage Center. This retrieves all settings for the current account
        used by Cinder.
        """
        r = self.client.get('StorageCenter/StorageCenter/%s/UserPreferences' %
                            self.ssn)
        if self._check_result(r):
            return self._get_json(r)
        return {}

    def _delete_server(self, scserver):
        """Deletes scserver from the backend.

        Just give it a shot.  If it fails it doesn't matter to cinder.  This
        is generally used when a create_server call fails in the middle of
        creation.  Cinder knows nothing of the servers objects on Dell backends
        so success or failure is purely an internal thing.

        Note that we do not delete a server object in normal operation.

        :param scserver: Dell server object to delete.
        :returns: Nothing.  Only logs messages.
        """
        LOG.debug('ScServer delete %s', self._get_id(scserver))
        if scserver.get('deleteAllowed') is True:
            r = self.client.delete('StorageCenter/ScServer/%s'
                                   % self._get_id(scserver), async=True)
            if self._check_result(r):
                LOG.debug('ScServer deleted.')
        else:
            LOG.debug('_delete_server: deleteAllowed is False.')

    def find_replay_profile(self, name):
        """Finds the Dell SC replay profile object name.

        :param name: Name of the replay profile object. This is the
                     consistency group id.
        :return: Dell SC replay profile or None.
        :raises: VolumeBackendAPIException
        """
        self.cg_except_on_no_support()
        pf = self._get_payload_filter()
        pf.append('ScSerialNumber', self.ssn)
        pf.append('Name', name)
        r = self.client.post('StorageCenter/ScReplayProfile/GetList',
                             pf.payload)
        if self._check_result(r):
            profilelist = self._get_json(r)
            if profilelist:
                if len(profilelist) > 1:
                    LOG.error(_LE('Multiple replay profiles under name %s'),
                              name)
                    raise exception.VolumeBackendAPIException(
                        data=_('Multiple profiles found.'))
                return profilelist[0]
        return None

    def create_replay_profile(self, name):
        """Creates a replay profile on the Dell SC.

        :param name: The ID of the consistency group.  This will be matched to
                     the name on the Dell SC.
        :return: SC profile or None.
        """
        self.cg_except_on_no_support()
        profile = self.find_replay_profile(name)
        if not profile:
            payload = {}
            payload['StorageCenter'] = self.ssn
            payload['Name'] = name
            payload['Type'] = 'Consistent'
            payload['Notes'] = self.notes
            r = self.client.post('StorageCenter/ScReplayProfile',
                                 payload, True)
            # 201 expected.
            if self._check_result(r):
                profile = self._first_result(r)
        return profile

    def delete_replay_profile(self, profile):
        """Delete the replay profile from the Dell SC.

        :param profile: SC replay profile.
        :return: Nothing.
        :raises: VolumeBackendAPIException
        """
        self.cg_except_on_no_support()
        r = self.client.delete('StorageCenter/ScReplayProfile/%s' %
                               self._get_id(profile), async=True)
        if self._check_result(r):
            LOG.info(_LI('Profile %s has been deleted.'),
                     profile.get('name'))
        else:
            # We failed due to a failure to delete an existing profile.
            # This is reason to raise an exception.
            LOG.error(_LE('Unable to delete profile %s.'), profile.get('name'))
            raise exception.VolumeBackendAPIException(
                data=_('Error deleting replay profile.'))

    def _get_volume_configuration(self, scvolume):
        """Get the ScVolumeConfiguration object.

        :param scvolume: The Dell SC volume object.
        :return: The SCVolumeConfiguration object or None.
        """
        r = self.client.get('StorageCenter/ScVolume/%s/VolumeConfiguration' %
                            self._get_id(scvolume))
        if self._check_result(r):
            return self._first_result(r)
        return None

    def _update_volume_profiles(self, scvolume, addid=None, removeid=None):
        """Either Adds or removes the listed profile from the SC volume.

        :param scvolume: Dell SC volume object.
        :param addid: Profile ID to be added to the SC volume configuration.
        :param removeid: ID to be removed to the SC volume configuration.
        :return: True/False on success/failure.
        """
        if scvolume:
            scvolumecfg = self._get_volume_configuration(scvolume)
            if scvolumecfg:
                profilelist = scvolumecfg.get('replayProfileList', [])
                newprofilelist = []
                # Do we have one to add?  Start the list with it.
                if addid:
                    newprofilelist = [addid]
                # Re-add our existing profiles.
                for profile in profilelist:
                    profileid = self._get_id(profile)
                    # Make sure it isn't one we want removed and that we
                    # haven't already added it.  (IE it isn't the addid.)
                    if (profileid != removeid and
                            newprofilelist.count(profileid) == 0):
                        newprofilelist.append(profileid)
                # Update our volume configuration.
                payload = {}
                payload['ReplayProfileList'] = newprofilelist
                r = self.client.put('StorageCenter/ScVolumeConfiguration/%s' %
                                    self._get_id(scvolumecfg), payload, True)
                # check result
                LOG.debug('_update_volume_profiles %s : %s : %s',
                          self._get_id(scvolume),
                          profilelist,
                          r)
                # Good return?
                if self._check_result(r):
                    return True
        return False

    def _add_cg_volumes(self, profileid, add_volumes):
        """Trundles through add_volumes and adds the replay profile to them.

        :param profileid: The ID of the replay profile.
        :param add_volumes: List of Dell SC volume objects that are getting
                            added to the consistency group.
        :return: True/False on success/failure.
        """
        for vol in add_volumes:
            scvolume = self.find_volume(vol['id'], vol['provider_id'])
            if (self._update_volume_profiles(scvolume,
                                             addid=profileid,
                                             removeid=None)):
                LOG.info(_LI('Added %s to cg.'), vol['id'])
            else:
                LOG.error(_LE('Failed to add %s to cg.'), vol['id'])
                return False
        return True

    def _remove_cg_volumes(self, profileid, remove_volumes):
        """Removes the replay profile from the remove_volumes list of vols.

        :param profileid: The ID of the replay profile.
        :param remove_volumes: List of Dell SC volume objects that are getting
                               removed from the consistency group.
        :return: True/False on success/failure.
        """
        for vol in remove_volumes:
            scvolume = self.find_volume(vol['id'], vol['provider_id'])
            if (self._update_volume_profiles(scvolume,
                                             addid=None,
                                             removeid=profileid)):
                LOG.info(_LI('Removed %s from cg.'), vol['id'])
            else:
                LOG.error(_LE('Failed to remove %s from cg.'), vol['id'])
                return False
        return True

    def update_cg_volumes(self, profile, add_volumes=None,
                          remove_volumes=None):
        """Adds or removes the profile from the specified volumes

        :param profile: Dell SC replay profile object.
        :param add_volumes: List of volumes we are adding to the consistency
                            group. (Which is to say we are adding the profile
                            to this list of volumes.)
        :param remove_volumes: List of volumes we are removing from the
                               consistency group. (Which is to say we are
                               removing the profile from this list of volumes.)
        :return: True/False on success/failure.
        """
        self.cg_except_on_no_support()
        ret = True
        profileid = self._get_id(profile)
        if add_volumes:
            LOG.info(_LI('Adding volumes to cg %s.'), profile['name'])
            ret = self._add_cg_volumes(profileid, add_volumes)
        if ret and remove_volumes:
            LOG.info(_LI('Removing volumes from cg %s.'), profile['name'])
            ret = self._remove_cg_volumes(profileid, remove_volumes)
        return ret

    def _init_cg_volumes(self, profileid):
        """Gets the cg volume list and maps/unmaps the non active volumes.

        :param profileid: Replay profile identifier.
        :return: Nothing
        """
        r = self.client.get('StorageCenter/ScReplayProfile/%s/VolumeList' %
                            profileid)
        if self._check_result(r):
            vols = self._get_json(r)
            for vol in vols:
                if (vol.get('active') is not True or
                        vol.get('replayAllowed') is not True):
                    self._init_volume(vol)

    def snap_cg_replay(self, profile, replayid, expire):
        """Snaps a replay of a consistency group.

        :param profile: The name of the consistency group profile.
        :param replayid: The name of the replay.
        :param expire: Time in mintues before a replay expires.  0 means no
                       expiration.
        :returns: Dell SC replay object.
        """
        self.cg_except_on_no_support()
        if profile:
            # We have to make sure these are snappable.
            self._init_cg_volumes(self._get_id(profile))

            # Succeed or fail we soldier on.
            payload = {}
            payload['description'] = replayid
            payload['expireTime'] = expire
            r = self.client.post('StorageCenter/ScReplayProfile/%s/'
                                 'CreateReplay'
                                 % self._get_id(profile), payload, True)
            if self._check_result(r):
                LOG.info(_LI('CreateReplay success %s'), replayid)
                return True

        return False

    def _find_sc_cg(self, profile, replayid):
        """Finds the sc consistency group that matches replayid

        :param profile: Dell profile object.
        :param replayid: Name to search for.  This is a portion of the
                         snapshot ID as we do not have space for the entire
                         GUID in the replay description.
        :return: Consistency group object or None.
        """
        self.cg_except_on_no_support()
        r = self.client.get(
            'StorageCenter/ScReplayProfile/%s/ConsistencyGroupList'
            % self._get_id(profile))
        if self._check_result(r):
            cglist = self._get_json(r)
            if cglist and isinstance(cglist, list):
                for cg in cglist:
                    desc = cg.get('description')
                    if (len(desc) >= 30 and
                            replayid.startswith(desc) is True):
                        # We found our cg so return it.
                        return cg
        return None

    def _find_cg_replays(self, profile, replayid):
        """Searches for the replays that match replayid for a given profile.

        replayid is stored in the replay's description attribute.

        :param profile: Dell profile object.
        :param replayid: Name to search for.  This is a portion of the
                         snapshot ID as we do not have space for the entire
                         GUID in the replay description.
        :returns: Dell replay object array.
        """
        self.cg_except_on_no_support()
        replays = []
        sccg = self._find_sc_cg(profile, replayid)
        if sccg:
            r = self.client.get(
                'StorageCenter/ScReplayConsistencyGroup/%s/ReplayList'
                % self._get_id(sccg))

            replays = self._get_json(r)
        else:
            LOG.error(_LE('Unable to locate snapshot %s'), replayid)

        return replays

    def delete_cg_replay(self, profile, replayid):
        """Finds a Dell cg replay by replayid string and expires it.

        Once marked for expiration we do not return the replay as a snapshot
        even though it might still exist.  (Backend requirements.)

        :param cg_name: Consistency Group name.  This is the ReplayProfileName.
        :param replayid: Name to search for.  This is a portion of the snapshot
                         ID as we do not have space for the entire GUID in the
                         replay description.
        :returns: Boolean for success or failure.
        """
        self.cg_except_on_no_support()
        LOG.debug('Expiring consistency group replay %s', replayid)
        replays = self._find_cg_replays(profile,
                                        replayid)
        for replay in replays:
            instanceid = self._get_id(replay)
            LOG.debug('Expiring replay %s', instanceid)
            r = self.client.post('StorageCenter/ScReplay/%s/Expire'
                                 % instanceid, {}, True)
            if not self._check_result(r):
                return False
        # We either couldn't find it or expired it.
        return True

    def cg_except_on_no_support(self):
        if not self.consisgroups:
            msg = _('Dell API 2.1 or later required'
                    ' for Consistency Group support')
            raise NotImplementedError(data=msg)

    @staticmethod
    def size_to_gb(spacestring):
        """Splits a SC size string into GB and a remainder.

        Space is returned in a string like ...
        7.38197504E8 Bytes
        Need to split that apart and convert to GB.

        :param spacestring: SC size string.
        :return: Size in GB and remainder in byte.
        """
        try:
            n = spacestring.split(' ', 1)
            fgb = int(float(n[0]) // 1073741824)
            frem = int(float(n[0]) % 1073741824)
            return fgb, frem

        except Exception:
            # We received an invalid size string.  Blow up.
            raise exception.VolumeBackendAPIException(
                data=_('Error retrieving volume size'))

    def _import_one(self, scvolume, newname):
        # Find our folder
        folder = self._find_volume_folder(True)

        # If we actually have a place to put our volume create it
        if folder is None:
            LOG.warning(_LW('Unable to create folder %s'), self.vfname)

        # Rename and move our volume.
        payload = {}
        payload['Name'] = newname
        if folder:
            payload['VolumeFolder'] = self._get_id(folder)

        r = self.client.put('StorageCenter/ScVolume/%s' %
                            self._get_id(scvolume), payload, True)
        if self._check_result(r):
            return self._get_json(r)
        return None

    def manage_existing(self, newname, existing):
        """Finds the volume named existing and renames it.

         This checks a few things. The volume has to exist.  There can
         only be one volume by that name.  Since cinder manages volumes
         by the GB it has to be defined on a GB boundary.

         This renames existing to newname.  newname is the guid from
         the cinder volume['id'].  The volume is moved to the defined
         cinder volume folder.

        :param newname: Name to rename the volume to.
        :param existing: The existing volume dict..
        :return: scvolume.
        :raises: VolumeBackendAPIException, ManageExistingInvalidReference
        """
        vollist = self._get_volume_list(existing.get('source-name'),
                                        existing.get('source-id'),
                                        False)
        count = len(vollist)
        # If we found one volume with that name we can work with it.
        if count == 1:
            # First thing to check is if the size is something we can
            # work with.
            sz, rem = self.size_to_gb(vollist[0]['configuredSize'])
            if rem > 0:
                raise exception.VolumeBackendAPIException(
                    data=_('Volume size must multiple of 1 GB.'))

            # We only want to grab detached volumes.
            mappings = self._find_mappings(vollist[0])
            if len(mappings) > 0:
                msg = _('Volume is attached to a server.  (%s)') % existing
                raise exception.VolumeBackendAPIException(data=msg)

            scvolume = self._import_one(vollist[0], newname)
            if scvolume:
                return scvolume

            msg = _('Unable to manage volume %s') % existing
            raise exception.VolumeBackendAPIException(data=msg)
        elif count > 1:
            raise exception.ManageExistingInvalidReference(
                existing_ref=existing, reason=_('Volume not unique.'))
        else:
            raise exception.ManageExistingInvalidReference(
                existing_ref=existing, reason=_('Volume not found.'))

    def get_unmanaged_volume_size(self, existing):
        """Looks up the volume named existing and returns its size string.

        :param existing: Existing volume dict.
        :return: The SC configuredSize string.
        :raises: ManageExistingInvalidReference
        """
        vollist = self._get_volume_list(existing.get('source-name'),
                                        existing.get('source-id'),
                                        False)
        count = len(vollist)
        # If we found one volume with that name we can work with it.
        if count == 1:
            sz, rem = self.size_to_gb(vollist[0]['configuredSize'])
            if rem > 0:
                raise exception.VolumeBackendAPIException(
                    data=_('Volume size must multiple of 1 GB.'))
            return sz
        elif count > 1:
            raise exception.ManageExistingInvalidReference(
                existing_ref=existing, reason=_('Volume not unique.'))
        else:
            raise exception.ManageExistingInvalidReference(
                existing_ref=existing, reason=_('Volume not found.'))

    def unmanage(self, scvolume):
        """Unmanage our volume.

        We simply rename with with a prefix of `Unmanaged_`  That's it.

        :param scvolume: The Dell SC volume object.
        :return: Nothing.
        :raises: VolumeBackendAPIException
        """
        newname = 'Unmanaged_' + scvolume['name']
        payload = {}
        payload['Name'] = newname
        r = self.client.put('StorageCenter/ScVolume/%s' %
                            self._get_id(scvolume), payload, True)
        if self._check_result(r):
            LOG.info(_LI('Volume %s unmanaged.'), scvolume['name'])
        else:
            msg = _('Unable to rename volume %(existing)s to %(newname)s') % {
                'existing': scvolume['name'],
                'newname': newname}
            raise exception.VolumeBackendAPIException(data=msg)

    def _find_qos(self, qosnode, ssn=-1):
        """Find Dell SC QOS Node entry for replication.

        :param qosnode: Name of qosnode.
        :param ssn: SSN to search on.
        :return: scqos node object.
        """
        if ssn == -1:
            ssn = self.ssn
        pf = self._get_payload_filter()
        pf.append('scSerialNumber', ssn)
        pf.append('name', qosnode)
        r = self.client.post('StorageCenter/ScReplicationQosNode/GetList',
                             pf.payload)
        if self._check_result(r):
            nodes = self._get_json(r)
            if len(nodes) > 0:
                return nodes[0]
            else:
                payload = {}
                payload['LinkSpeed'] = '1 Gbps'
                payload['Name'] = qosnode
                payload['StorageCenter'] = ssn
                payload['BandwidthLimited'] = False
                r = self.client.post('StorageCenter/ScReplicationQosNode',
                                     payload, True)
                if self._check_result(r):
                    return self._get_json(r)

        LOG.error(_LE('Unable to find or create QoS Node named %s'), qosnode)
        raise exception.VolumeBackendAPIException(
            data=_('Failed to find QoSnode'))

    def update_replicate_active_replay(self, scvolume, replactive):
        """Enables or disables replicating the active replay for given vol.

        :param scvolume: SC Volume object.
        :param replactive: True or False
        :return: True or False
        """
        r = self.client.get('StorageCenter/ScVolume/%s/ReplicationSourceList' %
                            self._get_id(scvolume))
        if self._check_result(r):
            replications = self._get_json(r)
            for replication in replications:
                if replication['replicateActiveReplay'] != replactive:
                    payload = {'ReplicateActiveReplay': replactive}
                    r = self.client.put('StorageCenter/ScReplication/%s' %
                                        replication['instanceId'],
                                        payload, True)
                    if not self._check_result(r):
                        return False
        return True

    def get_screplication(self, scvolume, destssn):
        """Find the screplication object for the volume on the dest backend.

        :param scvolume:
        :param destssn:
        :return:
        """
        LOG.debug('get_screplication')
        r = self.client.get('StorageCenter/ScVolume/%s/ReplicationSourceList' %
                            self._get_id(scvolume))
        if self._check_result(r):
            replications = self._get_json(r)
            for replication in replications:
                # So we need to find the replication we are looking for.
                LOG.debug(replication)
                LOG.debug('looking for %s', destssn)
                if replication.get('destinationScSerialNumber') == destssn:
                    return replication
        # Unable to locate replication.
        LOG.warning(_LW('Unable to locate replication %(vol)s to %(ssn)s'),
                    {'vol': scvolume.get('name'),
                     'ssn': destssn})
        return None

    def delete_replication(self, scvolume, destssn, deletedestvolume=True):
        """Deletes the SC replication object from scvolume to the destssn.

        :param scvolume: Dell SC Volume object.
        :param destssn: SC the replication is replicating to.
        :param deletedestvolume: Delete or keep dest volume.
        :return: True on success.  False on fail.
        """
        replication = self.get_screplication(scvolume, destssn)
        if replication:
            payload = {}
            payload['DeleteDestinationVolume'] = deletedestvolume
            payload['RecycleDestinationVolume'] = False
            payload['DeleteRestorePoint'] = True
            r = self.client.delete('StorageCenter/ScReplication/%s' %
                                   self._get_id(replication), payload=payload,
                                   async=True)
            if self._check_result(r):
                # check that we whacked the dest volume
                LOG.info(_LI('Replication %(vol)s to %(dest)s.'),
                         {'vol': scvolume.get('name'),
                          'dest': destssn})

                return True
            LOG.error(_LE('Unable to delete replication for '
                          '%(vol)s to %(dest)s.'),
                      {'vol': scvolume.get('name'),
                       'dest': destssn})
        return False

    def _repl_name(self, name):
        return self.repl_prefix + name

    def _get_disk_folder(self, ssn, foldername):
        diskfolder = None
        # If no folder name we just pass through this.
        if foldername:
            pf = self._get_payload_filter()
            pf.append('scSerialNumber', ssn)
            pf.append('name', foldername)
            r = self.client.post('StorageCenter/ScDiskFolder/GetList',
                                 pf.payload)
            if self._check_result(r):
                try:
                    # Go for broke.
                    diskfolder = self._get_json(r)[0]
                except Exception:
                    # We just log this as an error and return nothing.
                    LOG.error(_LE('Unable to find '
                                  'disk folder %(name)s on %(ssn)s'),
                              {'name': foldername,
                               'ssn': ssn})
        return diskfolder

    def create_replication(self, scvolume, destssn, qosnode,
                           synchronous, diskfolder, replicate_active):
        """Create repl from scvol to destssn.

        :param scvolume: Dell SC volume object.
        :param destssn: Destination SSN string.
        :param qosnode: Name of Dell SC QOS Node for this replication.
        :param synchronous: Boolean.
        :param diskfolder: optional disk folder name.
        :param replicate_active: replicate active replay.
        :return: Dell SC replication object.
        """
        screpl = None
        ssn = self.find_sc(int(destssn))
        payload = {}
        payload['DestinationStorageCenter'] = ssn
        payload['QosNode'] = self._get_id(self._find_qos(qosnode))
        payload['SourceVolume'] = self._get_id(scvolume)
        payload['StorageCenter'] = self.find_sc()
        # Have to replicate the active replay.
        payload['ReplicateActiveReplay'] = replicate_active or synchronous
        if synchronous:
            payload['Type'] = 'Synchronous'
            # If our type is synchronous we prefer high availability be set.
            payload['SyncMode'] = 'HighAvailability'
        else:
            payload['Type'] = 'Asynchronous'
        destinationvolumeattributes = {}
        destinationvolumeattributes['CreateSourceVolumeFolderPath'] = True
        destinationvolumeattributes['Notes'] = self.notes
        destinationvolumeattributes['Name'] = self._repl_name(scvolume['name'])
        # Find our disk folder.  If they haven't specified one this will just
        # drop through.  If they have specified one and it can't be found the
        # error will be logged but this will keep going.
        df = self._get_disk_folder(destssn, diskfolder)
        if df:
            destinationvolumeattributes['DiskFolder'] = self._get_id(df)
        payload['DestinationVolumeAttributes'] = destinationvolumeattributes
        r = self.client.post('StorageCenter/ScReplication', payload, True)
        # 201 expected.
        if self._check_result(r):
            LOG.info(_LI('Replication created for %(volname)s to %(destsc)s'),
                     {'volname': scvolume.get('name'),
                      'destsc': destssn})
            screpl = self._get_json(r)

        # Check we did something.
        if not screpl:
            # Failed to launch.  Inform user.  Throw.
            LOG.error(_LE('Unable to replicate %(volname)s to %(destsc)s'),
                      {'volname': scvolume.get('name'),
                       'destsc': destssn})
        return screpl

    def find_repl_volume(self, name, destssn, instance_id=None,
                         source=False, destination=True):
        """Find our replay destination volume on the destssn.

        :param name: Name to search for.
        :param destssn: Where to look for the volume.
        :param instance_id: If we know our exact volume ID use that.
        :param source: Replication source boolen.
        :param destination: Replication destination boolean.
        :return: SC Volume object or None
        """
        # Do a normal volume search.
        pf = self._get_payload_filter()
        pf.append('scSerialNumber', destssn)
        # Are we looking for a replication destination?
        pf.append('ReplicationDestination', destination)
        # Are we looking for a replication source?
        pf.append('ReplicationSource', source)
        # There is a chance we know the exact volume.  If so then use that.
        if instance_id:
            pf.append('instanceId', instance_id)
        else:
            # Try the name.
            pf.append('Name', name)
        r = self.client.post('StorageCenter/ScVolume/GetList',
                             pf.payload)
        if self._check_result(r):
            volumes = self._get_json(r)
            if len(volumes) == 1:
                return volumes[0]
        return None

    def remove_mappings(self, scvol):
        """Peels all the mappings off of scvol.

        :param scvol: Storage Center volume object.
        :return: True/False on Success/Failure.
        """
        if scvol:
            r = self.client.post('StorageCenter/ScVolume/%s/Unmap' %
                                 self._get_id(scvol), {}, True)
            return self._check_result(r)
        return False

    def break_replication(self, volumename, instance_id, destssn):
        """This just breaks the replication.

        If we find the source we just delete the replication.  If the source
        is down then we find the destination and unmap it.  Fail pretty much
        every time this goes south.

        :param volumename: Volume name is the guid from the cinder volume.
        :param instance_id: Storage Center volume object instance id.
        :param destssn: Destination ssn.
        :return: Replication SC volume object.
        """
        replinstanceid = None
        scvolume = self.find_volume(volumename, instance_id)
        screplication = self.get_screplication(scvolume, destssn)
        # if we got our replication volume we can do this nicely.
        if screplication:
            replinstanceid = screplication['destinationVolume']['instanceId']
        screplvol = self.find_repl_volume(self._repl_name(volumename),
                                          destssn, replinstanceid)
        # delete_replication fails to delete replication without also
        # stuffing it into the recycle bin.
        # Instead we try to unmap the destination volume which will break
        # the replication but leave the replication object on the SC.
        if self.remove_mappings(screplvol):
            # Try to kill mappings on the source.
            # We don't care that this succeeded or failed.  Just move on.
            self.remove_mappings(scvolume)

        return screplvol

    def _get_replay_list(self, scvolume):
        r = self.client.get('StorageCenter/ScVolume/%s/ReplayList'
                            % self._get_id(scvolume))
        if self._check_result(r):
            return self._get_json(r)
        return []

    def find_common_replay(self, svolume, dvolume):
        """Finds the common replay between two volumes.

        This assumes that one volume was replicated from the other. This
        should return the most recent replay.

        :param svolume: Source SC Volume.
        :param dvolume: Destination SC Volume.
        :return: Common replay or None.
        """
        if svolume and dvolume:
            sreplays = self._get_replay_list(svolume)
            dreplays = self._get_replay_list(dvolume)
            for dreplay in dreplays:
                for sreplay in sreplays:
                    if dreplay['globalIndex'] == sreplay['globalIndex']:
                        return dreplay
        return None

    def start_replication(self, svolume, dvolume,
                          replicationtype, qosnode, activereplay):
        """Starts a replication between volumes.

        Requires the dvolume to be in an appropriate state to start this.

        :param svolume: Source SC Volume.
        :param dvolume: Destiation SC Volume
        :param replicationtype: Asynchronous or synchronous.
        :param qosnode: QOS node name.
        :param activereplay: Boolean to replicate the active replay or not.
        :return: ScReplication object or None.
        """
        if svolume and dvolume:
            qos = self._find_qos(qosnode, svolume['scSerialNumber'])
            if qos:
                payload = {}
                payload['QosNode'] = self._get_id(qos)
                payload['SourceVolume'] = self._get_id(svolume)
                payload['StorageCenter'] = svolume['scSerialNumber']
                # Have to replicate the active replay.
                payload['ReplicateActiveReplay'] = activereplay
                payload['Type'] = replicationtype
                payload['DestinationVolume'] = self._get_id(dvolume)
                payload['DestinationStorageCenter'] = dvolume['scSerialNumber']
                r = self.client.post('StorageCenter/ScReplication', payload,
                                     True)
                # 201 expected.
                if self._check_result(r):
                    LOG.info(_LI('Replication created for '
                                 '%(src)s to %(dest)s'),
                             {'src': svolume.get('name'),
                              'dest': dvolume.get('name')})
                    screpl = self._get_json(r)
                    return screpl
        return None

    def replicate_to_common(self, svolume, dvolume, qosnode):
        """Reverses a replication between two volumes.

        :param fovolume: Failed over volume. (Current)
        :param ovolume: Original source volume.
        :param qosnode: QOS node name to use to create the replay.
        :return: ScReplication object or None.
        """
        # find our common replay.
        creplay = self.find_common_replay(svolume, dvolume)
        # if we found one.
        if creplay:
            # create a view volume from the common replay.
            payload = {}
            # funky name.
            payload['Name'] = 'fback:' + dvolume['name']
            payload['Notes'] = self.notes
            payload['VolumeFolder'] = self._get_id(dvolume['volumeFolder'])
            r = self.client.post('StorageCenter/ScReplay/%s/CreateView'
                                 % self._get_id(creplay), payload, True)
            if self._check_result(r):
                vvolume = self._get_json(r)
                if vvolume:
                    # snap a replay and start replicating.
                    if self.create_replay(svolume, 'failback', 600):
                        return self.start_replication(svolume, vvolume,
                                                      'Asynchronous', qosnode,
                                                      False)
        # No joy.  Error the volume.
        return None

    def flip_replication(self, svolume, dvolume, name,
                         replicationtype, qosnode, activereplay):
        """Enables replication from current destination volume to source.

        :param svolume: Current source. New destination.
        :param dvolume: Current destination.  New source.
        :param name: Volume name.
        :param replicationtype: Sync or async
        :param qosnode: qos node for the new source ssn.
        :param activereplay: replicate the active replay.
        :return: True/False.
        """
        # We are flipping a replication. That means there was a replication to
        # start with. Delete that.
        if self.delete_replication(svolume, dvolume['scSerialNumber'], False):
            # Kick off a replication going the other way.
            if self.start_replication(dvolume, svolume, replicationtype,
                                      qosnode, activereplay) is not None:
                # rename
                if (self.rename_volume(svolume, self._repl_name(name)) and
                        self.rename_volume(dvolume, name)):
                    return True
        LOG.warning(_LW('flip_replication: Unable to replicate '
                        '%(name)s from %(src)s to %(dst)s'),
                    {'name': name,
                     'src': dvolume['scSerialNumber'],
                     'dst': svolume['scSerialNumber']})
        return False

    def replication_progress(self, screplid):
        """Get's the current progress of the replication.

        :param screplid: instanceId of the ScReplication object.
        :return: Boolean for synced, float of remaining bytes. (Or None, None.)
        """
        if screplid:
            r = self.client.get(
                'StorageCenter/ScReplication/%s/CurrentProgress' % screplid)
            if self._check_result(r):
                progress = self._get_json(r)
                try:
                    remaining = float(
                        progress['amountRemaining'].split(' ', 1)[0])
                    return progress['synced'], remaining
                except Exception:
                    LOG.warning(_LW('replication_progress: Invalid replication'
                                    ' progress information returned: %s'),
                                progress)
        return None, None
