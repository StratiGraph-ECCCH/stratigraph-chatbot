"""A field session, end to end: say it, and the graph says it back.

The MVP the design note names (§2): create a unit by voice, attach photos to it.
These tests walk that, through the real intent parser, the real registry, the
real tools and a real container on disk — the only stubs are the ones a laptop
cannot avoid (no microphone, no Keycloak).

What is being defended, beyond "it works":

* **the author is the token's**, everywhere, and a write without one is refused;
* **the bytes go to the store before the graph points at them**, so a crash
  leaves an orphan object rather than a broken reference in a shared graph;
* **nothing is invented** — a missing unit number is asked for, an unknown unit
  is named rather than silently created.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.assets import InMemoryAssetStore                      # noqa: E402
from app.contract import invoke                                # noqa: E402
from app.intent import extract_us, understand                  # noqa: E402
from app.speech import PassthroughSTT, WhisperSTT, stt_from_env  # noqa: E402
from app.tools import build_registry                           # noqa: E402
from app.writer import LocalWriter                             # noqa: E402

ORCID = "0000-0002-1825-0097"


@pytest.fixture()
def node(tmp_path):
    """A field node: its own container, its own store, the five tools."""
    writer = LocalWriter(str(tmp_path / "scavo.em.json"), study="Saggio B")
    store = InMemoryAssetStore()
    return writer, store, build_registry(writer, store)


def _say(registry, sentence, author=ORCID, **slots):
    understood = understand(sentence, registry)
    descriptor = registry.route(understood.tool or "")
    return understood, invoke(descriptor, {**understood.slots, **slots},
                              author, registry=registry)


# ── the intent, without a model ─────────────────────────────────────────────

def test_the_field_cards_commands_are_understood_by_RULES(node):
    """The card is a closed vocabulary somebody designed on purpose, so the
    rules answer it — no model, no network, instant."""
    _, _, registry = node
    understood, _ = _say(registry, "crea una nuova scheda, US 12")
    assert (understood.tool, understood.slots, understood.via) == (
        "create_su", {"us": "12"}, "rules")

    understood, _ = _say(registry, "in che progetto sto lavorando?")
    assert understood.tool == "which_project"


def test_the_unit_number_is_read_out_of_the_sentence_never_guessed():
    assert extract_us("crea una nuova scheda, US 12") == "12"
    assert extract_us("unità stratigrafica 7") == "7"
    assert extract_us("scheda numero 103") == "103"
    assert extract_us("questa foto è per la US 12") == "12"
    assert extract_us("crea una nuova scheda") is None, \
        "no number in the sentence means no number — not a default"


def test_a_longer_command_wins_over_a_shorter_one_inside_it(node):
    """"crea una nuova scheda" must not lose to "nuova scheda"."""
    _, _, registry = node
    understood, _ = _say(registry, "crea una nuova scheda")
    assert understood.intent == "crea una nuova scheda"


def test_a_sentence_nobody_declared_is_a_clean_nothing(node):
    _, _, registry = node
    understood, result = _say(registry, "accendi il generatore")
    assert understood.tool is None and understood.via == "none"
    assert result.ok is False
    assert "Non so fare" in result.message
    assert "create_su" in result.message, "…and it says what it CAN do"


def test_the_model_may_only_route_to_tools_that_EXIST(node):
    """A hallucinated `dig_trench` would otherwise produce a confident answer
    about a capability nobody installed."""
    _, _, registry = node

    class Hallucinating:
        def parse(self, transcript, tools):
            return {"tool": "dig_trench", "slots": {}}

    assert understand("scava la trincea", registry,
                      model=Hallucinating()).tool is None

    class Sensible:
        def parse(self, transcript, tools):
            return {"tool": "create_su", "slots": {"us": "44"}}

    understood = understand("mi serve una scheda nuova per la quarantaquattro",
                            registry, model=Sensible())
    assert (understood.tool, understood.slots, understood.via) == (
        "create_su", {"us": "44"}, "llm")


def test_a_model_that_falls_over_does_not_end_the_conversation(node):
    _, _, registry = node

    class Broken:
        def parse(self, transcript, tools):
            raise RuntimeError("il modello non risponde")

    assert understand("qualcosa", registry, model=Broken()).via == "none"


# ── create_su ───────────────────────────────────────────────────────────────

def test_saying_it_writes_the_unit_with_the_speakers_ORCID(node):
    writer, _, registry = node
    _, result = _say(registry, "crea una nuova scheda, US 12")
    assert result.ok is True
    assert result.message == "Ho creato la US 12."
    assert result.delta.author == ORCID
    assert writer.has_node("US12")

    unit = next(n for n in writer._section(writer._read())["nodes"]
                if n["id"] == "US12")
    assert unit["node_type"] == "US"
    assert unit["data"]["created_by"] == ORCID, \
        "a record without a hand behind it is one nobody can defend"


def test_the_act_itself_is_recorded_as_a_DTC_process(node):
    writer, _, registry = node
    _, result = _say(registry, "crea una nuova scheda, US 12")
    process = result.delta.process
    assert process["node_type"] == "dtc_process"
    assert process["data"]["created_by"] == ORCID
    assert process["data"]["tool"] == "create_su"
    assert process["data"]["source"] == "stratigraph-chatbot"
    # …and it landed in the graph, not only in the answer
    assert writer.has_node(process["id"])


def test_saying_it_twice_does_not_make_two_units(node):
    """A flaky field network means a retry, and a retry must be safe."""
    writer, _, registry = node
    _say(registry, "crea una nuova scheda, US 12")
    _, again = _say(registry, "crea una nuova scheda, US 12")
    assert again.ok is True
    assert "già presente" in again.message
    assert writer.count_units() == 1


def test_without_a_number_it_asks_instead_of_inventing_one(node):
    writer, _, registry = node
    _, result = _say(registry, "crea una nuova scheda")
    assert result.ok is False and result.message == "Mi manca us."
    assert writer.count_units() == 0


def test_without_an_identity_it_refuses_to_write(node):
    writer, _, registry = node
    _, result = _say(registry, "crea una nuova scheda, US 12", author=None)
    assert result.ok is False
    assert "identità verificata" in result.message
    assert writer.count_units() == 0


# ── which_project ───────────────────────────────────────────────────────────

def test_it_can_say_where_you_are(node):
    _, _, registry = node
    _say(registry, "crea una nuova scheda, US 12")
    _, result = _say(registry, "in che progetto sto lavorando", author=None)
    assert result.ok is True, "a question needs no attribution"
    assert "Saggio B" in result.message and "1 unità" in result.message


# ── attach_photo_to_su ──────────────────────────────────────────────────────

def test_a_photo_goes_to_the_store_and_the_unit_points_at_it(node):
    writer, store, registry = node
    _say(registry, "crea una nuova scheda, US 12")
    _, result = _say(registry, "questa foto è per la US 12", photo=b"\xff\xd8jpeg")
    assert result.ok is True
    assert result.message == "Foto allegata alla US 12."

    # the bytes are really in the store, addressed by their own digest
    ref = result.data["sha256"]
    assert store.get(ref) == b"\xff\xd8jpeg"

    # …and the graph points at them, with the digest as the checksum
    section = writer._section(writer._read())
    resource = next(n for n in section["nodes"]
                    if n["id"] == result.data["resource_id"])
    assert resource["data"]["checksum"] == ref
    assert resource["data"]["created_by"] == ORCID
    edge = next(e for e in section["edges"] if e["target"] == resource["id"])
    assert (edge["source"], edge["edge_type"]) == ("US12", "has_linked_resource")


def test_a_photo_for_a_unit_that_does_not_exist_is_NAMED_not_created(node):
    """Silently creating US 12 because somebody said "12" would put a unit in
    the record that nobody decided to dig."""
    writer, store, registry = node
    _, result = _say(registry, "questa foto è per la US 99", photo=b"x")
    assert result.ok is False
    assert "Non trovo la US 99" in result.message
    assert result.data["reason"] == "unknown-unit"
    assert writer.count_units() == 0


def test_the_same_photo_twice_is_one_object(node):
    """Content addressing makes dedup free; saying it makes it useful."""
    _, store, registry = node
    _say(registry, "crea una nuova scheda, US 12")
    first = _say(registry, "questa foto è per la US 12", photo=b"same")[1]
    second = _say(registry, "questa foto è per la US 12", photo=b"same")[1]
    assert first.data["sha256"] == second.data["sha256"]
    assert first.data["created"] is True and second.data["created"] is False


# ── ingest_photos ───────────────────────────────────────────────────────────

def test_several_photos_at_once(node):
    writer, store, registry = node
    _say(registry, "crea una nuova scheda, US 12")
    _, result = _say(registry, "ti passo delle foto, US 12",
                     photos=[b"a", b"b", b"c"])
    assert result.ok is True
    assert result.message == "Ho messo 3 foto sulla US 12."
    resources = [n for n in writer._section(writer._read())["nodes"]
                 if n.get("node_type") == "resource"]
    assert len(resources) == 3


# ── query_kg ────────────────────────────────────────────────────────────────

def test_it_answers_from_the_graph(node):
    _, _, registry = node
    for number in ("1", "2"):
        _say(registry, f"crea una nuova scheda, US {number}")
    _, result = _say(registry, "quante unità abbiamo registrato", author=None)
    assert result.ok is True
    assert "2 unità" in result.message


def test_an_empty_trench_says_so(node):
    _, _, registry = node
    _, result = _say(registry, "cosa abbiamo registrato", author=None)
    assert "nessuna unità" in result.message


# ── speech ──────────────────────────────────────────────────────────────────

def test_passthrough_is_a_deployment_not_a_stub():
    """It is the ATRIUM case: the voice was captured and transcribed elsewhere."""
    assert PassthroughSTT().transcribe("crea una nuova scheda".encode()) \
        == "crea una nuova scheda"


def test_passthrough_refuses_audio_with_a_sentence_that_helps():
    with pytest.raises(ValueError, match="TRANSCRIPT"):
        PassthroughSTT().transcribe(b"\x00\x01\x02\xff\xfe")


def test_the_engine_is_chosen_by_configuration_never_silently():
    assert isinstance(stt_from_env({}), PassthroughSTT)
    with pytest.raises((NotImplementedError, ValueError)):
        WhisperSTT("/nowhere/model.bin")
