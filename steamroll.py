import os.path
from base64 import b64encode
from copy import deepcopy
from json import JSONEncoder
from bs4 import BeautifulSoup

import requests
import yaml
import click
import json
import collections.abc

from requests import RequestException

ref_root = None

class ScalarRef:
    def __init__(self, v=None, ref=None):
        self.v = v
        self.ref = ref

    def deref(self):
        return _lookup_child(ref_root, self.ref)

    def __repr__(self):
        return "SR:"+str(self.v)

    def __eq__(self, other):
        return self.v == other.v if isinstance(other, ScalarRef) else self.v == other


class MyDumper(yaml.SafeDumper):
    # HACK: insert blank lines between top-level objects
    # inspired by https://stackoverflow.com/a/44284819/3786245
    def write_line_break(self, data=None):
        super().write_line_break(data)

        if len(self.indents) == 1:
            super().write_line_break()

MyDumper.add_representer(ScalarRef, lambda dumper, data: dumper.represent_sequence('ref', data.ref, flow_style=True) if data.ref else dumper.represent_data(data.v))

yaml.SafeLoader.add_constructor('ref', lambda loader, node: ScalarRef(ref=loader.construct_sequence(node)))


class SRJSONEncoder(JSONEncoder):
    def default(self, obj):
        if isinstance(obj, ScalarRef):
            return obj.deref()
        return super().default(obj)

def _print_request_error(e: RequestException):
    print("Error: ", str(e))
    print("URL:   ", e.request.method, e.request.url)
    if hasattr(e.request, 'data'): print("Sent:  ", e.request.data)
    print("Got:   ", e.response.status_code, e.response.text)

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

    def __str__(self):
        return self.link_format.format(self.api_base, "/".join(self.fpath))

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
        new = APILink(self.api_base, self._headers, self.path + other, vars)
        new.link_format = self.link_format
        return new

    def __floordiv__(self, other):
        new = deepcopy(self)
        new.path = []
        return new / other

    def with_(self, link_format):
        l = deepcopy(self)
        l.link_format = '{}/control/{}'
        return l

    def _do_get_request(self, url=None):
        try:
            res = requests.get(url or self.__str__(), headers=self.headers)
            res.raise_for_status()
            return res
        except RequestException as e:
            print("Error: ", str(e))
            print("URL:   ", 'GET', self.__str__())
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

    def _do_json_request(self, method, body):
        res = None
        try:
            data = json.dumps(body, cls=SRJSONEncoder)
            res = requests.request(method, self.__str__(),
                  data=data,
                  headers={'Content-Type': 'application/json', 'Accept': 'application/json', **self.headers})
            res.raise_for_status()
            return res.json()
        except RequestException as e:
            _print_request_error(e)
            raise

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


def _force_type(o, idx, type, constructor):
    try:
        if isinstance(o[idx], type): return o[idx]
    except (KeyError, IndexError):
        pass
    return constructor()


def _deep_update(d, u):
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

def _kv(enumerable):
    return enumerable.items() if hasattr(enumerable, 'items') else enumerate(enumerable)

def _lookup_children(obj, path, assign_refs=False, ignore_key_errors=False, with_path=None, delete=False):
    if isinstance(path, str): path = path.split('.')[1:]
    if path[0] != '*':
        ignore_key_errors_local = False
        if path[0].endswith('?'):
            path[0] = path[0][:-1]
            ignore_key_errors_local = True
        try:
            child = obj[path[0]]
        except KeyError:
            if ignore_key_errors or ignore_key_errors_local:
                return []
            else:
                raise
        if len(path) == 1:
            if delete:
                del obj[path[0]]
                return []
            if assign_refs and not isinstance(child, ScalarRef): child = obj[path[0]] = ScalarRef(child)
            return [(with_path + [path[0]], child)] if with_path is not None else [child]
        else:
            return _lookup_children(child, path[1:], assign_refs, ignore_key_errors, with_path + [path[0]] if with_path is not None else None, delete)
    else:
        if len(path) == 1:
            if delete:
                obj[:] = []
            if assign_refs:
                for i, el in _kv(obj):
                    if not isinstance(el, ScalarRef): obj[i] = ScalarRef(el)
            return [(with_path + [i], child) for i, child in _kv(obj)] if with_path is not None else obj
        return _flatten([_lookup_children(x, path[1:], assign_refs, ignore_key_errors, with_path + [i] if with_path is not None else None, delete) for i, x in _kv(obj)])

def _lookup_child(obj, path):
    if len(path) < 2:
        return obj[path[0]]
    return _lookup_child(obj[path[0]], path[1:])

def _fixup_refs(obj, where, to_where, to_what):
    to_what = to_what.split('.')[1:]
    to_objs = _lookup_children(obj, to_where, with_path=[])
    from_obj = _lookup_children(obj, where, assign_refs=True)
    for from_id in from_obj:
        try:
            path, ref = next((path, y) for (path, y) in to_objs if _lookup_child(y, to_what) == from_id)
            from_id.ref = path + to_what
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
        for victim in _lookup_children(obj, path, ignore_key_errors=True):
            for key, value in defaults.items():
                if key in victim and (victim.get(key) == value or _is_dict_subset(victim.get(key), value)):
                    del victim[key]

def _extract_form_value(form, filter_keys={'csrfmiddlewaretoken'}):
    return {**{
        i.attrs.get('name'): 'checked' in i.attrs if i.attrs.get('type') == 'checkbox' else i.attrs.get('value')
        for i in form.find_all("input")
        if 'name' in i.attrs and not 'disabled' in i.attrs and (
                i.attrs.get('type') != 'radio' or 'checked' in i.attrs
        ) and not i.attrs['name'] in filter_keys
    }, **{
        i.attrs.get('name'): i.find('option', selected=True).attrs['value']
        for i in form.find_all("select") if not i.attrs['name'] in filter_keys
    }}

def _fetch_event_data(apiref):
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
        'discounts': (apiref / 'discounts').fetch_all(),
    }
    payment_ref = apiref.with_(link_format='{}/control/{}') // 'event' / '{organizer}' / '{event}' / 'settings' / 'payment'
    payment_html = payment_ref.get_html()
    try:
        payment_links = payment_html.find(class_='table-payment-providers').find_all("a")
        providers = [l.attrs['href'].split("/settings/payment/")[1] for l in payment_links]
        result['payment_providers'] = {
            key: _extract_form_value((payment_ref / ('provider', key)).get_html().find(class_='form-plugins'))
            for key in providers
        }
    except:
        print("Failed to load payment provider info")
    _fixup_refs(result, '.items.*.category', '.categories.*', '.id')
    _fixup_refs(result, '.items.*.addons.*.addon_category', '.categories.*', '.id')
    _fixup_refs(result, '.quotas.*.items.*', '.items.*', '.id')
    _fixup_refs(result, '.quotas.*.variations.*', '.items.*.variations.*', '.id')
    _fixup_refs(result, '.vouchers.*.item', '.items.*', '.id')
    _fixup_refs(result, '.vouchers.*.variation', '.items.*.variations.*', '.id')
    _fixup_refs(result, '.questions.*.items.*', '.items.*', '.id')
    _fixup_refs(result, '.questions.*.dependency_question', '.questions.*', '.id')
    _fixup_refs(result, '.categories.*.cross_selling_match_products?.*', '.items.*', '.id')
    _fixup_refs(result, '.event.seat_category_mapping.*', '.items.*', '.id')
    _fixup_refs(result, '.discounts.*.condition_limit_products.*', '.items.*', '.id')
    _fixup_refs(result, '.discounts.*.benefit_limit_products.*', '.items.*', '.id')

    if result['event']['has_subevents']:
        result['subevents'] = (apiref / 'subevents').fetch_all()
    return result

def maybeextendbasename(fn, extend):
    if not fn: return fn
    base, ext = os.path.splitext()
    return base + extend + ext

def _fetch_event_to_file(base, organizer, event, file=None, keep_defaults=False, keep_ids=True):
    apiref = APILink(base, auth_headers)
    if organizer == '*':
        organizers = apiref / 'organizers'
        for org in organizers.fetch_all():
            _fetch_event_to_file(base, org['slug'], event, maybeextendbasename(file, '_organizers_' + org['slug']), keep_defaults, keep_ids)
        return
    orgref = apiref / 'organizers' / ('organizer', organizer)
    if event == '*':
        events = orgref / 'events'
        for event in events.fetch_all():
            print("Fetching event ", organizer, event['slug'])
            _fetch_event_to_file(base, organizer, event['slug'], maybeextendbasename(file, '_events' + event['slug']), keep_defaults, keep_ids)
        return
    eventref = orgref / 'events' / ('event', event)
    print(eventref)
    result = _fetch_event_data(eventref)
    if not keep_defaults:
        _kill_defaults(result, _read_yaml('defaults.yml'))
    _lookup_children(result, '.event.item_meta_properties', delete=True, ignore_key_errors=True)
    if not keep_ids:
        _lookup_children(result, '.categories.*.id', delete=True)
        _lookup_children(result, '.item_meta_properties.*.id', delete=True)
        _lookup_children(result, '.items.*.id', delete=True)
        _lookup_children(result, '.items.*.variations?.*.id', delete=True)
        _lookup_children(result, '.questions.*.id', delete=True)
        _lookup_children(result, '.vouchers.*.id', delete=True)
        _lookup_children(result, '.taxrules.*.id', delete=True)
        _lookup_children(result, '.quotas.*.id', delete=True)
        _lookup_children(result, '.discounts.*.id', delete=True)
    with open(file or ('_'.join(eventref.fpath) + '.yml'), 'w') as f:
        yaml.dump(result, f, sort_keys=False, Dumper=MyDumper)

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
@click.option('--file', '-f')
@click.option('--keep-defaults/--filter-defaults', '-D/-d', show_default=True)
@click.option('--keep-ids/--filter-ids', '-I/-i', default=True, show_default=True)
@click.argument('base')
@click.argument('organizer')
@click.argument('event')
def fetch_event(base, organizer, event, file=None, keep_defaults=False, keep_ids=True):
    _fetch_event_to_file(base, organizer, event, file, keep_defaults, keep_ids)

@cli_event.command('create')
@click.option('--force', is_flag=True, help='Force override event, deleting any pre-existing data (incl. orders etc)')
@click.option('--file', '-f')
@click.argument('base')
@click.argument('organizer')
@click.argument('event')
def create_event(base, organizer, event, force=False, file=None):
    global ref_root
    events_base_api = APILink(base, auth_headers) / 'organizers' / ('organizer', organizer) / 'events'
    apiref = events_base_api / ('event', event)
    event_info = _read_yaml(file or ('_'.join(apiref.fpath) + '.yml'))
    ref_root = event_info

    if force:
        try:
            apiref.delete()
        except RequestException as e:
            _print_request_error(e)

    event_info['event']['slug'] = event

    event_create_body = dict(**event_info['event'])
    event_create_body.pop('live', 0)
    event_create_body.pop('seat_category_mapping', 0)
    event_response = events_base_api.post(event_create_body)

    (apiref / 'settings').patch(event_info['settings'])

    for item_meta_property in event_info.get('item_meta_properties', []):
        _deep_update(item_meta_property, (apiref / 'item_meta_properties').post(item_meta_property))
    for cat in event_info.get('categories', []):
        cat['id'] = (apiref / 'categories').post(_without_keys(cat, {"cross_selling_match_products"}))['id']
    for item in event_info.get('items', []):
        _deep_update(item, (apiref / 'items').post(item))
    for quota in event_info.get('quotas', []):
        _deep_update(quota, (apiref / 'quotas').post(quota))
    for voucher in event_info.get('vouchers', []):
        _deep_update(voucher, (apiref / 'vouchers').post(voucher))
    for taxrule in event_info.get('taxrules', []):
        _deep_update(taxrule, (apiref / 'taxrules').post(taxrule))
    for discount in event_info.get('discounts', []):
        _deep_update(discount, (apiref / 'discounts').post(discount))
    for question in event_info.get('questions', []):
        question['id'] = (apiref / 'questions').post(_without_keys(question, {"dependency_question", "dependency_value"}))['id']

    # fix circular dependencies

    for category in event_info.get('categories', []):
        if category.get("cross_selling_match_products"):
            (apiref / 'categories' / ('category', category['id'])).patch({"cross_selling_match_products": category["cross_selling_match_products"]})

    for question in event_info.get('questions', []):
        if question.get("dependency_question"):
            _deep_update(question, (apiref / 'questions' / ('question', question['id'])).patch({"dependency_question": question["dependency_question"]}))

    if event_info['event'].get('live'):
        apiref.patch({'live': event_info['event']['live']})
    if event_info['event'].get('seat_category_mapping'):
        apiref.patch({'seat_category_mapping': event_info['event']['seat_category_mapping']})

    print("Success: " + event_response['public_url'])

@cli_event.command('update')
@click.option('--file', '-f')
@click.option('--discounts', is_flag=True)
@click.argument('base')
@click.argument('organizer')
@click.argument('event')
def update_event(base, organizer, event, file=None, discounts=False):
    global ref_root
    events_base_api = APILink(base, auth_headers) / 'organizers' / ('organizer', organizer) / 'events'
    apiref = events_base_api / ('event', event)
    event_info = _read_yaml(file or ('_'.join(apiref.fpath) + '.yml'))
    ref_root = event_info

    event_response = apiref.patch(_without_keys(event_info['event'], {'slug'}))
    (apiref / 'settings').patch(event_info['settings'])

    if discounts:
        for old_discount in (apiref / 'discounts').fetch_all():
            (apiref / 'discounts' / ('discount', old_discount['id'])).delete()
        for discount in event_info['discounts']:
            _deep_update(discount, (apiref / 'discounts').post(discount))

    print("Success: " + event_response['public_url'])


@cli.group('auth')
def cli_auth():
    pass

@cli_auth.command('oauth')
@click.argument('base')
def oauth_grant(base):
    oauth_conf = _read_yaml('oauth.yml')
    link = APILink(base, {})
    conf = oauth_conf[link.api_base]
    link._headers = {
        link.api_base: {
            "Authorization": "Basic "+b64encode((conf["client_id"] + ":" + conf["client_secret"]).encode("utf-8")).decode("ascii")
        }
    }
    url = "{}/api/v1/oauth/authorize?client_id={}&response_type=code&scope=read+write&redirect_uri={}".format(link.api_base, conf['client_id'], conf['redirect_uri'])
    print("pls open in your browser:")
    print(url)
    print("")
    print("afterwards, enter code here:")
    code = input("Code:")
    if "?code=" in code:
        code = code.split("?code=")[1]
    print(code)
    response = requests.post(
        "{}/api/v1/oauth/token".format(link.api_base),
        data={ 'grant_type': 'authorization_code', 'code': code, 'redirect_uri': conf['redirect_uri'] },
        headers={
            "Authorization": "Basic "+b64encode((conf["client_id"] + ":" + conf["client_secret"]).encode("utf-8")).decode("ascii")
        }
    ).json()
    token_link = link / "oauth" / "token"
    token_link.link_format = link_format = "{}/api/v1/{}"
    #response = token_link.post()
    print(response)

auth_headers = _read_yaml('auth.yml')

if __name__ == '__main__':
    cli()