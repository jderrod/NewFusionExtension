"""Turn order parameter values into Fusion parameter expressions.

``ModelParam`` describes the parameter as it exists in the Fusion model; the
formatting depends on it (text parameters need quotes, numeric parameters get
their unit appended so Fusion never falls back to document default units).
"""

import math


class ParamFormatError(ValueError):
    pass


class ModelParam(object):
    __slots__ = ("name", "unit", "is_text", "expression", "value")

    def __init__(self, name, unit="", is_text=False, expression="", value=None):
        self.name = name
        self.unit = unit or ""
        self.is_text = bool(is_text)
        self.expression = expression
        self.value = value

    def to_dict(self):
        return {"name": self.name, "unit": self.unit, "is_text": self.is_text,
                "expression": self.expression, "value": self.value}


_TRUE = ("true", "yes", "on", "1")
_FALSE = ("false", "no", "off", "0")


def format_number(v):
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, int):
        return str(v)
    f = float(v)
    if math.isnan(f) or math.isinf(f):
        raise ParamFormatError("not a finite number: %r" % (v,))
    s = ("%.10f" % f).rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def _with_unit(num_text, unit):
    return "%s %s" % (num_text, unit) if unit else num_text


NULL_POLICIES = ("zero", "skip", "fail")


class SkipParameter(Exception):
    pass


def format_expression(pv, mp, null_policy="zero"):
    """Return the Fusion expression for order value ``pv`` on model param ``mp``.

    ``null_policy`` decides what a JSON null means: ``zero`` writes 0 / '' (the
    model is reused between components, so leaving the old value would leak the
    previous component's value), ``skip`` leaves the model value, ``fail``
    rejects the component."""
    value, dtype = pv.value, pv.dtype
    if value is None:
        if null_policy == "skip":
            raise SkipParameter()
        if null_policy != "zero":
            raise ParamFormatError("value is null")
        if mp.is_text:
            return "''"
        return _with_unit("0", mp.unit)

    if mp.is_text:
        if isinstance(value, bool):
            text = "true" if value else "false"
        elif isinstance(value, (int, float)):
            text = format_number(value)
        else:
            text = str(value)
        if "'" in text:
            raise ParamFormatError("text values cannot contain single quotes")
        return "'%s'" % text

    # Numeric model parameter.
    if dtype == "bool" or isinstance(value, bool):
        if isinstance(value, bool):
            b = value
        elif isinstance(value, (int, float)):
            b = value != 0
        else:
            low = str(value).strip().lower()
            if low in _TRUE:
                b = True
            elif low in _FALSE:
                b = False
            else:
                raise ParamFormatError("not a boolean: %r" % (value,))
        # Booleans drive unitless 0/1 switch parameters.
        return _with_unit("1" if b else "0", mp.unit)

    if isinstance(value, (int, float)):
        return _with_unit(format_number(value), mp.unit)

    text = str(value).strip()
    if not text:
        raise ParamFormatError("empty value for a numeric parameter")
    try:
        return _with_unit(format_number(float(text)), mp.unit)
    except ValueError:
        pass
    low = text.lower()
    if low in ("true", "false"):
        return _with_unit("1" if low == "true" else "0", mp.unit)
    # Assume a Fusion expression that carries its own units, e.g. "96 in".
    return text


def plan_assignments(component_params, model_params, skip=("series_id",), optional=(),
                     null_policy="zero"):
    """Match order parameters to model parameters.

    Returns ``(assignments, unknown, errors)`` where assignments is a list of
    ``(name, expression, ParamValue)`` (nulls skipped under the "skip" policy
    are left out), unknown lists order parameters the model
    doesn't have and errors lists ``(name, message)`` for unformattable values.
    ``series_id`` is used for model selection; it is only applied when the model
    actually has a parameter with that name.  Names in ``optional`` (derived
    parameters such as the order number) are silently skipped when missing.
    """
    assignments, unknown, errors = [], [], []
    for name, pv in component_params.items():
        mp = model_params.get(name)
        if mp is None:
            if name not in skip and name not in optional:
                unknown.append(name)
            continue
        try:
            assignments.append((name, format_expression(pv, mp, null_policy), pv))
        except SkipParameter:
            continue
        except ParamFormatError as e:
            errors.append((name, str(e)))
    return assignments, unknown, errors
