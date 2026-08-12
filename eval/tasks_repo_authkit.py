"""Wide repo goal: authkit_role (authkit)

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
    '"""Who exists in this system, in order of increasing authority."""\n'
    '\n'
    'ROLES = ("guest", "member", "manager", "owner")\n'
    '\n'
    '# What a human sees. A role missing from here is written as its capitalised id, which\n'
    '# is a placeholder that reads like a real answer and almost never is one.\n'
    'TITLES = {\n'
    '    "guest": "Guest",\n'
    '    "member": "Member",\n'
    '    "manager": "Manager",\n'
    '    "owner": "Owner",\n'
    '}\n'
    '\n'
    '# How much authority a role carries when two of them are compared. A role missing from\n'
    '# here ranks below everybody, which is safe and wrong for a role that really exists.\n'
    'RANK = {\n'
    '    "guest": 0,\n'
    '    "member": 10,\n'
    '    "manager": 20,\n'
    '    "owner": 30,\n'
    '}\n'
    '\n'
    '\n'
    'def title(role):\n'
    '    return TITLES.get(role, str(role).capitalize())\n'
    '\n'
    '\n'
    'def rank(role):\n'
    '    return RANK.get(role, 0)\n'
    '\n'
    '\n'
    'def known(role):\n'
    '    return role in RANK\n'
    '\n'
    '\n'
    'def ordered():\n'
    '    """Every role, weakest first."""\n'
    '    return list(ROLES)\n'
)

_F1 = (
    '"""What each role may actually do. One capability set per role, written out in full\n'
    'rather than derived, because the sets are not nested in practice."""\n'
    '\n'
    'from roles import ROLES\n'
    '\n'
    '# A role with no entry here holds NOTHING: every check against it answers no, quietly,\n'
    '# without ever raising.\n'
    'CAPABILITIES = {\n'
    '    "guest": ("read",),\n'
    '    "member": ("read", "comment"),\n'
    '    "manager": ("read", "comment", "write", "invite"),\n'
    '    "owner": ("read", "comment", "write", "invite", "billing", "delete"),\n'
    '}\n'
    '\n'
    '\n'
    'def capabilities(role):\n'
    '    """This role\'s capabilities, in the order they were granted."""\n'
    '    return list(CAPABILITIES.get(role, ()))\n'
    '\n'
    '\n'
    'def can(role, action):\n'
    '    return action in CAPABILITIES.get(role, ())\n'
    '\n'
    '\n'
    'def roles_with(action):\n'
    '    """Every role that may take `action`, weakest first."""\n'
    '    return [r for r in ROLES if action in CAPABILITIES.get(r, ())]\n'
)

_F2 = (
    '"""The evaluator. `capabilities` says what a role CAN do; policy says what happens\n'
    'when it tries."""\n'
    '\n'
    'from capabilities import can\n'
    'from roles import known, rank\n'
    '\n'
    '# Actions a role holds but may not complete alone -- they wait for a second approver.\n'
    '# A role with no entry here is held back by nothing, so everything it can do it simply\n'
    '# does.\n'
    'REVIEWED = {\n'
    '    "guest": (),\n'
    '    "member": (),\n'
    '    "manager": ("invite",),\n'
    '    "owner": ("delete",),\n'
    '}\n'
    '\n'
    '\n'
    'def evaluate(role, action):\n'
    '    """One of "allow", "review", "deny"."""\n'
    '    if not known(role):\n'
    '        return "deny"\n'
    '    if not can(role, action):\n'
    '        return "deny"\n'
    '    if action in REVIEWED.get(role, ()):\n'
    '        return "review"\n'
    '    return "allow"\n'
    '\n'
    '\n'
    'def strongest(candidates, action):\n'
    '    """Of these roles, the highest-ranked one not denied `action`. None if all are."""\n'
    '    allowed = [r for r in candidates if evaluate(r, action) != "deny"]\n'
    '    if not allowed:\n'
    '        return None\n'
    '    return max(allowed, key=rank)\n'
)

_F3 = (
    '"""Audit lines. Fixed width, so a whole day reads down a terminal."""\n'
    '\n'
    'from roles import ROLES, title\n'
    '\n'
    "# The three-letter tag a role's events carry in the log. A role with no tag is written\n"
    '# "ROLE": it parses, it sorts, and it tells the reader nothing.\n'
    'TAGS = {\n'
    '    "guest": "GST",\n'
    '    "member": "MBR",\n'
    '    "manager": "MGR",\n'
    '    "owner": "OWN",\n'
    '}\n'
    '\n'
    'ACTOR_WIDTH = 20\n'
    '\n'
    '\n'
    'def tag(role):\n'
    '    return TAGS.get(role, "ROLE")\n'
    '\n'
    '\n'
    'def line(event):\n'
    '    """`event` is {"role": ..., "action": ..., "target": ...}."""\n'
    '    role = event.get("role", "")\n'
    '    return "[{}] {} {} {}".format(\n'
    '        tag(role),\n'
    '        title(role).ljust(ACTOR_WIDTH),\n'
    '        event.get("action", ""),\n'
    '        event.get("target", ""),\n'
    '    )\n'
    '\n'
    '\n'
    'def log(events):\n'
    '    return "\\n".join(line(e) for e in events)\n'
    '\n'
    '\n'
    'def legend():\n'
    '    """The tag table on one line, weakest role first, for the top of a report."""\n'
    '    return " | ".join("{}={}".format(tag(r), title(r)) for r in ROLES)\n'
)

_F4 = (
    '"""Compact session tokens: `v1.<code>.<subject>.<check>`, all printable ASCII."""\n'
    '\n'
    'from roles import known\n'
    '\n'
    '# The single letter a role is written as inside a token. Tokens are matched byte for\n'
    '# byte, so a role with no letter falls back to "?", which encodes and decodes happily\n'
    '# and identifies nobody.\n'
    'CODES = {\n'
    '    "guest": "g",\n'
    '    "member": "m",\n'
    '    "manager": "n",\n'
    '    "owner": "o",\n'
    '}\n'
    '\n'
    '\n'
    'class BadToken(ValueError):\n'
    '    """Not one of ours, or edited on the way here."""\n'
    '\n'
    '\n'
    'def _check(body):\n'
    '    total = 0\n'
    '    for i, ch in enumerate(body):\n'
    '        total = (total * 31 + ord(ch) + i) % 4096\n'
    '    return format(total, "03x")\n'
    '\n'
    '\n'
    'def code_for(role):\n'
    '    return CODES.get(role, "?")\n'
    '\n'
    '\n'
    'def role_for(code):\n'
    '    for role, letter in CODES.items():\n'
    '        if letter == code:\n'
    '            return role\n'
    '    raise BadToken("unknown role code {!r}".format(code))\n'
    '\n'
    '\n'
    'def encode(subject, role):\n'
    '    if not known(role):\n'
    '        raise BadToken("no such role: {!r}".format(role))\n'
    '    body = "v1.{}.{}".format(code_for(role), subject)\n'
    '    return "{}.{}".format(body, _check(body))\n'
    '\n'
    '\n'
    'def decode(token):\n'
    '    parts = token.split(".")\n'
    '    if len(parts) != 4 or parts[0] != "v1":\n'
    '        raise BadToken("not a token: {!r}".format(token))\n'
    '    body = ".".join(parts[:3])\n'
    '    if _check(body) != parts[3]:\n'
    '        raise BadToken("checksum mismatch")\n'
    '    return {"subject": parts[2], "role": role_for(parts[1])}\n'
)

_F5 = (
    '"""Rate limits: how many requests a minute each role gets."""\n'
    '\n'
    'from roles import ROLES\n'
    '\n'
    '# Requests per minute. A role with no entry lands in the shared bucket of 60 -- roomy,\n'
    '# silent, and not a decision anybody made.\n'
    'TIERS = {\n'
    '    "guest": 10,\n'
    '    "member": 60,\n'
    '    "manager": 300,\n'
    '    "owner": 600,\n'
    '}\n'
    '\n'
    'DEFAULT_TIER = 60\n'
    '\n'
    '\n'
    'def limit(role):\n'
    '    return TIERS.get(role, DEFAULT_TIER)\n'
    '\n'
    '\n'
    'def throttled(role, used):\n'
    '    """True once this many requests have already gone through this minute."""\n'
    '    return used >= limit(role)\n'
    '\n'
    '\n'
    'def tier_table():\n'
    '    """Every role and its limit, weakest role first."""\n'
    '    return [[r, limit(r)] for r in ROLES]\n'
)

STARTING_FILES = (
    ('roles.py', _F0),
    ('capabilities.py', _F1),
    ('policy.py', _F2),
    ('audit.py', _F3),
    ('tokens.py', _F4),
    ('limits.py', _F5),
)

_P0 = (
    'import audit\n'
    'import capabilities as caps\n'
    'import limits\n'
    'import policy\n'
    'import roles\n'
    'import tokens\n'
    '\n'
    '\n'
    'def title_of(role):\n'
    '    return roles.title(role)\n'
    '\n'
    '\n'
    'def rank_of(role):\n'
    '    return roles.rank(role)\n'
    '\n'
    '\n'
    'def role_order():\n'
    '    return roles.ordered()\n'
    '\n'
    '\n'
    'def caps_of(role):\n'
    '    return caps.capabilities(role)\n'
    '\n'
    '\n'
    'def who_can(action):\n'
    '    return caps.roles_with(action)\n'
    '\n'
    '\n'
    'def decision(role, action):\n'
    '    return policy.evaluate(role, action)\n'
    '\n'
    '\n'
    'def strongest_for(candidates, action):\n'
    '    return policy.strongest(candidates, action)\n'
    '\n'
    '\n'
    'def decisions_for(role, actions):\n'
    '    return [policy.evaluate(role, a) for a in actions]\n'
    '\n'
    '\n'
    'def audit_line(event):\n'
    '    return audit.line(event)\n'
    '\n'
    '\n'
    'def audit_legend():\n'
    '    return audit.legend()\n'
    '\n'
    '\n'
    'def token_for(subject, role):\n'
    '    return tokens.encode(subject, role)\n'
    '\n'
    '\n'
    'def limit_for(role):\n'
    '    return limits.limit(role)\n'
    '\n'
    '\n'
    'def tier_rows():\n'
    '    return limits.tier_table()\n'
    '\n'
    '\n'
    'def auditor_capability_is_exact():\n'
    '    """True when the auditor holds EXACTLY the two capabilities it was given.\n'
    '\n'
    '    Positive and negative in ONE call on purpose. "an auditor may not delete" passes\n'
    '    on a project that has never heard of auditors -- an empty answer scores it -- so\n'
    '    it is only worth a point conjoined with the half that requires the grant.\n'
    '    """\n'
    '    return (\n'
    '        caps.can("auditor", "read")\n'
    '        and caps.can("auditor", "export")\n'
    '        and not caps.can("auditor", "write")\n'
    '        and not caps.can("auditor", "comment")\n'
    '        and not caps.can("auditor", "delete")\n'
    '    )\n'
    '\n'
    '\n'
    'def export_is_reviewed_but_read_is_not():\n'
    '    """True when the evaluator ALLOWS, REVIEWS and DENIES the auditor in the right\n'
    '    places. An evaluator that denied everything -- including a module that fails to\n'
    '    import -- would satisfy the deny half on its own."""\n'
    '    return (\n'
    '        policy.evaluate("auditor", "export") == "review"\n'
    '        and policy.evaluate("auditor", "read") == "allow"\n'
    '        and policy.evaluate("auditor", "delete") == "deny"\n'
    '        and policy.evaluate("manager", "invite") == "review"\n'
    '    )\n'
    '\n'
    '\n'
    'def token_round_trips(subject, role):\n'
    '    """True when a fresh token decodes back to this subject and role AND an edited\n'
    '    one is refused. Without the second half, a decoder that returns a constant would\n'
    '    pass; without the first, one that raises always would."""\n'
    '    try:\n'
    '        token = tokens.encode(subject, role)\n'
    '        back = tokens.decode(token)\n'
    '    except Exception:\n'
    '        return False\n'
    '    if back != {"subject": subject, "role": role}:\n'
    '        return False\n'
    '    tampered = token[:-1] + ("0" if token[-1] != "0" else "1")\n'
    '    try:\n'
    '        tokens.decode(tampered)\n'
    '    except Exception:\n'
    '        return True\n'
    '    return False\n'
    '\n'
    '\n'
    'def role_from_token(token):\n'
    '    return tokens.decode(token)["role"]\n'
    '\n'
    '\n'
    'def unknown_role_code_is_refused():\n'
    '    """True when NO token whose role letter we never issue decodes, AND a real token\n'
    '    still does. The brute force goes through the PUBLIC decoder, so it survives a\n'
    '    rewritten checksum: if nothing gets past the check the first half is vacuous and\n'
    '    the second half still has to hold."""\n'
    '    for n in range(4096):\n'
    '        try:\n'
    '            tokens.decode("v1.z.u7.{:03x}".format(n))\n'
    '        except Exception:\n'
    '            continue\n'
    '        return False\n'
    '    try:\n'
    '        return tokens.decode(tokens.encode("u1", "owner")) == {\n'
    '            "subject": "u1", "role": "owner",\n'
    '        }\n'
    '    except Exception:\n'
    '        return False\n'
    '\n'
    '\n'
    'def throttle_discriminates(role, allowed):\n'
    '    """True when the role\'s bucket is EXACTLY `allowed` wide: the last request under\n'
    '    the limit goes through and the one at the limit does not. Either half alone is\n'
    '    free -- the untouched default of 60 already refuses request 200."""\n'
    '    try:\n'
    '        return (\n'
    '            limits.throttled(role, allowed - 1) is False\n'
    '            and limits.throttled(role, allowed) is True\n'
    '        )\n'
    '    except Exception:\n'
    '        return False\n'
)

PROBE_FILES = (
    ('_probe.py', _P0),
)

_G0 = (
    '"""Who exists in this system, in order of increasing authority."""\n'
    '\n'
    'ROLES = ("guest", "member", "auditor", "manager", "owner")\n'
    '\n'
    '# What a human sees. A role missing from here is written as its capitalised id, which\n'
    '# is a placeholder that reads like a real answer and almost never is one.\n'
    'TITLES = {\n'
    '    "guest": "Guest",\n'
    '    "member": "Member",\n'
    '    "auditor": "Auditor (read-only)",\n'
    '    "manager": "Manager",\n'
    '    "owner": "Owner",\n'
    '}\n'
    '\n'
    '# How much authority a role carries when two of them are compared. A role missing from\n'
    '# here ranks below everybody, which is safe and wrong for a role that really exists.\n'
    'RANK = {\n'
    '    "guest": 0,\n'
    '    "member": 10,\n'
    '    "auditor": 15,\n'
    '    "manager": 20,\n'
    '    "owner": 30,\n'
    '}\n'
    '\n'
    '\n'
    'def title(role):\n'
    '    return TITLES.get(role, str(role).capitalize())\n'
    '\n'
    '\n'
    'def rank(role):\n'
    '    return RANK.get(role, 0)\n'
    '\n'
    '\n'
    'def known(role):\n'
    '    return role in RANK\n'
    '\n'
    '\n'
    'def ordered():\n'
    '    """Every role, weakest first."""\n'
    '    return list(ROLES)\n'
)

_G1 = (
    '"""What each role may actually do. One capability set per role, written out in full\n'
    'rather than derived, because the sets are not nested in practice."""\n'
    '\n'
    'from roles import ROLES\n'
    '\n'
    '# A role with no entry here holds NOTHING: every check against it answers no, quietly,\n'
    '# without ever raising.\n'
    'CAPABILITIES = {\n'
    '    "guest": ("read",),\n'
    '    "member": ("read", "comment"),\n'
    '    "auditor": ("read", "export"),\n'
    '    "manager": ("read", "comment", "write", "invite"),\n'
    '    "owner": ("read", "comment", "write", "invite", "billing", "delete"),\n'
    '}\n'
    '\n'
    '\n'
    'def capabilities(role):\n'
    '    """This role\'s capabilities, in the order they were granted."""\n'
    '    return list(CAPABILITIES.get(role, ()))\n'
    '\n'
    '\n'
    'def can(role, action):\n'
    '    return action in CAPABILITIES.get(role, ())\n'
    '\n'
    '\n'
    'def roles_with(action):\n'
    '    """Every role that may take `action`, weakest first."""\n'
    '    return [r for r in ROLES if action in CAPABILITIES.get(r, ())]\n'
)

_G2 = (
    '"""The evaluator. `capabilities` says what a role CAN do; policy says what happens\n'
    'when it tries."""\n'
    '\n'
    'from capabilities import can\n'
    'from roles import known, rank\n'
    '\n'
    '# Actions a role holds but may not complete alone -- they wait for a second approver.\n'
    '# A role with no entry here is held back by nothing, so everything it can do it simply\n'
    '# does.\n'
    'REVIEWED = {\n'
    '    "guest": (),\n'
    '    "member": (),\n'
    '    "auditor": ("export",),\n'
    '    "manager": ("invite",),\n'
    '    "owner": ("delete",),\n'
    '}\n'
    '\n'
    '\n'
    'def evaluate(role, action):\n'
    '    """One of "allow", "review", "deny"."""\n'
    '    if not known(role):\n'
    '        return "deny"\n'
    '    if not can(role, action):\n'
    '        return "deny"\n'
    '    if action in REVIEWED.get(role, ()):\n'
    '        return "review"\n'
    '    return "allow"\n'
    '\n'
    '\n'
    'def strongest(candidates, action):\n'
    '    """Of these roles, the highest-ranked one not denied `action`. None if all are."""\n'
    '    allowed = [r for r in candidates if evaluate(r, action) != "deny"]\n'
    '    if not allowed:\n'
    '        return None\n'
    '    return max(allowed, key=rank)\n'
)

_G3 = (
    '"""Audit lines. Fixed width, so a whole day reads down a terminal."""\n'
    '\n'
    'from roles import ROLES, title\n'
    '\n'
    "# The three-letter tag a role's events carry in the log. A role with no tag is written\n"
    '# "ROLE": it parses, it sorts, and it tells the reader nothing.\n'
    'TAGS = {\n'
    '    "guest": "GST",\n'
    '    "member": "MBR",\n'
    '    "auditor": "AUD",\n'
    '    "manager": "MGR",\n'
    '    "owner": "OWN",\n'
    '}\n'
    '\n'
    'ACTOR_WIDTH = 20\n'
    '\n'
    '\n'
    'def tag(role):\n'
    '    return TAGS.get(role, "ROLE")\n'
    '\n'
    '\n'
    'def line(event):\n'
    '    """`event` is {"role": ..., "action": ..., "target": ...}."""\n'
    '    role = event.get("role", "")\n'
    '    return "[{}] {} {} {}".format(\n'
    '        tag(role),\n'
    '        title(role).ljust(ACTOR_WIDTH),\n'
    '        event.get("action", ""),\n'
    '        event.get("target", ""),\n'
    '    )\n'
    '\n'
    '\n'
    'def log(events):\n'
    '    return "\\n".join(line(e) for e in events)\n'
    '\n'
    '\n'
    'def legend():\n'
    '    """The tag table on one line, weakest role first, for the top of a report."""\n'
    '    return " | ".join("{}={}".format(tag(r), title(r)) for r in ROLES)\n'
)

_G4 = (
    '"""Compact session tokens: `v1.<code>.<subject>.<check>`, all printable ASCII."""\n'
    '\n'
    'from roles import known\n'
    '\n'
    '# The single letter a role is written as inside a token. Tokens are matched byte for\n'
    '# byte, so a role with no letter falls back to "?", which encodes and decodes happily\n'
    '# and identifies nobody.\n'
    'CODES = {\n'
    '    "guest": "g",\n'
    '    "member": "m",\n'
    '    "auditor": "a",\n'
    '    "manager": "n",\n'
    '    "owner": "o",\n'
    '}\n'
    '\n'
    '\n'
    'class BadToken(ValueError):\n'
    '    """Not one of ours, or edited on the way here."""\n'
    '\n'
    '\n'
    'def _check(body):\n'
    '    total = 0\n'
    '    for i, ch in enumerate(body):\n'
    '        total = (total * 31 + ord(ch) + i) % 4096\n'
    '    return format(total, "03x")\n'
    '\n'
    '\n'
    'def code_for(role):\n'
    '    return CODES.get(role, "?")\n'
    '\n'
    '\n'
    'def role_for(code):\n'
    '    for role, letter in CODES.items():\n'
    '        if letter == code:\n'
    '            return role\n'
    '    raise BadToken("unknown role code {!r}".format(code))\n'
    '\n'
    '\n'
    'def encode(subject, role):\n'
    '    if not known(role):\n'
    '        raise BadToken("no such role: {!r}".format(role))\n'
    '    body = "v1.{}.{}".format(code_for(role), subject)\n'
    '    return "{}.{}".format(body, _check(body))\n'
    '\n'
    '\n'
    'def decode(token):\n'
    '    parts = token.split(".")\n'
    '    if len(parts) != 4 or parts[0] != "v1":\n'
    '        raise BadToken("not a token: {!r}".format(token))\n'
    '    body = ".".join(parts[:3])\n'
    '    if _check(body) != parts[3]:\n'
    '        raise BadToken("checksum mismatch")\n'
    '    return {"subject": parts[2], "role": role_for(parts[1])}\n'
)

_G5 = (
    '"""Rate limits: how many requests a minute each role gets."""\n'
    '\n'
    'from roles import ROLES\n'
    '\n'
    '# Requests per minute. A role with no entry lands in the shared bucket of 60 -- roomy,\n'
    '# silent, and not a decision anybody made.\n'
    'TIERS = {\n'
    '    "guest": 10,\n'
    '    "member": 60,\n'
    '    "auditor": 120,\n'
    '    "manager": 300,\n'
    '    "owner": 600,\n'
    '}\n'
    '\n'
    'DEFAULT_TIER = 60\n'
    '\n'
    '\n'
    'def limit(role):\n'
    '    return TIERS.get(role, DEFAULT_TIER)\n'
    '\n'
    '\n'
    'def throttled(role, used):\n'
    '    """True once this many requests have already gone through this minute."""\n'
    '    return used >= limit(role)\n'
    '\n'
    '\n'
    'def tier_table():\n'
    '    """Every role and its limit, weakest role first."""\n'
    '    return [[r, limit(r)] for r in ROLES]\n'
)

GOLDEN_FILES = (
    ('roles.py', _G0),
    ('capabilities.py', _G1),
    ('policy.py', _G2),
    ('audit.py', _G3),
    ('tokens.py', _G4),
    ('limits.py', _G5),
)

GOLDEN = "\n\n".join(
    f"```python\n# path: {path}\n{body.rstrip()}\n```"
    for path, body in GOLDEN_FILES
)

_REQUEST = (
    'Here is our little permissions toolkit. It works today.\n'
    '\n'
    '### roles.py\n'
    '```python\n'
    '"""Who exists in this system, in order of increasing authority."""\n'
    '\n'
    'ROLES = ("guest", "member", "manager", "owner")\n'
    '\n'
    '# What a human sees. A role missing from here is written as its capitalised id, which\n'
    '# is a placeholder that reads like a real answer and almost never is one.\n'
    'TITLES = {\n'
    '    "guest": "Guest",\n'
    '    "member": "Member",\n'
    '    "manager": "Manager",\n'
    '    "owner": "Owner",\n'
    '}\n'
    '\n'
    '# How much authority a role carries when two of them are compared. A role missing from\n'
    '# here ranks below everybody, which is safe and wrong for a role that really exists.\n'
    'RANK = {\n'
    '    "guest": 0,\n'
    '    "member": 10,\n'
    '    "manager": 20,\n'
    '    "owner": 30,\n'
    '}\n'
    '\n'
    '\n'
    'def title(role):\n'
    '    return TITLES.get(role, str(role).capitalize())\n'
    '\n'
    '\n'
    'def rank(role):\n'
    '    return RANK.get(role, 0)\n'
    '\n'
    '\n'
    'def known(role):\n'
    '    return role in RANK\n'
    '\n'
    '\n'
    'def ordered():\n'
    '    """Every role, weakest first."""\n'
    '    return list(ROLES)\n'
    '```\n'
    '\n'
    '### capabilities.py\n'
    '```python\n'
    '"""What each role may actually do. One capability set per role, written out in full\n'
    'rather than derived, because the sets are not nested in practice."""\n'
    '\n'
    'from roles import ROLES\n'
    '\n'
    '# A role with no entry here holds NOTHING: every check against it answers no, quietly,\n'
    '# without ever raising.\n'
    'CAPABILITIES = {\n'
    '    "guest": ("read",),\n'
    '    "member": ("read", "comment"),\n'
    '    "manager": ("read", "comment", "write", "invite"),\n'
    '    "owner": ("read", "comment", "write", "invite", "billing", "delete"),\n'
    '}\n'
    '\n'
    '\n'
    'def capabilities(role):\n'
    '    """This role\'s capabilities, in the order they were granted."""\n'
    '    return list(CAPABILITIES.get(role, ()))\n'
    '\n'
    '\n'
    'def can(role, action):\n'
    '    return action in CAPABILITIES.get(role, ())\n'
    '\n'
    '\n'
    'def roles_with(action):\n'
    '    """Every role that may take `action`, weakest first."""\n'
    '    return [r for r in ROLES if action in CAPABILITIES.get(r, ())]\n'
    '```\n'
    '\n'
    '### policy.py\n'
    '```python\n'
    '"""The evaluator. `capabilities` says what a role CAN do; policy says what happens\n'
    'when it tries."""\n'
    '\n'
    'from capabilities import can\n'
    'from roles import known, rank\n'
    '\n'
    '# Actions a role holds but may not complete alone -- they wait for a second approver.\n'
    '# A role with no entry here is held back by nothing, so everything it can do it simply\n'
    '# does.\n'
    'REVIEWED = {\n'
    '    "guest": (),\n'
    '    "member": (),\n'
    '    "manager": ("invite",),\n'
    '    "owner": ("delete",),\n'
    '}\n'
    '\n'
    '\n'
    'def evaluate(role, action):\n'
    '    """One of "allow", "review", "deny"."""\n'
    '    if not known(role):\n'
    '        return "deny"\n'
    '    if not can(role, action):\n'
    '        return "deny"\n'
    '    if action in REVIEWED.get(role, ()):\n'
    '        return "review"\n'
    '    return "allow"\n'
    '\n'
    '\n'
    'def strongest(candidates, action):\n'
    '    """Of these roles, the highest-ranked one not denied `action`. None if all are."""\n'
    '    allowed = [r for r in candidates if evaluate(r, action) != "deny"]\n'
    '    if not allowed:\n'
    '        return None\n'
    '    return max(allowed, key=rank)\n'
    '```\n'
    '\n'
    '### audit.py\n'
    '```python\n'
    '"""Audit lines. Fixed width, so a whole day reads down a terminal."""\n'
    '\n'
    'from roles import ROLES, title\n'
    '\n'
    "# The three-letter tag a role's events carry in the log. A role with no tag is written\n"
    '# "ROLE": it parses, it sorts, and it tells the reader nothing.\n'
    'TAGS = {\n'
    '    "guest": "GST",\n'
    '    "member": "MBR",\n'
    '    "manager": "MGR",\n'
    '    "owner": "OWN",\n'
    '}\n'
    '\n'
    'ACTOR_WIDTH = 20\n'
    '\n'
    '\n'
    'def tag(role):\n'
    '    return TAGS.get(role, "ROLE")\n'
    '\n'
    '\n'
    'def line(event):\n'
    '    """`event` is {"role": ..., "action": ..., "target": ...}."""\n'
    '    role = event.get("role", "")\n'
    '    return "[{}] {} {} {}".format(\n'
    '        tag(role),\n'
    '        title(role).ljust(ACTOR_WIDTH),\n'
    '        event.get("action", ""),\n'
    '        event.get("target", ""),\n'
    '    )\n'
    '\n'
    '\n'
    'def log(events):\n'
    '    return "\\n".join(line(e) for e in events)\n'
    '\n'
    '\n'
    'def legend():\n'
    '    """The tag table on one line, weakest role first, for the top of a report."""\n'
    '    return " | ".join("{}={}".format(tag(r), title(r)) for r in ROLES)\n'
    '```\n'
    '\n'
    '### tokens.py\n'
    '```python\n'
    '"""Compact session tokens: `v1.<code>.<subject>.<check>`, all printable ASCII."""\n'
    '\n'
    'from roles import known\n'
    '\n'
    '# The single letter a role is written as inside a token. Tokens are matched byte for\n'
    '# byte, so a role with no letter falls back to "?", which encodes and decodes happily\n'
    '# and identifies nobody.\n'
    'CODES = {\n'
    '    "guest": "g",\n'
    '    "member": "m",\n'
    '    "manager": "n",\n'
    '    "owner": "o",\n'
    '}\n'
    '\n'
    '\n'
    'class BadToken(ValueError):\n'
    '    """Not one of ours, or edited on the way here."""\n'
    '\n'
    '\n'
    'def _check(body):\n'
    '    total = 0\n'
    '    for i, ch in enumerate(body):\n'
    '        total = (total * 31 + ord(ch) + i) % 4096\n'
    '    return format(total, "03x")\n'
    '\n'
    '\n'
    'def code_for(role):\n'
    '    return CODES.get(role, "?")\n'
    '\n'
    '\n'
    'def role_for(code):\n'
    '    for role, letter in CODES.items():\n'
    '        if letter == code:\n'
    '            return role\n'
    '    raise BadToken("unknown role code {!r}".format(code))\n'
    '\n'
    '\n'
    'def encode(subject, role):\n'
    '    if not known(role):\n'
    '        raise BadToken("no such role: {!r}".format(role))\n'
    '    body = "v1.{}.{}".format(code_for(role), subject)\n'
    '    return "{}.{}".format(body, _check(body))\n'
    '\n'
    '\n'
    'def decode(token):\n'
    '    parts = token.split(".")\n'
    '    if len(parts) != 4 or parts[0] != "v1":\n'
    '        raise BadToken("not a token: {!r}".format(token))\n'
    '    body = ".".join(parts[:3])\n'
    '    if _check(body) != parts[3]:\n'
    '        raise BadToken("checksum mismatch")\n'
    '    return {"subject": parts[2], "role": role_for(parts[1])}\n'
    '```\n'
    '\n'
    '### limits.py\n'
    '```python\n'
    '"""Rate limits: how many requests a minute each role gets."""\n'
    '\n'
    'from roles import ROLES\n'
    '\n'
    '# Requests per minute. A role with no entry lands in the shared bucket of 60 -- roomy,\n'
    '# silent, and not a decision anybody made.\n'
    'TIERS = {\n'
    '    "guest": 10,\n'
    '    "member": 60,\n'
    '    "manager": 300,\n'
    '    "owner": 600,\n'
    '}\n'
    '\n'
    'DEFAULT_TIER = 60\n'
    '\n'
    '\n'
    'def limit(role):\n'
    '    return TIERS.get(role, DEFAULT_TIER)\n'
    '\n'
    '\n'
    'def throttled(role, used):\n'
    '    """True once this many requests have already gone through this minute."""\n'
    '    return used >= limit(role)\n'
    '\n'
    '\n'
    'def tier_table():\n'
    '    """Every role and its limit, weakest role first."""\n'
    '    return [[r, limit(r)] for r in ROLES]\n'
    '```\n'
    '\n'
    'WHAT I NEED\n'
    '-----------\n'
    'Compliance are getting their own seat, so we are adding a fifth role: `auditor`. It is\n'
    'a read-only role, and in authority it sits between `member` and `manager` -- more than\n'
    'a member, less than a manager -- so that is where it belongs anywhere roles are listed\n'
    'in order.\n'
    '\n'
    'It has to be a first-class role everywhere the four we already have are:\n'
    '\n'
    '* it ranks 15, and wherever a role is shown to a human it reads `Auditor (read-only)`\n'
    '  rather than a bare id;\n'
    '* the only two things it may do are `read` and `export`. `export` is a new capability\n'
    '  and nobody else gets it -- not even the owner;\n'
    "* an auditor's `export` does not complete on its own: it waits for a second approver,\n"
    "  the way a manager's invite already does. Anything else an auditor holds goes\n"
    '  straight through;\n'
    '* its events carry the tag `AUD` in the log;\n'
    '* inside a token it is written `a`, and a token carrying `a` must decode back to it;\n'
    '* and it gets 120 requests a minute.\n'
    '\n'
    'Everything the toolkit already does must keep working exactly as it does now -- the\n'
    'other four roles, and whatever the code does today about roles nobody has listed.\n'
    '\n'
    'Answer with the complete contents of every file you changed. Each block must be that\n'
    "file's COMPLETE current contents — every function and constant it has now, including\n"
    'the ones you did not touch. A file reduced to the part you changed is not a shorter\n'
    'answer, it is a broken file. Put each one in its own\n'
    '```python block whose FIRST LINE is a comment naming the file, like:\n'
    '\n'
    '```python\n'
    '# path: roles.py\n'
    '...the whole file...\n'
    '```\n'
    '\n'
    'Files you did not change do not need to be included.\n'
)

# The request already carries the starting tree.
GOAL = _REQUEST

CHANGE = [
    ('the auditor prints with the title we ship',
     '_probe', 'title_of',
     ['auditor'],
     'Auditor (read-only)'),
    ('the auditor ranks between member and manager', '_probe', 'rank_of', ['auditor'], 15),
    ('the auditor takes its place in the role order',
     '_probe', 'role_order',
     [],
     ['guest', 'member', 'auditor', 'manager', 'owner']),
    ("the auditor's capability set is the one asked for",
     '_probe', 'caps_of',
     ['auditor'],
     ['read', 'export']),
    ('the auditor holds those two and nothing else',
     '_probe', 'auditor_capability_is_exact',
     [],
     True),
    ('export belongs to the auditor alone', '_probe', 'who_can', ['export'], ['auditor']),
    ('the auditor joins the readers, in role order',
     '_probe', 'who_can',
     ['read'],
     ['guest', 'member', 'auditor', 'manager', 'owner']),
    ("an auditor's export waits for a second approver",
     '_probe', 'decision',
     ['auditor', 'export'],
     'review'),
    ('the evaluator allows, reviews and denies in the right places',
     '_probe', 'export_is_reviewed_but_read_is_not',
     [],
     True),
    ('every auditor verdict at once',
     '_probe', 'decisions_for',
     ['auditor', ['read', 'export', 'write', 'comment', 'delete']],
     ['allow', 'review', 'deny', 'deny', 'deny']),
    ('an auditor export is logged under its own tag',
     '_probe', 'audit_line',
     [{'role': 'auditor', 'action': 'export', 'target': 'ledger'}],
     '[AUD] Auditor (read-only)  export ledger'),
    ('and so is an auditor read',
     '_probe', 'audit_line',
     [{'role': 'auditor', 'action': 'read', 'target': 'invoice-9'}],
     '[AUD] Auditor (read-only)  read invoice-9'),
    ('the legend gains the auditor, in role order',
     '_probe', 'audit_legend',
     [],
     'GST=Guest | MBR=Member | AUD=Auditor (read-only) | MGR=Manager | OWN=Owner'),
    ("an auditor token carries the auditor's letter",
     '_probe', 'token_for',
     ['u7', 'auditor'],
     'v1.a.u7.705'),
    ('an auditor token round-trips and refuses an edit',
     '_probe', 'token_round_trips',
     ['u7', 'auditor'],
     True),
    ('an auditor token decodes back to the auditor',
     '_probe', 'role_from_token',
     ['v1.a.u7.705'],
     'auditor'),
    ('the auditor gets its own rate limit', '_probe', 'limit_for', ['auditor'], 120),
    ("the auditor's bucket is exactly that wide",
     '_probe', 'throttle_discriminates',
     ['auditor', 120],
     True),
    ('the tier table gains the auditor, in role order',
     '_probe', 'tier_rows',
     [],
     [['guest', 10], ['member', 60], ['auditor', 120], ['manager', 300], ['owner', 600]]),
]

REGRESSION = [
    ('the owner still prints as Owner', '_probe', 'title_of', ['owner'], 'Owner'),
    ('the guest still prints as Guest', '_probe', 'title_of', ['guest'], 'Guest'),
    ('manager still ranks 20', '_probe', 'rank_of', ['manager'], 20),
    ('the owner still holds everything',
     '_probe', 'caps_of',
     ['owner'],
     ['read', 'comment', 'write', 'invite', 'billing', 'delete']),
    ('the guest still holds only read', '_probe', 'caps_of', ['guest'], ['read']),
    ('billing still belongs to the owner alone', '_probe', 'who_can', ['billing'], ['owner']),
    ('a manager invite still needs review', '_probe', 'decision', ['manager', 'invite'], 'review'),
    ('an owner delete still needs review', '_probe', 'decision', ['owner', 'delete'], 'review'),
    ('a guest write is still denied', '_probe', 'decision', ['guest', 'write'], 'deny'),
    ('a member comment still goes straight through',
     '_probe', 'decision',
     ['member', 'comment'],
     'allow'),
    ("a manager's audit line is untouched",
     '_probe', 'audit_line',
     [{'role': 'manager', 'action': 'invite', 'target': 'u42'}],
     '[MGR] Manager              invite u42'),
    ("a guest's audit line is untouched",
     '_probe', 'audit_line',
     [{'role': 'guest', 'action': 'read', 'target': 'invoice-9'}],
     '[GST] Guest                read invoice-9'),
    ('an owner token is byte-identical', '_probe', 'token_for', ['u1', 'owner'], 'v1.o.u1.431'),
    ('a manager token still round-trips and refuses an edit',
     '_probe', 'token_round_trips',
     ['u9', 'manager'],
     True),
    ('an old manager token still decodes',
     '_probe', 'role_from_token',
     ['v1.n.u9.fda'],
     'manager'),
    ('the guest limit is unchanged', '_probe', 'limit_for', ['guest'], 10),
    ('the member bucket is still exactly 60 wide',
     '_probe', 'throttle_discriminates',
     ['member', 60],
     True),
    ('an unknown role still prints as its capitalised id',
     '_probe', 'title_of',
     ['robot'],
     'Robot'),
    ('an unknown role still ranks below everyone', '_probe', 'rank_of', ['robot'], 0),
    ('an unknown role still holds nothing', '_probe', 'caps_of', ['robot'], []),
    ('an unknown role is still denied outright', '_probe', 'decision', ['robot', 'read'], 'deny'),
    ('an unlisted role is still logged as ROLE',
     '_probe', 'audit_line',
     [{'role': 'robot', 'action': 'read', 'target': 'x'}],
     '[ROLE] Robot                read x'),
    ('an unlisted role still lands in the shared bucket', '_probe', 'limit_for', ['robot'], 60),
    ('a token letter we never issue is still refused',
     '_probe', 'unknown_role_code_is_refused',
     [],
     True),
]

TASK = build_repo_task(
    task_id='authkit_role',
    goal=GOAL,
    starting_files=STARTING_FILES,
    probe_files=PROBE_FILES,
    change=CHANGE,
    regression=REGRESSION,
)
