"""Parse order JSON files into a flat list of components.

Order format (values may be wrapped as ``[value, "type", "description"]``)::

    {
      "order_id": ["3X8X_DV_0622", "string", "..."],
      "doors":  [{"id": ["D1", "string", "..."], "parameters": {...}}, ...],
      "stiles": [...],
      "panels": [...]
    }

Any number of components of any type is allowed.
"""

import os
import re

TYPE_NAMES = {
    "string": "string", "str": "string", "text": "string",
    "float": "float", "double": "float", "number": "float", "real": "float",
    "int": "int", "integer": "int",
    "bool": "bool", "boolean": "bool",
}

MAX_ORDER_BYTES = 5 * 1024 * 1024


class OrderError(ValueError):
    pass


class ParamValue(object):
    __slots__ = ("value", "dtype", "description")

    def __init__(self, value, dtype, description=""):
        self.value = value
        self.dtype = dtype
        self.description = description

    def to_dict(self):
        return {"value": self.value, "type": self.dtype, "description": self.description}

    def __repr__(self):
        return "ParamValue(%r, %r)" % (self.value, self.dtype)


class Component(object):
    def __init__(self, cid, ctype, params, source_key, index):
        self.id = cid
        self.type = ctype
        self.params = params            # name -> ParamValue (JSON order)
        self.source_key = source_key
        self.index = index

    @property
    def series_id(self):
        pv = self.params.get("series_id")
        return str(pv.value) if pv is not None and pv.value is not None else ""

    def to_dict(self):
        return {"id": self.id, "type": self.type, "series_id": self.series_id,
                "source_key": self.source_key, "index": self.index,
                "parameters": {k: v.to_dict() for k, v in self.params.items()}}


class Order(object):
    def __init__(self, order_id, components, source_name=""):
        self.order_id = order_id
        self.components = components
        self.source_name = source_name

    @property
    def safe_id(self):
        return safe_name(self.order_id)

    def counts(self):
        out = {}
        for c in self.components:
            out[c.type] = out.get(c.type, 0) + 1
        return out


def safe_name(text):
    s = re.sub(r"[^\w.\-]+", "_", str(text)).strip("._")
    return s[:120] or "order"


def infer_type(value):
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    return "string"


def unwrap(raw):
    """Return a ParamValue from either a typed triple or a bare value."""
    if isinstance(raw, list) and 2 <= len(raw) <= 3 and isinstance(raw[1], str) \
            and raw[1].strip().lower() in TYPE_NAMES:
        desc = raw[2] if len(raw) == 3 and isinstance(raw[2], str) else ""
        return ParamValue(raw[0], TYPE_NAMES[raw[1].strip().lower()], desc)
    if isinstance(raw, (list, dict)):
        raise OrderError("unsupported parameter value %r" % (raw,))
    return ParamValue(raw, infer_type(raw))


def _scalar(raw):
    if isinstance(raw, list) and raw:
        return raw[0]
    return raw


def parse_order(data, source_name="", type_by_key=None, type_by_prefix=None):
    if not isinstance(data, dict):
        raise OrderError("order JSON must be an object")
    type_by_key = type_by_key or {"doors": "door", "stiles": "stile", "panels": "panel"}
    type_by_prefix = type_by_prefix or {"D": "door", "S": "stile", "P": "panel"}

    order_id = _scalar(data.get("order_id"))
    if order_id in (None, ""):
        order_id = os.path.splitext(os.path.basename(source_name))[0] or "order"
    order_id = str(order_id)

    components = []
    seen = set()
    for key, value in data.items():
        if key == "order_id" or not isinstance(value, list):
            continue
        if not value or not all(isinstance(x, dict) for x in value):
            continue
        if not any("parameters" in x for x in value):
            continue
        for pos, item in enumerate(value):
            cid = _scalar(item.get("id"))
            if cid in (None, ""):
                raise OrderError("%s[%d] has no id" % (key, pos))
            cid = str(cid).strip()
            if cid in seen:
                raise OrderError("duplicate component id %r" % cid)
            seen.add(cid)
            params_raw = item.get("parameters")
            if not isinstance(params_raw, dict):
                raise OrderError("component %s has no parameters object" % cid)
            params = {}
            for pname, praw in params_raw.items():
                try:
                    params[pname] = unwrap(praw)
                except OrderError as e:
                    raise OrderError("component %s parameter %s: %s" % (cid, pname, e))
            ctype = type_by_key.get(key)
            if not ctype:
                prefix = re.match(r"[A-Za-z]+", cid)
                ctype = type_by_prefix.get(prefix.group(0).upper()) if prefix else None
            if not ctype:
                ctype = key[:-1] if key.endswith("s") else key
            components.append(Component(cid, ctype, params, key, len(components)))

    if not components:
        raise OrderError("order contains no components")
    return Order(order_id, components, source_name)


def load_order_file(path, **kw):
    import json
    size = os.path.getsize(path)
    if size > MAX_ORDER_BYTES:
        raise OrderError("order file is too large (%d bytes)" % size)
    with open(path, "r", encoding="utf-8-sig") as fh:
        try:
            data = json.load(fh)
        except ValueError as e:
            raise OrderError("invalid JSON: %s" % e)
    return parse_order(data, os.path.basename(path), **kw)
