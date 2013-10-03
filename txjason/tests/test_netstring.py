import json

from twisted.internet import defer, task
from twisted.trial import unittest
from twisted.test import proto_helpers
from txjason.netstring import JSONRPCClientFactory, JSONRPCServerFactory
from txjason import client, handler

from common import TXJasonTestCase


def readNetstring(string):
    prefix, sep, rest = string.partition(':')
    if not sep or len(rest) != int(prefix) + 1:
        raise ValueError('not a valid netstring')
    return rest[:-1]


def makeNetstring(string):
    return '%d:%s,' % (len(string), string)


class TestHandler(handler.Handler):
    @handler.exportRPC()
    def add(self, x, y):
        return x + y


class FakeReactor(object):
    def connectTCP(self, host, port, factory):
        proto = factory.buildProtocol(host)
        proto.makeConnection(proto_helpers.StringTransport())


class FakeError(Exception):
    pass


class FakeEndpoint(object):
    def __init__(self, deferred=None, fail=False):
        self.deferred = deferred
        self.fail = fail
        self.connected = False

    def connect(self, fac):
        if self.deferred:
            return self.deferred
        if self.fail:
            return defer.fail(FakeError())
        self.proto = fac.buildProtocol(None)
        self.transport = proto_helpers.StringTransport()
        self.proto.makeConnection(self.transport)
        self.connected = True
        return defer.succeed(self.proto)

    def disconnect(self, reason):
        self.connected = False
        self.proto.connectionLost(reason)
        self.proto = self.transport = None


class ServerTestCase(TXJasonTestCase):
    def setUp(self):
        self.factory = JSONRPCServerFactory()
        self.factory.addHandler(TestHandler(), 'foo')
        self.proto = self.factory.buildProtocol(('127.0.0.1', 0))
        self.tr = proto_helpers.StringTransport()
        self.proto.makeConnection(self.tr)
        self.client = client.JSONRPCClient()

    def _test(self, request, expected):
        request = makeNetstring(request)
        self.proto.dataReceived(request)
        self.assertEqual(self.tr.value(), expected)

    def test_request(self):
        request = self.client._getPayload('foo.add', 'X', 1, 2)
        self._test(request, '42:{"jsonrpc": "2.0", "result": 3, "id": "X"},')

    def test_notification(self):
        request = self.client._getPayload('foo.add', None, 1, 2)
        self._test(request, '')

    def test_error(self):
        request = self.client._getPayload('add', 'X', 1, 2)
        self._test(request, '87:{"jsonrpc": "2.0", "id": "X", "error": {"message": "Method not found", "code": -32601}},')


class ClientTestCase(TXJasonTestCase):
    def setUp(self):
        self.reactor = task.Clock()
        self.endpoint = FakeEndpoint()
        self.factory = JSONRPCClientFactory(
            self.endpoint, _reactor=self.reactor)

    def test_callRemote(self):
        self.assertFalse(self.endpoint.connected)
        d = self.factory.callRemote('spam')
        self.assert_(self.endpoint.connected)
        self.assertEqual(
            json.loads(readNetstring(self.endpoint.transport.value())),
            {'params': [], 'jsonrpc': '2.0', 'method': 'spam', 'id': 1})
        self.endpoint.proto.stringReceived(json.dumps(
            {'jsonrpc': '2.0', 'id': 1, 'result': 'eggs'}))
        d.addCallback(self.assertEqual, 'eggs')
        return d

    def test_callRemote_error_response(self):
        d = self.factory.callRemote('spam')
        self.endpoint.proto.stringReceived(json.dumps(
            {'jsonrpc': '2.0', 'id': 1, 'error': {
                'message': 'error', 'code': -19}}))
        return self.assertFailure(d, client.JSONRPCClientError)

    def test_notifyRemote(self):
        self.assertFalse(self.endpoint.connected)
        self.factory.notifyRemote('spam')
        self.assert_(self.endpoint.connected)
        self.assertEqual(
            json.loads(readNetstring(self.endpoint.transport.value())),
            {'params': [], 'jsonrpc': '2.0', 'method': 'spam'})

    def test_callRemote_connection_failure(self):
        self.assertFalse(self.endpoint.connected)
        self.endpoint.fail = True
        d = self.factory.callRemote('spam')
        self.assertEqual(len(self.flushLoggedErrors(FakeError)), 1)
        return self.assertFailure(d, FakeError)

    def test_notifyRemote_connection_failure(self):
        self.assertFalse(self.endpoint.connected)
        self.endpoint.fail = True
        d = self.factory.notifyRemote('spam')
        self.assertEqual(len(self.flushLoggedErrors(FakeError)), 1)
        return self.assertFailure(d, FakeError)

    def test_notifyRemote_two_connection_failures(self):
        self.assertFalse(self.endpoint.connected)
        self.endpoint.fail = True
        d1 = self.factory.notifyRemote('spam')
        d2 = self.factory.notifyRemote('spam')
        self.assertEqual(len(self.flushLoggedErrors(FakeError)), 2)
        return defer.gatherResults([
            self.assertFailure(d1, FakeError),
            self.assertFailure(d2, FakeError),
        ])

    def test_notifyRemote_two_pending_connection_failures(self):
        self.assertFalse(self.endpoint.connected)
        self.endpoint.deferred = defer.Deferred()
        d1 = self.factory.notifyRemote('spam')
        d2 = self.factory.notifyRemote('spam')
        self.endpoint.deferred.errback(FakeError())
        self.assertEqual(len(self.flushLoggedErrors(FakeError)), 1)
        return defer.gatherResults([
            self.assertFailure(d1, FakeError),
            self.assertFailure(d2, FakeError),
        ])

    def test_callRemote_cancellation_during_connection(self):
        self.assertFalse(self.endpoint.connected)
        canceled = []
        self.endpoint.deferred = defer.Deferred(canceled.append)
        d = self.factory.callRemote('spam')
        d.cancel()
        self.assert_(canceled)
        self.assertEqual(len(self.flushLoggedErrors(defer.CancelledError)), 1)
        return self.assertFailure(d, defer.CancelledError)

    def test_callRemote_cancellation_during_request(self):
        self.assertFalse(self.endpoint.connected)
        d = self.factory.callRemote('spam')
        d.cancel()
        return self.assertFailure(d, defer.CancelledError)

    def test_notifyRemote_cancellation_during_connection(self):
        self.assertFalse(self.endpoint.connected)
        canceled = []
        self.endpoint.deferred = defer.Deferred(canceled.append)
        d = self.factory.notifyRemote('spam')
        d.cancel()
        self.assert_(canceled)
        self.assertEqual(len(self.flushLoggedErrors(defer.CancelledError)), 1)
        return self.assertFailure(d, defer.CancelledError)

    def test_reconnection(self):
        self.assertFalse(self.endpoint.connected)
        self.factory.notifyRemote('spam')
        self.assert_(self.endpoint.connected)
        self.endpoint.disconnect(FakeError())
        self.assertFalse(self.endpoint.connected)
        self.assertEqual(len(self.flushLoggedErrors(FakeError)), 1)
        self.factory.notifyRemote('eggs')
        self.assert_(self.endpoint.connected)
        self.assertEqual(
            json.loads(readNetstring(self.endpoint.transport.value())),
            {'params': [], 'jsonrpc': '2.0', 'method': 'eggs'})
