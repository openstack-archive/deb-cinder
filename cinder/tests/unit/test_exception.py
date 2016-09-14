
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

from cinder import exception
from cinder import test

import mock
import six
import webob.util


class ExceptionTestCase(test.TestCase):
    @staticmethod
    def _raise_exc(exc):
        raise exc()

    def test_exceptions_raise(self):
        # NOTE(dprince): disable format errors since we are not passing kwargs
        self.flags(fatal_exception_format_errors=False)
        for name in dir(exception):
            exc = getattr(exception, name)
            if isinstance(exc, type):
                self.assertRaises(exc, self._raise_exc, exc)


class CinderExceptionTestCase(test.TestCase):
    def test_default_error_msg(self):
        class FakeCinderException(exception.CinderException):
            message = "default message"

        exc = FakeCinderException()
        self.assertEqual('default message', six.text_type(exc))

    def test_error_msg(self):
        self.assertEqual('test',
                         six.text_type(exception.CinderException('test')))

    def test_default_error_msg_with_kwargs(self):
        class FakeCinderException(exception.CinderException):
            message = "default message: %(code)s"

        exc = FakeCinderException(code=500)
        self.assertEqual('default message: 500', six.text_type(exc))

    def test_error_msg_exception_with_kwargs(self):
        # NOTE(dprince): disable format errors for this test
        self.flags(fatal_exception_format_errors=False)

        class FakeCinderException(exception.CinderException):
            message = "default message: %(misspelled_code)s"

        exc = FakeCinderException(code=500)
        self.assertEqual('default message: %(misspelled_code)s',
                         six.text_type(exc))

    def test_default_error_code(self):
        class FakeCinderException(exception.CinderException):
            code = 404

        exc = FakeCinderException()
        self.assertEqual(404, exc.kwargs['code'])

    def test_error_code_from_kwarg(self):
        class FakeCinderException(exception.CinderException):
            code = 500

        exc = FakeCinderException(code=404)
        self.assertEqual(404, exc.kwargs['code'])

    def test_error_msg_is_exception_to_string(self):
        msg = 'test message'
        exc1 = Exception(msg)
        exc2 = exception.CinderException(exc1)
        self.assertEqual(msg, exc2.msg)

    def test_exception_kwargs_to_string(self):
        msg = 'test message'
        exc1 = Exception(msg)
        exc2 = exception.CinderException(kwarg1=exc1)
        self.assertEqual(msg, exc2.kwargs['kwarg1'])

    def test_message_in_format_string(self):
        class FakeCinderException(exception.CinderException):
            message = 'FakeCinderException: %(message)s'

        exc = FakeCinderException(message='message')
        self.assertEqual('FakeCinderException: message', six.text_type(exc))

    def test_message_and_kwarg_in_format_string(self):
        class FakeCinderException(exception.CinderException):
            message = 'Error %(code)d: %(message)s'

        exc = FakeCinderException(message='message', code=404)
        self.assertEqual('Error 404: message', six.text_type(exc))

    def test_message_is_exception_in_format_string(self):
        class FakeCinderException(exception.CinderException):
            message = 'Exception: %(message)s'

        msg = 'test message'
        exc1 = Exception(msg)
        exc2 = FakeCinderException(message=exc1)
        self.assertEqual('Exception: test message', six.text_type(exc2))


class CinderConvertedExceptionTestCase(test.TestCase):
    def test_default_args(self):
        exc = exception.ConvertedException()
        self.assertNotEqual('', exc.title)
        self.assertEqual(500, exc.code)
        self.assertEqual('', exc.explanation)

    def test_standard_status_code(self):
        with mock.patch.dict(webob.util.status_reasons, {200: 'reason'}):
            exc = exception.ConvertedException(code=200)
            self.assertEqual('reason', exc.title)

    @mock.patch.dict(webob.util.status_reasons, {500: 'reason'})
    def test_generic_status_code(self):
        with mock.patch.dict(webob.util.status_generic_reasons,
                             {5: 'generic_reason'}):
            exc = exception.ConvertedException(code=599)
            self.assertEqual('generic_reason', exc.title)
