"""Chi ha scritto quel campo — e le due prove che il prompt dice che nessuno fa.

La prima è facile: un campo passato dal modello porta l'autore AI.

**La seconda è quella che di solito nessuno fa**: un campo dettato e
trascritto VERBATIM non cambia autore. Sarebbe stato comodo marcare AI tutto
ciò che entra da un microfono — e avrebbe attribuito a una macchina ogni parola
detta sullo scavo.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import authorship                                       # noqa: E402
from app.assets import InMemoryAssetStore                        # noqa: E402
from app.intent import understand                                # noqa: E402
from app.tools import (build_registry, make_create_su,            # noqa: E402
                       make_update_su, make_validate_field, unit_id_for)
from app.writer import LocalWriter                               # noqa: E402

ORCID = "0000-0002-1825-0097"
ALTRO = "0000-0001-5109-3700"


@pytest.fixture
def writer(tmp_path):
    w = LocalWriter(str(tmp_path / "scavo.em.json"), study="Saggio B")
    assert make_create_su(w).handler({"us": "12"}, ORCID).ok
    return w


def node(writer):
    return writer.node("US12")


# ── 0 · il datamodel, misurato ──────────────────────────────────────────────

def test_an_ai_author_is_an_actor_the_datamodel_already_declares():
    """Nessun tipo di nodo nuovo: il recinto lo vieta, e non serve."""
    from s3dgraphy.mappings.authoring import target_groups

    targets = [t for g in target_groups() for t in g["targets"]]
    assert len(targets) == 32
    ai = [t for t in targets if t.get("em_type") == authorship.AI_AUTHOR_NODE_TYPE]
    assert len(ai) == 1
    assert ai[0]["label"] == "AI Author"
    assert ai[0]["cidoc"] == "E39 Actor"


def test_the_authorship_key_collides_with_nothing_the_crdt_reserves():
    """Guardato, non sperato."""
    from s3dgraphy.crdt import META_KEYS

    assert authorship.PREFIX not in META_KEYS
    assert authorship.field_key("colore") not in META_KEYS


def test_authorship_of_two_fields_merges_independently(writer):
    """La ragione della chiave nidificata: due persone che validano due campi
    diversi non si sovrascrivono. Il CRDT dà a `data.authorship.X` un orologio
    di campo suo, e questo è il test di quel fatto."""
    writer.update("US12", {"colore": "bruno",
                           authorship.field_key("colore"): {"by": "ai"}},
                  author=ORCID)
    writer.update("US12", {"misure": "0,25",
                           authorship.field_key("misure"): {"by": "human"}},
                  author=ALTRO)

    clocks = node(writer)["data"]["field_clocks"]
    assert "data.authorship.colore" in clocks
    assert "data.authorship.misure" in clocks
    assert clocks["data.authorship.colore"]["by"] == ORCID
    assert clocks["data.authorship.misure"]["by"] == ALTRO


# ── 1 · UN CAMPO COMPOSTO DAL MODELLO LO PORTA SCRITTO ─────────────────────

def test_a_field_the_model_composed_carries_the_ai_author(writer):
    result = make_update_su(writer).handler(
        {"us": "12",
         "fields": {"interpretazione": "crollo del tetto"},
         "authored_by": {"interpretazione": "ai"},
         "model": "llama-3.2-3b"}, ORCID)
    assert result.ok, result.message

    said = authorship.read(node(writer), "interpretazione")
    assert said["by"] == authorship.AI
    assert said["model"] == "llama-3.2-3b"
    assert said["validated"] is False
    assert said["declared"] is True


def test_a_verbatim_field_does_NOT_change_author(writer):
    """**LA PROVA CHE DI SOLITO NESSUNO FA.**

    Lo stesso atto, lo stesso microfono, la stessa persona — e nessun modello
    di mezzo. Il campo resta suo. Se questo test diventasse rosso, ogni parola
    detta sullo scavo sarebbe attribuita a una macchina.
    """
    result = make_update_su(writer).handler(
        {"us": "12", "fields": {"descrizione": "terra bruna con inclusi"}},
        ORCID)
    assert result.ok, result.message

    said = authorship.read(node(writer), "descrizione")
    assert said["by"] == authorship.HUMAN
    assert said["validated"] is False
    assert "model" not in said or said["model"] is None


def test_the_two_kinds_of_field_sit_side_by_side_on_one_unit(writer):
    """Il caso normale, non l'eccezione: undici campi di una persona e uno del
    modello. È il motivo per cui l'autorialità è per CAMPO."""
    make_update_su(writer).handler(
        {"us": "12",
         "fields": {"descrizione": "terra bruna", "interpretazione": "crollo"},
         "authored_by": {"interpretazione": "ai"},
         "model": "llama-3.2-3b"}, ORCID)

    stored = node(writer)
    assert authorship.read(stored, "descrizione")["by"] == authorship.HUMAN
    assert authorship.read(stored, "interpretazione")["by"] == authorship.AI
    assert authorship.needs_validation(
        stored, ["descrizione", "interpretazione"]) == ["interpretazione"]


def test_transcription_alone_is_not_the_criterion():
    """Whisper trascrive, non compone. Il criterio è `Intent.via`, che vale
    `rules` quando nessun modello di intento è stato coinvolto — ed è ciò che
    `intent.py` dichiara di riportare proprio per questo."""
    import tempfile

    w = LocalWriter(tempfile.mkdtemp() + "/s.em.json")
    registry = build_registry(w, InMemoryAssetStore())
    understood = understand("crea una nuova scheda per la us 12", registry)
    assert understood.via == "rules", (
        "se questa frase passasse dal modello, il criterio dovrebbe essere "
        "ridiscusso: verrebbe marcata AI una dettatura verbatim")


def test_a_field_written_before_this_mechanism_existed_reads_as_human(writer):
    """Nessuna riscrittura della storia: le schede già compilate non
    diventano AI perché il marcatore non c'era."""
    writer.update("US12", {"colore": "bruno"}, author=ORCID)
    said = authorship.read(node(writer), "colore")
    assert said["by"] == authorship.HUMAN
    assert said["declared"] is False, (
        "un campo senza marcatore non deve sembrare uno marcato `human`: "
        "la differenza è fra «nessuno l'ha detto» e «qualcuno l'ha detto»")


def test_an_author_that_is_neither_is_refused():
    with pytest.raises(ValueError) as refusal:
        authorship.stamp("chatgpt")
    assert "human" in str(refusal.value) and "ai" in str(refusal.value)


def test_the_marks_do_not_get_counted_as_fields(writer):
    """Chi ha compilato due caselle deve leggere «2 campi aggiornati», non
    quattro."""
    result = make_update_su(writer).handler(
        {"us": "12", "fields": {"colore": "bruno", "misure": "0,25"},
         "authored_by": {"colore": "ai"}}, ORCID)
    assert result.ok
    assert len(result.data["updated"]) == 2, result.data["updated"]
    assert "2 campi aggiornati" in result.message


# ── 2 · LA VALIDAZIONE TRASFERISCE L'AUTORIALITÀ ───────────────────────────

def test_validating_transfers_the_authorship_to_the_person(writer):
    """PRIMA e DOPO, sul grafo."""
    make_update_su(writer).handler(
        {"us": "12", "fields": {"interpretazione": "crollo del tetto"},
         "authored_by": {"interpretazione": "ai"},
         "model": "llama-3.2-3b"}, ORCID)

    prima = authorship.read(node(writer), "interpretazione")
    assert prima["by"] == authorship.AI and not prima["validated"]

    result = make_validate_field(writer).handler(
        {"us": "12", "fields": ["interpretazione"]}, ALTRO)
    assert result.ok, result.message
    assert result.data["validated"] == ["interpretazione"]

    dopo = authorship.read(node(writer), "interpretazione")
    assert dopo["by"] == authorship.HUMAN, "l'autorialità non è passata"
    assert dopo["validated"] is True
    assert dopo["validated_by"] == ALTRO
    assert dopo["validated_at"]


def test_validation_keeps_the_memory_of_how_the_value_arrived(writer):
    """Cancellarla trasformerebbe una validazione in una riscrittura della
    storia: fra tre anni nessuno saprebbe più che quel testo l'aveva proposto
    una macchina."""
    make_update_su(writer).handler(
        {"us": "12", "fields": {"interpretazione": "crollo"},
         "authored_by": {"interpretazione": "ai"},
         "model": "llama-3.2-3b"}, ORCID)
    make_validate_field(writer).handler(
        {"us": "12", "fields": ["interpretazione"]}, ALTRO)

    stored = node(writer)["data"][authorship.field_key("interpretazione")]
    assert stored["composed_by"] == authorship.AI
    assert stored["model"] == "llama-3.2-3b"


def test_validation_does_not_touch_the_value(writer):
    """«Ho letto e va bene» non è «ho cambiato». Un tool che facesse entrambe
    le cose renderebbe impossibile distinguerle nel record."""
    make_update_su(writer).handler(
        {"us": "12", "fields": {"interpretazione": "crollo del tetto"},
         "authored_by": {"interpretazione": "ai"}}, ORCID)
    make_validate_field(writer).handler(
        {"us": "12", "fields": ["interpretazione"]}, ALTRO)
    assert node(writer)["data"]["interpretazione"] == "crollo del tetto"


def test_validating_a_field_nobody_composed_is_not_a_tick(writer):
    """Nessuna spunta accanto a un'affermazione che nessuno ha messo in
    dubbio: un campo scritto da una persona non ha bisogno di validazione, e
    dirgli di sì sarebbe un'affermazione che nessuno ha fatto."""
    make_update_su(writer).handler(
        {"us": "12", "fields": {"descrizione": "terra bruna"}}, ORCID)

    result = make_validate_field(writer).handler(
        {"us": "12", "fields": ["descrizione"]}, ALTRO)
    assert result.ok
    assert result.data["validated"] == []
    assert result.data["skipped"] == ["descrizione"]
    assert "niente da validare" in result.message

    said = authorship.read(node(writer), "descrizione")
    assert said["validated"] is False
    assert said["by"] == authorship.HUMAN


def test_validating_twice_is_not_a_second_validation(writer):
    make_update_su(writer).handler(
        {"us": "12", "fields": {"interpretazione": "crollo"},
         "authored_by": {"interpretazione": "ai"}}, ORCID)
    tool = make_validate_field(writer)
    first = tool.handler({"us": "12", "fields": ["interpretazione"]}, ALTRO)
    second = tool.handler({"us": "12", "fields": ["interpretazione"]}, ORCID)
    assert first.data["validated"] == ["interpretazione"]
    assert second.data["validated"] == []
    # …e il primo validatore resta quello
    assert authorship.read(node(writer),
                           "interpretazione")["validated_by"] == ALTRO


def test_validating_on_a_unit_that_is_not_there_is_refused(writer):
    result = make_validate_field(writer).handler(
        {"us": "99", "fields": ["colore"]}, ORCID)
    assert not result.ok
    assert "99" in result.message


def test_the_act_of_validating_is_recorded(writer):
    make_update_su(writer).handler(
        {"us": "12", "fields": {"interpretazione": "crollo"},
         "authored_by": {"interpretazione": "ai"}}, ORCID)
    make_validate_field(writer).handler(
        {"us": "12", "fields": ["interpretazione"]}, ALTRO)

    doc = json.loads(writer.path.read_text(encoding="utf-8"))
    section = next(iter(doc["graphs"].values()))
    tools = {n["data"].get("tool") for n in section["nodes"]
             if n.get("node_type") == "dtc_process"}
    assert "validate_field" in tools
