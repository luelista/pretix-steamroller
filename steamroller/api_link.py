import json
from copy import deepcopy

import requests
from bs4 import BeautifulSoup
from requests import RequestException
from .scalar_ref import SRJSONEncoder

def print_request_error(e: RequestException):
    print("Error: ", str(e))
    print("URL:   ", e.request.method, e.request.url)
    if hasattr(e.request, 'data'): print("Sent:  ", e.request.data)
    if e.response:
        print("Got:   ", e.response.status_code, '>>>'+e.response.text[:300]+'<<<')

class APILink:
    link_format = "{}/api/v1/{}/"
    def __init__(self, api_base, headers, path=[], vars={}):
        if not api_base.startswith("http:") and not api_base.startswith("https:"):
            if api_base.startswith("localhost:"):
                api_base = "http://" + api_base
            else:
                api_base = "https://" + api_base
        self.api_base = api_base
        self._headers = headers
        self.path = path
        self.vars = vars
        self.request_kwargs = {'verify': False}

    def __str__(self):
        return self.link_format.format(self.api_base, "/".join(self.fpath))

    @property
    def fpath(self):
        return (el.format(**self.vars) for el in self.path)

    @property
    def headers(self):
        try:
            return self._headers[self.api_base + '/' + self.vars['organizer']]
        except:
            return self._headers[self.api_base]

    def __truediv__(self, other):
        vars = self.vars
        if isinstance(other, str):
            other = [other]
        elif isinstance(other, tuple):
            varname, varvalue = other
            vars = dict(**vars, **{varname: varvalue})
            other = ['{' + varname + '}']
        new = APILink(self.api_base, self._headers, self.path + other, vars)
        new.link_format = self.link_format
        return new

    def __floordiv__(self, other):
        new = deepcopy(self)
        new.path = []
        return new / other

    def with_(self, link_format):
        l = deepcopy(self)
        l.link_format = link_format
        return l

    def _do_get_request(self, url=None):
        res = None
        try:
            res = requests.get(url or self.__str__(), headers=self.headers, **self.request_kwargs)
            res.raise_for_status()
            return res
        except RequestException as e:
            print("Error: ", str(e))
            print("URL:   ", 'GET', self.__str__())
            if res:
                print("Got:   ", res.status_code, res.text)
            raise

    def get_html(self):
        return BeautifulSoup(self._do_get_request().text, 'html.parser')

    def fetch_single(self):
        return self._do_get_request().json()

    def fetch_all(self):
        results = []
        response = self._do_get_request().json()
        results.extend(response['results'])
        while response.get('next'):
            response = self._do_get_request(response['next']).json()
            results.extend(response['results'])
        return results

    def _do_form_request(self, method, body):
        try:
            res = requests.request(method, self.__str__(), data=body, headers=self.headers, **self.request_kwargs)
            res.raise_for_status()
            return res.json()
        except RequestException as e:
            print_request_error(e)
            raise

    def _do_json_request(self, method, body):
        data = json.dumps(body, cls=SRJSONEncoder)
        res = requests.request(method, self.__str__(), data=data,
              headers={'Content-Type': 'application/json', 'Accept': 'application/json', **self.headers})
        res.raise_for_status()
        return res.json()

    def post(self, body):
        return self._do_json_request('POST', body)
    def patch(self, body):
        return self._do_json_request('PATCH', body)
    def put(self, body):
        return self._do_json_request('PUT', body)
    def delete(self):
        res = requests.request('DELETE', self.__str__(),
              headers={'Content-Type': 'application/json', 'Accept': 'application/json', **self.headers})
        res.raise_for_status()

