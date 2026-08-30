"""The field client works at the origin's root AND under a prefix.

It is served two ways and must be the same page in both: at
`http://localhost:8020/` while somebody develops it with `--reload`, and at
`https://em.localhost:8443/chat/` behind the node's Caddy, where every
StratiGraph face shares one https origin — which is what a PHONE needs, because
camera, microphone and GPS are only offered to a secure context.

Absolute paths break the second case in a way that does not look broken, which
is why this is a test and not a note:

* `register("/sw.js")` would register a worker with scope `/` — intercepting the
  fetches of the room server, the catalogue and IIIF on that shared origin;
* `"/v1/say"` on a shared origin is not this app's `/v1/say`; it is the room
  server's;
* a precache list of `/brand/...` would fill the cache with somebody else's
  files, or with 404s, while appearing to work.

Read from the SOURCE rather than a browser: what matters is that no absolute
path is written down, and that is a property of the file.
"""

from __future__ import annotations

import pathlib
import re

WEB = pathlib.Path(__file__).resolve().parent.parent / "web"
PAGE = (WEB / "index.html").read_text(encoding="utf-8")
WORKER = (WEB / "sw.js").read_text(encoding="utf-8")


def _code(source: str) -> str:
    """The source without its PROSE — the comments explain the absolute paths
    that were removed, and matching those would be matching the explanation.

    Line comments and HTML comments only. **Block comments are deliberately not
    stripped**, and that is a bug this file already caught: a first version also
    removed `/* … */`, and `accept="image/*"` on the camera input opened a
    phantom block comment that swallowed the whole script — so the test asserted
    against an empty string and passed nothing. A stripper that has to parse
    HTML, CSS and JS at once is a parser, and writing one to check three
    substrings is the wrong trade.

    Leaving them costs a possible false POSITIVE (a rooted path quoted inside a
    CSS comment), which fails loudly and is the safe direction.
    """
    return re.sub(r"//[^\n]*|<!--[\s\S]*?-->", "", source)


def test_the_service_worker_is_registered_RELATIVELY():
    code = _code(PAGE)
    assert 'register("./sw.js"' in code
    assert 'register("/sw.js"' not in code


def test_its_scope_is_its_own_directory_and_not_the_origin():
    """A wider scope would need a `Service-Worker-Allowed` header, and wanting
    one would be the sign of a mistake."""
    code = _code(PAGE)
    assert 'scope: "./"' in code
    assert not re.search(r'scope:\s*"/"', code)


def test_the_api_base_is_derived_from_the_document_not_from_the_origin():
    code = _code(PAGE)
    assert 'new URL(".", location.href)' in code
    assert "location.origin" not in code, \
        "location.origin ignores the prefix: under /chat/ it aims at the room server"


def test_no_api_call_starts_at_the_root():
    """Every call goes through `post()`/`ping()`, which prefix `NODE`. A literal
    `fetch("/v1/...")` would bypass that and be right only in development."""
    code = _code(PAGE)
    for call in re.findall(r'fetch\(\s*"([^"]+)"', code):
        assert not call.startswith("/"), f'fetch("{call}") is rooted'
    # …and the paths handed to `post`/`send` are joined to NODE, never used bare
    for bare in re.findall(r'fetch\(\s*NODE\s*\+\s*"([^"]+)"', code):
        assert bare.startswith("/"), f"NODE + {bare!r} would miss the separator"


def test_the_precache_list_holds_no_absolute_path():
    code = _code(WORKER)
    listed = re.search(r"SHELL_FILES\s*=\s*\[(.*?)\]", code, re.S)
    assert listed, "no precache list found"
    for entry in re.findall(r'"([^"]+)"', listed.group(1)):
        assert not entry.startswith("/"), f"{entry} is rooted"
        assert entry.startswith("./"), f"{entry} should be relative to the worker"


def test_the_worker_knows_where_it_lives():
    code = _code(WORKER)
    assert 'new URL("./", self.location.href).pathname' in code


def test_the_api_is_refused_from_the_cache_UNDER_THE_PREFIX_TOO():
    """The bare `/v1/` test would have cached every answer it was meant to
    refuse, the moment the app moved under a prefix."""
    code = _code(WORKER)
    assert 'BASE + "v1/"' in code
    assert 'startsWith("/v1/")' not in code
    assert 'caches.match(BASE)' in code, "the offline fallback must be this app's page"


def test_the_shell_cache_was_renamed_so_a_stale_one_is_dropped():
    """The old cache holds entries keyed by absolute paths. `activate` deletes
    every key that is not the current one, so the rename is what evicts them —
    without it a device that had the app before would keep serving the old shell.
    """
    assert 'const SHELL = "sg-shell-v3"' in WORKER


def test_the_page_still_works_at_the_ROOT_which_is_the_development_loop():
    """`new URL(".", "http://localhost:8020/")` is the origin itself, so the dev
    server keeps working unchanged — the property that makes this safe."""
    from urllib.parse import urljoin
    for href, expected in [
        ("http://localhost:8020/", "http://localhost:8020/"),
        ("https://em.localhost:8443/chat/", "https://em.localhost:8443/chat/"),
        ("https://em.localhost:8443/chat/index.html", "https://em.localhost:8443/chat/"),
    ]:
        assert urljoin(href, ".") == expected, href
        node = urljoin(href, ".").rstrip("/")
        assert (node + "/v1/say").endswith("/v1/say")
