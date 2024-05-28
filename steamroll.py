from json import JSONEncoder

import requests
import yaml
import click
import json
import os

from requests import HTTPError, RequestException

auth_headers = json.loads(os.environ.get('AUTH_HEADERS', '{}'))

class ScalarRef:
    def __init__(self, v=None, ref=None):
        self.v = v
        self.ref = ref

    def deref(self):
        print("deref",self.ref)
        return _lookup_child(self.ref[0], self.ref[1].split('.'))

    def __repr__(self):
        return "SR:"+str(self.v)

    def __eq__(self, other):
        return self.v == other.v if isinstance(other, ScalarRef) else self.v == other


yaml.add_representer(ScalarRef, lambda dumper, data: dumper.represent_sequence('ref', data.ref, flow_style=True) if data.ref else dumper.represent_data(data.v))

yaml.SafeLoader.add_constructor('ref', lambda loader, node: ScalarRef(ref=loader.construct_sequence(node)))

class SRJSONEncoder(JSONEncoder):
    def default(self, obj):
        if isinstance(obj, ScalarRef):
            return obj.deref()
        return super().default(obj)

class APILink:
    def __init__(self, api_base, headers, path=[], vars={}):
        if not api_base.startswith("http:") and not api_base.startswith("https:"):
            if api_base.startswith("localhost:"):
                api_base = "http://" + api_base
            else:
                api_base = "https://" + api_base
        self.api_base = api_base
        self.headers = headers
        self.path = path
        self.vars = vars

    def __str__(self):
        return "{}/api/v1/{}/".format(self.api_base, "/".join(self.fpath))

    @property
    def fpath(self):
        return (el.format(**self.vars) for el in self.path)

    def __truediv__(self, other):
        vars = self.vars
        if isinstance(other, str):
            other = [other]
        elif isinstance(other, tuple):
            varname, varvalue = other
            vars = dict(**vars, **{varname: varvalue})
            other = ['{' + varname + '}']
        return APILink(self.api_base, self.headers, self.path + other, vars)

    def fetch_single(self):
        return requests.get(self.__str__(), headers=self.headers).json()

    def fetch_all(self):
        results = []
        response = requests.get(self.__str__(), headers=self.headers).json()
        print(response)
        results.extend(response['results'])
        while response.get('next'):
            response = requests.get(response['next'], headers=self.headers).json()
            results.extend(response['results'])
        return results

    def json_request(self, method, body):
        res = None
        try:
            data = json.dumps(body, cls=SRJSONEncoder)
            res = requests.request(method, self.__str__(),
                  data=data,
                  headers={'Content-Type': 'application/json', 'Accept': 'application/json', **self.headers})
            res.raise_for_status()
            return res.json()
        except RequestException as e:
            print("URL: ",method, self.__str__())
            print("Sent: ",data)
            print("Got: ",res.text)
            raise

    def post(self, body):
        return self.json_request('POST', body)
    def patch(self, body):
        return self.json_request('PATCH', body)
    def put(self, body):
        return self.json_request('PUT', body)

def flatten(x):
    #if isinstance(x, list) and all(isinstance(y, list) for y in x):
        return [el for y in x for el in y]
    #return x

def _lookup_children(obj, path, assign_refs=False):
    if len(path) == 1:
        if path[0] == '*':
            if assign_refs:
                for i, el in enumerate(obj):
                    if not isinstance(el, ScalarRef): obj[i] = ScalarRef(el)
            return obj
        else:
            if assign_refs and not isinstance(obj[path[0]], ScalarRef): obj[path[0]] = ScalarRef(obj[path[0]])
            return [obj[path[0]]]
    if path[0] == '*': return flatten([_lookup_children(x, path[1:], assign_refs) for x in obj])
    #if isinstance(obj, list): return [x for y in obj for x in _lookup_children(y[path[0]], path[1:])]
    return _lookup_children(obj[path[0]], path[1:], assign_refs)

def _lookup_child(obj, path, assign=None):
    if len(path) < 2:
        #if assign:
        #    obj[path[0]] = assign
        #    return
        #if not isinstance(obj[path[0]], ScalarRef): obj[path[0]] = ScalarRef(obj[path[0]])
        return obj[path[0]]
    return _lookup_child(obj[path[0]], path[1:])

def _fixup_refs(obj, where, to_where, to_what):
    where = where.split('.')[1:]
    to_where = to_where.split('.')[1:]
    to_what = to_what.split('.')[1:]
    to_objs = _lookup_children(obj, to_where)

    from_obj = _lookup_children(obj, where, assign_refs=True)
    print(from_obj)
    for from_id in from_obj:
        try:
            ref = next(y for y in to_objs if _lookup_child(y, to_what) == from_id)
            from_id.ref = [ref, ".".join(to_what)]
        except StopIteration:
            pass


def _fetch_event(apiref):
    result = {
        'event': apiref.fetch_single(),
        'settings': (apiref / 'settings').fetch_single(),
        'taxrules': (apiref / 'taxrules').fetch_all(),
        'categories': (apiref / 'categories').fetch_all(),
        'items': (apiref / 'items').fetch_all(),
        'quotas': (apiref / 'quotas').fetch_all(),
        'item_meta_properties': (apiref / 'item_meta_properties').fetch_all(),
        'questions': (apiref / 'questions').fetch_all(),
        'vouchers': (apiref / 'vouchers').fetch_all(),
    }
    _fixup_refs(result, '.items.*.category', '.categories.*', '.id')
    _fixup_refs(result, '.quotas.*.items.*', '.items.*', '.id')
    if result['event']['has_subevents']:
        result['subevents'] = (apiref / 'subevents').fetch_all()
    return result

@click.group()
def cli():
    pass

@cli.group('event')
def cli_event():
    pass

@cli_event.command('fetch')
@click.option('--file')
@click.argument('base')
@click.argument('organizer')
@click.argument('event')
def fetch_event(base, organizer, event, file=None):
    apiref = APILink(base, auth_headers) / 'organizers' / ('organizer', organizer) / 'events' / ('event', event)
    result = _fetch_event(apiref)
    with open(file or ('_'.join(apiref.fpath) + '.yml'), 'w') as f:
        yaml.dump(result, f, sort_keys=False)


@cli_event.command('create')
@click.option('--force')
@click.option('--file')
@click.argument('base')
@click.argument('organizer')
@click.argument('event')
def create_event(base, organizer, event, force=False, file=None):
    apiref = APILink(base, auth_headers) / 'organizers' / ('organizer', organizer) / 'events' / ('event', event)
    create_apiref = APILink(base, auth_headers) / 'organizers' / ('organizer', organizer) / 'events'
    with open(file or ('_'.join(apiref.fpath) + '.yml'), 'r') as f:
        event_info = yaml.safe_load(f)
    print(event_info)

    if force:
        try:
            apiref.json_request('DELETE', {})
        except:
            pass

    event_info['event']['slug'] = event

    print(create_apiref.post({**event_info['event'], 'live': False}))

    (apiref / 'settings').patch(event_info['settings'])
    for cat in event_info['categories']:
        cat['id'] = (apiref / 'categories').post(cat)['id']
    for item in event_info['items']:
        item['id'] = (apiref / 'items').post(item)['id']
    for quota in event_info['quotas']:
        quota['id'] = (apiref / 'quotas').post(quota)['id']


if __name__ == '__main__':
    cli()