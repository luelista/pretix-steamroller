from json import JSONEncoder

import requests
import yaml
import click
import json
import os
import collections.abc

from requests import HTTPError, RequestException

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
        self._headers = headers
        self.path = path
        self.vars = vars

    def __str__(self):
        return "{}/api/v1/{}/".format(self.api_base, "/".join(self.fpath))

    @property
    def fpath(self):
        return (el.format(**self.vars) for el in self.path)

    @property
    def headers(self):
        return self._headers[self.api_base]

    def __truediv__(self, other):
        vars = self.vars
        if isinstance(other, str):
            other = [other]
        elif isinstance(other, tuple):
            varname, varvalue = other
            vars = dict(**vars, **{varname: varvalue})
            other = ['{' + varname + '}']
        return APILink(self.api_base, self._headers, self.path + other, vars)

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

def _force_type(o, idx, type, constructor):
    try:
        if isinstance(o[idx], type): return o[idx]
    except (KeyError, IndexError):
        pass
    return constructor()


def _deep_update(d, u):
    print("D-U",d,u)
    for k, v in u.items() if isinstance(u, collections.abc.Mapping) else (enumerate(u)):
        if isinstance(v, collections.abc.Mapping):
            d[k] = _deep_update(_force_type(d, k, collections.abc.Mapping, dict), v)
        elif isinstance(v, list):
            d[k] = _deep_update(_force_type(d, k, list, list), v)
        else:
            if isinstance(d, list) and len(d) <= k:
                d.append(v)
            else:
                d[k] = v
    return d

def _flatten(x):
    return [el for y in x for el in y]

def _lookup_children(obj, path, assign_refs=False, ignore_key_errors=False):
    if path[0] != '*':
        try:
            child = obj[path[0]]
        except KeyError:
            if ignore_key_errors:
                return []
            else:
                raise
        if len(path) == 1:
            if assign_refs and not isinstance(child, ScalarRef): child = obj[path[0]] = ScalarRef(child)
            return [child]
        else:
            return _lookup_children(child, path[1:], assign_refs, ignore_key_errors)
    else:
        if len(path) == 1:
            if assign_refs:
                for i, el in enumerate(obj):
                    if not isinstance(el, ScalarRef): obj[i] = ScalarRef(el)
            return obj
        return _flatten([_lookup_children(x, path[1:], assign_refs, ignore_key_errors) for x in obj])

def _lookup_child(obj, path):
    if len(path) < 2:
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

def _without_keys(d, keys):
    return {x: d[x] for x in d if x not in keys}

def _is_dict_subset(subset, superset):
    if not isinstance(subset, dict) or not isinstance(superset, dict):
        return False
    for key, value in subset.items():
        if superset.get(key) != value:
            return False
    return True

def _kill_defaults(obj, default_file):
    for path, defaults in default_file.items():
        for victim in _lookup_children(obj, path.split('.')[1:], ignore_key_errors=True):
            for key, value in defaults.items():
                if victim.get(key) == value or _is_dict_subset(victim.get(key), value):
                    del victim[key]

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
    _fixup_refs(result, '.items.*.addons.*.addon_category', '.categories.*', '.id')
    _fixup_refs(result, '.quotas.*.items.*', '.items.*', '.id')
    _fixup_refs(result, '.quotas.*.variations.*', '.items.*.variations.*', '.id')
    _fixup_refs(result, '.vouchers.*.item', '.items.*', '.id')
    _fixup_refs(result, '.vouchers.*.variation', '.items.*.variations.*', '.id')
    _fixup_refs(result, '.questions.*.items.*', '.items.*', '.id')
    _fixup_refs(result, '.questions.*.dependency_question', '.questions.*', '.id')
    _fixup_refs(result, '.categories.*.cross_selling_match_products.*', '.items.*', '.id')

    if result['event']['has_subevents']:
        result['subevents'] = (apiref / 'subevents').fetch_all()
    return result

def _read_yaml(filename):
    with open(filename, 'r') as f:
        return yaml.safe_load(f)

@click.group()
def cli():
    pass

@cli.group('event')
def cli_event():
    pass

@cli_event.command('fetch')
@click.option('--file')
@click.option('--keep-defaults')
@click.argument('base')
@click.argument('organizer')
@click.argument('event')
def fetch_event(base, organizer, event, file=None, keep_defaults=False):
    apiref = APILink(base, auth_headers) / 'organizers' / ('organizer', organizer) / 'events' / ('event', event)
    result = _fetch_event(apiref)
    if not keep_defaults:
        _kill_defaults(result, _read_yaml('defaults.yml'))
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
    event_info = _read_yaml(file or ('_'.join(apiref.fpath) + '.yml'))
    print(event_info)

    if force:
        try:
            apiref.json_request('DELETE', {})
        except:
            pass

    event_info['event']['slug'] = event

    print(create_apiref.post({**event_info['event'], 'live': False}))

    (apiref / 'settings').patch(event_info['settings'])
    for item_meta_property in event_info['item_meta_properties']:
        _deep_update(item_meta_property, (apiref / 'item_meta_properties').post(item_meta_property))
    for cat in event_info['categories']:
        _deep_update(cat, (apiref / 'categories').post(cat))
    for item in event_info['items']:
        _deep_update(item, (apiref / 'items').post(item))
    for quota in event_info['quotas']:
        _deep_update(quota, (apiref / 'quotas').post(quota))
    for voucher in event_info['vouchers']:
        _deep_update(voucher, (apiref / 'vouchers').post(voucher))

    for question in event_info['questions']:
        question['id'] = (apiref / 'questions').post(_without_keys(question, {"dependency_question", "dependency_value"}))['id']
    for question in event_info['questions']:
        if question.get("dependency_question"):
            _deep_update(question, (apiref / 'questions' / ('question', question['id'])).patch({"dependency_question": question["dependency_question"]}))


auth_headers = _read_yaml('auth.yml')

if __name__ == '__main__':
    cli()