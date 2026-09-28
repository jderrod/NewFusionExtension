"""A tiny, safe expression language for the editable CAM tables.

Conditions in the toolpath tables are written as Python-looking
expressions, e.g.::

    swing_out and L_any and not R_any and L_ext
    component_height >= component_width
    (cutout_A_width > 0 and cutout_A_height > 0) or cutout_A != ''

The text is parsed with ``ast`` and every node is checked against a strict
whitelist: ``and`` / ``or`` / ``not``, comparisons (chains allowed),
``+ - * /`` on numbers, unary ``-`` / ``+``, int / float / str constants,
``True`` / ``False`` (also ``true`` / ``false``), variable names and
parentheses.  Anything else (calls, attributes, subscripts, lambdas,
comprehensions, ``**`` ...) is rejected at compile time, so a table edited
in the dashboard can never run code.  The checked tree is turned into
plain closures; nothing is ever passed to ``eval``.
"""

import ast
import functools

MAX_LENGTH = 2000

_LITERAL_NAMES = {"true": True, "false": False, "True": True, "False": False}

_UNSUPPORTED = {
    "Call": "function calls", "Attribute": "attribute access",
    "Subscript": "subscripts", "Slice": "subscripts", "Lambda": "lambda",
    "ListComp": "comprehensions", "SetComp": "comprehensions",
    "DictComp": "comprehensions", "GeneratorExp": "comprehensions",
    "IfExp": "if/else expressions", "NamedExpr": "':=' assignments",
    "List": "lists", "Tuple": "tuples", "Set": "sets", "Dict": "dicts",
    "JoinedStr": "f-strings", "Await": "await", "Yield": "yield",
    "YieldFrom": "yield", "Starred": "'*' unpacking",
}


class ExprError(ValueError):
    pass


def _num(v, op, text):
    # Arithmetic is for numbers only (no "abc" * 100000 surprises).
    if isinstance(v, (int, float)):
        return v
    raise ExprError("'%s' needs numbers, got %r in %r" % (op, v, text))


def _add(a, b):
    return a + b


def _sub(a, b):
    return a - b


def _mul(a, b):
    return a * b


def _div(a, b):
    return a / b


_BINOPS = {ast.Add: ("+", _add), ast.Sub: ("-", _sub),
           ast.Mult: ("*", _mul), ast.Div: ("/", _div)}

_CMPOPS = {
    ast.Eq: ("==", lambda a, b: a == b), ast.NotEq: ("!=", lambda a, b: a != b),
    ast.Lt: ("<", lambda a, b: a < b), ast.LtE: ("<=", lambda a, b: a <= b),
    ast.Gt: (">", lambda a, b: a > b), ast.GtE: (">=", lambda a, b: a >= b),
}


class Expr(object):
    """A compiled expression: ``.text``, ``.names`` and ``.eval(env)``."""

    __slots__ = ("text", "names", "_fn")

    def __init__(self, text, names, fn):
        self.text = text
        self.names = frozenset(names)
        self._fn = fn

    def eval(self, env):
        try:
            return self._fn(env)
        except ExprError:
            raise
        except ZeroDivisionError:
            raise ExprError("division by zero in %r" % self.text)
        except TypeError as e:
            raise ExprError("cannot evaluate %r: %s" % (self.text, e))

    def __repr__(self):
        return "Expr(%r)" % self.text


class _Compiler(object):
    def __init__(self, text):
        self.text = text
        self.names = set()

    def fail(self, node, what):
        col = getattr(node, "col_offset", None)
        where = " at column %d" % (col + 1) if col is not None else ""
        raise ExprError("%s are not allowed%s in %r" % (what, where, self.text))

    def build(self, node):
        kind = type(node).__name__
        method = getattr(self, "_" + kind, None)
        if method is None:
            self.fail(node, _UNSUPPORTED.get(kind, "'%s' expressions" % kind))
        return method(node)

    def _Expression(self, node):
        return self.build(node.body)

    def _Constant(self, node):
        v = node.value
        if isinstance(v, bool) or isinstance(v, (int, float, str)):
            return lambda env: v
        self.fail(node, "%s constants" % type(v).__name__)

    def _Name(self, node):
        name = node.id
        if name in _LITERAL_NAMES:
            v = _LITERAL_NAMES[name]
            return lambda env: v
        self.names.add(name)
        text = self.text

        def get(env):
            try:
                return env[name]
            except KeyError:
                raise ExprError("unknown variable '%s' in %r" % (name, text))
        return get

    def _BoolOp(self, node):
        parts = [self.build(v) for v in node.values]
        if isinstance(node.op, ast.And):
            def f(env):
                v = True
                for p in parts:
                    v = p(env)
                    if not v:
                        return v
                return v
        else:
            def f(env):
                v = False
                for p in parts:
                    v = p(env)
                    if v:
                        return v
                return v
        return f

    def _UnaryOp(self, node):
        operand = self.build(node.operand)
        text = self.text
        if isinstance(node.op, ast.Not):
            return lambda env: not operand(env)
        if isinstance(node.op, ast.USub):
            return lambda env: -_num(operand(env), "-", text)
        if isinstance(node.op, ast.UAdd):
            return lambda env: +_num(operand(env), "+", text)
        self.fail(node, "'%s' operators" % type(node.op).__name__)

    def _BinOp(self, node):
        entry = _BINOPS.get(type(node.op))
        if entry is None:
            self.fail(node, "'%s' operators" % type(node.op).__name__)
        sym, fn = entry
        left, right = self.build(node.left), self.build(node.right)
        text = self.text
        return lambda env: fn(_num(left(env), sym, text), _num(right(env), sym, text))

    def _Compare(self, node):
        first = self.build(node.left)
        steps = []
        for op, comp in zip(node.ops, node.comparators):
            entry = _CMPOPS.get(type(op))
            if entry is None:
                self.fail(node, "'%s' comparisons" % type(op).__name__)
            steps.append((entry[1], self.build(comp)))

        def f(env):
            a = first(env)
            for fn, get in steps:
                b = get(env)
                if not fn(a, b):
                    return False
                a = b
            return True
        return f


@functools.lru_cache(maxsize=1024)
def compile_expr(text):
    """Compile ``text``; raises ExprError for bad or unsupported syntax."""
    if not isinstance(text, str):
        raise ExprError("expression must be text, got %r" % (text,))
    src = text.strip()
    if not src:
        raise ExprError("empty expression")
    if len(src) > MAX_LENGTH:
        raise ExprError("expression longer than %d characters" % MAX_LENGTH)
    try:
        tree = ast.parse(src, mode="eval")
    except SyntaxError as e:
        raise ExprError("syntax error in %r: %s" % (text, e.msg))
    except (ValueError, RecursionError, MemoryError) as e:
        raise ExprError("cannot parse %r: %s" % (text, e))
    comp = _Compiler(text)
    try:
        fn = comp.build(tree)
    except RecursionError:
        raise ExprError("expression %r is nested too deeply" % text)
    return Expr(text, comp.names, fn)


def evaluate(text, env):
    return compile_expr(text).eval(env)
