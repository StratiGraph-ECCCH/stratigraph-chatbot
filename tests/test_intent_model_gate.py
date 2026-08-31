"""The declared capability has a switch — and pulling it changes nothing by default.

`/health` declared `intent_model` as a boolean from the MVP on, and until
2026-09-02 **nothing could set it**: `INTENT_MODEL = None` in `app/main.py` and
no variable populated it. The seam was there and well made — the `IntentModel`
protocol, `llm_parse` refusing any tool the registry does not declare, the rules
going first — but there was no lever. A capability that declares itself and
cannot be configured is a promise with no switch, and it was the one place where
E.D.'s rule was not carried through:

    If the node has an AI you have functions; if it does not, you do not — and
    the surface says so.

What this file defends, in the order that matters:

1. **with the variable unset, everything behaves exactly as before.** This is the
   regression guard and it is the most important test here: the lever must be
   invisible to a node that does not use it;
2. a HALF configuration refuses with a sentence — `speech.py`'s gesture — and
   never falls back;
3. there is **no default endpoint**, because a default would be somebody's cloud
   (design note §5);
4. a non-local endpoint is SAID, loudly, in what the node publishes;
5. the rules still go first, and the model is asked only for what they miss.
"""

from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import main as main_module                              # noqa: E402
from app.intent import (INTENT_ENDPOINT_VAR, INTENT_MODEL_VAR,   # noqa: E402
                        IntentModelError, describe,
                        intent_model_from_env, understand)


@pytest.fixture()
def client():
    return TestClient(main_module.app)


# ── 1 · unset is unchanged. The guard. ───────────────────────────────────────

def test_unset_means_no_model_and_nothing_else_changes():
    assert intent_model_from_env({}) is None


def test_the_default_health_says_rules_only_and_names_the_variables(client):
    """Absent is not silence: it says which variable would configure it — the
    habit this service already has for OIDC."""
    health = client.get("/health").json()
    assert health["intent_model"] is False
    assert health["intent"].startswith("rules only")
    intent = next(c for c in health["capabilities"] if c["name"] == "intent")
    assert intent["state"] == "absent"
    assert intent["missing"] == [INTENT_MODEL_VAR, INTENT_ENDPOINT_VAR]


def test_the_rules_answer_with_no_model_at_all():
    """The MVP's whole behaviour, and it must not have moved."""
    registry = main_module.REGISTRY
    found = understand("crea una nuova scheda, US 12", registry, model=None)
    assert found.tool == "create_su"
    assert found.via == "rules"
    assert found.slots.get("us") == "12"


# ── 2 · a half configuration refuses, with a sentence ───────────────────────

@pytest.mark.parametrize("environ, says", [
    ({INTENT_MODEL_VAR: "llama3.2"}, INTENT_ENDPOINT_VAR),
    ({INTENT_ENDPOINT_VAR: "http://127.0.0.1:11434/v1"}, INTENT_MODEL_VAR),
    ({INTENT_MODEL_VAR: "anthropic:claude",
      INTENT_ENDPOINT_VAR: "http://127.0.0.1:1/v1"}, "openai"),
    ({INTENT_MODEL_VAR: "openai:m", INTENT_ENDPOINT_VAR: "http://127.0.0.1:1/v1",
      "EM_CHATBOT_INTENT_TIMEOUT": "soon"}, "not a number"),
])
def test_a_half_configuration_refuses_and_names_what_is_missing(environ, says):
    with pytest.raises(IntentModelError) as refusal:
        intent_model_from_env(environ)
    assert says in str(refusal.value)


def test_there_is_NO_default_endpoint():
    """A default would be somebody's cloud. Design note §5: an excavation's words
    must not leave it because a variable was missing."""
    with pytest.raises(IntentModelError) as refusal:
        intent_model_from_env({INTENT_MODEL_VAR: "llama3.2"})
    message = str(refusal.value)
    assert "NO DEFAULT" in message
    assert "api.openai.com" not in message, \
        "not even as an example: an example is a thing somebody pastes"


def test_a_bare_model_name_means_the_one_provider_this_build_speaks():
    model = intent_model_from_env({INTENT_MODEL_VAR: "llama3.2",
                                   INTENT_ENDPOINT_VAR: "http://127.0.0.1:11434/v1"})
    assert model is not None
    assert model.provider == "openai"
    assert model.model == "llama3.2"


# ── 3 · local or not, and the node says which ───────────────────────────────

@pytest.mark.parametrize("endpoint, local", [
    ("http://127.0.0.1:11434/v1", True),
    ("http://localhost:8080/v1", True),
    ("http://[::1]:8080/v1", True),
    ("http://192.168.1.20:11434/v1", True),
    ("http://10.0.0.5/v1", True),
    ("http://fcn/v1", True),                      # a bare LAN name
    ("http://nodo.local:11434/v1", True),
    ("https://api.openai.com/v1", False),
    ("https://example.org/v1", False),
    ("http://8.8.8.8/v1", False),
])
def test_locality_is_read_from_the_endpoint(endpoint, local):
    model = intent_model_from_env({INTENT_MODEL_VAR: "m",
                                   INTENT_ENDPOINT_VAR: endpoint})
    assert model.local is local, endpoint


def test_a_non_local_endpoint_is_SAID_and_not_only_logged(monkeypatch):
    """A log line is read by whoever is looking at the log. What the node
    PUBLISHES is read by everybody else."""
    model = intent_model_from_env({INTENT_MODEL_VAR: "gpt",
                                   INTENT_ENDPOINT_VAR: "https://api.openai.com/v1"})
    monkeypatch.setattr(main_module, "INTENT_MODEL", model)
    intent = next(c for c in main_module._capabilities() if c.name == "intent")
    assert "NOT LOCAL" in intent.engine
    assert any("NOT local" in m for m in intent.missing), intent.missing


def test_the_unknown_locality_is_treated_as_NOT_local():
    """A wrong «local» would silence the one warning that matters."""
    model = intent_model_from_env({INTENT_MODEL_VAR: "m",
                                   INTENT_ENDPOINT_VAR: "not a url at all"})
    assert model.local is False


# ── 4 · the model answers, and only for what the rules missed ───────────────

class _Router(BaseHTTPRequestHandler):
    """An OpenAI-compatible endpoint the size of the contract."""

    answer = {"tool": "create_su", "slots": {"us": "77"}}

    def do_POST(self):                                        # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        self.body = json.loads(self.rfile.read(length) or b"{}")
        _Router.seen.append(self.body)
        # wrapped in prose ON PURPOSE: local models do this, and refusing an
        # otherwise good answer because it arrived inside a fence would make the
        # capability look broken on half the models an operator might install.
        content = ("Sure, here you go:\n```json\n"
                   + json.dumps(_Router.answer) + "\n```")
        payload = json.dumps({"choices": [{"message": {"content": content}}]})
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload.encode())

    def log_message(self, *args):                             # noqa: A003
        pass


_Router.seen = []


@pytest.fixture()
def fake_model():
    """A real HTTP endpoint on loopback — which is also what makes it `local`."""
    _Router.seen = []
    server = HTTPServer(("127.0.0.1", 0), _Router)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    try:
        yield intent_model_from_env({
            INTENT_MODEL_VAR: "llama3.2",
            INTENT_ENDPOINT_VAR: f"http://{host}:{port}/v1",
        })
    finally:
        server.shutdown()


def test_the_rules_go_FIRST_and_the_model_is_not_even_asked(fake_model):
    """A sentence the field vocabulary covers exactly must not cost a model call:
    slower, less predictable, and occasionally wrong."""
    found = understand("crea una nuova scheda, US 12", main_module.REGISTRY,
                       model=fake_model)
    assert found.via == "rules"
    assert _Router.seen == [], "the model was asked about a sentence the rules knew"


def test_the_model_answers_what_the_rules_missed(fake_model):
    found = understand("mi serve una scheda nuova per lo strato di crollo",
                       main_module.REGISTRY, model=fake_model)
    assert found.via == "llm", found
    assert found.tool == "create_su"
    assert found.slots.get("us") == "77"
    assert len(_Router.seen) == 1
    # …and it was told only about tools that EXIST
    tools = json.dumps(_Router.seen[0]["messages"][0]["content"])
    assert "create_su" in tools


def test_a_model_that_names_a_tool_nobody_installed_is_refused(fake_model,
                                                              monkeypatch):
    """`llm_parse`'s check, which predates this work and is the reason a
    hallucination costs a shrug instead of a wrong record."""
    monkeypatch.setattr(_Router, "answer", {"tool": "dig_trench", "slots": {}})
    found = understand("fai una cosa che questo nodo non sa fare",
                       main_module.REGISTRY, model=fake_model)
    assert found.tool is None
    assert found.via != "llm"


def test_a_model_that_does_not_answer_does_not_end_the_conversation():
    """The endpoint is closed. The rules had their turn; «I did not understand»
    is a fine answer, and an exception in a trench is not."""
    model = intent_model_from_env({INTENT_MODEL_VAR: "m",
                                   INTENT_ENDPOINT_VAR: "http://127.0.0.1:1/v1"})
    found = understand("una frase che le regole non pescano",
                       main_module.REGISTRY, model=model)
    assert found.tool is None


# ── 5 · one builder, so the two places cannot disagree ──────────────────────

def test_health_and_the_capability_list_tell_the_same_story(client):
    health = client.get("/health").json()
    intent = next(c for c in health["capabilities"] if c["name"] == "intent")
    assert intent["engine"] == health["intent"]
    assert (intent["state"] != "absent") is health["intent_model"]


def test_speech_is_declared_the_same_way(client):
    """The two capabilities are said in one shape — that is the point of §4."""
    health = client.get("/health").json()
    speech = next(c for c in health["capabilities"] if c["name"] == "speech")
    assert speech["engine"] == health["speech"]
    assert speech["missing"] == ["EM_CHATBOT_WHISPER_MODEL"]


def test_describe_never_returns_an_empty_string():
    """An empty field reads as a missing one."""
    assert describe(None).strip()
    assert describe(intent_model_from_env({
        INTENT_MODEL_VAR: "m", INTENT_ENDPOINT_VAR: "http://127.0.0.1:1/v1"})).strip()
