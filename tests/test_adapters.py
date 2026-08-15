"""The partner adapters — and the claim they exist to prove.

Design note §10: *a partner adds a capability by writing a descriptor and a thin
adapter*. That is a claim about this codebase, and a claim nobody measures is a
hope. So the last test in this file is the important one: **`contract.py` is not
touched**, and the adapters use only its public surface.

The two fixtures are the two partners' own shapes — an ATRIUM context sheet and
a PyArchInit US record — not shapes we invented for a test to pass.
"""

import inspect
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import contract                                        # noqa: E402
from app.adapters import atrium, pyarchinit                     # noqa: E402
from app.assets import InMemoryAssetStore                       # noqa: E402
from app.tools import build_registry                            # noqa: E402
from app.writer import LocalWriter                              # noqa: E402

ORCID = "0000-0002-1825-0097"

#: An ATRIUM context sheet, in the shape of the kickoff slides.
ATRIUM_SHEET = {
    "Ctx Number": "12",
    "Description": "Muro in opus mixtum, due filari conservati.",
    "Interpretation": "Fondazione del portico, prima fase.",
    "Recording": "ctx-012.m4a",
}

#: A PyArchInit US record, in the shape its REST export gives.
PYARCHINIT_RECORD = {
    "sito": "Saggio B", "area": "1", "us": "12",
    "d_stratigrafica": "muro",
    "d_interpretativa": "fondazione",
    "descrizione": "Due filari in opus mixtum.",
    "interpretazione": "Portico, prima fase.",
    "periodo_iniziale": "1200",
    "rapporti": "[[\"copre\",\"13\"]]",     # a field this adapter does not map
    "unita_misura": "cm",                   # …and another
}


@pytest.fixture()
def node(tmp_path):
    writer = LocalWriter(str(tmp_path / "scavo.em.json"), study="Saggio B")
    store = InMemoryAssetStore()
    return writer, store, build_registry(writer, store)


# ── ATRIUM ──────────────────────────────────────────────────────────────────

def test_an_atrium_sheet_becomes_a_unit_with_its_recording(node):
    writer, store, registry = node
    atrium.register(registry)
    results = atrium.ingest_sheet(registry, ATRIUM_SHEET, ORCID,
                                  recording=b"ID3\x04audio")
    assert [r.ok for r in results] == [True, True]
    assert writer.has_node("US12")

    unit = next(n for n in writer._section(writer._read())["nodes"]
                if n["id"] == "US12")
    assert unit["data"]["created_by"] == ORCID, "attributed, as everything is"

    # the recording is a RESOURCE, not a transcript pasted into a field: the
    # evidence for the sentence is one click from the sentence
    resource = next(n for n in writer._section(writer._read())["nodes"]
                    if n.get("node_type") == "resource")
    assert store.get(resource["data"]["checksum"]) == b"ID3\x04audio"
    assert "ATRIUM" in resource["name"]


def test_description_and_interpretation_stay_apart():
    """Two fields because they are two acts — what is there, and what somebody
    thinks it means. Flattening them loses what makes a sheet reviewable."""
    slots = atrium.slots_from_sheet(ATRIUM_SHEET)
    assert slots["us"] == "12"
    assert slots["description"].startswith("Muro in opus mixtum")
    assert slots["interpretation"].startswith("Fondazione del portico")


def test_a_sheet_without_a_context_number_is_ASKED_about_not_guessed(node):
    _, _, registry = node
    atrium.register(registry)
    sheet = {**ATRIUM_SHEET}
    del sheet["Ctx Number"]
    results = atrium.ingest_sheet(registry, sheet, ORCID)
    assert results[0].ok is False
    assert results[0].message == "Mi manca us."
    assert len(results) == 1, "no unit, so nothing to attach to"


def test_the_export_spelling_does_not_have_to_be_exact():
    """A CSV round-trip lowercases and underscores things. A partner should not
    have to normalise before talking to us."""
    assert atrium.slots_from_sheet({"ctx_number": "7"})["us"] == "7"
    assert atrium.slots_from_sheet({"ctx": "7"})["us"] == "7"


def test_atrium_registers_as_an_ordinary_tool(node):
    _, _, registry = node
    descriptor = atrium.register(registry)
    assert registry.route("scheda atrium") is descriptor
    assert descriptor.service == "rest"
    # …and it is in the registry the assistant advertises, like any other
    assert "ingest_atrium_sheet" in {d.name for d in registry.list()}


# ── PyArchInit ──────────────────────────────────────────────────────────────

def test_a_pyarchinit_record_becomes_a_unit(node):
    writer, _, registry = node
    pyarchinit.register(registry)
    result = pyarchinit.ingest_record(registry, PYARCHINIT_RECORD, ORCID)
    assert result.ok is True
    assert writer.has_node("US12")
    unit = next(n for n in writer._section(writer._read())["nodes"]
                if n["id"] == "US12")
    assert unit["data"]["created_by"] == ORCID


def test_a_field_the_adapter_does_not_map_is_CARRIED_not_dropped():
    """Dropping somebody's data on the way in would make the graph a lossy copy
    of their database.

    Note WHICH fields are mapped, because the first version of this got it
    wrong: PyArchInit keeps the short classification (`d_interpretativa`,
    "fondazione") apart from the free text (`interpretazione`, a sentence), and
    only the free text has a home on a stratigraphic unit. The classifications
    ride in `extra` — carried verbatim rather than squeezed into a field that
    means something else.
    """
    slots = pyarchinit.slots_from_record(PYARCHINIT_RECORD)
    assert slots["description"] == "Due filari in opus mixtum."
    assert slots["interpretation"] == "Portico, prima fase.", \
        "the free text, not the one-word classification"
    assert slots["extra"]["d_interpretativa"] == "fondazione"
    assert slots["extra"]["d_stratigrafica"] == "muro"
    assert slots["extra"]["periodo_iniziale"] == "1200"
    assert slots["extra"]["rapporti"] == '[["copre","13"]]'
    assert slots["extra"]["unita_misura"] == "cm"


def test_the_key_is_site_area_and_number_not_the_number_alone():
    """Two trenches both have a US 1, and merging them is the worst kind of
    silent data loss."""
    assert pyarchinit.unit_key(PYARCHINIT_RECORD) == "Saggio B/1/12"
    other = {**PYARCHINIT_RECORD, "area": "2"}
    assert pyarchinit.unit_key(other) != pyarchinit.unit_key(PYARCHINIT_RECORD)


def test_several_records_at_once_report_how_many_landed(node):
    _, _, registry = node
    descriptor = pyarchinit.register(registry)
    result = contract.invoke(descriptor, {"records": [
        PYARCHINIT_RECORD, {**PYARCHINIT_RECORD, "us": "13"},
        {**PYARCHINIT_RECORD, "us": ""},        # no number: refused, and counted
    ]}, ORCID, registry=registry)
    assert result.data["records"] == 3
    assert result.data["written"] == 2
    assert "2 unità su 3" in result.message, "a partial import SAYS it is partial"


# ── the claim: pluggable means the core is untouched ────────────────────────

def test_the_adapters_use_ONLY_the_contracts_public_surface():
    """The measured version of "a descriptor and a thin adapter"."""
    public = {"GraphDelta", "Slot", "ToolDescriptor", "ToolRegistry",
              "ToolResult", "invoke", "stable_id", "ToolHandler",
              "UNKNOWN_INTENT", "TOOL_NAMESPACE"}
    for module in (atrium, pyarchinit):
        # what the module actually BOUND from the contract, not what the text
        # of an import line says — a rename or a star import cannot hide here
        bound = {name for name, value in vars(module).items()
                 if getattr(value, "__module__", None) == contract.__name__}
        assert bound, f"{module.__name__} does not use the contract at all"
        assert bound <= public, \
            f"{module.__name__} reaches past the contract's surface: " \
            f"{sorted(bound - public)}"
        assert not any(name.startswith("_") for name in bound), \
            f"{module.__name__} uses a private name from the contract"


def test_registering_a_partner_does_not_change_the_core():
    """The file itself, byte for byte. If adding a partner needed a line in
    `contract.py`, the base would not be a base."""
    before = Path(contract.__file__).read_bytes()
    registry = build_registry(LocalWriter("/tmp/_probe.em.json"),
                              InMemoryAssetStore())
    atrium.register(registry)
    pyarchinit.register(registry)
    assert Path(contract.__file__).read_bytes() == before


def test_the_partners_do_not_disturb_the_MVP_routing(node):
    """A plug that stole an existing intent would be worse than one that failed
    to register."""
    _, _, registry = node
    before = dict(registry.intents())
    atrium.register(registry)
    pyarchinit.register(registry)
    after = registry.intents()
    for intent, tool in before.items():
        assert after[intent] == tool, f"{intent} was stolen by {after[intent]}"
    assert registry.route("crea una nuova scheda").name == "create_su"


# ── the partners' data lands WHOLE (the ARC-B limit, closed) ────────────────
#
# The adapters did not change: they were already passing description,
# interpretation and the unmapped fields. What changed is the TOOL, which now
# honours them — which is where the continuation belonged.

def _unit(writer, node_id="US12"):
    return next(n for n in writer._section(writer._read())["nodes"]
                if n["id"] == node_id)


def test_an_atrium_sheet_lands_WHOLE_not_only_its_number(node):
    writer, _, registry = node
    atrium.register(registry)
    results = atrium.ingest_sheet(registry, ATRIUM_SHEET, ORCID,
                                  recording=b"ID3\x04audio")
    assert all(r.ok for r in results)

    unit = _unit(writer)
    assert unit["description"] == "Muro in opus mixtum, due filari conservati."
    assert unit["data"]["interpretation"] == "Fondazione del portico, prima fase."
    # …and the recording is still a resource, one click from the sentence
    assert any(n.get("node_type") == "resource"
               for n in writer._section(writer._read())["nodes"])


def test_a_pyarchinit_record_lands_WHOLE_including_what_we_do_not_map(node):
    writer, _, registry = node
    pyarchinit.register(registry)
    assert pyarchinit.ingest_record(registry, PYARCHINIT_RECORD, ORCID).ok

    unit = _unit(writer)
    assert unit["description"] == "Due filari in opus mixtum."
    assert unit["data"]["interpretation"] == "Portico, prima fase."
    assert unit["data"]["sito"] == "Saggio B" and unit["data"]["area"] == "1"
    # the fields the adapter deliberately does not map are carried, not dropped
    carried = unit["data"]["source_fields"]
    assert carried["rapporti"] == '[["copre","13"]]'
    assert carried["unita_misura"] == "cm"


def test_the_adapters_themselves_did_not_have_to_change(node):
    """The point of the arc: the continuation belonged in the tool. What the
    adapters produce is unchanged — the same slots they always passed."""
    slots = pyarchinit.slots_from_record(PYARCHINIT_RECORD)
    assert {"us", "description", "interpretation", "extra"} <= set(slots)
    sheet = atrium.slots_from_sheet(ATRIUM_SHEET)
    assert {"us", "description", "interpretation"} <= set(sheet)


def test_the_core_is_STILL_untouched_after_enriching_the_tool():
    """The byte-for-byte guard, re-asserted where it matters most: this arc
    enriched a TOOL, and a tool getting richer must not cost the base."""
    before = Path(contract.__file__).read_bytes()
    registry = build_registry(LocalWriter("/tmp/_probe2.em.json"),
                              InMemoryAssetStore())
    atrium.register(registry)
    pyarchinit.register(registry)
    contract.invoke(registry.route("create_su"),
                    {"us": "1", "description": "d", "interpretation": "i",
                     "extra": {"x": "1"}}, ORCID, registry=registry)
    assert Path(contract.__file__).read_bytes() == before
