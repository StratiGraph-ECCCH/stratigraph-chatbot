"""The HTTP surface, and who is allowed to speak through it.

Design note §7: login is ORCID, and every unit and photo is attributed to a real
person. So the route-level claim is one sentence — **the author is the token's,
never the client's** — and these tests are that sentence, measured against a
real signature.
"""

import base64
import datetime
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

jwt = pytest.importorskip("jwt")
pytest.importorskip("cryptography")

from fastapi.testclient import TestClient          # noqa: E402

from app import main as main_module                # noqa: E402
from app.assets import InMemoryAssetStore          # noqa: E402
from app.auth import OidcSettings, authenticator, principal_orcid  # noqa: E402
from app.tools import build_registry               # noqa: E402
from app.writer import LocalWriter                 # noqa: E402

ISSUER = "https://keycloak.example/realms/stratigraph"
AUDIENCE = "stratigraph-chatbot"
KID = "field-key-1"
ORCID = "0000-0002-1825-0097"


@pytest.fixture()
def node(tmp_path, monkeypatch):
    writer = LocalWriter(str(tmp_path / "scavo.em.json"), study="Saggio B")
    store = InMemoryAssetStore()
    monkeypatch.setattr(main_module, "WRITER", writer)
    monkeypatch.setattr(main_module, "ASSET_STORE", store)
    monkeypatch.setattr(main_module, "REGISTRY", build_registry(writer, store))
    return writer, store


@pytest.fixture()
def realm():
    """An enforcing authenticator whose signing key this test owns."""
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    class _Keys:
        def key_for(self, kid):
            assert kid == KID
            return key.public_key()

    previous = (authenticator.settings, authenticator._jwks)
    authenticator.settings = OidcSettings(
        issuer=ISSUER, audience=AUDIENCE,
        jwks_uri=f"{ISSUER}/protocol/openid-connect/certs")
    authenticator._jwks = _Keys()

    def token(*, orcid=ORCID):
        now = datetime.datetime.now(datetime.timezone.utc)
        claims = {"sub": "user-1", "iss": ISSUER, "aud": AUDIENCE,
                  "exp": now + datetime.timedelta(minutes=30), "iat": now}
        if orcid:
            claims["orcid"] = orcid
        return jwt.encode(claims, key, algorithm="RS256", headers={"kid": KID})

    try:
        yield token
    finally:
        authenticator.settings, authenticator._jwks = previous


@pytest.fixture()
def client(node):
    with TestClient(main_module.app) as c:
        yield c


# ── what is public, and what is not ─────────────────────────────────────────

def test_health_and_the_contract_are_readable_without_a_token(client, realm):
    """A partner writing an adapter must be able to see the contract. A
    descriptor names capabilities, not data."""
    assert client.get("/health").status_code == 200
    tools = client.get("/v1/tools")
    assert tools.status_code == 200
    assert "create_su" in {t["name"] for t in tools.json()["tools"]}


def test_speaking_needs_a_token(client, realm):
    assert client.post("/v1/say", json={"transcript": "crea una nuova scheda, US 1"}
                       ).status_code == 401


def test_a_bad_token_is_refused(client, realm):
    for bad in ("not-a-jwt", ""):
        answer = client.post("/v1/say", json={"transcript": "x"},
                             headers={"Authorization": f"Bearer {bad}"})
        assert answer.status_code == 401


# ── the author is the token's ───────────────────────────────────────────────

def test_the_unit_carries_the_ORCID_of_the_TOKEN(client, realm, node):
    writer, _ = node
    answer = client.post("/v1/say",
                         json={"transcript": "crea una nuova scheda, US 12"},
                         headers={"Authorization": f"Bearer {realm()}"})
    assert answer.status_code == 200
    body = answer.json()
    assert body["ok"] is True and body["message"] == "Ho creato la US 12."
    assert body["tool"] == "create_su" and body["via"] == "rules"

    unit = next(n for n in writer._section(writer._read())["nodes"]
                if n["id"] == "US12")
    assert unit["data"]["created_by"] == ORCID


def test_a_client_cannot_declare_who_it_is(client, realm, node):
    """The one thing a field client must never be able to do: sign somebody
    else's name to a record."""
    writer, _ = node
    client.post("/v1/say",
                json={"transcript": "crea una nuova scheda, US 5",
                      "slots": {"author": "0000-0000-0000-0000",
                                "created_by": "qualcun altro"}},
                headers={"Authorization": f"Bearer {realm()}"})
    unit = next(n for n in writer._section(writer._read())["nodes"]
                if n["id"] == "US5")
    assert unit["data"]["created_by"] == ORCID


def test_a_realm_without_an_orcid_broker_still_gives_a_stable_identity():
    """It will not LOOK like an ORCID, which is the honest outcome."""
    assert principal_orcid({"sub": "user-1"}) == "user-1"
    assert principal_orcid({"orcid": ORCID, "sub": "user-1"}) == ORCID


# ── a photo, over HTTP ──────────────────────────────────────────────────────

def test_a_photo_arrives_base64_and_lands_in_the_store(client, realm, node):
    writer, store = node
    head = {"Authorization": f"Bearer {realm()}"}
    client.post("/v1/say", json={"transcript": "crea una nuova scheda, US 12"},
                headers=head)
    answer = client.post("/v1/photo", json={
        "transcript": "questa foto è per la US 12", "us": "12",
        "photo_base64": base64.b64encode(b"\xff\xd8jpeg").decode(),
        "filename": "muro.jpg"}, headers=head)
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["ok"] is True and body["message"] == "Foto allegata alla US 12."
    assert store.get(body["data"]["sha256"]) == b"\xff\xd8jpeg"
    # the slots that come back never carry the bytes: an answer is read aloud
    assert "photo" not in body["slots"]


def test_a_malformed_photo_is_the_callers_problem(client, realm):
    answer = client.post("/v1/photo",
                         json={"us": "1", "photo_base64": "not base64!!"},
                         headers={"Authorization": f"Bearer {realm()}"})
    assert answer.status_code == 400


# ── audio ───────────────────────────────────────────────────────────────────

def test_audio_goes_through_the_nodes_engine(client, realm, node):
    """With the passthrough engine (the ATRIUM case) the 'recording' IS the
    transcript, and the same route serves both."""
    writer, _ = node
    answer = client.post(
        "/v1/listen",
        files={"audio": ("t.txt", b"crea una nuova scheda, US 7", "text/plain")},
        headers={"Authorization": f"Bearer {realm()}"})
    assert answer.status_code == 200, answer.text
    assert answer.json()["message"] == "Ho creato la US 7."
    assert writer.has_node("US7")


def test_a_node_that_cannot_transcribe_says_501_not_500(client, realm):
    """The request was fine; this node simply cannot do that."""
    answer = client.post(
        "/v1/listen",
        files={"audio": ("a.wav", b"\x00\x01\xff\xfe", "audio/wav")},
        headers={"Authorization": f"Bearer {realm()}"})
    assert answer.status_code == 501
    assert "transcri" in answer.text.lower()
