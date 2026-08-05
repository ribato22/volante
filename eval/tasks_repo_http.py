"""The second wide repo goal: one new HTTP method across six per-method tables.

Same shape as `recordkit_field` and deliberately so: the measurement needs SIX to EIGHT
wide goals before a cross-goal interval means anything, and three goals returned
+0.333 [-1.101, +1.768] — an interval that includes zero because three points cannot
bound it. Within-goal variance was exactly zero across fifteen runs, so more runs add
nothing; only more goals do.

Every module carries its own per-method table, and PATCH differs from every one of
their fallbacks: it has a body where the fallback assumes none, it is NOT idempotent
where the fallback assumes it is, it needs a Content-Type where the fallback requires
nothing, and its retry policy differs from the optimistic default. That is the property
two earlier drafts lacked — a default that agrees with the requirement is not a
requirement, and a file whose omission is free is width the goal only claims.

Measured before it joined the suite: golden 1.0000, untouched tree 0.0000, and dropping
any single file costs at least three change cases (methods.py, the keystone every other
module reads, costs fourteen).
"""

from __future__ import annotations

from eval.repo_goal import build_repo_task, file_block

_METHODS = '''\
"""Which methods this toolkit knows about, and what each one means."""

METHODS = ("GET", "POST", "DELETE")


class UnknownMethod(ValueError):
    """A method the toolkit has no entry for."""


# Whether a request of this method carries a body. A method with no entry here is
# assumed to send nothing, which is the quiet answer for something we have never seen
# and the wrong one for anything that actually puts bytes on the wire.
HAS_BODY = {
    "GET": False,
    "POST": True,
    "DELETE": False,
}

# Whether sending the same request twice is the same as sending it once. Unlisted
# methods are assumed idempotent -- optimistic, and the assumption the client's retry
# policy was written against.
IDEMPOTENT = {
    "GET": True,
    "POST": False,
    "DELETE": True,
}


def normalise(method):
    """`get` and ` Get ` are both GET."""
    return method.strip().upper()


def is_known(method):
    return normalise(method) in METHODS


def check_known(method):
    name = normalise(method)
    if name not in METHODS:
        raise UnknownMethod(f"unknown method {name!r}")
    return name


def has_body(method):
    return HAS_BODY.get(normalise(method), False)


def is_idempotent(method):
    return IDEMPOTENT.get(normalise(method), True)
'''

_HEADERS = '''\
"""Header names are case-insensitive on the wire and fixed inside the toolkit, so
everything gets canonicalised on the way in."""

from methods import check_known

# The spelling we use once a header is through the door. Anything not listed keeps the
# lower-cased name it arrived with.
CANONICAL = {
    "accept": "Accept",
    "content-type": "Content-Type",
    "if-match": "If-Match",
    "user-agent": "User-Agent",
}

# What each method will not leave home without, in the order we report them. A method
# with no entry demands nothing at all, which is how a request nobody wrote a row for
# sails past every check this module exists to make.
REQUIRED = {
    "GET": ("Accept",),
    "POST": ("Accept", "Content-Type"),
    "DELETE": (),
}


def canonical(name):
    key = name.strip().lower()
    return CANONICAL.get(key, key)


def normalise_headers(headers):
    """Canonical names, stripped values."""
    return {canonical(k): str(v).strip() for k, v in headers.items()}


def required_for(method):
    return list(REQUIRED.get(check_known(method), ()))


def missing(method, headers):
    """The required headers this request has not got, in table order."""
    have = set(normalise_headers(headers))
    return [name for name in required_for(method) if name not in have]
'''

_STATUS = '''\
"""What a response code means, and which one each method answers with when it worked."""

from methods import normalise

REASON = {
    200: "OK",
    201: "Created",
    204: "No Content",
    400: "Bad Request",
    404: "Not Found",
    405: "Method Not Allowed",
    409: "Conflict",
    412: "Precondition Failed",
    500: "Internal Server Error",
    503: "Service Unavailable",
}

# The code each method answers with when nothing went wrong. A method with no entry is
# assumed to answer 200 -- what you would guess, and not what the service does.
SUCCESS_STATUS = {
    "GET": 200,
    "POST": 201,
    "DELETE": 204,
}


def reason(code):
    return REASON.get(code, "Unknown")


def success_status(method):
    return SUCCESS_STATUS.get(normalise(method), 200)


def describe(method, code):
    """One line about a response: the code, what it means, and whether we wanted it."""
    mark = "expected" if code == success_status(method) else "unexpected"
    return f"{normalise(method)} {code} {reason(code)} ({mark})"
'''

_ROUTER = '''\
"""Which handler serves a request, and what to say when none of them does."""

from methods import METHODS, check_known
from status import reason

# Every path a method serves, and the handler it lands on. A method with no entry here
# serves nothing anywhere, so it 404s across the whole surface -- silently, and only
# once somebody calls it.
ROUTES = {
    "GET": {"/items": "list_items", "/items/{id}": "get_item"},
    "POST": {"/items": "create_item"},
    "DELETE": {"/items/{id}": "delete_item"},
}


def _matches(pattern, path):
    want = pattern.strip("/").split("/")
    got = path.strip("/").split("/")
    if len(want) != len(got):
        return False
    return all(w.startswith("{") or w == g for w, g in zip(want, got))


def resolve(method, path):
    """The handler name, or None when this method does not serve this path."""
    for pattern, handler in ROUTES.get(check_known(method), {}).items():
        if _matches(pattern, path):
            return handler
    return None


def allow(path):
    """The `Allow` header for a path: every method that serves it, in table order."""
    serving = [m for m in METHODS if any(_matches(p, path) for p in ROUTES.get(m, {}))]
    return ", ".join(serving)


def dispatch(method, path):
    """The handler, or the status line for a request nothing here will serve."""
    handler = resolve(method, path)
    if handler is not None:
        return handler
    allowed = allow(path)
    if allowed:
        return f"405 {reason(405)} (Allow: {allowed})"
    return f"404 {reason(404)}"
'''

_LOG = '''\
"""One line per request, in the shape our log parser expects."""

from methods import normalise
from status import reason

# What we call each method in the log. A method with no entry is logged as `call`,
# which parses cleanly and tells the person reading it nothing.
VERB = {
    "GET": "read",
    "POST": "create",
    "DELETE": "delete",
}

# Both columns are padded so the paths line up down the page.
VERB_WIDTH = 6
METHOD_WIDTH = 6


def verb(method):
    return VERB.get(normalise(method), "call")


def line(method, path, code):
    return (
        f"{verb(method):<{VERB_WIDTH}} {normalise(method):<{METHOD_WIDTH}} "
        f"{path} -> {code} {reason(code)}"
    )


def lines(requests):
    """One log line per `(method, path, code)` triple."""
    return "\\n".join(line(m, p, c) for m, p, c in requests)
'''

_RETRY = '''\
"""How hard the client tries when the network is having a day."""

from methods import normalise

# How many times we send each method IN TOTAL, first attempt included. A method with no
# entry gets the house default of three, which is exactly right for a request that is
# safe to repeat and exactly wrong for one that is not.
ATTEMPTS = {
    "GET": 3,
    "POST": 1,
    "DELETE": 2,
}

RETRY_ON = (429, 500, 502, 503, 504)
BASE_DELAY_MS = 200


def attempts(method):
    return ATTEMPTS.get(normalise(method), 3)


def should_retry(method, code, sent):
    """Given `sent` attempts already made, do we send it again?"""
    return code in RETRY_ON and sent < attempts(method)


def backoff(method):
    """The delays between sends, in ms -- one fewer than the attempt count."""
    return [BASE_DELAY_MS * 2**i for i in range(attempts(method) - 1)]
'''

STARTING_FILES = (
    ("methods.py", _METHODS),
    ("headers.py", _HEADERS),
    ("status.py", _STATUS),
    ("router.py", _ROUTER),
    ("log.py", _LOG),
    ("retry.py", _RETRY),
)

_PROBE = '''\
import headers
import log
import methods
import retry
import router
import status


def method_table():
    return list(methods.METHODS)


def known(name):
    return methods.is_known(name)


def has_body(name):
    return methods.has_body(name)


def idempotent(name):
    return methods.is_idempotent(name)


def method_rule_discriminates():
    return methods.is_known("patch") and not methods.is_known("TRACE")


def required_for(name):
    return headers.required_for(name)


def missing_headers(name, hdrs):
    return headers.missing(name, hdrs)


def canonical(name):
    return headers.canonical(name)


def header_rule_discriminates():
    full = {"accept": "*/*", "content-type": "application/json", "if-match": '"v7"'}
    without = {k: v for k, v in full.items() if k != "if-match"}
    return headers.missing("PATCH", full) == [] and headers.missing("PATCH", without) == [
        "If-Match"
    ]


def success_status(name):
    return status.success_status(name)


def describe(name, code):
    return status.describe(name, code)


def reason(code):
    return status.reason(code)


def status_rule_discriminates():
    return status.describe("PATCH", 204).endswith("(expected)") and status.describe(
        "PATCH", 200
    ).endswith("(unexpected)")


def resolve(name, path):
    return router.resolve(name, path)


def allow(path):
    return router.allow(path)


def dispatch(name, path):
    return router.dispatch(name, path)


def route_rule_discriminates():
    return router.resolve("PATCH", "/items/7") == "patch_item" and (
        router.resolve("PATCH", "/items") is None
    )


def verb(name):
    return log.verb(name)


def log_line(name, path, code):
    return log.line(name, path, code)


def log_rule_discriminates():
    return log.verb("PATCH") == "update" and log.verb("GET") == "read"


def attempts(name):
    return retry.attempts(name)


def backoff(name):
    return retry.backoff(name)


def should_retry(name, code, sent):
    return retry.should_retry(name, code, sent)


def retry_rule_discriminates():
    return not retry.should_retry("PATCH", 503, 1) and bool(
        retry.should_retry("GET", 503, 1)
    )
'''

PROBE_FILES = (("_probe.py", _PROBE),)


def _sub(src: str, old: str, new: str) -> str:
    if old not in src:
        raise AssertionError(f"golden patch did not apply: {old!r}")
    return src.replace(old, new, 1)


GOLDEN_FILES = (
    (
        "methods.py",
        _sub(
            _sub(
                _sub(
                    _METHODS,
                    'METHODS = ("GET", "POST", "DELETE")',
                    'METHODS = ("GET", "POST", "PATCH", "DELETE")',
                ),
                '    "POST": True,\n    "DELETE": False,',
                '    "POST": True,\n    "PATCH": True,\n    "DELETE": False,',
            ),
            '    "POST": False,\n    "DELETE": True,',
            '    "POST": False,\n    "PATCH": False,\n    "DELETE": True,',
        ),
    ),
    (
        "headers.py",
        _sub(
            _HEADERS,
            '    "POST": ("Accept", "Content-Type"),\n    "DELETE": (),',
            '    "POST": ("Accept", "Content-Type"),\n'
            '    "PATCH": ("Accept", "Content-Type", "If-Match"),\n'
            '    "DELETE": (),',
        ),
    ),
    (
        "status.py",
        _sub(
            _STATUS,
            '    "POST": 201,\n    "DELETE": 204,',
            '    "POST": 201,\n    "PATCH": 204,\n    "DELETE": 204,',
        ),
    ),
    (
        "router.py",
        _sub(
            _ROUTER,
            '    "POST": {"/items": "create_item"},\n    "DELETE"',
            '    "POST": {"/items": "create_item"},\n'
            '    "PATCH": {"/items/{id}": "patch_item"},\n    "DELETE"',
        ),
    ),
    (
        "log.py",
        _sub(
            _LOG,
            '    "POST": "create",\n    "DELETE": "delete",',
            '    "POST": "create",\n    "PATCH": "update",\n    "DELETE": "delete",',
        ),
    ),
    (
        "retry.py",
        _sub(
            _RETRY,
            '    "POST": 1,\n    "DELETE": 2,',
            '    "POST": 1,\n    "PATCH": 1,\n    "DELETE": 2,',
        ),
    ),
)

GOLDEN = "\n\n".join(
    f"```python\n# path: {path}\n{body.rstrip()}\n```" for path, body in GOLDEN_FILES
)

CHANGE = [
    ("PATCH is a method the toolkit knows", "_probe", "known", ["PATCH"], True),
    ("PATCH carries a body, like POST", "_probe", "has_body", ["patch"], True),
    ("PATCH is not idempotent", "_probe", "idempotent", ["PATCH"], False),
    ("PATCH is known and TRACE still is not", "_probe", "method_rule_discriminates", [], True),
    ("PATCH sits between POST and DELETE in the method table", "_probe", "method_table", [],
     ["GET", "POST", "PATCH", "DELETE"]),
    ("PATCH on the collection is refused the usual way", "_probe", "dispatch",
     ["PATCH", "/items"], "405 Method Not Allowed (Allow: GET, POST)"),

    ("PATCH demands three headers, in order", "_probe", "required_for", ["PATCH"],
     ["Accept", "Content-Type", "If-Match"]),
    ("a PATCH with only Accept is missing two", "_probe", "missing_headers",
     ["PATCH", {"accept": "*/*"}], ["Content-Type", "If-Match"]),
    ("a PATCH without If-Match is missing one", "_probe", "missing_headers",
     ["PATCH", {"Accept": "*/*", "CONTENT-TYPE": "application/json"}], ["If-Match"]),
    ("the header rule both accepts and complains", "_probe", "header_rule_discriminates", [], True),

    ("a PATCH that worked answers 204", "_probe", "success_status", ["PATCH"], 204),
    ("204 reads as expected for PATCH", "_probe", "describe", ["PATCH", 204],
     "PATCH 204 No Content (expected)"),
    ("200 reads as unexpected for PATCH", "_probe", "describe", ["patch", 200],
     "PATCH 200 OK (unexpected)"),
    ("the status rule both expects and does not", "_probe", "status_rule_discriminates", [], True),

    ("PATCH on an item reaches patch_item", "_probe", "resolve", ["PATCH", "/items/7"],
     "patch_item"),
    ("the item path now allows PATCH, in table order", "_probe", "allow", ["/items/7"],
     "GET, PATCH, DELETE"),
    ("a wrong-method request on an item now advertises PATCH", "_probe", "dispatch",
     ["POST", "/items/7"], "405 Method Not Allowed (Allow: GET, PATCH, DELETE)"),
    ("the route both resolves and declines", "_probe", "route_rule_discriminates", [], True),

    ("PATCH logs as update", "_probe", "verb", ["PATCH"], "update"),
    ("the log line keeps its columns", "_probe", "log_line", ["PATCH", "/items/7", 204],
     "update PATCH  /items/7 -> 204 No Content"),
    ("the log line normalises the method", "_probe", "log_line", ["patch", "/items/7", 412],
     "update PATCH  /items/7 -> 412 Precondition Failed"),
    ("the log rule renames PATCH and nothing else", "_probe", "log_rule_discriminates", [], True),

    ("PATCH is sent exactly once", "_probe", "attempts", ["PATCH"], 1),
    ("PATCH has no backoff schedule", "_probe", "backoff", ["PATCH"], []),
    ("the retry rule drops PATCH and keeps GET", "_probe", "retry_rule_discriminates", [], True),
]

REGRESSION = [
    ("POST still carries a body", "_probe", "has_body", ["POST"], True),
    ("GET still carries none", "_probe", "has_body", ["GET"], False),
    ("DELETE is still idempotent", "_probe", "idempotent", ["DELETE"], True),
    ("POST still is not", "_probe", "idempotent", ["POST"], False),
    ("a method we do not have is still unknown", "_probe", "known", ["TRACE"], False),
    ("header names are still canonicalised", "_probe", "canonical", ["  CONTENT-Type "],
     "Content-Type"),
    ("an unlisted header still comes through lower-cased", "_probe", "canonical", ["X-Trace"],
     "x-trace"),
    ("POST still wants a content type", "_probe", "missing_headers",
     ["POST", {"accept": "*/*"}], ["Content-Type"]),
    ("DELETE still demands nothing", "_probe", "required_for", ["DELETE"], []),
    ("reason phrases still resolve", "_probe", "reason", [404], "Not Found"),
    ("POST still succeeds with 201", "_probe", "describe", ["POST", 201],
     "POST 201 Created (expected)"),
    ("a GET 500 still reads as unexpected", "_probe", "describe", ["GET", 500],
     "GET 500 Internal Server Error (unexpected)"),
    ("GET on an item still routes", "_probe", "resolve", ["GET", "/items/7"], "get_item"),
    ("the collection still allows exactly GET and POST", "_probe", "allow", ["/items"],
     "GET, POST"),
    ("DELETE on the collection is still a 405", "_probe", "dispatch", ["DELETE", "/items"],
     "405 Method Not Allowed (Allow: GET, POST)"),
    ("an unrouted path is still a 404", "_probe", "dispatch", ["GET", "/orders"],
     "404 Not Found"),
    ("the GET log line is unchanged", "_probe", "log_line", ["GET", "/items", 200],
     "read   GET    /items -> 200 OK"),
    ("DELETE still logs as delete", "_probe", "verb", ["DELETE"], "delete"),
    ("GET is still tried three times", "_probe", "attempts", ["GET"], 3),
    ("the GET backoff schedule is unchanged", "_probe", "backoff", ["GET"], [200, 400]),
    ("POST still has no backoff", "_probe", "backoff", ["POST"], []),
    ("a failed GET is still sent again", "_probe", "should_retry", ["GET", 503, 1], True),
    ("a 404 is still not worth retrying", "_probe", "should_retry", ["GET", 404, 1], False),
]

CHANGE_REQUEST = """\
WHAT I NEED
-----------
We are adding `PATCH` to the toolkit. Our docs list the methods as GET, POST, PATCH,
DELETE and the code should read the same way, so it goes between POST and DELETE
wherever methods are listed in order.
"""

HTTPKIT_GOAL = (
    "Here is our little HTTP toolkit. It works today.\n\n"
    + file_block(STARTING_FILES)
    + "\n\n"
    + CHANGE_REQUEST
)

HTTPKIT_TASK = build_repo_task(
    task_id="httpkit_method",
    goal=HTTPKIT_GOAL,
    starting_files=STARTING_FILES,
    probe_files=PROBE_FILES,
    change=CHANGE,
    regression=REGRESSION,
)
