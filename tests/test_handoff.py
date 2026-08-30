"""The deep-link, consumed by the field assistant.

The link is StratiGraph Server's contract and its one property is that it carries
a place and never a permission. So the measurement is not only "do we read it"
but "would we ACCEPT a credential in one" — and, on the way out, that a
`RoomWriter` ends up configured without anybody typing an address, a room name or
a token.

The same strings appear in `stratigraph-server/tests/test_handoff.py` and
`EMStudio/frontend/scripts/check-handoff.mjs`. Three implementations of one
grammar drift unless something holds them to the same inputs.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import handoff as ho                       # noqa: E402
from app.writer import LocalWriter, RoomWriter, writer_from_env  # noqa: E402

SECRETS = ("token", "access_token", "id_token", "password", "secret", "code",
           "authorization", "bearer", "api_key")
ORCID = "0000-0002-1825-0097"


# ── 1 · the grammar ──────────────────────────────────────────────────────────

def test_both_forms_read_back_to_the_same_place():
    scheme = "stratigraph://open?server=https%3A%2F%2Fem.example.org&room=saggio-b"
    web = "https://em.example.org/open?server=https%3A%2F%2Fem.example.org&room=saggio-b"
    assert ho.parse(scheme) == ho.parse(web) == {
        "server": "https://em.example.org", "room": "saggio-b"}


def test_the_web_form_may_leave_the_server_implicit():
    assert ho.parse("https://em.example.org/open?room=r")["server"] == \
        "https://em.example.org"


@pytest.mark.parametrize("secret", SECRETS)
def test_a_link_carrying_a_credential_is_refused_by_name(secret):
    with pytest.raises(ho.HandoffError) as exc:
        ho.parse(f"stratigraph://open?server=https%3A%2F%2Fx&room=r&{secret}=v")
    assert secret in str(exc.value)
    assert "never a permission" in str(exc.value)


@pytest.mark.parametrize("bad, fragment", [
    ("", "empty"),
    ("stratigraph://join?room=r", "unknown action"),
    ("mailto:someone@example.org", "not a handoff link"),
    ("https://em.example.org/rooms?room=r", "not a handoff link"),
    ("stratigraph://open?server=https%3A%2F%2Fx", "names no room"),
])
def test_what_is_not_a_handoff_is_said(bad, fragment):
    with pytest.raises(ho.HandoffError) as exc:
        ho.parse(bad)
    assert fragment in str(exc.value)


def test_the_scheme_is_the_ecosystems():
    assert ho.SCHEME == "stratigraph"
    assert ho.ACTION == "open"


# ── 2 · link → a configured writer ───────────────────────────────────────────

def test_a_link_configures_the_room_writer_without_anybody_typing_anything():
    asked = []

    def fake_sign_in(server):
        asked.append(server)
        return "tok-from-oidc"

    writer = ho.writer_from_link(
        "stratigraph://open?server=https%3A%2F%2Fem.example.org&room=saggio-b",
        sign_in_with=fake_sign_in)
    assert isinstance(writer, RoomWriter)
    assert writer.base_url == "https://em.example.org"
    assert writer.room_id == "saggio-b"
    # the token came from the SIGN-IN, against the server the LINK named
    assert asked == ["https://em.example.org"]
    assert writer._token == "tok-from-oidc"


def test_a_node_with_no_oidc_joins_without_a_token_rather_than_failing():
    """A dev stack runs open. That is a fact about the deployment, and refusing
    to work against it would make the honest case the broken one."""
    writer = ho.writer_from_link(
        "stratigraph://open?server=http%3A%2F%2F127.0.0.1%3A8000&room=r",
        sign_in_with=lambda _s: None)
    assert writer.room_id == "r" and writer._token == ""


def test_the_token_is_never_written_down():
    source = (Path(__file__).resolve().parent.parent / "app" / "handoff.py"
              ).read_text(encoding="utf-8")
    for sink in ("open(", "Path(", "json.dump", "os.environ["):
        assert f"{sink}" not in source.split("def sign_in")[1].split("def writer_from_link")[0] \
            or sink == "open(",  f"{sink} inside the sign-in"
    # …and no client secret, ever: a public client that sent one would publish it
    assert "client_secret" not in source.replace("# NO client_secret", "")


def test_pkce_is_S256_and_the_state_is_checked():
    source = (Path(__file__).resolve().parent.parent / "app" / "handoff.py"
              ).read_text(encoding="utf-8")
    assert '"code_challenge_method": "S256"' in source
    assert 'got.get("state") != state' in source


# ── 3 · the environment: a link wins, and the old way still works ────────────

def test_a_handoff_in_the_environment_configures_the_room(tmp_path):
    writer = writer_from_env({
        "EM_CHATBOT_CONTAINER": str(tmp_path / "s.em.json"),
        "EM_CHATBOT_HANDOFF":
            "stratigraph://open?server=https%3A%2F%2Fem.example.org&room=from-link",
        "EM_CHATBOT_TOKEN": "tok",
    })
    assert isinstance(writer, RoomWriter)
    assert writer.room_id == "from-link"
    assert writer.base_url == "https://em.example.org"


def test_a_handoff_wins_over_the_split_variables(tmp_path):
    """Both set: the LINK is the more recent intention — it is the thing a person
    was handed."""
    writer = writer_from_env({
        "EM_CHATBOT_CONTAINER": str(tmp_path / "s.em.json"),
        "EM_SERVER_URL": "https://old.example.org",
        "EM_CHATBOT_ROOM": "old-room",
        "EM_CHATBOT_HANDOFF":
            "stratigraph://open?server=https%3A%2F%2Fnew.example.org&room=new-room",
        "EM_CHATBOT_TOKEN": "tok",
    })
    assert writer.room_id == "new-room"


def test_a_handoff_with_no_token_refuses_rather_than_opening_a_browser_at_boot(tmp_path):
    """`writer_from_env` runs where a service starts. Opening a browser as a side
    effect of a module load is how a service hangs at boot with no log line."""
    with pytest.raises(RuntimeError) as exc:
        writer_from_env({
            "EM_CHATBOT_CONTAINER": str(tmp_path / "s.em.json"),
            "EM_CHATBOT_HANDOFF":
                "stratigraph://open?server=https%3A%2F%2Fx&room=r",
        })
    assert "sign-in" in str(exc.value)


def test_a_bad_link_in_the_environment_is_named(tmp_path):
    with pytest.raises(RuntimeError) as exc:
        writer_from_env({
            "EM_CHATBOT_CONTAINER": str(tmp_path / "s.em.json"),
            "EM_CHATBOT_HANDOFF": "https://example.org/not-a-handoff",
        })
    assert "not a handoff link" in str(exc.value)


def test_the_old_way_still_works_because_a_headless_node_has_no_browser(tmp_path):
    writer = writer_from_env({
        "EM_CHATBOT_CONTAINER": str(tmp_path / "s.em.json"),
        "EM_SERVER_URL": "https://em.example.org",
        "EM_CHATBOT_ROOM": "saggio-b",
        "EM_CHATBOT_TOKEN": "tok",
    })
    assert isinstance(writer, RoomWriter) and writer.room_id == "saggio-b"


def test_with_nothing_configured_it_is_still_the_local_container(tmp_path):
    writer = writer_from_env({
        "EM_CHATBOT_CONTAINER": str(tmp_path / "s.em.json")})
    assert isinstance(writer, LocalWriter)


# ── 4 · the round-trip: the same room, the other editor ─────────────────────

def test_a_voice_asks_the_node_how_to_open_the_room_it_is_in():
    """Not a transfer: the graph lives in the ROOM, so opening it elsewhere is
    another client joining the same room."""
    from app.assets import InMemoryAssetStore
    from app.contract import invoke
    from app.tools import build_registry

    asked = []

    class Node:
        room_id = "scavo-cs03"

        def read(self, path):
            asked.append(path)
            return {"room": "scavo-cs03", "server": "https://em.example.org",
                    "carries_token": False,
                    "scheme": "stratigraph://open?server=x&room=scavo-cs03",
                    "web": "https://em.example.org/open?room=scavo-cs03",
                    "tools": {"emstudio": {
                        "label": "EMStudio",
                        "scheme": "stratigraph://open?server=x&room=scavo-cs03",
                        "browser": "http://localhost:5177/?server=x&room=scavo-cs03"}}}

        def apply(self, delta): pass
        def has_node(self, node_id): return True
        def study_name(self): return "Saggio B"
        def count_units(self): return 1
        def answer(self, q): return ""

    node = Node()
    registry = build_registry(node, InMemoryAssetStore())
    result = invoke(registry.route("open_in_emstudio"), {}, ORCID)
    assert result.ok, result.message
    assert asked == ["/v1/rooms/scavo-cs03/open"]
    # the browser door wins where a web build is deployed
    assert result.data["kind"] == "browser"
    assert "room=scavo-cs03" in result.data["link"]
    assert result.data["carries_token"] is False
    for secret in SECRETS:
        assert f"{secret}=" not in result.data["link"].lower()


def test_the_desktop_scheme_is_offered_when_no_web_build_is_deployed():
    from app.assets import InMemoryAssetStore
    from app.tools import make_open_in_emstudio

    class Node:
        room_id = "r"

        def read(self, path):
            return {"room": "r", "scheme": "stratigraph://open?room=r",
                    "carries_token": False,
                    "tools": {"emstudio": {"scheme": "stratigraph://open?room=r"}}}

    result = make_open_in_emstudio(Node(), InMemoryAssetStore()).handler({}, ORCID)
    assert result.ok and result.data["kind"] == "scheme"


def test_off_a_room_it_says_so_rather_than_offering_a_link_to_nowhere():
    from app.assets import InMemoryAssetStore
    from app.tools import make_open_in_emstudio
    from app.writer import LocalWriter

    local = LocalWriter("/tmp/nowhere.em.json", study="x")
    result = make_open_in_emstudio(local, InMemoryAssetStore()).handler({}, ORCID)
    assert not result.ok and result.data["reason"] == "no-room"


def test_asking_where_a_room_opens_is_not_an_act_on_the_record():
    """`writes=False`, so the core's no-author refusal does not fire — and the
    tool writes nothing."""
    from app.assets import InMemoryAssetStore
    from app.contract import invoke
    from app.tools import make_open_in_emstudio

    class Node:
        room_id = "r"
        def read(self, path):
            return {"room": "r", "scheme": "stratigraph://open?room=r",
                    "tools": {"emstudio": {"scheme": "stratigraph://open?room=r"}}}

    descriptor = make_open_in_emstudio(Node(), InMemoryAssetStore())
    assert descriptor.writes is False
    result = invoke(descriptor, {}, None)          # no author at all
    assert result.ok, result.message
