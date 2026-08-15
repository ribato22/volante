"""Wide repo goal: unitkit_unit (unitkit)

Six per-item tables, one new item, and the new item differs from EVERY module's
fallback — the property two earlier drafts lacked, where a default that agreed with
the requirement made a file free to omit.

Gated before it joined the suite: golden exactly 1.000, untouched tree exactly 0.000,
the prompt carries the whole starting tree exactly once, and dropping any single file
costs at least two change cases.

Same archetype as the other wide goals, and that is recorded rather than hidden: five
instances of one shape ("add one item to N per-item tables") are not five independent
samples of repo work, so a cross-goal interval over them is narrower than it looks.
"""

from __future__ import annotations

from eval.repo_goal import build_repo_task, file_block

_F0 = (
    '"""Which units unitkit knows, how each is written, and what each one measures.\n'
    "\n"
    "Nothing here decides behaviour: this is the registry the rest of the toolkit looks\n"
    "things up in. Every other module keeps its OWN table, because how a unit converts has\n"
    "nothing to do with how it prints.\n"
    '"""\n'
    "\n"
    "\n"
    "class UnknownUnit(KeyError):\n"
    '    """A unit nobody registered."""\n'
    "\n"
    "\n"
    "# Canonical order. Listings and reports walk this, so it is the display order too.\n"
    'UNITS = ("g", "kg", "ml", "l", "pct")\n'
    "\n"
    "# How a unit is written for a human. Not always the key -- the key is what code passes\n"
    "# around, the symbol is what ends up on the page.\n"
    "SYMBOL = {\n"
    '    "g": "g",\n'
    '    "kg": "kg",\n'
    '    "ml": "mL",\n'
    '    "l": "L",\n'
    '    "pct": "%",\n'
    "}\n"
    "\n"
    "# What the unit measures. Two units convert into each other only when this matches.\n"
    "DIMENSION = {\n"
    '    "g": "mass",\n'
    '    "kg": "mass",\n'
    '    "ml": "volume",\n'
    '    "l": "volume",\n'
    '    "pct": "fraction",\n'
    "}\n"
    "\n"
    "\n"
    "def is_known(unit):\n"
    "    return unit in UNITS\n"
    "\n"
    "\n"
    "def symbol(unit):\n"
    "    if unit not in SYMBOL:\n"
    "        raise UnknownUnit(unit)\n"
    "    return SYMBOL[unit]\n"
    "\n"
    "\n"
    "def dimension_of(unit):\n"
    "    if unit not in DIMENSION:\n"
    "        raise UnknownUnit(unit)\n"
    "    return DIMENSION[unit]\n"
    "\n"
    "\n"
    "def known_units():\n"
    '    """Every unit we know, in canonical order."""\n'
    "    return list(UNITS)\n"
)

_F1 = (
    '"""Quantities arrive as text: `12.5 kg`, `3%`, `900 mL`."""\n'
    "\n"
    "from units import is_known\n"
    "\n"
    "\n"
    "class BadQuantity(ValueError):\n"
    '    """The text was not a number followed by a unit we accept."""\n'
    "\n"
    "\n"
    "# Every spelling we accept, mapped to the canonical unit key. Case matters: `mL` and\n"
    "# `ml` are both written in the wild, and a technician does not think `L` and `l` are\n"
    "# the same character. A spelling with no entry here is refused outright -- silently\n"
    "# guessing that the token IS the unit key is how `litres` became a unit for a week.\n"
    "SPELLINGS = {\n"
    '    "g": "g",\n'
    '    "gram": "g",\n'
    '    "grams": "g",\n'
    '    "kg": "kg",\n'
    '    "kilogram": "kg",\n'
    '    "kilograms": "kg",\n'
    '    "ml": "ml",\n'
    '    "mL": "ml",\n'
    '    "millilitre": "ml",\n'
    '    "milliliter": "ml",\n'
    '    "l": "l",\n'
    '    "L": "l",\n'
    '    "litre": "l",\n'
    '    "liter": "l",\n'
    '    "%": "pct",\n'
    '    "pct": "pct",\n'
    '    "percent": "pct",\n'
    "}\n"
    "\n"
    "\n"
    "def _split(text):\n"
    "    s = text.strip()\n"
    "    i = 0\n"
    '    while i < len(s) and (s[i].isdigit() or s[i] in "+-."):\n'
    "        i += 1\n"
    "    return s[:i], s[i:].strip()\n"
    "\n"
    "\n"
    "def parse_quantity(text):\n"
    "    number, token = _split(text)\n"
    "    if not number or not token:\n"
    '        raise BadQuantity(f"not a quantity: {text!r}")\n'
    "    try:\n"
    "        value = float(number)\n"
    "    except ValueError:\n"
    '        raise BadQuantity(f"not a number: {number!r}")\n'
    "    unit = SPELLINGS.get(token)\n"
    "    if unit is None:\n"
    '        raise BadQuantity(f"unknown unit {token!r} in {text!r}")\n'
    "    if not is_known(unit):\n"
    '        raise BadQuantity(f"{unit!r} is not a registered unit")\n'
    '    return {"value": value, "unit": unit}\n'
    "\n"
    "\n"
    "def parse_all(text):\n"
    '    """One quantity per non-blank line. Blank lines are skipped, bad ones are not."""\n'
    "    return [parse_quantity(line) for line in text.splitlines() if line.strip()]\n"
)

_F2 = (
    '"""Conversion, via each dimension\'s base unit."""\n'
    "\n"
    "from units import dimension_of\n"
    "\n"
    "\n"
    "class IncompatibleUnits(ValueError):\n"
    '    """Asked to convert between two different kinds of thing."""\n'
    "\n"
    "\n"
    "# How many base units one of this unit is worth. The base of `mass` is `g`, of\n"
    "# `volume` is `ml`, of `fraction` is `pct`. A unit with no entry is assumed to BE the\n"
    "# base of its dimension -- a factor of 1.0 -- which is silently, invisibly wrong for\n"
    "# anything that is not.\n"
    "FACTOR = {\n"
    '    "g": 1.0,\n'
    '    "kg": 1000.0,\n'
    '    "ml": 1.0,\n'
    '    "l": 1000.0,\n'
    '    "pct": 1.0,\n'
    "}\n"
    "\n"
    "\n"
    "def to_base(value, unit):\n"
    "    return value * FACTOR.get(unit, 1.0)\n"
    "\n"
    "\n"
    "def from_base(value, unit):\n"
    "    return value / FACTOR.get(unit, 1.0)\n"
    "\n"
    "\n"
    "def convert(value, frm, to):\n"
    "    if dimension_of(frm) != dimension_of(to):\n"
    '        raise IncompatibleUnits(f"cannot convert {frm} to {to}")\n'
    "    return from_base(to_base(value, frm), to)\n"
)

_F3 = (
    '"""Human-facing rendering: how many decimals, and how the symbol is attached."""\n'
    "\n"
    "from units import symbol\n"
    "\n"
    "# Decimal places per unit. A unit with no entry gets 2, which is right for anything\n"
    "# money-shaped and wrong for anything measured coarsely or measured finely.\n"
    "PRECISION = {\n"
    '    "g": 1,\n'
    '    "kg": 3,\n'
    '    "ml": 0,\n'
    '    "l": 2,\n'
    '    "pct": 1,\n'
    "}\n"
    "\n"
    "# Whether a space sits between the number and the symbol. `3%` reads right, `3 %` does\n"
    "# not. A unit with no entry is written tight against its symbol.\n"
    "SPACED = {\n"
    '    "g": True,\n'
    '    "kg": True,\n'
    '    "ml": True,\n'
    '    "l": True,\n'
    '    "pct": False,\n'
    "}\n"
    "\n"
    "\n"
    "def render(value, unit):\n"
    "    digits = PRECISION.get(unit, 2)\n"
    '    gap = " " if SPACED.get(unit, False) else ""\n'
    '    return f"{value:.{digits}f}{gap}{symbol(unit)}"\n'
    "\n"
    "\n"
    "def render_quantity(q):\n"
    '    return render(q["value"], q["unit"])\n'
)

_F4 = (
    '"""Rolling several readings in the same unit into one number."""\n'
    "\n"
    "\n"
    "class MixedUnits(ValueError):\n"
    '    """Readings in more than one unit have no single answer."""\n'
    "\n"
    "\n"
    "# How several readings of a unit combine. Masses and volumes add up; a percentage does\n"
    "# not -- adding two of them is meaningless, so we average instead. A unit with no entry\n"
    "# is summed, which is right for everything you can pour into one bucket.\n"
    "RULE = {\n"
    '    "g": "sum",\n'
    '    "kg": "sum",\n'
    '    "ml": "sum",\n'
    '    "l": "sum",\n'
    '    "pct": "mean",\n'
    "}\n"
    "\n"
    "\n"
    "def _apply(rule, values):\n"
    '    if rule == "mean":\n'
    "        return sum(values) / len(values)\n"
    "    return sum(values)\n"
    "\n"
    "\n"
    "def aggregate(readings):\n"
    "    if not readings:\n"
    '        raise ValueError("nothing to aggregate")\n'
    '    seen = {r["unit"] for r in readings}\n'
    "    if len(seen) != 1:\n"
    '        raise MixedUnits(f"cannot aggregate {sorted(seen)} together")\n'
    '    unit = readings[0]["unit"]\n'
    '    values = [r["value"] for r in readings]\n'
    '    return {"value": _apply(RULE.get(unit, "sum"), values), "unit": unit}\n'
)

_F5 = (
    '"""The wire shape: what a quantity looks like once it leaves the process."""\n'
    "\n"
    "import json\n"
    "\n"
    "from units import symbol\n"
    "\n"
    "# How each unit's VALUE is encoded. `number` writes a float; `string` writes it with its\n"
    "# symbol attached, for units whose value means nothing on its own. A unit with no entry\n"
    "# goes out as a bare float, which quietly changes the type of a field the consumers\n"
    "# downstream have already agreed on.\n"
    "WIRE = {\n"
    '    "g": "number",\n'
    '    "kg": "number",\n'
    '    "ml": "number",\n'
    '    "l": "number",\n'
    '    "pct": "string",\n'
    "}\n"
    "\n"
    "\n"
    "def encode(q):\n"
    '    unit = q["unit"]\n'
    '    value = q["value"]\n'
    '    how = WIRE.get(unit, "number")\n'
    '    if how == "string":\n'
    '        return {"unit": unit, "value": f"{value:g}{symbol(unit)}"}\n'
    '    return {"unit": unit, "value": float(value)}\n'
    "\n"
    "\n"
    "def to_json(quantities):\n"
    '    return json.dumps([encode(q) for q in quantities], separators=(",", ":"))\n'
)

STARTING_FILES = (
    ("units.py", _F0),
    ("parse.py", _F1),
    ("convert.py", _F2),
    ("formatting.py", _F3),
    ("aggregate.py", _F4),
    ("serialise.py", _F5),
)

_P0 = (
    "import json\n"
    "\n"
    "import aggregate as agg\n"
    "import convert as conv\n"
    "import formatting as fmt\n"
    "import parse as parse_mod\n"
    "import serialise\n"
    "import units\n"
    "\n"
    "\n"
    "def listed_units():\n"
    "    return units.known_units()\n"
    "\n"
    "\n"
    "def unit_symbol(unit):\n"
    "    return units.symbol(unit)\n"
    "\n"
    "\n"
    "def unit_dimension(unit):\n"
    "    return units.dimension_of(unit)\n"
    "\n"
    "\n"
    "def parse_one(text):\n"
    "    return parse_mod.parse_quantity(text)\n"
    "\n"
    "\n"
    "def parse_lines(text):\n"
    "    return parse_mod.parse_all(text)\n"
    "\n"
    "\n"
    "def _refuses(text):\n"
    "    try:\n"
    "        parse_mod.parse_quantity(text)\n"
    "    except Exception:\n"
    "        return True\n"
    "    return False\n"
    "\n"
    "\n"
    "def parse_rejects(text):\n"
    '    """True when a ppm reading PARSES and `text` is REFUSED.\n'
    "\n"
    '    Both halves in one call on purpose. "a bogus unit is refused" on its own is\n'
    "    satisfied by a toolkit that has never heard of ppm either -- an empty answer\n"
    "    passes it -- and a free point measures nothing.\n"
    '    """\n'
    "    try:\n"
    '        parse_mod.parse_quantity("2500 ppm")\n'
    "    except Exception:\n"
    "        return False\n"
    "    return _refuses(text)\n"
    "\n"
    "\n"
    "def parse_still_refuses(text):\n"
    '    """The same conjunction with a control the change never touches."""\n'
    "    try:\n"
    '        parse_mod.parse_quantity("1.5 kg")\n'
    "    except Exception:\n"
    "        return False\n"
    "    return _refuses(text)\n"
    "\n"
    "\n"
    "def converted(value, frm, to):\n"
    "    return round(conv.convert(value, frm, to), 9)\n"
    "\n"
    "\n"
    "def _refuses_convert(frm, to):\n"
    "    try:\n"
    "        conv.convert(1.0, frm, to)\n"
    "    except Exception:\n"
    "        return True\n"
    "    return False\n"
    "\n"
    "\n"
    "def convert_discriminates(frm, to):\n"
    '    """True when ppm converts INTO pct correctly AND `frm` -> `to` is refused."""\n'
    "    try:\n"
    '        if round(conv.convert(2500.0, "ppm", "pct"), 9) != 0.25:\n'
    "            return False\n"
    "    except Exception:\n"
    "        return False\n"
    "    return _refuses_convert(frm, to)\n"
    "\n"
    "\n"
    "def convert_still_discriminates(frm, to):\n"
    '    """The same conjunction with a control the change never touches."""\n'
    "    try:\n"
    '        if round(conv.convert(2.0, "kg", "g"), 9) != 2000.0:\n'
    "            return False\n"
    "    except Exception:\n"
    "        return False\n"
    "    return _refuses_convert(frm, to)\n"
    "\n"
    "\n"
    "def rendered(value, unit):\n"
    "    return fmt.render(value, unit)\n"
    "\n"
    "\n"
    "def rendered_text(text):\n"
    "    return fmt.render_quantity(parse_mod.parse_quantity(text))\n"
    "\n"
    "\n"
    "def aggregated(values, unit):\n"
    '    readings = [{"value": float(v), "unit": unit} for v in values]\n'
    "    return agg.aggregate(readings)\n"
    "\n"
    "\n"
    "def _refuses_mixed():\n"
    "    try:\n"
    '        agg.aggregate([{"value": 1.0, "unit": "kg"}, {"value": 2.0, "unit": "g"}])\n'
    "    except Exception:\n"
    "        return True\n"
    "    return False\n"
    "\n"
    "\n"
    "def aggregate_discriminates():\n"
    '    """True when a ppm batch reports its peak AND mixed units are still refused."""\n'
    "    peak = agg.aggregate(\n"
    "        [\n"
    '            {"value": 2500.0, "unit": "ppm"},\n'
    '            {"value": 1800.0, "unit": "ppm"},\n'
    '            {"value": 900.0, "unit": "ppm"},\n'
    "        ]\n"
    "    )\n"
    '    if peak != {"value": 2500.0, "unit": "ppm"}:\n'
    "        return False\n"
    "    return _refuses_mixed()\n"
    "\n"
    "\n"
    "def aggregate_still_discriminates():\n"
    '    """The same conjunction with a control the change never touches."""\n'
    "    total = agg.aggregate(\n"
    "        [\n"
    '            {"value": 1.0, "unit": "kg"},\n'
    '            {"value": 2.0, "unit": "kg"},\n'
    '            {"value": 3.0, "unit": "kg"},\n'
    "        ]\n"
    "    )\n"
    '    if total != {"value": 6.0, "unit": "kg"}:\n'
    "        return False\n"
    "    return _refuses_mixed()\n"
    "\n"
    "\n"
    "def wire_of(quantities):\n"
    '    """The encoded values with their JSON types, so int 2500 and float 2500.0 differ.\n'
    "\n"
    "    Comparing decoded values alone would not tell them apart -- `2500 == 2500.0` in\n"
    "    Python -- and which of the two goes over the wire is the whole point of the entry.\n"
    '    """\n'
    "    data = json.loads(serialise.to_json(quantities))\n"
    '    return [[d["unit"], d["value"], type(d["value"]).__name__] for d in data]\n'
)

PROBE_FILES = (("_probe.py", _P0),)

_G0 = (
    '"""Which units unitkit knows, how each is written, and what each one measures.\n'
    "\n"
    "Nothing here decides behaviour: this is the registry the rest of the toolkit looks\n"
    "things up in. Every other module keeps its OWN table, because how a unit converts has\n"
    "nothing to do with how it prints.\n"
    '"""\n'
    "\n"
    "\n"
    "class UnknownUnit(KeyError):\n"
    '    """A unit nobody registered."""\n'
    "\n"
    "\n"
    "# Canonical order. Listings and reports walk this, so it is the display order too.\n"
    'UNITS = ("g", "kg", "ml", "l", "pct", "ppm")\n'
    "\n"
    "# How a unit is written for a human. Not always the key -- the key is what code passes\n"
    "# around, the symbol is what ends up on the page.\n"
    "SYMBOL = {\n"
    '    "g": "g",\n'
    '    "kg": "kg",\n'
    '    "ml": "mL",\n'
    '    "l": "L",\n'
    '    "pct": "%",\n'
    '    "ppm": "ppm",\n'
    "}\n"
    "\n"
    "# What the unit measures. Two units convert into each other only when this matches.\n"
    "DIMENSION = {\n"
    '    "g": "mass",\n'
    '    "kg": "mass",\n'
    '    "ml": "volume",\n'
    '    "l": "volume",\n'
    '    "pct": "fraction",\n'
    '    "ppm": "fraction",\n'
    "}\n"
    "\n"
    "\n"
    "def is_known(unit):\n"
    "    return unit in UNITS\n"
    "\n"
    "\n"
    "def symbol(unit):\n"
    "    if unit not in SYMBOL:\n"
    "        raise UnknownUnit(unit)\n"
    "    return SYMBOL[unit]\n"
    "\n"
    "\n"
    "def dimension_of(unit):\n"
    "    if unit not in DIMENSION:\n"
    "        raise UnknownUnit(unit)\n"
    "    return DIMENSION[unit]\n"
    "\n"
    "\n"
    "def known_units():\n"
    '    """Every unit we know, in canonical order."""\n'
    "    return list(UNITS)\n"
)

_G1 = (
    '"""Quantities arrive as text: `12.5 kg`, `3%`, `900 mL`."""\n'
    "\n"
    "from units import is_known\n"
    "\n"
    "\n"
    "class BadQuantity(ValueError):\n"
    '    """The text was not a number followed by a unit we accept."""\n'
    "\n"
    "\n"
    "# Every spelling we accept, mapped to the canonical unit key. Case matters: `mL` and\n"
    "# `ml` are both written in the wild, and a technician does not think `L` and `l` are\n"
    "# the same character. A spelling with no entry here is refused outright -- silently\n"
    "# guessing that the token IS the unit key is how `litres` became a unit for a week.\n"
    "SPELLINGS = {\n"
    '    "g": "g",\n'
    '    "gram": "g",\n'
    '    "grams": "g",\n'
    '    "kg": "kg",\n'
    '    "kilogram": "kg",\n'
    '    "kilograms": "kg",\n'
    '    "ml": "ml",\n'
    '    "mL": "ml",\n'
    '    "millilitre": "ml",\n'
    '    "milliliter": "ml",\n'
    '    "l": "l",\n'
    '    "L": "l",\n'
    '    "litre": "l",\n'
    '    "liter": "l",\n'
    '    "%": "pct",\n'
    '    "pct": "pct",\n'
    '    "percent": "pct",\n'
    '    "ppm": "ppm",\n'
    '    "PPM": "ppm",\n'
    "}\n"
    "\n"
    "\n"
    "def _split(text):\n"
    "    s = text.strip()\n"
    "    i = 0\n"
    '    while i < len(s) and (s[i].isdigit() or s[i] in "+-."):\n'
    "        i += 1\n"
    "    return s[:i], s[i:].strip()\n"
    "\n"
    "\n"
    "def parse_quantity(text):\n"
    "    number, token = _split(text)\n"
    "    if not number or not token:\n"
    '        raise BadQuantity(f"not a quantity: {text!r}")\n'
    "    try:\n"
    "        value = float(number)\n"
    "    except ValueError:\n"
    '        raise BadQuantity(f"not a number: {number!r}")\n'
    "    unit = SPELLINGS.get(token)\n"
    "    if unit is None:\n"
    '        raise BadQuantity(f"unknown unit {token!r} in {text!r}")\n'
    "    if not is_known(unit):\n"
    '        raise BadQuantity(f"{unit!r} is not a registered unit")\n'
    '    return {"value": value, "unit": unit}\n'
    "\n"
    "\n"
    "def parse_all(text):\n"
    '    """One quantity per non-blank line. Blank lines are skipped, bad ones are not."""\n'
    "    return [parse_quantity(line) for line in text.splitlines() if line.strip()]\n"
)

_G2 = (
    '"""Conversion, via each dimension\'s base unit."""\n'
    "\n"
    "from units import dimension_of\n"
    "\n"
    "\n"
    "class IncompatibleUnits(ValueError):\n"
    '    """Asked to convert between two different kinds of thing."""\n'
    "\n"
    "\n"
    "# How many base units one of this unit is worth. The base of `mass` is `g`, of\n"
    "# `volume` is `ml`, of `fraction` is `pct`. A unit with no entry is assumed to BE the\n"
    "# base of its dimension -- a factor of 1.0 -- which is silently, invisibly wrong for\n"
    "# anything that is not.\n"
    "FACTOR = {\n"
    '    "g": 1.0,\n'
    '    "kg": 1000.0,\n'
    '    "ml": 1.0,\n'
    '    "l": 1000.0,\n'
    '    "pct": 1.0,\n'
    '    "ppm": 0.0001,\n'
    "}\n"
    "\n"
    "\n"
    "def to_base(value, unit):\n"
    "    return value * FACTOR.get(unit, 1.0)\n"
    "\n"
    "\n"
    "def from_base(value, unit):\n"
    "    return value / FACTOR.get(unit, 1.0)\n"
    "\n"
    "\n"
    "def convert(value, frm, to):\n"
    "    if dimension_of(frm) != dimension_of(to):\n"
    '        raise IncompatibleUnits(f"cannot convert {frm} to {to}")\n'
    "    return from_base(to_base(value, frm), to)\n"
)

_G3 = (
    '"""Human-facing rendering: how many decimals, and how the symbol is attached."""\n'
    "\n"
    "from units import symbol\n"
    "\n"
    "# Decimal places per unit. A unit with no entry gets 2, which is right for anything\n"
    "# money-shaped and wrong for anything measured coarsely or measured finely.\n"
    "PRECISION = {\n"
    '    "g": 1,\n'
    '    "kg": 3,\n'
    '    "ml": 0,\n'
    '    "l": 2,\n'
    '    "pct": 1,\n'
    '    "ppm": 0,\n'
    "}\n"
    "\n"
    "# Whether a space sits between the number and the symbol. `3%` reads right, `3 %` does\n"
    "# not. A unit with no entry is written tight against its symbol.\n"
    "SPACED = {\n"
    '    "g": True,\n'
    '    "kg": True,\n'
    '    "ml": True,\n'
    '    "l": True,\n'
    '    "pct": False,\n'
    '    "ppm": True,\n'
    "}\n"
    "\n"
    "\n"
    "def render(value, unit):\n"
    "    digits = PRECISION.get(unit, 2)\n"
    '    gap = " " if SPACED.get(unit, False) else ""\n'
    '    return f"{value:.{digits}f}{gap}{symbol(unit)}"\n'
    "\n"
    "\n"
    "def render_quantity(q):\n"
    '    return render(q["value"], q["unit"])\n'
)

_G4 = (
    '"""Rolling several readings in the same unit into one number."""\n'
    "\n"
    "\n"
    "class MixedUnits(ValueError):\n"
    '    """Readings in more than one unit have no single answer."""\n'
    "\n"
    "\n"
    "# How several readings of a unit combine. Masses and volumes add up; a percentage does\n"
    "# not -- adding two of them is meaningless, so we average instead. A unit with no entry\n"
    "# is summed, which is right for everything you can pour into one bucket.\n"
    "RULE = {\n"
    '    "g": "sum",\n'
    '    "kg": "sum",\n'
    '    "ml": "sum",\n'
    '    "l": "sum",\n'
    '    "pct": "mean",\n'
    '    "ppm": "max",\n'
    "}\n"
    "\n"
    "\n"
    "def _apply(rule, values):\n"
    '    if rule == "mean":\n'
    "        return sum(values) / len(values)\n"
    '    if rule == "max":\n'
    "        return max(values)\n"
    "    return sum(values)\n"
    "\n"
    "\n"
    "def aggregate(readings):\n"
    "    if not readings:\n"
    '        raise ValueError("nothing to aggregate")\n'
    '    seen = {r["unit"] for r in readings}\n'
    "    if len(seen) != 1:\n"
    '        raise MixedUnits(f"cannot aggregate {sorted(seen)} together")\n'
    '    unit = readings[0]["unit"]\n'
    '    values = [r["value"] for r in readings]\n'
    '    return {"value": _apply(RULE.get(unit, "sum"), values), "unit": unit}\n'
)

_G5 = (
    '"""The wire shape: what a quantity looks like once it leaves the process."""\n'
    "\n"
    "import json\n"
    "\n"
    "from units import symbol\n"
    "\n"
    "# How each unit's VALUE is encoded. `number` writes a float; `string` writes it with its\n"
    "# symbol attached, for units whose value means nothing on its own. A unit with no entry\n"
    "# goes out as a bare float, which quietly changes the type of a field the consumers\n"
    "# downstream have already agreed on.\n"
    "WIRE = {\n"
    '    "g": "number",\n'
    '    "kg": "number",\n'
    '    "ml": "number",\n'
    '    "l": "number",\n'
    '    "pct": "string",\n'
    '    "ppm": "integer",\n'
    "}\n"
    "\n"
    "\n"
    "def encode(q):\n"
    '    unit = q["unit"]\n'
    '    value = q["value"]\n'
    '    how = WIRE.get(unit, "number")\n'
    '    if how == "string":\n'
    '        return {"unit": unit, "value": f"{value:g}{symbol(unit)}"}\n'
    '    if how == "integer":\n'
    '        return {"unit": unit, "value": int(round(value))}\n'
    '    return {"unit": unit, "value": float(value)}\n'
    "\n"
    "\n"
    "def to_json(quantities):\n"
    '    return json.dumps([encode(q) for q in quantities], separators=(",", ":"))\n'
)

GOLDEN_FILES = (
    ("units.py", _G0),
    ("parse.py", _G1),
    ("convert.py", _G2),
    ("formatting.py", _G3),
    ("aggregate.py", _G4),
    ("serialise.py", _G5),
)

GOLDEN = "\n\n".join(
    f"```python\n# path: {path}\n{body.rstrip()}\n```" for path, body in GOLDEN_FILES
)

_REQUEST = (
    "WHAT I NEED\n"
    "-----------\n"
    "The water-quality feed has started reporting concentrations in parts per million, so\n"
    "unitkit needs `ppm` as a unit in its own right. It is a concentration in exactly the\n"
    "way `pct` is — the two are the same kind of thing and must convert into each other —\n"
    "and one part per million is a ten-thousandth of a percent, so 2500 ppm is 0.25%.\n"
    "\n"
    "Everywhere the other units work, it has to work:\n"
    "\n"
    "* it belongs in the list of units we know, last, after `pct`, and it is written `ppm`\n"
    "  wherever a unit is written out for a human;\n"
    "* it parses out of text like the others, and the lab exports shout it, so `2500 PPM`\n"
    "  has to come through as the same thing as `2500 ppm` — the same way we already take\n"
    "  both `mL` and `ml`;\n"
    "* it converts to and from `pct` with that ten-thousandth factor, and asking for it as\n"
    "  a mass or a volume must still be refused the way it is today;\n"
    "* on screen it is a whole number with a space before the unit — `2500 ppm`, never\n"
    "  `2500.00ppm` — so 12.7 ppm reads as `13 ppm`;\n"
    "* rolling a batch of ppm readings into one number, a total is meaningless and so is an\n"
    "  average: what we report is the worst reading in the batch, the peak;\n"
    "* and on the wire the value goes out as a whole number rather than a float, because\n"
    "  the ingest schema downstream declares that field an integer and rejects `2500.0`.\n"
    "\n"
    "Everything the toolkit already does must keep working exactly as it does now.\n"
    "\n"
    "Answer with the complete contents of every file you changed. Each block must be that\n"
    "file's COMPLETE current contents — every function and constant it has now, including\n"
    "the ones you did not touch. A file reduced to the part you changed is not a shorter\n"
    "answer, it is a broken file. Put each one in its own\n"
    "```python block whose FIRST LINE is a comment naming the file, like:\n"
    "\n"
    "```python\n"
    "# path: units.py\n"
    "...the whole file...\n"
    "```\n"
    "\n"
    "Files you did not change do not need to be included.\n"
)

# The FULL tree goes in the prompt: the two arms that only ever see the
# prompt would otherwise be asked to change code they were never shown.
GOAL = file_block(STARTING_FILES) + "\n\n" + _REQUEST

CHANGE = [
    (
        "ppm joins the canonical listing, last",
        "_probe",
        "listed_units",
        [],
        ["g", "kg", "ml", "l", "pct", "ppm"],
    ),
    ("ppm has a symbol to print", "_probe", "unit_symbol", ["ppm"], "ppm"),
    ("ppm measures the same thing pct measures", "_probe", "unit_dimension", ["ppm"], "fraction"),
    ("a ppm reading parses", "_probe", "parse_one", ["2500 ppm"], {"value": 2500, "unit": "ppm"}),
    (
        "the shouted spelling parses to the same unit",
        "_probe",
        "parse_one",
        ["2500 PPM"],
        {"value": 2500, "unit": "ppm"},
    ),
    ("ppm parses AND a bogus unit is still refused", "_probe", "parse_rejects", ["2500 zzz"], True),
    ("ppm converts down to pct", "_probe", "converted", [2500, "ppm", "pct"], 0.25),
    ("pct converts up to ppm", "_probe", "converted", [0.25, "pct", "ppm"], 2500),
    (
        "ppm converts inside its dimension AND refuses to leave it",
        "_probe",
        "convert_discriminates",
        ["ppm", "g"],
        True,
    ),
    ("ppm renders whole, spaced from its symbol", "_probe", "rendered", [2500, "ppm"], "2500 ppm"),
    ("a fractional ppm reading rounds to whole", "_probe", "rendered", [12.7, "ppm"], "13 ppm"),
    (
        "a parsed ppm reading renders the same way",
        "_probe",
        "rendered_text",
        ["2500.7 PPM"],
        "2501 ppm",
    ),
    (
        "a ppm batch reports its peak",
        "_probe",
        "aggregated",
        [[2500, 1800, 900], "ppm"],
        {"value": 2500, "unit": "ppm"},
    ),
    (
        "the peak is found wherever in the batch it sits",
        "_probe",
        "aggregated",
        [[12, 99.5, 7.25], "ppm"],
        {"value": 99.5, "unit": "ppm"},
    ),
    ("ppm peaks AND mixed units are still refused", "_probe", "aggregate_discriminates", [], True),
    (
        "ppm goes over the wire as a whole number",
        "_probe",
        "wire_of",
        [[{"value": 2500, "unit": "ppm"}]],
        [["ppm", 2500, "int"]],
    ),
    (
        "a fractional ppm reading is rounded on the wire",
        "_probe",
        "wire_of",
        [[{"value": 2500.4, "unit": "ppm"}]],
        [["ppm", 2500, "int"]],
    ),
    (
        "ppm keeps its wire shape alongside other units",
        "_probe",
        "wire_of",
        [[{"value": 1.5, "unit": "kg"}, {"value": 2500, "unit": "ppm"}]],
        [["kg", 1.5, "float"], ["ppm", 2500, "int"]],
    ),
]

REGRESSION = [
    ("mL still writes itself with a capital L", "_probe", "unit_symbol", ["ml"], "mL"),
    ("litres are still a volume", "_probe", "unit_dimension", ["l"], "volume"),
    (
        "a kilogram reading still parses",
        "_probe",
        "parse_one",
        ["1.5 kg"],
        {"value": 1.5, "unit": "kg"},
    ),
    (
        "a percentage with no space still parses",
        "_probe",
        "parse_one",
        ["3%"],
        {"value": 3, "unit": "pct"},
    ),
    (
        "blank lines are still skipped",
        "_probe",
        "parse_lines",
        ["1.5 kg\n\n  \n900 mL"],
        [{"value": 1.5, "unit": "kg"}, {"value": 900, "unit": "ml"}],
    ),
    ("a bare unit with no number is still refused", "_probe", "parse_still_refuses", ["kg"], True),
    ("kilograms still convert to grams", "_probe", "converted", [2, "kg", "g"], 2000),
    ("millilitres still convert to litres", "_probe", "converted", [1500, "ml", "l"], 1.5),
    (
        "mass still refuses to become volume",
        "_probe",
        "convert_still_discriminates",
        ["kg", "l"],
        True,
    ),
    ("kilograms still render to three decimals", "_probe", "rendered", [1.5, "kg"], "1.500 kg"),
    ("percentages still sit tight against the sign", "_probe", "rendered", [3, "pct"], "3.0%"),
    ("millilitres still render whole", "_probe", "rendered", [900, "ml"], "900 mL"),
    (
        "kilograms still add up",
        "_probe",
        "aggregated",
        [[1, 2, 3], "kg"],
        {"value": 6, "unit": "kg"},
    ),
    (
        "percentages are still averaged",
        "_probe",
        "aggregated",
        [[10, 20], "pct"],
        {"value": 15, "unit": "pct"},
    ),
    ("a batch in two units is still refused", "_probe", "aggregate_still_discriminates", [], True),
    (
        "kilograms still go out as floats",
        "_probe",
        "wire_of",
        [[{"value": 1.5, "unit": "kg"}]],
        [["kg", 1.5, "float"]],
    ),
    (
        "percentages still go out as strings",
        "_probe",
        "wire_of",
        [[{"value": 3, "unit": "pct"}]],
        [["pct", "3%", "str"]],
    ),
]

TASK = build_repo_task(
    task_id="unitkit_unit",
    goal=GOAL,
    starting_files=STARTING_FILES,
    probe_files=PROBE_FILES,
    change=CHANGE,
    regression=REGRESSION,
)
