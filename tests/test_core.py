import json
import os
import unittest

import helpers  # noqa: F401  (sets sys.path)
from nfe import config as config_mod
from nfe import rules
from nfe.orders import OrderError, load_order_file, parse_order, unwrap
from nfe.params import ModelParam, ParamFormatError, format_expression, plan_assignments


class OrderParsing(unittest.TestCase):
    def test_example_order(self):
        """Loads the fixture `20260206_115328_3X8X_DV_0622.json` with `load_order_file`:
        order id `3X8X_DV_0622`, one component `D1` of type `door` with series
        `3082G.67P`, `component_height` = 88.5625, and `door_hinging_right` unwrapped as
        dtype `bool` with value `True`."""
        o = load_order_file(helpers.EXAMPLE_ORDER)
        self.assertEqual(o.order_id, "3X8X_DV_0622")
        self.assertEqual(len(o.components), 1)
        d1 = o.components[0]
        self.assertEqual((d1.id, d1.type, d1.series_id), ("D1", "door", "3082G.67P"))
        self.assertEqual(d1.params["component_height"].value, 88.5625)
        self.assertEqual(d1.params["door_hinging_right"].dtype, "bool")
        self.assertIs(d1.params["door_hinging_right"].value, True)

    def test_many_components_of_every_type_in_document_order(self):
        """An order with 7 doors, 9 stiles and 12 panels (panels with bare values) parses
        to counts `{door: 7, stile: 9, panel: 12}`, component `index` values are 0..27
        in document order, and a bare integer value gets dtype `int`."""
        data = helpers.order(doors=7, stiles=9)
        data["panels"] = [{"id": "P%d" % i, "parameters": {"component_height": 50}} for i in range(1, 13)]
        o = parse_order(data)
        self.assertEqual(o.counts(), {"door": 7, "stile": 9, "panel": 12})
        self.assertEqual([c.index for c in o.components], list(range(28)))
        self.assertEqual(o.components[-1].params["component_height"].dtype, "int")

    def test_unknown_key_typed_by_prefix(self):
        """A component list under an unrecognised key (`misc`) with id `S4` is typed
        `stile` from the id prefix (`type_by_prefix`)."""
        o = parse_order({"order_id": "X", "misc": [{"id": "S4", "parameters": {}}]})
        self.assertEqual(o.components[0].type, "stile")

    def test_errors(self):
        """`parse_order` raises `OrderError` for an order with no components, duplicate
        component ids (`D1` twice), a component without an id, and a top-level JSON
        value that is not an object."""
        with self.assertRaises(OrderError):
            parse_order({"order_id": "X"})
        with self.assertRaises(OrderError):
            parse_order({"doors": [{"id": "D1", "parameters": {}}, {"id": "D1", "parameters": {}}]})
        with self.assertRaises(OrderError):
            parse_order({"doors": [{"parameters": {}}]})
        with self.assertRaises(OrderError):
            parse_order([1, 2])

    def test_order_id_falls_back_to_filename(self):
        """Without `order_id`, the order id is the source file name without extension
        (`abc.json` → `abc`)."""
        o = parse_order({"doors": [{"id": "D1", "parameters": {}}]}, "abc.json")
        self.assertEqual(o.order_id, "abc")

    def test_unwrap(self):
        """`unwrap` handles a 3-element typed triple (value 1.5), a 2-element triple (dtype
        `string`), a bare `True` (dtype `bool`) and a bare string (dtype `string`)."""
        self.assertEqual(unwrap([1.5, "float", "x"]).value, 1.5)
        self.assertEqual(unwrap(["a", "string"]).dtype, "string")
        self.assertEqual(unwrap(True).dtype, "bool")
        self.assertEqual(unwrap("x").dtype, "string")


class ParamFormatting(unittest.TestCase):
    def fe(self, value, dtype, unit="", text=False):
        return format_expression(unwrap([value, dtype]), ModelParam("p", unit, text))

    def test_numbers_get_units(self):
        """Numeric values get the model parameter's unit: `88.5625 in`, `4.5 in`, int `3
        in`; float noise is removed (`0.1 + 0.2` in mm → `0.3 mm`); a unitless int stays
        `2`."""
        self.assertEqual(self.fe(88.5625, "float", "in"), "88.5625 in")
        self.assertEqual(self.fe(4.5, "float", "in"), "4.5 in")
        self.assertEqual(self.fe(3, "int", "in"), "3 in")
        self.assertEqual(self.fe(0.1 + 0.2, "float", "mm"), "0.3 mm")
        self.assertEqual(self.fe(2, "int"), "2")

    def test_bools(self):
        """Booleans become `1`/`0` on numeric parameters, the string `"true"` with dtype
        `bool` becomes `1`, `True` on a text parameter becomes `'true'`, and `"maybe"`
        as a bool raises `ParamFormatError`."""
        self.assertEqual(self.fe(True, "bool"), "1")
        self.assertEqual(self.fe(False, "bool"), "0")
        self.assertEqual(self.fe("true", "bool"), "1")
        self.assertEqual(self.fe(True, "bool", text=True), "'true'")
        with self.assertRaises(ParamFormatError):
            self.fe("maybe", "bool")

    def test_strings(self):
        """A text parameter gets a quoted value (`'3082G.67P'`); a string with its own unit
        (`"96 in"`) is passed through; a numeric string (`"12.5"`) gets the unit (`12.5
        in`); a text value containing `'` raises. Under the default `zero` null policy,
        null becomes `0 in`, `0` (unitless bool) and `''` (text)."""
        self.assertEqual(self.fe("3082G.67P", "string", text=True), "'3082G.67P'")
        self.assertEqual(self.fe("96 in", "string", "in"), "96 in")
        self.assertEqual(self.fe("12.5", "string", "in"), "12.5 in")
        with self.assertRaises(ParamFormatError):
            self.fe("it's", "string", text=True)
        # nulls: default "zero" policy
        self.assertEqual(self.fe(None, "float", "in"), "0 in")
        self.assertEqual(self.fe(None, "bool"), "0")
        self.assertEqual(self.fe(None, "string", text=True), "''")

    def test_null_policies(self):
        """A null under policy `fail` raises `ParamFormatError`, under `skip` raises
        `SkipParameter`, and `plan_assignments(..., null_policy="skip")` returns no
        assignments, no unknowns and no errors."""
        from nfe.params import SkipParameter
        pv, mp = unwrap([None, "float"]), ModelParam("p", "in")
        with self.assertRaises(ParamFormatError):
            format_expression(pv, mp, "fail")
        with self.assertRaises(SkipParameter):
            format_expression(pv, mp, "skip")
        a, unknown, errors = plan_assignments({"p": pv}, {"p": mp}, null_policy="skip")
        self.assertEqual((a, unknown, errors), ([], [], []))

    def test_plan(self):
        """`plan_assignments` on the example order against the door parameters minus
        `door_perp_stile_keeper` reports exactly `["door_perp_stile_keeper"]` as unknown
        (`series_id` is not reported), no errors, and assigns `component_height` =
        `88.5625 in`."""
        o = load_order_file(helpers.EXAMPLE_ORDER)
        mp = helpers.door_params()
        del mp["door_perp_stile_keeper"]
        a, unknown, errors = plan_assignments(o.components[0].params, mp)
        self.assertEqual(unknown, ["door_perp_stile_keeper"])   # series_id is not "unknown"
        self.assertEqual(errors, [])
        self.assertIn(("component_height", "88.5625 in"), [(n, e) for n, e, _ in a])


class Rules(unittest.TestCase):
    def test_family(self):
        """`family_of("3082G.67P")` = `3X8X`, `family_of("S2186")` = `2X8X`,
        `family_of("abc")` = `""`, and `family_from_order_id("3X8X_DV_0622")` = `3X8X`."""
        self.assertEqual(rules.family_of("3082G.67P"), "3X8X")
        self.assertEqual(rules.family_of("S2186"), "2X8X")
        self.assertEqual(rules.family_of("abc"), "")
        self.assertEqual(rules.family_from_order_id("3X8X_DV_0622"), "3X8X")

    def test_most_specific_wins(self):
        """With a `*` family rule, a door family rule, stile rules `3?82*` and `3?86*` and
        a disabled exact-series door rule: a door `3082G.67P` gets the door family rule
        (disabled rule ignored, exact type beats `*`); stile `3186G` gets `3?86*`; stile
        `3082` gets `3?82*`; a panel falls back to the `*` rule; a door `2082` matches
        nothing; a door with an empty series id resolves through the fallback family
        `3X8X`."""
        rs = [
            {"id": "any", "type": "*", "family": "3X8X", "model": {"id": "a"}},
            {"id": "door", "type": "door", "family": "3X8X", "model": {"id": "b"}},
            {"id": "3082", "type": "stile", "series": "3?82*", "model": {"id": "c"}},
            {"id": "3086", "type": "stile", "series": "3?86*", "model": {"id": "d"}},
            {"id": "off", "type": "door", "series": "3082G.67P", "enabled": False, "model": {"id": "e"}},
        ]
        self.assertEqual(rules.resolve(rs, "door", "3082G.67P")["id"], "door")
        self.assertEqual(rules.resolve(rs, "stile", "3186G")["id"], "3086")
        self.assertEqual(rules.resolve(rs, "stile", "3082")["id"], "3082")
        self.assertEqual(rules.resolve(rs, "panel", "3082")["id"], "any")
        self.assertIsNone(rules.resolve(rs, "door", "2082"))
        self.assertEqual(rules.resolve(rs, "door", "", fallback_family="3X8X")["id"], "door")


class Suggestions(unittest.TestCase):
    FILES = [
        {"id": "a", "name": "3X8X_Door", "ext": "f3d"},
        {"id": "b", "name": "3X8X_Door Drawing", "ext": "f2d"},
        {"id": "c", "name": "3X82_Stile", "ext": "f3d"},
        {"id": "d", "name": "3X82_Stile Drawing", "ext": "f2d"},
        {"id": "e", "name": "2X81/2_Stile", "ext": "f3d"},
        {"id": "f", "name": "3X8X_Panel_Standard", "ext": "f3d"},
        {"id": "g", "name": "3X8X_Panel_Standard Drawing", "ext": "f2d"},
        {"id": "h", "name": "Door example", "ext": "f3d"},
    ]

    def test_suggest(self):
        """From eight hub files, `suggest_rules` proposes exactly the four models that
        follow the naming convention (`Door example` and drawings are not rules):
        `3X8X_Door` → door family rule with its drawing; `3X82_Stile` → `3?82*`;
        `2X81/2_Stile` → `2?8[12]*` with no drawing (`{}`); `3X8X_Panel_Standard` → type
        `panel`. The suggested rules resolve `2182G` and `2081` to the 2X81/2 stile,
        `2086` to nothing and door `3082G.67P` to `3X8X_Door`; passing them as
        `existing` yields no new suggestions."""
        out = {r["model"]["name"]: r for r in rules.suggest_rules(self.FILES)}
        self.assertEqual(set(out), {"3X8X_Door", "3X82_Stile", "2X81/2_Stile", "3X8X_Panel_Standard"})
        self.assertEqual((out["3X8X_Door"]["type"], out["3X8X_Door"]["series"]), ("door", ""))
        self.assertEqual(out["3X8X_Door"]["drawing"]["id"], "b")
        self.assertEqual(out["3X82_Stile"]["series"], "3?82*")
        self.assertEqual(out["2X81/2_Stile"]["series"], "2?8[12]*")
        self.assertEqual(out["2X81/2_Stile"]["drawing"], {})
        self.assertEqual(out["3X8X_Panel_Standard"]["type"], "panel")
        # suggested rules resolve real series ids
        rs = list(out.values())
        self.assertEqual(rules.resolve(rs, "stile", "2182G")["model"]["id"], "e")
        self.assertEqual(rules.resolve(rs, "stile", "2081")["model"]["id"], "e")
        self.assertIsNone(rules.resolve(rs, "stile", "2086"))
        self.assertEqual(rules.resolve(rs, "door", "3082G.67P")["model"]["id"], "a")
        # already-configured models are not suggested again
        self.assertEqual(len(rules.suggest_rules(self.FILES, existing=rs)), 0)


class Config(unittest.TestCase):
    def test_defaults_valid_and_merge(self):
        """`validate(DEFAULTS)` returns no errors; `deep_merge` overrides a nested key
        (`processing.save_drawing = True`) and keeps sibling defaults (`save_timeout_s`
        = 300)."""
        self.assertEqual(config_mod.validate(config_mod.DEFAULTS), [])
        merged = config_mod.deep_merge(config_mod.DEFAULTS, {"processing": {"save_drawing": True}})
        self.assertTrue(merged["processing"]["save_drawing"])
        self.assertEqual(merged["processing"]["save_timeout_s"], 300)

    def test_validation(self):
        """A config with a duplicate rule id, a rule of type `gate` and an enabled rule
        without a model reports all three errors (`duplicate`, `type must be`, `no
        model`)."""
        cfg = config_mod.deep_merge(config_mod.DEFAULTS, {"rules": [
            {"id": "a", "type": "door", "family": "3X8X", "model": {"id": "x"}},
            {"id": "a", "type": "gate", "model": {}}]})
        errs = config_mod.validate(cfg)
        self.assertTrue(any("duplicate" in e for e in errs))
        self.assertTrue(any("type must be" in e for e in errs))
        self.assertTrue(any("no model" in e for e in errs))


if __name__ == "__main__":
    unittest.main()
