from json import JSONEncoder

import ruamel.yaml


ref_root = None

def set_ref_root(new_root):
    global ref_root
    ref_root = new_root

def lookup_child(obj, path):
    if len(path) < 2:
        return obj[path[0]]
    return lookup_child(obj[path[0]], path[1:])


class ScalarRef:
    def __init__(self, v=None, ref=None):
        self.v = v
        self.ref = ref

    def deref(self):
        return lookup_child(ref_root, self.ref)

    def __repr__(self):
        return "SR:"+str(self.v)

    def __eq__(self, other):
        return self.v == other.v if isinstance(other, ScalarRef) else self.v == other


ruamel.yaml.SafeConstructor.add_constructor('ref', lambda loader, node: ScalarRef(ref=loader.construct_sequence(node)))


class SRJSONEncoder(JSONEncoder):
    def default(self, obj):
        if isinstance(obj, ScalarRef):
            return obj.deref()
        return super().default(obj)

