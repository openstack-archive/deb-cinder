
# Copyright 2010 United States Government as represented by the
# Administrator of the National Aeronautics and Space Administration.
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
Unit Tests for remote procedure calls using queue
"""

import ddt
import mock
from oslo_concurrency import processutils
from oslo_config import cfg
from oslo_db import exception as db_exc

from cinder import context
from cinder import db
from cinder import exception
from cinder import manager
from cinder import objects
from cinder import rpc
from cinder import service
from cinder import test


test_service_opts = [
    cfg.StrOpt("fake_manager",
               default="cinder.tests.unit.test_service.FakeManager",
               help="Manager for testing"),
    cfg.StrOpt("test_service_listen",
               help="Host to bind test service to"),
    cfg.IntOpt("test_service_listen_port",
               default=0,
               help="Port number to bind test service to"), ]

CONF = cfg.CONF
CONF.register_opts(test_service_opts)


class FakeManager(manager.Manager):
    """Fake manager for tests."""
    def __init__(self, host=None,
                 db_driver=None, service_name=None, cluster=None):
        super(FakeManager, self).__init__(host=host,
                                          db_driver=db_driver,
                                          cluster=cluster)

    def test_method(self):
        return 'manager'


class ExtendedService(service.Service):
    def test_method(self):
        return 'service'


class ServiceManagerTestCase(test.TestCase):
    """Test cases for Services."""

    @mock.patch('cinder.service.Service.is_svc_upgrading_to_n',
                return_value=False)
    def test_message_gets_to_manager(self, is_upgrading_mock):
        serv = service.Service('test',
                               'test',
                               'test',
                               'cinder.tests.unit.test_service.FakeManager')
        serv.start()
        self.assertEqual('manager', serv.test_method())

    @mock.patch('cinder.service.Service.is_svc_upgrading_to_n',
                return_value=False)
    def test_override_manager_method(self, is_upgrading_mock):
        serv = ExtendedService('test',
                               'test',
                               'test',
                               'cinder.tests.unit.test_service.FakeManager')
        serv.start()
        self.assertEqual('service', serv.test_method())

    @mock.patch('cinder.service.Service.is_svc_upgrading_to_n',
                return_value=False)
    @mock.patch('cinder.rpc.LAST_OBJ_VERSIONS', {'test': '1.5'})
    @mock.patch('cinder.rpc.LAST_RPC_VERSIONS', {'test': '1.3'})
    def test_reset(self, is_upgrading_mock):
        serv = service.Service('test',
                               'test',
                               'test',
                               'cinder.tests.unit.test_service.FakeManager')
        serv.start()
        serv.reset()
        self.assertEqual({}, rpc.LAST_OBJ_VERSIONS)
        self.assertEqual({}, rpc.LAST_RPC_VERSIONS)


class ServiceFlagsTestCase(test.TestCase):
    @mock.patch('cinder.service.Service.is_svc_upgrading_to_n',
                return_value=False)
    def test_service_enabled_on_create_based_on_flag(self,
                                                     is_upgrading_mock=False):
        ctxt = context.get_admin_context()
        self.flags(enable_new_services=True)
        host = 'foo'
        binary = 'cinder-fake'
        cluster = 'cluster'
        app = service.Service.create(host=host, binary=binary, cluster=cluster)
        ref = db.service_get(ctxt, app.service_id)
        db.service_destroy(ctxt, app.service_id)
        self.assertFalse(ref.disabled)

        # Check that the cluster is also enabled
        db_cluster = objects.ClusterList.get_all(ctxt)[0]
        self.assertFalse(db_cluster.disabled)
        db.cluster_destroy(ctxt, db_cluster.id)

    @mock.patch('cinder.service.Service.is_svc_upgrading_to_n',
                return_value=False)
    def test_service_disabled_on_create_based_on_flag(self, is_upgrading_mock):
        ctxt = context.get_admin_context()
        self.flags(enable_new_services=False)
        host = 'foo'
        binary = 'cinder-fake'
        cluster = 'cluster'
        app = service.Service.create(host=host, binary=binary, cluster=cluster)
        ref = db.service_get(ctxt, app.service_id)
        db.service_destroy(ctxt, app.service_id)
        self.assertTrue(ref.disabled)

        # Check that the cluster is also enabled
        db_cluster = objects.ClusterList.get_all(ctxt)[0]
        self.assertTrue(db_cluster.disabled)
        db.cluster_destroy(ctxt, db_cluster.id)


@ddt.ddt
class ServiceTestCase(test.TestCase):
    """Test cases for Services."""

    def setUp(self):
        super(ServiceTestCase, self).setUp()
        self.host = 'foo'
        self.binary = 'cinder-fake'
        self.topic = 'fake'
        self.service_ref = {'host': self.host,
                            'binary': self.binary,
                            'topic': self.topic,
                            'report_count': 0,
                            'availability_zone': 'nova',
                            'id': 1}
        self.ctxt = context.get_admin_context()

    def _check_app(self, app, cluster=None, cluster_exists=None,
                   is_upgrading=False, svc_id=None, added_to_cluster=None):
        """Check that Service instance and DB service and cluster are ok."""
        self.assertIsNotNone(app)

        # Check that we have the service ID
        self.assertTrue(hasattr(app, 'service_id'))

        if svc_id:
            self.assertEqual(svc_id, app.service_id)

        # Check that cluster has been properly set
        self.assertEqual(cluster, app.cluster)
        # Check that the entry has been really created in the DB
        svc = objects.Service.get_by_id(self.ctxt, app.service_id)

        cluster_name = cluster if cluster_exists is not False else None

        # Check that cluster name matches
        self.assertEqual(cluster_name, svc.cluster_name)

        clusters = objects.ClusterList.get_all(self.ctxt)

        if added_to_cluster is None:
            added_to_cluster = not is_upgrading

        if cluster_name:
            # Make sure we have created the cluster in the DB
            self.assertEqual(1, len(clusters))
            cluster = clusters[0]
            self.assertEqual(cluster_name, cluster.name)
            self.assertEqual(self.binary, cluster.binary)
        else:
            # Make sure we haven't created any cluster in the DB
            self.assertListEqual([], clusters.objects)

        self.assertEqual(added_to_cluster, app.added_to_cluster)

    @ddt.data(False, True)
    @mock.patch('cinder.service.Service.is_svc_upgrading_to_n')
    def test_create(self, is_upgrading, is_upgrading_mock):
        """Test non clustered service creation."""
        is_upgrading_mock.return_value = is_upgrading

        # NOTE(vish): Create was moved out of mock replay to make sure that
        #             the looping calls are created in StartService.
        app = service.Service.create(host=self.host,
                                     binary=self.binary,
                                     topic=self.topic)
        self._check_app(app, is_upgrading=is_upgrading)

    @mock.patch('cinder.service.Service.is_svc_upgrading_to_n',
                return_value=False)
    def test_create_with_cluster_not_upgrading(self, is_upgrading_mock):
        """Test DB cluster creation when service is created."""
        cluster_name = 'cluster'
        app = service.Service.create(host=self.host, binary=self.binary,
                                     cluster=cluster_name, topic=self.topic)
        self._check_app(app, cluster_name)

    @mock.patch('cinder.service.Service.is_svc_upgrading_to_n',
                return_value=True)
    def test_create_with_cluster_upgrading(self, is_upgrading_mock):
        """Test that we don't create the cluster while we are upgrading."""
        cluster_name = 'cluster'
        app = service.Service.create(host=self.host, binary=self.binary,
                                     cluster=cluster_name, topic=self.topic)
        self._check_app(app, cluster_name, cluster_exists=False,
                        is_upgrading=True)

    @mock.patch('cinder.service.Service.is_svc_upgrading_to_n',
                return_value=False)
    def test_create_svc_exists_upgrade_cluster(self, is_upgrading_mock):
        """Test that we update cluster_name field when cfg has changed."""
        # Create the service in the DB
        db_svc = db.service_create(context.get_admin_context(),
                                   {'host': self.host, 'binary': self.binary,
                                    'topic': self.topic,
                                    'cluster_name': None})
        cluster_name = 'cluster'
        app = service.Service.create(host=self.host, binary=self.binary,
                                     cluster=cluster_name, topic=self.topic)
        self._check_app(app, cluster_name, svc_id=db_svc.id)

    @mock.patch('cinder.service.Service.is_svc_upgrading_to_n',
                return_value=True)
    def test_create_svc_exists_not_upgrade_cluster(self, is_upgrading_mock):
        """Test we don't update cluster_name on cfg change when upgrading."""
        # Create the service in the DB
        db_svc = db.service_create(context.get_admin_context(),
                                   {'host': self.host, 'binary': self.binary,
                                    'topic': self.topic,
                                    'cluster': None})
        cluster_name = 'cluster'
        app = service.Service.create(host=self.host, binary=self.binary,
                                     cluster=cluster_name, topic=self.topic)
        self._check_app(app, cluster_name, cluster_exists=False,
                        is_upgrading=True, svc_id=db_svc.id)

    @mock.patch('cinder.service.Service.is_svc_upgrading_to_n',
                return_value=False)
    @mock.patch.object(objects.service.Service, 'get_by_args')
    @mock.patch.object(objects.service.Service, 'get_by_id')
    def test_report_state_newly_disconnected(self, get_by_id, get_by_args,
                                             is_upgrading_mock):
        get_by_args.side_effect = exception.NotFound()
        get_by_id.side_effect = db_exc.DBConnectionError()
        with mock.patch.object(objects.service, 'db') as mock_db:
            mock_db.service_create.return_value = self.service_ref

            serv = service.Service(
                self.host,
                self.binary,
                self.topic,
                'cinder.tests.unit.test_service.FakeManager'
            )
            serv.start()
            serv.report_state()
            self.assertTrue(serv.model_disconnected)
            self.assertFalse(mock_db.service_update.called)

    @mock.patch('cinder.service.Service.is_svc_upgrading_to_n',
                return_value=False)
    @mock.patch.object(objects.service.Service, 'get_by_args')
    @mock.patch.object(objects.service.Service, 'get_by_id')
    def test_report_state_disconnected_DBError(self, get_by_id, get_by_args,
                                               is_upgrading_mock):
        get_by_args.side_effect = exception.NotFound()
        get_by_id.side_effect = db_exc.DBError()
        with mock.patch.object(objects.service, 'db') as mock_db:
            mock_db.service_create.return_value = self.service_ref

            serv = service.Service(
                self.host,
                self.binary,
                self.topic,
                'cinder.tests.unit.test_service.FakeManager'
            )
            serv.start()
            serv.report_state()
            self.assertTrue(serv.model_disconnected)
            self.assertFalse(mock_db.service_update.called)

    @mock.patch('cinder.service.Service.is_svc_upgrading_to_n',
                return_value=False)
    @mock.patch('cinder.db.sqlalchemy.api.service_update')
    @mock.patch('cinder.db.sqlalchemy.api.service_get')
    def test_report_state_newly_connected(self, get_by_id, service_update,
                                          is_upgrading_mock):
        get_by_id.return_value = self.service_ref

        serv = service.Service(
            self.host,
            self.binary,
            self.topic,
            'cinder.tests.unit.test_service.FakeManager'
        )
        serv.start()
        serv.model_disconnected = True
        serv.report_state()

        self.assertFalse(serv.model_disconnected)
        self.assertTrue(service_update.called)

    @mock.patch('cinder.service.Service.is_svc_upgrading_to_n',
                return_value=False)
    def test_report_state_manager_not_working(self, is_upgrading_mock):
        with mock.patch('cinder.db') as mock_db:
            mock_db.service_get.return_value = self.service_ref

            serv = service.Service(
                self.host,
                self.binary,
                self.topic,
                'cinder.tests.unit.test_service.FakeManager'
            )
            serv.manager.is_working = mock.Mock(return_value=False)
            serv.start()
            serv.report_state()

            serv.manager.is_working.assert_called_once_with()
            self.assertFalse(mock_db.service_update.called)

    @mock.patch('cinder.service.Service.is_svc_upgrading_to_n',
                return_value=False)
    def test_service_with_long_report_interval(self, is_upgrading_mock):
        self.override_config('service_down_time', 10)
        self.override_config('report_interval', 10)
        service.Service.create(
            binary="test_service",
            manager="cinder.tests.unit.test_service.FakeManager")
        self.assertEqual(25, CONF.service_down_time)

    @mock.patch('cinder.service.Service.is_svc_upgrading_to_n',
                return_value=False)
    @mock.patch.object(rpc, 'get_server')
    @mock.patch('cinder.db')
    def test_service_stop_waits_for_rpcserver(self, mock_db, mock_rpc,
                                              is_upgrading_mock):
        serv = service.Service(
            self.host,
            self.binary,
            self.topic,
            'cinder.tests.unit.test_service.FakeManager'
        )
        serv.start()
        serv.stop()
        serv.wait()
        serv.rpcserver.start.assert_called_once_with()
        serv.rpcserver.stop.assert_called_once_with()
        serv.rpcserver.wait.assert_called_once_with()

    @mock.patch('cinder.service.Service.is_svc_upgrading_to_n',
                return_value=False)
    @mock.patch('cinder.service.Service.report_state')
    @mock.patch('cinder.service.Service.periodic_tasks')
    @mock.patch.object(service.loopingcall, 'FixedIntervalLoopingCall')
    @mock.patch.object(rpc, 'get_server')
    @mock.patch('cinder.db')
    def test_service_stop_waits_for_timers(self, mock_db, mock_rpc,
                                           mock_loopcall, mock_periodic,
                                           mock_report, is_upgrading_mock):
        """Test that we wait for loopcalls only if stop succeeds."""
        serv = service.Service(
            self.host,
            self.binary,
            self.topic,
            'cinder.tests.unit.test_service.FakeManager',
            report_interval=5,
            periodic_interval=10,
        )

        # One of the loopcalls will raise an exception on stop
        mock_loopcall.side_effect = (
            mock.Mock(**{'stop.side_effect': Exception}),
            mock.Mock())

        serv.start()
        serv.stop()
        serv.wait()
        serv.rpcserver.start.assert_called_once_with()
        serv.rpcserver.stop.assert_called_once_with()
        serv.rpcserver.wait.assert_called_once_with()

        # The first loopcall will have failed on the stop call, so we will not
        # have waited for it to stop
        self.assertEqual(1, serv.timers[0].start.call_count)
        self.assertEqual(1, serv.timers[0].stop.call_count)
        self.assertFalse(serv.timers[0].wait.called)

        # We will wait for the second loopcall
        self.assertEqual(1, serv.timers[1].start.call_count)
        self.assertEqual(1, serv.timers[1].stop.call_count)
        self.assertEqual(1, serv.timers[1].wait.call_count)

    @mock.patch('cinder.manager.Manager.init_host')
    @mock.patch.object(service.loopingcall, 'FixedIntervalLoopingCall')
    @mock.patch('oslo_messaging.Target')
    @mock.patch.object(rpc, 'get_server')
    def _check_rpc_servers_and_init_host(self, app, added_to_cluster, cluster,
                                         rpc_mock, target_mock, loop_mock,
                                         init_host_mock):
        app.start()

        # Since we have created the service entry we call init_host with
        # added_to_cluster=True
        init_host_mock.assert_called_once_with(
            added_to_cluster=added_to_cluster)

        expected_target_calls = [mock.call(topic=self.topic, server=self.host)]
        expected_rpc_calls = [mock.call(target_mock.return_value, mock.ANY,
                                        mock.ANY),
                              mock.call().start()]

        if cluster and added_to_cluster:
            self.assertIsNotNone(app.cluster_rpcserver)
            expected_target_calls.append(mock.call(topic=self.topic,
                                                   server=cluster))
            expected_rpc_calls.extend(expected_rpc_calls[:])

        # Check that we create message targets for host and cluster
        target_mock.assert_has_calls(expected_target_calls)

        # Check we get and start rpc services for host and cluster
        rpc_mock.assert_has_calls(expected_rpc_calls)

        self.assertIsNotNone(app.rpcserver)

        app.stop()

    @mock.patch('cinder.objects.Service.get_minimum_obj_version',
                return_value='1.6')
    def test_start_rpc_and_init_host_no_cluster(self, is_upgrading_mock):
        """Test that without cluster we don't create rpc service."""
        app = service.Service.create(host=self.host, binary='cinder-volume',
                                     cluster=None, topic=self.topic)
        self._check_rpc_servers_and_init_host(app, True, None)

    @ddt.data('1.3', '1.7')
    @mock.patch('cinder.objects.Service.get_minimum_obj_version')
    def test_start_rpc_and_init_host_cluster(self, obj_version,
                                             get_min_obj_mock):
        """Test that with cluster we create the rpc service."""
        get_min_obj_mock.return_value = obj_version
        cluster = 'cluster'
        app = service.Service.create(host=self.host, binary='cinder-volume',
                                     cluster=cluster, topic=self.topic)
        self._check_rpc_servers_and_init_host(app, obj_version != '1.3',
                                              cluster)


class TestWSGIService(test.TestCase):

    def setUp(self):
        super(TestWSGIService, self).setUp()

    @mock.patch('oslo_service.wsgi.Loader')
    def test_service_random_port(self, mock_loader):
        test_service = service.WSGIService("test_service")
        self.assertEqual(0, test_service.port)
        test_service.start()
        self.assertNotEqual(0, test_service.port)
        test_service.stop()
        self.assertTrue(mock_loader.called)

    @mock.patch('oslo_service.wsgi.Loader')
    def test_reset_pool_size_to_default(self, mock_loader):
        test_service = service.WSGIService("test_service")
        test_service.start()

        # Stopping the service, which in turn sets pool size to 0
        test_service.stop()
        self.assertEqual(0, test_service.server._pool.size)

        # Resetting pool size to default
        test_service.reset()
        test_service.start()
        self.assertEqual(cfg.CONF.wsgi_default_pool_size,
                         test_service.server._pool.size)
        self.assertTrue(mock_loader.called)

    @mock.patch('oslo_service.wsgi.Loader')
    def test_workers_set_default(self, mock_loader):
        self.override_config('osapi_volume_listen_port',
                             CONF.test_service_listen_port)
        test_service = service.WSGIService("osapi_volume")
        self.assertEqual(processutils.get_worker_count(),
                         test_service.workers)
        self.assertTrue(mock_loader.called)

    @mock.patch('oslo_service.wsgi.Loader')
    def test_workers_set_good_user_setting(self, mock_loader):
        self.override_config('osapi_volume_listen_port',
                             CONF.test_service_listen_port)
        self.override_config('osapi_volume_workers', 8)
        test_service = service.WSGIService("osapi_volume")
        self.assertEqual(8, test_service.workers)
        self.assertTrue(mock_loader.called)

    @mock.patch('oslo_service.wsgi.Loader')
    def test_workers_set_zero_user_setting(self, mock_loader):
        self.override_config('osapi_volume_listen_port',
                             CONF.test_service_listen_port)
        self.override_config('osapi_volume_workers', 0)
        test_service = service.WSGIService("osapi_volume")
        # If a value less than 1 is used, defaults to number of procs
        # available
        self.assertEqual(processutils.get_worker_count(),
                         test_service.workers)
        self.assertTrue(mock_loader.called)

    @mock.patch('oslo_service.wsgi.Loader')
    def test_workers_set_negative_user_setting(self, mock_loader):
        self.override_config('osapi_volume_workers', -1)
        self.assertRaises(exception.InvalidInput,
                          service.WSGIService, "osapi_volume")
        self.assertTrue(mock_loader.called)

    @mock.patch('oslo_service.wsgi.Server')
    @mock.patch('oslo_service.wsgi.Loader')
    def test_ssl_enabled(self, mock_loader, mock_server):
        self.override_config('osapi_volume_use_ssl', True)

        service.WSGIService("osapi_volume")
        mock_server.assert_called_once_with(mock.ANY, mock.ANY, mock.ANY,
                                            port=mock.ANY, host=mock.ANY,
                                            use_ssl=True)

        self.assertTrue(mock_loader.called)


class OSCompatibilityTestCase(test.TestCase):
    def _test_service_launcher(self, fake_os):
        # Note(lpetrut): The cinder-volume service needs to be spawned
        # differently on Windows due to an eventlet bug. For this reason,
        # we must check the process launcher used.
        fake_process_launcher = mock.MagicMock()
        with mock.patch('os.name', fake_os):
            with mock.patch('cinder.service.process_launcher',
                            fake_process_launcher):
                launcher = service.get_launcher()
                if fake_os == 'nt':
                    self.assertEqual(service.Launcher, type(launcher))
                else:
                    self.assertEqual(fake_process_launcher(), launcher)

    def test_process_launcher_on_windows(self):
        self._test_service_launcher('nt')

    def test_process_launcher_on_linux(self):
        self._test_service_launcher('posix')
