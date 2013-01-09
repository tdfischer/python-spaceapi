import requests
import json
import socket
import time
import urlparse
import dns.resolver
import warnings
import os
from bs4 import BeautifulSoup

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

class DiscoveryError(APIError):
    pass

class URLError(APIError):
  pass

class API(object):
    def __init__(self, url, verify=True, timeout=None):
        assert(isinstance(url, basestring))
        self._url = urlparse.urlparse(url)
        if len(self._url.netloc) == 0:
          raise URLError
        self._cache = None
        self._verify = verify
        self._timeout = timeout

    def __repr__(self):
        return "API('%s')"%(self._url)

    def __str__(self):
        return repr(self)

    @property
    def _data(self):
      if self._cache is None:
        self.load()
      return self._cache

    def load(self, timeout=None):
        if timeout is None:
          timeout = self._timeout
        try:
            page = requests.get(self._url.geturl(), verify=self._verify, timeout=timeout)
            if page.json:
              self._cache = page.json
            elif 'text/html' in page.headers['content-type']:
              dom = BeautifulSoup(page.text)
              meta = dom.find("link", rel="space-api")
              if meta:
                url = urlparse.urlparse(meta['href'])
                if url.scheme == "":
                  url = urlparse.urlparse("%s://%s%s"%(self._url.scheme, self._url.netloc, meta['href']))
                self._cache = requests.get(url.geturl(), verify=self._verify, timeout=timeout).json
            if not self._cache:
              data = requests.get("%s://%s/status.json"%(self._url.scheme, self._url.netloc), verify=self._verify, timeout=timeout).json
              data = None
              if data:
                self._cache = data
              else:
                host = self._url.netloc.split(':')[0]
                try:
                  answer = dns.resolver.query("default._spaceapis._tcp", "SRV")[0]
                except dns.resolver.NXDOMAIN:
                  raise DiscoveryError
                try:
                  meta = dns.resolver.query("default._spaceapis._tcp", "TXT")
                except dns.resolver.NXDOMAIN:
                  raise DiscoveryError
                path = "/"
                for m in meta:
                    for s in m.strings:
                        key, value = s.split('=')
                        if key == 'path':
                            path = value
                url = "https://%s:%s%s"%(answer.target, answer.port, path)
                self._cache = requests.get(url, verify=self._verify, timeout=timeout).json

        except requests.exceptions.ConnectionError:
            raise ConnectError
        except socket.timeout:
            raise ConnectError
        except requests.exceptions.Timeout:
            raise ConnectError
        if self._cache is None:
            raise ParseError

    @property
    def apiurl(self):
        return self._url.geturl()

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

    def __init__(self, cache=True):
        self.loop = DBusGMainLoop()
        self.bus = dbus.SystemBus(mainloop=self.loop)
        self._pending = 0
        self._shouldCache = cache
        self._cache = {}

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

    def directory(self, verify=False, progress=None, timeout=None):
        ret = []
        req = requests.get("http://hackerspaces.org/wiki/Special:Ask/-5B-5BCategory:Hackerspace-5D-5D-5B-5Bhackerspace-20status::active-5D-5D/-3FWebsite/mainlabel%3Dhackerspace/order%3DDESC/sort%3D/limit%3D500/format%3Djson").json

        if self._shouldCache:
          try:
            self._cache = json.load(open(os.path.expanduser("~/.cache/spaceapi-directory"), 'r'))
          except IOError:
            pass
          except ValueError:
            pass
        if len(self._cache) == 0and self._shouldCache:
          print "Starting with a blank cache. This could take a while."
        total = len(req['items'])
        num = -1
        for meta in req['items']:
            num += 1 
            if 'website' in meta:
              try:
                api = API(meta['website'], verify=verify, timeout=timeout)
              except URLError:
                continue
              if self._shouldCache:
                if meta['website'] in self._cache:
                  if self._cache[meta['website']]['stamp'] < time.time()-3600:
                    del self._cache[meta['website']]
                if meta['website'] not in self._cache:
                  self._cache[meta['website']] = {'stamp': time.time(), 'valid': True}
                if self._cache[meta['website']]['valid']:
                  try:
                    api.load()
                    ret.append(api)
                    if progress:
                      progress(num, total, api.apiurl, True)
                  except APIError:
                    self._cache[meta['website']]['valid'] = False
                    if progress:
                      progress(num, total, api.apiurl, False)
                  json.dump(self._cache, open(os.path.expanduser("~/.cache/spaceapi-directory"), 'w'))
              else:
                ret.append(api)

        return ret

    def discover(self, verify=False, timeout=None):
        ret = []
        if _hasAvahi:
            self._discoverLoop = gobject.MainLoop()
            self._discoverLoop.run()
            for res in self.results:
                ret.append(API(res, verify=verify, timeout=timeout))
        else:
          raise DiscoverError, "Avahi not found."
        try:
          ret.append(self.defaultAPI(verify))
        except DiscoveryError:
          pass
        return ret

    def defaultAPI(self, verify=False):
        try:
          answer = dns.resolver.query("default._spaceapis._tcp", "SRV")[0]
        except dns.resolver.NXDOMAIN:
          raise DiscoveryError
        try:
          meta = dns.resolver.query("default._spaceapis._tcp", "TXT")
        except dns.resolver.NXDOMAIN:
          meta = []
        path = "/"
        for m in meta:
            for s in m.strings:
                key, value = s.split('=')
                if key == 'path':
                    path = value
        url = "https://%s:%s%s"%(answer.target, answer.port, path)
        return API(url, verify=verify)


    def all(self):
        ret = self.directory()
        try:
          ret += self.discover()
        except:
          pass

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
    def progress(num, total, url, success):
      if success:
        status = "+"
      else:
        status = "-"
      print "%d/%d %s %s"%(num, total, status, url)
    browser = Browser()
    for space in browser.directory(timeout=1, verify=False, progress=progress):
        try:
            print "%s: %s running %s"%(space.name, space._url, space._data['api'])
        except ConnectError:
            print "Could not load", space.apiurl
        except DiscoveryError:
            print "Could not discover API for", space.apiurl
        except APIError:
            print "Unknown error with", space.apiurl
        except KeyError:
            pass
