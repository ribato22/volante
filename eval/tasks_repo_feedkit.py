"""Wide repo goal: feedkit_source (feedkit)

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

from eval.repo_goal import build_repo_task

_F0 = (
    '"""Which feeds we pull, in the order they appear in the digest."""\n'
    "\n"
    'SOURCES = ("blog", "forum", "paper")\n'
    "\n"
    "LABELS = {\n"
    '    "blog": "From the blogs",\n'
    '    "forum": "Forum threads",\n'
    '    "paper": "New papers",\n'
    "}\n"
)

_F1 = (
    '"""Items arrive one per line: `source|id|title|points`."""\n'
    "\n"
    "from sources import SOURCES\n"
    "\n"
    "# What each feed does to its own titles, and therefore what we have to undo before\n"
    "# anything downstream compares two of them. A source with no entry is left exactly as\n"
    "# typed, which is right for a feed that already writes clean titles and wrong for one\n"
    "# that shouts.\n"
    "TITLE_CASE = {\n"
    '    "blog": "as_is",\n'
    '    "forum": "lower",\n'
    '    "paper": "title",\n'
    "}\n"
    "\n"
    "\n"
    "class MalformedItem(ValueError):\n"
    '    """A line did not carry four values."""\n'
    "\n"
    "\n"
    "class UnknownSource(ValueError):\n"
    '    """A line named a feed we do not pull."""\n'
    "\n"
    "\n"
    "def _cased(how, text):\n"
    '    if how == "lower":\n'
    "        return text.lower()\n"
    '    if how == "title":\n'
    "        return text.title()\n"
    "    return text\n"
    "\n"
    "\n"
    "def parse_line(line):\n"
    '    parts = line.split("|")\n'
    "    if len(parts) != 4:\n"
    '        raise MalformedItem(f"expected 4 values, got {len(parts)}: {line!r}")\n'
    "    source, ident, title, points = (p.strip() for p in parts)\n"
    "    if source not in SOURCES:\n"
    '        raise UnknownSource(f"not a feed we pull: {source!r}")\n'
    "    return {\n"
    '        "source": source,\n'
    '        "id": ident,\n'
    '        "title": _cased(TITLE_CASE.get(source, "as_is"), title),\n'
    '        "points": int(points),\n'
    "    }\n"
    "\n"
    "\n"
    "def parse(text):\n"
    '    """Every non-blank line. Blank lines are skipped, bad ones are not."""\n'
    "    return [parse_line(line) for line in text.splitlines() if line.strip()]\n"
)

_F2 = (
    '"""When two items are the same item. One key rule per feed."""\n'
    "\n"
    "from sources import SOURCES\n"
    "\n"
    "# The fields that make an item unique within its feed. A source with no entry is keyed\n"
    "# on its id, which is right for a feed whose ids are stable and useless for one that\n"
    "# mints a fresh id every time the same thing is published again.\n"
    "KEY_FIELDS = {\n"
    '    "blog": ("id",),\n'
    '    "forum": ("id",),\n'
    '    "paper": ("title",),\n'
    "}\n"
    "\n"
    "\n"
    "def key_of(item):\n"
    '    fields = KEY_FIELDS.get(item.get("source"), ("id",))\n'
    '    return (item.get("source"), tuple(item.get(name) for name in fields))\n'
    "\n"
    "\n"
    "def dedupe(items):\n"
    '    """First occurrence wins; later ones are dropped."""\n'
    "    seen = set()\n"
    "    kept = []\n"
    "    for item in items:\n"
    "        marker = key_of(item)\n"
    "        if marker in seen:\n"
    "            continue\n"
    "        seen.add(marker)\n"
    "        kept.append(item)\n"
    "    return kept\n"
    "\n"
    "\n"
    "def counts(items):\n"
    '    """How many items survive deduplication, per feed, in feed order."""\n'
    "    kept = dedupe(items)\n"
    "    tally = {}\n"
    "    for source in SOURCES:\n"
    '        n = sum(1 for item in kept if item.get("source") == source)\n'
    "        if n:\n"
    "            tally[source] = n\n"
    "    return tally\n"
)

_F3 = (
    '"""Ranking. An item is worth what its feed is worth."""\n'
    "\n"
    "from sources import SOURCES\n"
    "\n"
    "# Multiplier applied to an item's points before anything is compared. A source with no\n"
    "# entry counts at face value, which is the right default for a feed nobody games.\n"
    "WEIGHTS = {\n"
    '    "blog": 1.0,\n'
    '    "forum": 0.5,\n'
    '    "paper": 2.0,\n'
    "}\n"
    "\n"
    "\n"
    "def weighted(item):\n"
    '    return item.get("points", 0) * WEIGHTS.get(item.get("source"), 1.0)\n'
    "\n"
    "\n"
    "def rank(items):\n"
    '    """Highest weighted score first. Ties keep the order they arrived in."""\n'
    "    return sorted(items, key=weighted, reverse=True)\n"
    "\n"
    "\n"
    "def by_source(items):\n"
    '    """The best item from each feed, in feed order. Feeds with nothing are skipped."""\n'
    "    best = []\n"
    "    for source in SOURCES:\n"
    '        mine = [item for item in items if item.get("source") == source]\n'
    "        if mine:\n"
    "            best.append(rank(mine)[0])\n"
    "    return best\n"
)

_F4 = (
    '"""The digest: one section per feed, in feed order, empty sections omitted."""\n'
    "\n"
    "from sources import LABELS, SOURCES\n"
    "\n"
    "# How many items each section shows. A source with no entry shows three, which is the\n"
    "# house default rather than a decision anybody made about that feed.\n"
    "LIMIT = {\n"
    '    "blog": 3,\n'
    '    "forum": 5,\n'
    '    "paper": 2,\n'
    "}\n"
    "\n"
    "\n"
    "def section(source, items):\n"
    '    mine = [item for item in items if item.get("source") == source]\n'
    "    picked = mine[: LIMIT.get(source, 3)]\n"
    "    if not picked:\n"
    "        return []\n"
    "    return [LABELS[source]] + [\n"
    "        f\"  {item['title']} ({item['points']})\" for item in picked\n"
    "    ]\n"
    "\n"
    "\n"
    "def digest(items):\n"
    "    lines = []\n"
    "    for source in SOURCES:\n"
    "        lines.extend(section(source, items))\n"
    '    return "\\n".join(lines)\n'
)

_F5 = (
    '"""The archive: everything we saw, filed by feed."""\n'
    "\n"
    "from sources import SOURCES\n"
    "\n"
    "# Which log each feed is filed under. Anything without an entry lands in the catch-all,\n"
    "# which is where things go to be found by nobody.\n"
    "ARCHIVE_FILE = {\n"
    '    "blog": "posts.log",\n'
    '    "forum": "threads.log",\n'
    '    "paper": "papers.log",\n'
    "}\n"
    "\n"
    "\n"
    "def filed_as(item):\n"
    '    return ARCHIVE_FILE.get(item.get("source"), "misc.log")\n'
    "\n"
    "\n"
    "def bundle(items):\n"
    '    """`{filename: text}`. An item from a feed we do not pull is not archived."""\n'
    "    grouped = {}\n"
    "    for item in items:\n"
    '        if item.get("source") not in SOURCES:\n'
    "            continue\n"
    "        grouped.setdefault(filed_as(item), []).append(\n"
    "            f\"{item['id']}\\t{item['title']}\"\n"
    "        )\n"
    '    return {name: "\\n".join(lines) for name, lines in grouped.items()}\n'
)

STARTING_FILES = (
    ("sources.py", _F0),
    ("parse.py", _F1),
    ("dedupe.py", _F2),
    ("rank.py", _F3),
    ("digest.py", _F4),
    ("archive.py", _F5),
)

_P0 = (
    "import archive\n"
    "import dedupe as dedupe_mod\n"
    "import digest as digest_mod\n"
    "import parse as parse_mod\n"
    "import rank as rank_mod\n"
    "import sources\n"
    "\n"
    "\n"
    "def source_names():\n"
    "    return list(sources.SOURCES)\n"
    "\n"
    "\n"
    "def titles(text):\n"
    '    return [item["title"] for item in parse_mod.parse(text)]\n'
    "\n"
    "\n"
    "def title_at(text, index):\n"
    '    return parse_mod.parse(text)[index]["title"]\n'
    "\n"
    "\n"
    "def kept_ids(text):\n"
    '    return [item["id"] for item in dedupe_mod.dedupe(parse_mod.parse(text))]\n'
    "\n"
    "\n"
    "def kept_counts(text):\n"
    "    return dedupe_mod.counts(parse_mod.parse(text))\n"
    "\n"
    "\n"
    "def ranked_ids(text):\n"
    '    return [item["id"] for item in rank_mod.rank(parse_mod.parse(text))]\n'
    "\n"
    "\n"
    "def best_ids(text):\n"
    '    return [item["id"] for item in rank_mod.by_source(parse_mod.parse(text))]\n'
    "\n"
    "\n"
    "def weight_of(text, index):\n"
    "    return rank_mod.weighted(parse_mod.parse(text)[index])\n"
    "\n"
    "\n"
    "def digest_of(text):\n"
    "    return digest_mod.digest(parse_mod.parse(text))\n"
    "\n"
    "\n"
    "def archive_of(text):\n"
    "    return archive.bundle(parse_mod.parse(text))\n"
    "\n"
    "\n"
    "def archive_names(text):\n"
    "    return sorted(archive.bundle(parse_mod.parse(text)))\n"
    "\n"
    "\n"
    "def archive_of_items(items):\n"
    "    return archive.bundle(items)\n"
    "\n"
    "\n"
    "def parse_rejects(line):\n"
    '    """True when parse WORKS on a good line and REJECTS this one.\n'
    "\n"
    "    The control call is the point: a parser that raises on everything -- including a\n"
    '    module that fails at import -- would satisfy a bare "it raised" check for free.\n'
    "    The control line is valid before AND after the change, so this probe means the\n"
    "    same thing on both trees.\n"
    '    """\n'
    "    try:\n"
    '        parse_mod.parse_line("blog|b1|Ship it on Friday|20")\n'
    "    except Exception:\n"
    "        return False\n"
    "    try:\n"
    "        parse_mod.parse_line(line)\n"
    "    except Exception:\n"
    "        return True\n"
    "    return False\n"
    "\n"
    "\n"
    "def dedupe_discriminates():\n"
    '    """True when the video rule collapses a re-upload AND keeps two different clips.\n'
    "\n"
    '    Two halves in ONE call on purpose. "two different clips are both kept" on its own\n'
    "    is satisfied by the untouched id-keyed default, so as its own case it would be a\n"
    "    free point for an answer that never opened the dedup rules.\n"
    "\n"
    "    Built from item dicts rather than from parsed text so it answers about the dedup\n"
    "    rules ALONE: it is the one dedupe case that still means something when the feed\n"
    "    table was the file the model dropped.\n"
    '    """\n'
    "    same = [\n"
    '        {"source": "video", "id": "v9", "title": "how to solder", "points": 40},\n'
    '        {"source": "video", "id": "v7", "title": "how to solder", "points": 12},\n'
    "    ]\n"
    "    other = [\n"
    "        same[0],\n"
    '        {"source": "video", "id": "v3", "title": "rust in 100 seconds", "points": 8},\n'
    "    ]\n"
    "    return len(dedupe_mod.dedupe(same)) == 1 and len(dedupe_mod.dedupe(other)) == 2\n"
    "\n"
    "\n"
    "def video_weight_discriminates():\n"
    '    """True when a video is discounted AND a blog post is not.\n'
    "\n"
    '    "the video sorts below the blog post" alone can be had by discounting everything;\n'
    "    the second half refuses that. Item dicts again, for the same reason as above.\n"
    '    """\n'
    '    video = {"source": "video", "id": "v9", "title": "how to solder", "points": 40}\n'
    '    post = {"source": "blog", "id": "b1", "title": "Ship it on Friday", "points": 20}\n'
    "    return rank_mod.weighted(video) == 10.0 and rank_mod.weighted(post) == 20.0\n"
)

PROBE_FILES = (("_probe.py", _P0),)

_G0 = (
    '"""Which feeds we pull, in the order they appear in the digest."""\n'
    "\n"
    'SOURCES = ("blog", "forum", "paper", "video")\n'
    "\n"
    "LABELS = {\n"
    '    "blog": "From the blogs",\n'
    '    "forum": "Forum threads",\n'
    '    "paper": "New papers",\n'
    '    "video": "Video picks",\n'
    "}\n"
)

_G1 = (
    '"""Items arrive one per line: `source|id|title|points`."""\n'
    "\n"
    "from sources import SOURCES\n"
    "\n"
    "# What each feed does to its own titles, and therefore what we have to undo before\n"
    "# anything downstream compares two of them. A source with no entry is left exactly as\n"
    "# typed, which is right for a feed that already writes clean titles and wrong for one\n"
    "# that shouts.\n"
    "TITLE_CASE = {\n"
    '    "blog": "as_is",\n'
    '    "forum": "lower",\n'
    '    "paper": "title",\n'
    '    "video": "lower",\n'
    "}\n"
    "\n"
    "\n"
    "class MalformedItem(ValueError):\n"
    '    """A line did not carry four values."""\n'
    "\n"
    "\n"
    "class UnknownSource(ValueError):\n"
    '    """A line named a feed we do not pull."""\n'
    "\n"
    "\n"
    "def _cased(how, text):\n"
    '    if how == "lower":\n'
    "        return text.lower()\n"
    '    if how == "title":\n'
    "        return text.title()\n"
    "    return text\n"
    "\n"
    "\n"
    "def parse_line(line):\n"
    '    parts = line.split("|")\n'
    "    if len(parts) != 4:\n"
    '        raise MalformedItem(f"expected 4 values, got {len(parts)}: {line!r}")\n'
    "    source, ident, title, points = (p.strip() for p in parts)\n"
    "    if source not in SOURCES:\n"
    '        raise UnknownSource(f"not a feed we pull: {source!r}")\n'
    "    return {\n"
    '        "source": source,\n'
    '        "id": ident,\n'
    '        "title": _cased(TITLE_CASE.get(source, "as_is"), title),\n'
    '        "points": int(points),\n'
    "    }\n"
    "\n"
    "\n"
    "def parse(text):\n"
    '    """Every non-blank line. Blank lines are skipped, bad ones are not."""\n'
    "    return [parse_line(line) for line in text.splitlines() if line.strip()]\n"
)

_G2 = (
    '"""When two items are the same item. One key rule per feed."""\n'
    "\n"
    "from sources import SOURCES\n"
    "\n"
    "# The fields that make an item unique within its feed. A source with no entry is keyed\n"
    "# on its id, which is right for a feed whose ids are stable and useless for one that\n"
    "# mints a fresh id every time the same thing is published again.\n"
    "KEY_FIELDS = {\n"
    '    "blog": ("id",),\n'
    '    "forum": ("id",),\n'
    '    "paper": ("title",),\n'
    '    "video": ("title",),\n'
    "}\n"
    "\n"
    "\n"
    "def key_of(item):\n"
    '    fields = KEY_FIELDS.get(item.get("source"), ("id",))\n'
    '    return (item.get("source"), tuple(item.get(name) for name in fields))\n'
    "\n"
    "\n"
    "def dedupe(items):\n"
    '    """First occurrence wins; later ones are dropped."""\n'
    "    seen = set()\n"
    "    kept = []\n"
    "    for item in items:\n"
    "        marker = key_of(item)\n"
    "        if marker in seen:\n"
    "            continue\n"
    "        seen.add(marker)\n"
    "        kept.append(item)\n"
    "    return kept\n"
    "\n"
    "\n"
    "def counts(items):\n"
    '    """How many items survive deduplication, per feed, in feed order."""\n'
    "    kept = dedupe(items)\n"
    "    tally = {}\n"
    "    for source in SOURCES:\n"
    '        n = sum(1 for item in kept if item.get("source") == source)\n'
    "        if n:\n"
    "            tally[source] = n\n"
    "    return tally\n"
)

_G3 = (
    '"""Ranking. An item is worth what its feed is worth."""\n'
    "\n"
    "from sources import SOURCES\n"
    "\n"
    "# Multiplier applied to an item's points before anything is compared. A source with no\n"
    "# entry counts at face value, which is the right default for a feed nobody games.\n"
    "WEIGHTS = {\n"
    '    "blog": 1.0,\n'
    '    "forum": 0.5,\n'
    '    "paper": 2.0,\n'
    '    "video": 0.25,\n'
    "}\n"
    "\n"
    "\n"
    "def weighted(item):\n"
    '    return item.get("points", 0) * WEIGHTS.get(item.get("source"), 1.0)\n'
    "\n"
    "\n"
    "def rank(items):\n"
    '    """Highest weighted score first. Ties keep the order they arrived in."""\n'
    "    return sorted(items, key=weighted, reverse=True)\n"
    "\n"
    "\n"
    "def by_source(items):\n"
    '    """The best item from each feed, in feed order. Feeds with nothing are skipped."""\n'
    "    best = []\n"
    "    for source in SOURCES:\n"
    '        mine = [item for item in items if item.get("source") == source]\n'
    "        if mine:\n"
    "            best.append(rank(mine)[0])\n"
    "    return best\n"
)

_G4 = (
    '"""The digest: one section per feed, in feed order, empty sections omitted."""\n'
    "\n"
    "from sources import LABELS, SOURCES\n"
    "\n"
    "# How many items each section shows. A source with no entry shows three, which is the\n"
    "# house default rather than a decision anybody made about that feed.\n"
    "LIMIT = {\n"
    '    "blog": 3,\n'
    '    "forum": 5,\n'
    '    "paper": 2,\n'
    '    "video": 1,\n'
    "}\n"
    "\n"
    "\n"
    "def section(source, items):\n"
    '    mine = [item for item in items if item.get("source") == source]\n'
    "    picked = mine[: LIMIT.get(source, 3)]\n"
    "    if not picked:\n"
    "        return []\n"
    "    return [LABELS[source]] + [\n"
    "        f\"  {item['title']} ({item['points']})\" for item in picked\n"
    "    ]\n"
    "\n"
    "\n"
    "def digest(items):\n"
    "    lines = []\n"
    "    for source in SOURCES:\n"
    "        lines.extend(section(source, items))\n"
    '    return "\\n".join(lines)\n'
)

_G5 = (
    '"""The archive: everything we saw, filed by feed."""\n'
    "\n"
    "from sources import SOURCES\n"
    "\n"
    "# Which log each feed is filed under. Anything without an entry lands in the catch-all,\n"
    "# which is where things go to be found by nobody.\n"
    "ARCHIVE_FILE = {\n"
    '    "blog": "posts.log",\n'
    '    "forum": "threads.log",\n'
    '    "paper": "papers.log",\n'
    '    "video": "clips.log",\n'
    "}\n"
    "\n"
    "\n"
    "def filed_as(item):\n"
    '    return ARCHIVE_FILE.get(item.get("source"), "misc.log")\n'
    "\n"
    "\n"
    "def bundle(items):\n"
    '    """`{filename: text}`. An item from a feed we do not pull is not archived."""\n'
    "    grouped = {}\n"
    "    for item in items:\n"
    '        if item.get("source") not in SOURCES:\n'
    "            continue\n"
    "        grouped.setdefault(filed_as(item), []).append(\n"
    "            f\"{item['id']}\\t{item['title']}\"\n"
    "        )\n"
    '    return {name: "\\n".join(lines) for name, lines in grouped.items()}\n'
)

GOLDEN_FILES = (
    ("sources.py", _G0),
    ("parse.py", _G1),
    ("dedupe.py", _G2),
    ("rank.py", _G3),
    ("digest.py", _G4),
    ("archive.py", _G5),
)

GOLDEN = "\n\n".join(
    f"```python\n# path: {path}\n{body.rstrip()}\n```" for path, body in GOLDEN_FILES
)

_REQUEST = (
    "Here is our feed toolkit. It works today.\n"
    "\n"
    "### sources.py\n"
    "```python\n"
    '"""Which feeds we pull, in the order they appear in the digest."""\n'
    "\n"
    'SOURCES = ("blog", "forum", "paper")\n'
    "\n"
    "LABELS = {\n"
    '    "blog": "From the blogs",\n'
    '    "forum": "Forum threads",\n'
    '    "paper": "New papers",\n'
    "}\n"
    "```\n"
    "\n"
    "### parse.py\n"
    "```python\n"
    '"""Items arrive one per line: `source|id|title|points`."""\n'
    "\n"
    "from sources import SOURCES\n"
    "\n"
    "# What each feed does to its own titles, and therefore what we have to undo before\n"
    "# anything downstream compares two of them. A source with no entry is left exactly as\n"
    "# typed, which is right for a feed that already writes clean titles and wrong for one\n"
    "# that shouts.\n"
    "TITLE_CASE = {\n"
    '    "blog": "as_is",\n'
    '    "forum": "lower",\n'
    '    "paper": "title",\n'
    "}\n"
    "\n"
    "\n"
    "class MalformedItem(ValueError):\n"
    '    """A line did not carry four values."""\n'
    "\n"
    "\n"
    "class UnknownSource(ValueError):\n"
    '    """A line named a feed we do not pull."""\n'
    "\n"
    "\n"
    "def _cased(how, text):\n"
    '    if how == "lower":\n'
    "        return text.lower()\n"
    '    if how == "title":\n'
    "        return text.title()\n"
    "    return text\n"
    "\n"
    "\n"
    "def parse_line(line):\n"
    '    parts = line.split("|")\n'
    "    if len(parts) != 4:\n"
    '        raise MalformedItem(f"expected 4 values, got {len(parts)}: {line!r}")\n'
    "    source, ident, title, points = (p.strip() for p in parts)\n"
    "    if source not in SOURCES:\n"
    '        raise UnknownSource(f"not a feed we pull: {source!r}")\n'
    "    return {\n"
    '        "source": source,\n'
    '        "id": ident,\n'
    '        "title": _cased(TITLE_CASE.get(source, "as_is"), title),\n'
    '        "points": int(points),\n'
    "    }\n"
    "\n"
    "\n"
    "def parse(text):\n"
    '    """Every non-blank line. Blank lines are skipped, bad ones are not."""\n'
    "    return [parse_line(line) for line in text.splitlines() if line.strip()]\n"
    "```\n"
    "\n"
    "### dedupe.py\n"
    "```python\n"
    '"""When two items are the same item. One key rule per feed."""\n'
    "\n"
    "from sources import SOURCES\n"
    "\n"
    "# The fields that make an item unique within its feed. A source with no entry is keyed\n"
    "# on its id, which is right for a feed whose ids are stable and useless for one that\n"
    "# mints a fresh id every time the same thing is published again.\n"
    "KEY_FIELDS = {\n"
    '    "blog": ("id",),\n'
    '    "forum": ("id",),\n'
    '    "paper": ("title",),\n'
    "}\n"
    "\n"
    "\n"
    "def key_of(item):\n"
    '    fields = KEY_FIELDS.get(item.get("source"), ("id",))\n'
    '    return (item.get("source"), tuple(item.get(name) for name in fields))\n'
    "\n"
    "\n"
    "def dedupe(items):\n"
    '    """First occurrence wins; later ones are dropped."""\n'
    "    seen = set()\n"
    "    kept = []\n"
    "    for item in items:\n"
    "        marker = key_of(item)\n"
    "        if marker in seen:\n"
    "            continue\n"
    "        seen.add(marker)\n"
    "        kept.append(item)\n"
    "    return kept\n"
    "\n"
    "\n"
    "def counts(items):\n"
    '    """How many items survive deduplication, per feed, in feed order."""\n'
    "    kept = dedupe(items)\n"
    "    tally = {}\n"
    "    for source in SOURCES:\n"
    '        n = sum(1 for item in kept if item.get("source") == source)\n'
    "        if n:\n"
    "            tally[source] = n\n"
    "    return tally\n"
    "```\n"
    "\n"
    "### rank.py\n"
    "```python\n"
    '"""Ranking. An item is worth what its feed is worth."""\n'
    "\n"
    "from sources import SOURCES\n"
    "\n"
    "# Multiplier applied to an item's points before anything is compared. A source with no\n"
    "# entry counts at face value, which is the right default for a feed nobody games.\n"
    "WEIGHTS = {\n"
    '    "blog": 1.0,\n'
    '    "forum": 0.5,\n'
    '    "paper": 2.0,\n'
    "}\n"
    "\n"
    "\n"
    "def weighted(item):\n"
    '    return item.get("points", 0) * WEIGHTS.get(item.get("source"), 1.0)\n'
    "\n"
    "\n"
    "def rank(items):\n"
    '    """Highest weighted score first. Ties keep the order they arrived in."""\n'
    "    return sorted(items, key=weighted, reverse=True)\n"
    "\n"
    "\n"
    "def by_source(items):\n"
    '    """The best item from each feed, in feed order. Feeds with nothing are skipped."""\n'
    "    best = []\n"
    "    for source in SOURCES:\n"
    '        mine = [item for item in items if item.get("source") == source]\n'
    "        if mine:\n"
    "            best.append(rank(mine)[0])\n"
    "    return best\n"
    "```\n"
    "\n"
    "### digest.py\n"
    "```python\n"
    '"""The digest: one section per feed, in feed order, empty sections omitted."""\n'
    "\n"
    "from sources import LABELS, SOURCES\n"
    "\n"
    "# How many items each section shows. A source with no entry shows three, which is the\n"
    "# house default rather than a decision anybody made about that feed.\n"
    "LIMIT = {\n"
    '    "blog": 3,\n'
    '    "forum": 5,\n'
    '    "paper": 2,\n'
    "}\n"
    "\n"
    "\n"
    "def section(source, items):\n"
    '    mine = [item for item in items if item.get("source") == source]\n'
    "    picked = mine[: LIMIT.get(source, 3)]\n"
    "    if not picked:\n"
    "        return []\n"
    "    return [LABELS[source]] + [\n"
    "        f\"  {item['title']} ({item['points']})\" for item in picked\n"
    "    ]\n"
    "\n"
    "\n"
    "def digest(items):\n"
    "    lines = []\n"
    "    for source in SOURCES:\n"
    "        lines.extend(section(source, items))\n"
    '    return "\\n".join(lines)\n'
    "```\n"
    "\n"
    "### archive.py\n"
    "```python\n"
    '"""The archive: everything we saw, filed by feed."""\n'
    "\n"
    "from sources import SOURCES\n"
    "\n"
    "# Which log each feed is filed under. Anything without an entry lands in the catch-all,\n"
    "# which is where things go to be found by nobody.\n"
    "ARCHIVE_FILE = {\n"
    '    "blog": "posts.log",\n'
    '    "forum": "threads.log",\n'
    '    "paper": "papers.log",\n'
    "}\n"
    "\n"
    "\n"
    "def filed_as(item):\n"
    '    return ARCHIVE_FILE.get(item.get("source"), "misc.log")\n'
    "\n"
    "\n"
    "def bundle(items):\n"
    '    """`{filename: text}`. An item from a feed we do not pull is not archived."""\n'
    "    grouped = {}\n"
    "    for item in items:\n"
    '        if item.get("source") not in SOURCES:\n'
    "            continue\n"
    "        grouped.setdefault(filed_as(item), []).append(\n"
    "            f\"{item['id']}\\t{item['title']}\"\n"
    "        )\n"
    '    return {name: "\\n".join(lines) for name, lines in grouped.items()}\n'
    "```\n"
    "\n"
    "WHAT I NEED\n"
    "-----------\n"
    "We are pulling a fourth feed, `video`. Its items arrive on the same\n"
    "`source|id|title|points` line as everything else, and everywhere feeds are listed it\n"
    "goes LAST, after the papers.\n"
    "\n"
    "It has to work everywhere the other three do, and it behaves like none of them:\n"
    "\n"
    "* the CDN writes its titles in capitals, so a video title has to come through\n"
    "  lower-cased the way the forum's do rather than as typed;\n"
    "* the same clip is re-uploaded under a fresh id every time it is re-encoded, so two\n"
    "  video items with the SAME TITLE are the same item -- matching on the id, which is\n"
    "  what happens to anything we have no rule for, is exactly what we need to stop here;\n"
    "* videos are noisy, so a video's points are worth a QUARTER of their face value when\n"
    "  things are ranked (a blog post counts at face value, a paper double);\n"
    "* the digest shows at most ONE video, under the heading `Video picks`, where the blog,\n"
    "  forum and paper sections show three, five and two;\n"
    "* and video items are archived into `clips.log`, not the catch-all.\n"
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
    "# path: sources.py\n"
    "...the whole file...\n"
    "```\n"
    "\n"
    "Files you did not change do not need to be included.\n"
)

# The request already carries the starting tree.
GOAL = _REQUEST

CHANGE = [
    (
        "video joins the feed list, last",
        "_probe",
        "source_names",
        [],
        ["blog", "forum", "paper", "video"],
    ),
    (
        "the per-feed board reaches video, last",
        "_probe",
        "best_ids",
        ["video|v9|HOW TO SOLDER|40\nblog|b1|Ship it on Friday|20\nvideo|v3|RUST IN 100 SECONDS|8"],
        ["b1", "v9"],
    ),
    (
        "the digest puts the video section after the papers",
        "_probe",
        "digest_of",
        [
            "blog|b1|Ship it on Friday|20\nvideo|v9|HOW TO SOLDER|40\npaper|p3|attention is all you need|9\nvideo|v3|RUST IN 100 SECONDS|8",  # noqa: E501
        ],
        "From the blogs\n  Ship it on Friday (20)\nNew papers\n  Attention Is All You Need (9)\nVideo picks\n  how to solder (40)",  # noqa: E501
    ),
    (
        "a shouted video title comes through lower-cased",
        "_probe",
        "title_at",
        ["video|v9|HOW TO SOLDER|40", 0],
        "how to solder",
    ),
    (
        "every video title is lower-cased, not just the first",
        "_probe",
        "titles",
        ["video|v9|HOW TO SOLDER|40\nvideo|v3|RUST IN 100 SECONDS|8"],
        ["how to solder", "rust in 100 seconds"],
    ),
    (
        "a video in a mixed batch is lower-cased too",
        "_probe",
        "title_at",
        ["blog|b1|Ship it on Friday|20\nvideo|v9|HOW TO SOLDER|40", 1],
        "how to solder",
    ),
    (
        "a re-upload under a new id is the same video",
        "_probe",
        "kept_ids",
        ["video|v9|HOW TO SOLDER|40\nvideo|v7|HOW TO SOLDER|12"],
        ["v9"],
    ),
    ("the video key both collapses and keeps", "_probe", "dedupe_discriminates", [], True),
    (
        "the per-feed survivor count sees one video",
        "_probe",
        "kept_counts",
        ["video|v9|HOW TO SOLDER|40\nvideo|v7|HOW TO SOLDER|12\nblog|b1|Ship it on Friday|20"],
        {"blog": 1, "video": 1},
    ),
    (
        "a video's points count for a quarter",
        "_probe",
        "weight_of",
        ["video|v9|HOW TO SOLDER|40", 0],
        10,
    ),
    (
        "the discount lands on video and nowhere else",
        "_probe",
        "video_weight_discriminates",
        [],
        True,
    ),
    (
        "a 40-point video ranks below a 20-point post",
        "_probe",
        "ranked_ids",
        ["video|v9|HOW TO SOLDER|40\nblog|b1|Ship it on Friday|20"],
        ["b1", "v9"],
    ),
    (
        "a video ranks below a paper and a thread too",
        "_probe",
        "ranked_ids",
        [
            "video|v9|HOW TO SOLDER|40\nforum|f2|WHY IS MY BUILD SLOW|30\npaper|p3|attention is all you need|9",  # noqa: E501
        ],
        ["p3", "f2", "v9"],
    ),
    (
        "the digest shows one video out of three",
        "_probe",
        "digest_of",
        ["video|v9|HOW TO SOLDER|40\nvideo|v7|HOW TO SOLDER|12\nvideo|v3|RUST IN 100 SECONDS|8"],
        "Video picks\n  how to solder (40)",
    ),
    (
        "the video cap does not touch the blog section",
        "_probe",
        "digest_of",
        [
            "blog|b1|Ship it on Friday|20\nblog|b2|Two on Tuesday|6\nvideo|v9|HOW TO SOLDER|40\nvideo|v3|RUST IN 100 SECONDS|8",  # noqa: E501
        ],
        "From the blogs\n  Ship it on Friday (20)\n  Two on Tuesday (6)\nVideo picks\n  how to solder (40)",  # noqa: E501
    ),
    (
        "the video cap does not touch the forum section",
        "_probe",
        "digest_of",
        [
            "forum|f1|first thread|4\nforum|f2|second thread|2\nvideo|v9|HOW TO SOLDER|40\nvideo|v3|RUST IN 100 SECONDS|8",  # noqa: E501
        ],
        "Forum threads\n  first thread (4)\n  second thread (2)\nVideo picks\n  how to solder (40)",
    ),
    (
        "a video is filed into clips.log",
        "_probe",
        "archive_of",
        ["video|v9|HOW TO SOLDER|40"],
        {"clips.log": "v9\thow to solder"},
    ),
    (
        "clips.log sits alongside the logs that already exist",
        "_probe",
        "archive_names",
        ["blog|b1|Ship it on Friday|20\nvideo|v9|HOW TO SOLDER|40"],
        ["clips.log", "posts.log"],
    ),
    (
        "every video lands in the same log",
        "_probe",
        "archive_of",
        ["video|v9|HOW TO SOLDER|40\nvideo|v3|RUST IN 100 SECONDS|8"],
        {"clips.log": "v9\thow to solder\nv3\trust in 100 seconds"},
    ),
]

REGRESSION = [
    (
        "paper titles are still title-cased",
        "_probe",
        "title_at",
        ["paper|p3|attention is all you need|9", 0],
        "Attention Is All You Need",
    ),
    (
        "forum titles are still lower-cased",
        "_probe",
        "title_at",
        ["forum|f2|WHY IS MY BUILD SLOW|30", 0],
        "why is my build slow",
    ),
    (
        "blog titles are still left alone",
        "_probe",
        "titles",
        ["blog|b1|Ship it on Friday|20"],
        ["Ship it on Friday"],
    ),
    ("a feed we do not pull is still refused", "_probe", "parse_rejects", ["spam|x|hi|1"], True),
    (
        "a line with three values is still refused",
        "_probe",
        "parse_rejects",
        ["blog|b1|hello"],
        True,
    ),
    (
        "a line with five values is still refused",
        "_probe",
        "parse_rejects",
        ["blog|b1|hello|1|extra"],
        True,
    ),
    (
        "blogs still deduplicate on the id",
        "_probe",
        "kept_ids",
        ["blog|b1|A|1\nblog|b1|A again|2"],
        ["b1"],
    ),
    (
        "papers still deduplicate on the title",
        "_probe",
        "kept_counts",
        ["paper|p1|deep|1\npaper|p2|deep|2"],
        {"paper": 1},
    ),
    (
        "the same id in two feeds is still two items",
        "_probe",
        "kept_ids",
        ["blog|x1|A|1\nforum|x1|hey|2"],
        ["x1", "x1"],
    ),
    (
        "forum points are still halved",
        "_probe",
        "weight_of",
        ["forum|f2|WHY IS MY BUILD SLOW|30", 0],
        15,
    ),
    (
        "the existing three still rank as they did",
        "_probe",
        "ranked_ids",
        [
            "blog|b1|Ship it on Friday|20\npaper|p3|attention is all you need|9\nforum|f2|WHY IS MY BUILD SLOW|30",  # noqa: E501
        ],
        ["b1", "p3", "f2"],
    ),
    (
        "the per-feed board still picks the best of each",
        "_probe",
        "best_ids",
        ["forum|f1|one|4\nblog|b1|A|1\nforum|f2|two|9"],
        ["b1", "f2"],
    ),
    (
        "the blog section still shows three",
        "_probe",
        "digest_of",
        ["blog|b1|A|1\nblog|b2|B|2\nblog|b3|C|3\nblog|b4|D|4"],
        "From the blogs\n  A (1)\n  B (2)\n  C (3)",
    ),
    (
        "a feed with nothing still gets no heading",
        "_probe",
        "digest_of",
        ["forum|f1|WHY IS MY BUILD SLOW|3"],
        "Forum threads\n  why is my build slow (3)",
    ),
    (
        "the existing sections keep their order",
        "_probe",
        "digest_of",
        ["paper|p1|deep nets|5\nblog|b1|A|1\nforum|f1|hey|2"],
        "From the blogs\n  A (1)\nForum threads\n  hey (2)\nNew papers\n  Deep Nets (5)",
    ),
    (
        "posts and papers still file where they did",
        "_probe",
        "archive_of",
        ["blog|b1|Ship it on Friday|20\npaper|p1|deep nets|5"],
        {"posts.log": "b1\tShip it on Friday", "papers.log": "p1\tDeep Nets"},
    ),
    (
        "the existing log names are unchanged",
        "_probe",
        "archive_names",
        ["blog|b1|A|1\npaper|p1|deep|2"],
        ["papers.log", "posts.log"],
    ),
    (
        "an item from a feed we do not pull is still not archived",
        "_probe",
        "archive_of_items",
        [[{"source": "spam", "id": "x", "title": "t", "points": 1}]],
        {},
    ),
]

TASK = build_repo_task(
    task_id="feedkit_source",
    goal=GOAL,
    starting_files=STARTING_FILES,
    probe_files=PROBE_FILES,
    change=CHANGE,
    regression=REGRESSION,
)
