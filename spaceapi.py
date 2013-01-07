import requests
import warnings

try:
    import dbus, gobject, avahi
    from dbus import DBusException
    from dbus.mainloop.glib import DBusGMainLoop
    _hasAvahi = True
except:
    warnings.warn("Avahi support not found. Discovery will not function.", RuntimeWarning)
    _hasAvahi = False

class APIError(Exception):
    pass

class ConnectError(APIError):
    pass

class ParseError(APIError):
    pass

class API(object):
    def __init__(self, url):
        self._url = url
        self._cache = None

    def __repr__(self):
        return "API('%s')"%(self._url)

    def __str__(self):
        return repr(self)

    @property
    def _data(self):
        if self._cache is None:
            try:
                self._cache = requests.get(self._url).json
            except requests.exceptions.ConnectionError:
                raise ConnectError
        if self._cache is None:
            raise ParseError
        return self._cache

    @property
    def apiurl(self):
        return self._url

    @property
    def address(self):
        return self._data['address']

    @property
    def name(self):
        return self._data['space']

    @property
    def logo(self):
        return self._data['logo']

class Browser(object):

    def __init__(self):
        self.loop = DBusGMainLoop()
        self.bus = dbus.SystemBus(mainloop=self.loop)
        self._pending = 0

        self.server = dbus.Interface(
            self.bus.get_object(
                avahi.DBUS_NAME,
                '/'
            ),
            'org.freedesktop.Avahi.Server'
        )
        self.dbrowser = dbus.Interface(
            self.bus.get_object(
                avahi.DBUS_NAME,
                self.server.DomainBrowserNew(avahi.IF_UNSPEC, avahi.PROTO_UNSPEC, "", avahi.DOMAIN_BROWSER_BROWSE, dbus.UInt32(0))
            ),
            avahi.DBUS_INTERFACE_DOMAIN_BROWSER
        )
        self._started()
        self.dbrowser.connect_to_signal('ItemNew', self._new_domain)
        self.dbrowser.connect_to_signal('AllForNow', self._done)
        self.dbrowser.connect_to_signal('Failure', self._done)
        self.results = []

    def _new_domain(self, interface, protocol, domain, flags):
        for type in ('_spaceapis._tcp', '_spaceapi._tcp'):
            sbrowser = dbus.Interface(
                self.bus.get_object(
                    avahi.DBUS_NAME,
                    self.server.ServiceBrowserNew(
                        avahi.IF_UNSPEC,
                        avahi.PROTO_UNSPEC,
                        type,
                        domain,
                        dbus.UInt32(0)
                    )
                ),
                avahi.DBUS_INTERFACE_SERVICE_BROWSER
            )
            self._started()
            sbrowser.connect_to_signal('ItemNew', self._handler)
            sbrowser.connect_to_signal('AllForNow', self._done)
            sbrowser.connect_to_signal('Failure', self._done)

    def _started(self):
        self._pending += 1

    def _done(self, *args):
        self._pending -= 1
        if self._pending == 0:
            self._discoverLoop.quit()

    def _service_resolved(self, interface, protocol, name, type, domain, host, aprotocol, address, port, txt, flags):
        txt = avahi.txt_array_to_string_array(txt)
        meta = {'path': '/'}
        for t in txt:
            key, value = t.split('=')
            meta[key] = value
        self._done()
        self.results.append("https://%s:%s%s"%(host, port, meta['path']))

    def directory(self):
        ret = []
        for api in requests.get("http://openspace.slopjong.de/directory.json").json.iteritems():
            ret.append(API(api[1]))
        return ret

    def discover(self):
        ret = []
        if _hasAvahi:
            self._discoverLoop = gobject.MainLoop()
            self._discoverLoop.run()
            for res in self.results:
                ret.append(API(res))
        return ret

    def all(self):
        return self.directory() + self.discover()

    def _print_error(self, *args):
        self._done()
        print args

    def _handler(self, interface, protocol, name, stype, domain, flags):
        self._started()
        self.server.ResolveService(
            interface,
            protocol,
            name,
            stype,
            domain,
            avahi.PROTO_UNSPEC,
            dbus.UInt32(0),
            reply_handler=self._service_resolved,
            error_handler=self._print_error
        )

if __name__ == "__main__":
    browser = Browser()
    for space in browser.all():
        try:
            print "%s: %s"%(space.name, space.address)
        except ConnectError:
            print "Could not load", space.apiurl
        except APIError:
            print "Unknown error with", space.apiurl
        except KeyError:
            pass
