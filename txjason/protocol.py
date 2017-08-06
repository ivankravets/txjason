from twisted.internet import defer, protocol
import service


class BaseServerFactory(protocol.ServerFactory):

    def __init__(self, seperator='.', timeout=None):
        self.service = service.JSONRPCService(timeout)
        self.seperator = seperator

    def buildProtocol(self, addr):
        return self.protocol(self.service)

    def addHandler(self, handler, namespace=None):
        handler.addToService(
            self.service, namespace=namespace, seperator=self.seperator)


class BaseClientFactory(protocol.ClientFactory):
    pass


class JSONRPCServerProtocol(protocol.Protocol):

    def __init__(self, service):
        self.service = service

    @defer.inlineCallbacks
    def dataReceived(self, data):
        result = yield self.service.call(data)
        if result is not None:
            self.transport.write(result)


class JSONRPCServerFactory(BaseServerFactory):
    protocol = JSONRPCServerProtocol
