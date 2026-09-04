"""The field assistant SIGNS, and the tablet changes hands without changing author.

Design note *«Il field assistant firma, e ricorda»* §0: **no ingestion without an
author.** The node already refuses to write without an identity — that refusal
lives in `contract.py` and is structural. What was missing was the other half:
a page that stops somebody from dictating into a form whose words could never be
attributed, and a hand-over that actually hands over.

Three properties are worth a test rather than a comment, because each of them
fails SILENTLY:

* a token written to disk outlives the person holding the device, and nothing
  about the page would look different;
* an authorization code left in the address bar is a code in the history and in
  every screenshot, and the sign-in works anyway;
* «esci» that only forgets the token lets the next sign-in walk back in on
  Keycloak's cookie **as the previous person** — the whole trap, and the screen
  looks correct while it happens.

Read from the SOURCE, like `test_pwa_under_a_prefix.py`: these are properties of
the file, and a browser is not needed to see them.
"""

from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

WEB = pathlib.Path(__file__).resolve().parent.parent / "web"
PAGE = (WEB / "index.html").read_text(encoding="utf-8")


def _code(source: str) -> str:
    """The source without its prose — the comments EXPLAIN the things being
    forbidden, so matching them would be matching the explanation. Same stripper
    and same caveat as `test_pwa_under_a_prefix._code`."""
    return re.sub(r"//[^\n]*|<!--[\s\S]*?-->", "", source)


CODE = _code(PAGE)


# ── reading the page's own dictionaries ──────────────────────────────────────
#
# The strings live INLINE in the page, by design: it is one HTML file so it can
# be read and installed on a device in a trench, with no build step and nothing
# to fetch. So a test that wants to check a string reads them out of the source,
# the same way the rest of this file reads properties out of it.

def locales() -> dict:
    """`{locale: {key: value}}` as the page declares them."""
    block = re.search(r"const STRINGS = \{(.*?)\n\};", PAGE, re.S)
    assert block, "the page declares no STRINGS"
    found: dict = {}
    for match in re.finditer(r"^  (\w+): \{(.*?)^  \}", block.group(1),
                             re.S | re.M):
        code, body = match.group(1), match.group(2)
        found[code] = {
            key: value for key, value in
            re.findall(r'"([^"]+)":\s*"((?:[^"\\]|\\.)*)"', body)
        }
    # the empty-slot locales are declared on one line: `ro: {}, el: {}, …`
    for code in re.findall(r"(\w+): \{\},", block.group(1)):
        found.setdefault(code, {})
    return found


LOCALES = locales()


def _string(locale: str, key: str) -> str:
    return LOCALES.get(locale, {}).get(key, "")


# ── the token never touches the disk ─────────────────────────────────────────

def test_the_token_is_never_written_to_storage():
    """A field device is lent, dropped and borrowed. What the page keeps on its
    disk outlives the person who was holding it."""
    for call in re.findall(r"(localStorage|sessionStorage)\.setItem\(([^,]+),",
                           CODE):
        store, key = call
        assert "TOKEN" not in key, f"{store}.setItem({key.strip()}) stores a token"
    # …and no assignment reads one back out
    assert not re.search(r"TOKEN\s*=\s*\w*[sS]torage", CODE)
    assert "sg.queue" in CODE and "sg.pkce" in CODE, "the two keys we DO write"


def test_only_the_verifier_and_the_state_survive_the_redirect():
    """They have to: the page is unloaded while the browser is at Keycloak. They
    are not credentials, they are use-and-throw, and `sessionStorage` dies with
    the tab."""
    saved = re.findall(r"sessionStorage\.setItem\(([^,]+),", CODE)
    assert saved == ["PKCE_KEY"], saved
    assert re.search(r"sessionStorage\.removeItem\(PKCE_KEY\)", CODE), \
        "a verifier that outlives its exchange is a spare key on the doormat"


def test_the_token_is_adopted_in_exactly_one_place():
    """`adopt()` is the only assignment, so «who is signed» and «which token» can
    never drift apart."""
    # `let TOKEN = ""` is the declaration; these are the WRITES.
    assignments = re.findall(r"^\s+TOKEN\s*=", CODE, re.M)
    assert len(assignments) == 2, assignments   # adopt() and signOut()


# ── the code does not stay in the address bar ────────────────────────────────

def test_the_url_is_cleaned_after_the_exchange():
    assert "history.replaceState({}, \"\", location.pathname)" in CODE
    body = CODE[CODE.index("async function signInReturn"):]
    body = body[:body.index("\nfunction signOut")]
    assert "cleanUrl();" in body, "the code must come off the URL on EVERY path"
    # …before the exchange, so an error path cannot leave it behind either
    assert body.index("cleanUrl();") < body.index("token_endpoint")


def test_the_state_is_checked_before_the_code_is_spent():
    """A code delivered with somebody else's state is a code this tab did not
    ask for. Without this the round trip means nothing."""
    body = CODE[CODE.index("async function signInReturn"):]
    assert "returned !== saved.state" in body
    assert body.index("returned !== saved.state") < body.index("grant_type")


def test_the_challenge_is_S256_and_the_verifier_is_random():
    assert 'code_challenge_method", "S256"' in CODE
    assert 'crypto.subtle.digest(\n    "SHA-256"' in CODE or \
           '"SHA-256"' in CODE
    assert "crypto.getRandomValues" in CODE


# ── the hand-over ────────────────────────────────────────────────────────────

def test_signing_out_goes_through_the_realm_and_not_only_this_tab():
    """THE measure of this whole change. If «esci» only forgot the token, the
    next touch on «firma» would find Keycloak's session cookie still valid and
    return silently AS THE PREVIOUS PERSON."""
    body = CODE[CODE.index("function signOut()"):]
    body = body[:body.index("function render()")]
    assert "end_session_endpoint" in body
    assert "post_logout_redirect_uri" in body, \
        "Keycloak needs client_id + post_logout_redirect_uri to come back here"
    assert "location.assign" in body, "forgetting the token is not signing out"


def test_a_node_without_a_realm_logout_says_so_instead_of_pretending():
    body = CODE[CODE.index("function signOut()"):]
    body = body[:body.index("function render()")]
    # the sentence itself now lives in the dictionary, keyed — so what this
    # asserts is that the branch SAYS something, and that the something exists
    # in both complete locales.
    assert 't("signout.local")' in body
    for locale in ("en", "it"):
        assert _string(locale, "signout.local"), locale


# ── no dictation without a signature ─────────────────────────────────────────

def test_the_work_area_and_the_signature_start_HIDDEN_in_the_markup():
    """Not hidden by a script that runs later: for the instant before it runs,
    the three gestures would be on screen, and on a slow device that instant is
    long enough to touch one."""
    assert re.search(r'<section id="work" hidden>', PAGE)
    assert re.search(r'<div class="signature" id="signature" hidden>', PAGE)


def test_the_input_bar_lives_inside_the_work_area():
    """The gate is not a banner over a working page — the page does not work."""
    work = PAGE[PAGE.index('<section id="work"'):]
    work = work[:work.index("</section>")]
    for control in ('id="typed"', 'id="rec"', 'id="send"', 'id="shoot"'):
        assert control in work, f"{control} is reachable without a signature"


def test_a_node_that_cannot_attribute_offers_no_sign_in_button():
    """dev-no-auth is not «sign in here»: it is «this node could not attribute
    what you said». Offering a button that cannot work is the worse lie."""
    body = CODE[CODE.index("function render()"):]
    assert '$("signin").hidden = !canSign()' in body
    assert 't("gate.why.noAuth")' in body
    assert _string("en", "gate.why.noAuth")


# ── the claim spellings agree with the node's ────────────────────────────────

def test_the_page_reads_the_SAME_orcid_claims_as_the_node():
    """The node stamps the author from the token; the page only shows it and
    files it beside a queued note. If the two lists disagreed, the queue would
    hold notes for a person the node thinks is somebody else."""
    from app.auth import ORCID_CLAIMS

    # Read from PAGE and not from CODE, and that is not an oversight: the
    # comment stripper eats `//orcid.org/id` because it begins with `//`. The
    # same trap the block-comment note in `test_pwa_under_a_prefix` describes,
    # sprung from the other side — and this test caught it the first time it ran.
    listed = re.search(r"const ORCID_CLAIMS = \[(.*?)\];", PAGE, re.S)
    assert listed, "the page does not declare the claim list"
    page_claims = tuple(re.findall(r'"([^"]+)"', listed.group(1)))
    assert page_claims == ORCID_CLAIMS


def test_the_claims_are_read_for_DISPLAY_and_decide_nothing():
    """The page does not verify the signature and must not: the node checks it
    against the realm's JWKS and takes the author from what it verified."""
    assert "display only" in PAGE.lower()
    assert "jwt.verify" not in CODE and "verifySignature" not in CODE


# ── the queue carries who dictated ───────────────────────────────────────────

def _pure_queue_rules() -> str:
    """The queue rules as a self-contained block: `isMine` through `heldBy`,
    with no DOM, no storage and — since the page learned six languages — no
    `t()` between them. That they CAN be sliced out is half the point: a rule
    tangled into the rendering is a rule that can only be checked by eye.

    `queueSentence` is deliberately OUTSIDE this block now. It builds a sentence
    and a sentence has a language; the RULE is the partition, and that is what
    stays pure."""
    start = CODE.index("const isMine =")
    end = CODE.index("function queueSentence(")
    return CODE[start:end]


def test_the_queue_rules_are_pure():
    block = _pure_queue_rules()
    for forbidden in ("localStorage", "document", "$(", "fetch("):
        assert forbidden not in block, f"{forbidden} in the queue rules"


@pytest.mark.skipif(shutil.which("node") is None,
                    reason="no JS runtime here to RUN the rule")
def test_only_the_signed_persons_notes_are_sent_and_the_rest_are_ANNOUNCED():
    """Design note §3.2, run rather than read.

    A note queued by Anna at 10:00 and sent at 14:00 while Marco is signed would
    become a note BY MARCO — and the node cannot notice: it takes the author from
    the token it sees and does the right thing with the wrong fact.
    """
    harness = _pure_queue_rules() + """
const queue = [
  { subject: "0000-0001-0000-0001", by: "Anna Rossi",    describe: "US 12" },
  { subject: "0000-0002-0000-0002", by: "Marco Bianchi", describe: "US 13" },
  { subject: "0000-0001-0000-0001", by: "Anna Rossi",    describe: "foto"  },
  { subject: "",                    by: "",              describe: "vecchia" },
];
console.log(JSON.stringify({
  anna:  partitionQueue(queue, "0000-0001-0000-0001"),
  marco: partitionQueue(queue, "0000-0002-0000-0002"),
  nobody: partitionQueue(queue, ""),
}));
"""
    out = subprocess.run([shutil.which("node"), "--input-type=module", "-e", harness],
                         capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    result = json.loads(out.stdout)

    # Anna is signed: her two go, the other two WAIT.
    assert [i["describe"] for i in result["anna"]["mine"]] == ["US 12", "foto"]
    assert [i["describe"] for i in result["anna"]["held"]] == ["US 13", "vecchia"]
    # Marco signs instead: the partition flips, and nothing was re-attributed.
    assert [i["describe"] for i in result["marco"]["mine"]] == ["US 13"]
    assert len(result["marco"]["held"]) == 3
    # Nobody signed: NOTHING goes. An unsigned flush is the failure mode itself.
    assert result["nobody"]["mine"] == []
    assert len(result["nobody"]["held"]) == 4


def test_the_page_SAYS_whose_the_waiting_notes_are():
    """The rule holds them; this is the half that tells somebody. Asserted on the
    dictionary rather than on a rendered string, because the sentence now has six
    possible languages and the placeholder is the contract."""
    for key in ("queue.mine", "queue.theirs.one", "queue.theirs.many",
                "queue.unsigned.one", "queue.unsigned.many"):
        assert _string("en", key), key
    assert "{who}" in _string("en", "queue.theirs.many")
    assert "{n}" in _string("en", "queue.mine")
    body = CODE[CODE.index("function queueSentence("):]
    body = body[:body.index("function renderQueue()")]
    assert 't("queue.theirs"' in body and 't("queue.unsigned"' in body


def test_a_queued_note_is_stamped_when_it_is_DICTATED_not_when_it_is_sent():
    body = CODE[CODE.index("async function send("):]
    body = body[:body.index("async function flush()")]
    assert "subject: WHO?.subject" in body, \
        "stamped at send time it would carry whoever is signed hours later"


def test_flush_keeps_somebody_elses_note_in_its_place():
    body = CODE[CODE.index("async function flush()"):]
    body = body[:body.index("async function ping()")]
    assert "if (!isMine(item, subject)) { left.push(item); continue; }" in body
    assert "writeQueue(left)" in body


# ── the node says how a browser signs in, on its OWN origin ──────────────────

@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    from app import main as main_module
    return TestClient(main_module.app)


@pytest.fixture()
def realm(monkeypatch):
    """Make this node enforce a realm, without a Keycloak to talk to."""
    from app.auth import OidcSettings, authenticator

    issuer = "https://sso.example.org/realms/em"
    monkeypatch.setattr(
        authenticator, "settings",
        OidcSettings(issuer=issuer, audience="stratigraph-chatbot",
                     jwks_uri=f"{issuer}/protocol/openid-connect/certs"))
    return issuer


def test_auth_config_is_reachable_WITHOUT_a_token(client, realm):
    """It has to be: it is what a browser reads in order to GET one. There is no
    secret in it — an issuer and a public client id are public by construction."""
    answer = client.get("/v1/auth-config")
    assert answer.status_code == 200


def test_the_endpoints_are_derived_from_the_issuer(client, realm):
    """Derived and not configured, for the reason `auth.py` gives about the
    issuer and the JWKS: two URLs that must agree are two URLs that will one day
    disagree."""
    config = client.get("/v1/auth-config").json()
    assert config["enforcing"] is True
    assert config["issuer"] == realm
    assert config["authorization_endpoint"] == f"{realm}/protocol/openid-connect/auth"
    assert config["token_endpoint"] == f"{realm}/protocol/openid-connect/token"
    assert config["end_session_endpoint"] == f"{realm}/protocol/openid-connect/logout"


def test_the_browser_client_is_public_and_overridable(client, realm, monkeypatch):
    config = client.get("/v1/auth-config").json()
    assert config["client_id"] == "em-console", "the stack's existing PUBLIC client"
    monkeypatch.setenv("EM_CHATBOT_CLIENT_ID", "em-field")
    assert client.get("/v1/auth-config").json()["client_id"] == "em-field"


def test_a_node_with_no_realm_advertises_NOTHING_rather_than_a_dead_endpoint(client):
    """dev-no-auth. The page must be able to tell «this node cannot attribute»
    apart from «here is where to sign in», and an issuer-less endpoint like
    `/protocol/openid-connect/auth` would look like the second."""
    config = client.get("/v1/auth-config").json()
    assert config["enforcing"] is False
    for field in ("issuer", "client_id", "authorization_endpoint",
                  "token_endpoint", "end_session_endpoint"):
        assert config[field] == "", field


def test_the_page_asks_ITS_OWN_node_and_not_the_room_server(client):
    """`/em/v1/auth-config` is one fetch away behind Caddy and would work today.
    It would also bake in the knowledge that the room server lives under `/em` —
    true now, invisible when it stops being, and wrong at `:8020` bare."""
    assert 'fetch(NODE + "/v1/auth-config"' in CODE
    assert "/em/v1/" not in CODE


# ── a node that cannot attribute SAYS what is in the way ─────────────────────

def test_health_and_auth_config_tell_the_SAME_story(client):
    """One helper feeds both. A probe and a gate that disagreed about why the
    node cannot take a dictation would send two people looking in two places."""
    health = client.get("/health").json()
    config = client.get("/v1/auth-config").json()
    assert health["accepts_dictation"] is False
    assert health["missing"] == config["missing"]


def test_what_is_missing_is_NAMED_and_not_merely_absent(client):
    """The habit `auth.py` set when it refuses to start on a half-configured
    realm: say what is not there rather than behave oddly. A bare gate cannot be
    told apart from a forgotten variable by the person standing in front of it."""
    missing = client.get("/health").json()["missing"]
    assert any("OIDC_ISSUER" in m for m in missing), missing
    assert any("OIDC_AUDIENCE" in m for m in missing), missing


def test_a_node_that_enforces_has_nothing_in_the_way(client, realm):
    health = client.get("/health").json()
    assert health["accepts_dictation"] is True
    assert health["missing"] == []


def test_declared_anonymity_is_IN_THE_WAY_too(client, monkeypatch):
    """`EM_CHATBOT_ALLOW_ANON` is not a missing variable — it is a present one,
    and it still leaves the dictation with nobody to attribute."""
    from app.auth import OidcSettings, authenticator

    monkeypatch.setattr(authenticator, "settings",
                        OidcSettings(anon_declared=True))
    missing = client.get("/health").json()["missing"]
    assert any("ALLOW_ANON" in m for m in missing), missing


def test_the_gate_repeats_what_the_node_named(client):
    assert "AUTHCFG.missing" in CODE
    assert 't("gate.why.missing"' in CODE
    assert "{what}" in _string("en", "gate.why.missing")


# ── the theme is what makes `hidden` win, and it is vendored ─────────────────

def test_the_vendored_theme_carries_the_hidden_rule():
    """It is a USER-AGENT rule the browser ships, so any `display:` an app
    writes beats it — on ORIGIN, not on specificity. The theme puts it back for
    all four StratiGraph faces at once; this page must not carry its own copy,
    or the day the theme changes there would be two answers."""
    theme = (WEB / "brand" / "stratigraph-theme.css").read_text(encoding="utf-8")
    assert "[hidden] { display: none !important; }" in theme
    # The page has no `<style>` of its own since 2026-09-23 — the CSS lives in
    # `shell.css` and `scheda.css`. The property is the same and now covers
    # both: no stylesheet of this app carries a second copy of the rule.
    import pathlib as _pathlib
    web = _pathlib.Path(__file__).resolve().parent.parent / "web"
    assert "<style>" not in PAGE, (
        "the page grew a style block again: the CSS was extracted so that "
        "there is one place to look")
    page_style = "\n".join((web / name).read_text(encoding="utf-8")
                            for name in ("shell.css", "scheda.css"))
    assert "[hidden] { display: none" not in page_style, \
        "the rule belongs to the theme; a local copy is a second source of truth"


# ── which graph language this node runs ──────────────────────────────────────

def test_health_says_which_s3dgraphy_is_running(client):
    """The three services of a stack share em.json files and one vocabulary, and
    they install s3Dgraphy separately because their extras differ. This one was
    the only face that did not SAY which version it had — which is part of why
    three images installing three different specs went unnoticed until somebody
    read a build log. `dev-stack/smoke_s3dgraphy.py` is what reads it.
    """
    import s3dgraphy

    assert client.get("/health").json()["s3dgraphy"] == s3dgraphy.__version__
    assert client.get("/v1/health").json()["s3dgraphy"] == s3dgraphy.__version__
