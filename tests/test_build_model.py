"""The sixth tool: a sentence in the trench starts a reconstruction on the node.

The node is faked — what is measured is the VOICE's half: that the phrase routes,
that the request carries what the endpoint needs, that the three ways this can go
wrong are three different sentences, and that a control set is data rather than
something anybody dictates.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.assets import InMemoryAssetStore                      # noqa: E402
from app.contract import invoke                                # noqa: E402
from app.intent import understand                              # noqa: E402
from app.tools import build_registry, make_build_model         # noqa: E402
from app.writer import LocalWriter                             # noqa: E402

ORCID = "0000-0002-1825-0097"


class FakeNode:
    """A graph writer that IS in a room, with the node's API faked."""

    room_id = "saggio-b"

    def __init__(self, answer=None, reachable=True):
        self.answer = answer if answer is not None else {
            "job_id": "abcdef0123456789", "status": "queued", "image_count": 12}
        self.reachable = reachable
        self.calls = []

    def call(self, path, payload):
        self.calls.append((path, payload))
        return self.answer if self.reachable else None

    # the rest of the writer surface, unused here
    def apply(self, delta): pass
    def update(self, node_id, fields, *, author, process=None): return []
    def has_node(self, node_id): return True
    def node(self, node_id): return None
    def study_name(self): return "Saggio B"
    def count_units(self): return 1
    def answer_q(self, q): return ""


def _tool(writer):
    return make_build_model(writer, InMemoryAssetStore())


# ── 1 · the phrase routes ────────────────────────────────────────────────────

@pytest.mark.parametrize("sentence", [
    "costruisci il modello 3D di questa US dalle foto",
    "costruisci il modello 3d della US 12",
    "fai il modello 3d della us 12 dalle foto",
    "ricostruisci la US 12",
])
def test_the_field_phrase_routes_to_build_model(sentence):
    registry = build_registry(FakeNode(), InMemoryAssetStore())
    understood = understand(sentence, registry)
    assert understood.tool == "build_model", understood.as_dict()
    assert understood.via == "rules"


def test_the_unit_number_comes_out_of_the_sentence_when_it_is_in_it():
    registry = build_registry(FakeNode(), InMemoryAssetStore())
    assert understand("ricostruisci la US 12", registry).slots["us"] == "12"
    # …and is NOT invented when it is not: "questa US" names no number
    assert "us" not in understand(
        "costruisci il modello 3D di questa US dalle foto", registry).slots


def test_the_registry_now_holds_ten_tools():
    """The membership AND the count, so a tool that appears without anybody
    deciding to add it shows up here.

    It has grown twice, and both times this test is what said so:

    * 2026-08-30 — `open_in_emstudio`, the round-trip's voice;
    * 2026-09-21 — `update_su`, because a scheda is opened on a unit that
      already exists far more often than it creates one, and `add_node` on an
      id that is not there CREATES it (measured in
      `s3dgraphy.crdt.apply_op_to_section`). Correcting a record and inventing
      one had to stop being the same operation;
    * 2026-09-21 — `relate_su`, the intent that was MISSING. Marking the ICCD
      sheet in `stratigraph-templates` showed that none of the seven let a
      person say «la 12 copre la 18», which is a thing said all day in a
      trench — so the sheet's ten relationship boxes had to stay `unknown`
      there for want of a criterion here;
    * 2026-09-21 — `validate_field`, because a value a model composed and
      nobody confirmed must not look like any other value, and confirming it
      is an act of its own: «I read it and it is right» is not «I changed it».
    """
    registry = build_registry(FakeNode(), InMemoryAssetStore())
    assert {d.name for d in registry.list()} == {
        "create_su", "update_su", "relate_su", "validate_field",
        "which_project", "attach_photo_to_su", "ingest_photos", "query_kg",
        "build_model", "open_in_emstudio"}


# ── 2 · what the node is asked ───────────────────────────────────────────────

def test_the_request_names_the_room_the_cluster_and_the_subject():
    node = FakeNode()
    result = _tool(node).handler({"us": "12"}, ORCID)
    assert result.ok, result.message
    path, payload = node.calls[0]
    assert path == "/v1/photogrammetry"
    assert payload == {"room_id": "saggio-b", "cluster": "US12",
                       "mode": "local", "subject": "US12"}
    assert result.data["job_id"] == "abcdef0123456789"
    assert "12 foto" in result.message


def test_control_points_switch_the_mode_because_they_are_the_whole_difference():
    node = FakeNode()
    gcps = {"crs": "EPSG:32633",
            "points": [{"id": "a", "world": [1, 2, 3],
                        "observations": [{"image": "IMG_0001.JPG",
                                          "pixel": [10, 20]}]}]}
    result = _tool(node).handler({"us": "12", "gcps": gcps}, ORCID)
    assert result.ok
    _path, payload = node.calls[0]
    assert payload["mode"] == "absolute"
    assert payload["gcps"] == gcps
    assert "georeferenziato" in result.message


def test_a_cluster_can_be_named_directly_without_a_unit():
    node = FakeNode()
    result = _tool(node).handler({"cluster": "acq.march"}, ORCID)
    assert result.ok
    _path, payload = node.calls[0]
    assert payload["cluster"] == "acq.march"
    assert "subject" not in payload


# ── 3 · the three ways it can go wrong are three sentences ───────────────────

def test_without_a_room_it_refuses_and_says_where_the_engine_lives(tmp_path):
    local = LocalWriter(str(tmp_path / "scavo.em.json"), study="Saggio B")
    result = _tool(local).handler({"us": "12"}, ORCID)
    assert not result.ok
    assert result.data["reason"] == "no-room"
    assert "contenitore locale" in result.message


def test_an_unreachable_node_reassures_about_the_photographs():
    result = _tool(FakeNode(reachable=False)).handler({"us": "12"}, ORCID)
    assert not result.ok
    assert result.data["reason"] == "unreachable"
    assert "al sicuro" in result.message


def test_the_nodes_own_refusal_is_read_out_in_its_own_words():
    node = FakeNode(answer={"detail": "absolute mode needs ground control points"})
    result = _tool(node).handler({"us": "12"}, ORCID)
    assert not result.ok
    assert result.data["reason"] == "refused"
    assert "ground control points" in result.message


def test_no_unit_and_no_cluster_is_a_question_not_a_guess():
    result = _tool(FakeNode()).handler({}, ORCID)
    assert not result.ok
    assert "quale US" in result.message


# ── 4 · the core's refusal still applies to it ───────────────────────────────

def test_a_reconstruction_with_nobody_behind_it_is_refused():
    node = FakeNode()
    result = invoke(_tool(node), {"us": "12"}, None)
    assert not result.ok
    assert result.data["reason"] == "no-author"
    assert node.calls == []          # the node was never asked
