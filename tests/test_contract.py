"""The tool contract — the part that must not be wrong.

Everything else in this service is replaceable: the speech engine, the intent
model, the web app, even the tools. This is the shape a partner writes against,
so what is asserted here is not that it works but that it **refuses correctly** —
the four refusals are the design, and each one is a thing a field assistant must
never do.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.contract import (GraphDelta, Slot, ToolDescriptor, ToolRegistry,  # noqa: E402
                          ToolResult, UNKNOWN_INTENT, invoke, stable_id)

ORCID = "0000-0002-1825-0097"


def _echo_tool(**over):
    calls = []

    def handler(slots, author):
        calls.append((dict(slots), author))
        return ToolResult(ok=True, message=f"fatto: {slots.get('us')}",
                          delta=GraphDelta(nodes=[{"id": f"US{slots.get('us')}",
                                                   "node_type": "US"}]))
    spec = dict(
        name="create_su",
        intents=["crea una nuova scheda", "nuova US", "create_su"],
        input_schema=[Slot("us", "string", True, "il numero dell'unità")],
        handler=handler, description="una nuova US nel grafo",
        service="s3dgraphy",
    )
    spec.update(over)
    return ToolDescriptor(**spec), calls


@pytest.fixture()
def registry():
    r = ToolRegistry()
    descriptor, calls = _echo_tool()
    r.register(descriptor)
    r.calls = calls          # type: ignore[attr-defined]
    return r


# ── the declaration ─────────────────────────────────────────────────────────

def test_a_descriptor_says_what_it_answers_needs_and_changes(registry):
    """The whole interoperability surface, in one dict a partner can read."""
    d = registry.get("create_su").as_dict()
    assert d["name"] == "create_su"
    assert "nuova US" in d["intents"]
    assert d["input_schema"] == [
        {"name": "us", "kind": "string", "required": True,
         "description": "il numero dell'unità"}]
    assert d["output"] == "graph-delta"
    assert d["service"] == "s3dgraphy" and d["writes"] is True


def test_the_registry_is_the_documentation(registry):
    assert [t.name for t in registry.list()] == ["create_su"]
    assert registry.intents()["nuova us"] == "create_su"


def test_registering_the_same_name_twice_is_refused(registry):
    """A tool quietly shadowed by a partner's adapter is a bug nobody can see."""
    other, _ = _echo_tool()
    with pytest.raises(ValueError, match="already registered"):
        registry.register(other)


# ── routing ─────────────────────────────────────────────────────────────────

def test_routing_finds_a_tool_by_intent_or_by_its_own_name(registry):
    assert registry.route("crea una nuova scheda").name == "create_su"
    assert registry.route("NUOVA US").name == "create_su", "case does not matter"
    assert registry.route("create_su").name == "create_su", \
        "a caller that knows the tool should not have to find a synonym"


def test_an_unknown_intent_routes_to_NOTHING_not_to_the_nearest_thing(registry):
    """No fuzzy match. On a dig, acting on a sentence nobody meant puts a wrong
    record in a graph that outlives the excavation."""
    assert registry.route("crea una nuova trincea") is None
    assert registry.route("") is None


# ── the four refusals ───────────────────────────────────────────────────────

def test_1_an_unknown_intent_is_a_clean_answer_with_a_way_forward(registry):
    result = invoke(None, {}, ORCID, registry=registry)
    assert result.ok is False
    assert result.message.startswith(UNKNOWN_INTENT)
    assert "create_su" in result.message, \
        "'I don't know' with no way forward is what makes people stop asking"
    assert result.delta.writes is False


def test_2_a_declared_tool_with_no_adapter_says_so(registry):
    """A real state during integration; pretending otherwise wastes an
    afternoon of somebody else's time."""
    naked = ToolDescriptor(name="atrium_sheet", intents=["scheda ATRIUM"])
    result = invoke(naked, {}, ORCID)
    assert result.ok is False
    assert "non ancora collegato" in result.message
    assert result.data["reason"] == "no-handler"


def test_3_a_missing_slot_is_asked_for_never_invented(registry):
    """An assistant that invented a unit number would put a wrong number in a
    record nobody can correct later."""
    result = invoke(registry.get("create_su"), {}, ORCID)
    assert result.ok is False
    assert result.message == "Mi manca us."
    assert result.data["missing"] == ["us"]
    assert registry.calls == [], "the handler never ran"


def test_4_a_writing_tool_without_an_author_is_refused(registry):
    """The whole reason ORCID is in the design: an unattributed record is one
    nobody can defend."""
    result = invoke(registry.get("create_su"), {"us": "12"}, None)
    assert result.ok is False
    assert result.data["reason"] == "no-author"
    assert registry.calls == []


def test_a_reading_tool_needs_no_author():
    """A question is not a claim: `which_project` has nobody to attribute to."""
    asked = []
    reader = ToolDescriptor(
        name="which_project", intents=["in che progetto sto lavorando"],
        writes=False,
        handler=lambda slots, author: (asked.append(author)
                                       or ToolResult(True, "Stai su Portico")))
    result = invoke(reader, {}, None)
    assert result.ok is True and result.message == "Stai su Portico"
    assert asked == [None]


# ── the act ─────────────────────────────────────────────────────────────────

def test_invoking_produces_a_delta_attributed_to_the_token(registry):
    result = invoke(registry.get("create_su"), {"us": "12"}, ORCID)
    assert result.ok is True
    assert result.message == "fatto: 12"
    assert result.delta.nodes == [{"id": "US12", "node_type": "US"}]
    assert result.delta.author == ORCID, \
        "stamped on the way out, so a handler that forgot cannot leak an " \
        "unattributed write"
    assert registry.calls == [({"us": "12"}, ORCID)]


def test_the_author_is_stamped_even_when_the_handler_forgot():
    """The one place that sees every write is this one."""
    forgetful = ToolDescriptor(
        name="sloppy", intents=["x"],
        handler=lambda slots, author: ToolResult(
            True, "ok", GraphDelta(nodes=[{"id": "n1"}])))
    result = invoke(forgetful, {}, ORCID)
    assert result.delta.author == ORCID


def test_a_handler_that_raises_does_not_take_the_assistant_down():
    """On a dig, one failing tool must not end the conversation."""
    def boom(slots, author):
        raise RuntimeError("il magazzino non risponde")

    broken = ToolDescriptor(name="attach_photo_to_su", intents=["foto"],
                            handler=boom)
    result = invoke(broken, {}, ORCID)
    assert result.ok is False
    assert "non è riuscito" in result.message
    assert "magazzino" in result.message, "the reason travels, in words"
    assert result.data["reason"] == "handler-failed"


def test_every_answer_carries_something_to_SAY(registry):
    """The person hearing it has their hands in the soil. Every path — success,
    refusal, failure — produces a sentence."""
    for result in (
        invoke(None, {}, ORCID, registry=registry),
        invoke(registry.get("create_su"), {}, ORCID),
        invoke(registry.get("create_su"), {"us": "1"}, None),
        invoke(registry.get("create_su"), {"us": "1"}, ORCID),
    ):
        assert result.message and isinstance(result.message, str)


# ── identity of an act ──────────────────────────────────────────────────────

def test_the_same_act_asked_twice_has_the_same_id():
    """Idempotence has to be definable, and a random id would make it not be."""
    assert stable_id("create_su", "US12") == stable_id("create_su", "US12")
    assert stable_id("create_su", "US12") != stable_id("create_su", "US13")
