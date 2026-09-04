"""L'ottavo tool: correggere una scheda che esiste, e NON inventarne una.

Tutto il file gira attorno a una riga misurata in
`s3dgraphy.crdt.apply_op_to_section`:

    if kind == "add_node":
        existing = by_id.get(node_id)
        if existing is None:
            nodes.append(payload)
            return OpResult(True, "added", node_id)

**`add_node` su un id che non c'è lo CREA.** Quindi una scheda che corregge la
US 21 quando nel grafo c'è la US 12 — un numero digitato male, un modulo aperto
sullo studio sbagliato — con `add_node` non fallirebbe: coniarebbe un'unità
nuova con un campo dentro, a nome di chi ha salvato, e riferirebbe un successo.
`update_field` la rifiuta con «node '…' is not here».

Il test `test_the_same_change_as_add_node_would_have_invented_a_unit` fa vedere
**l'effetto della rottura**, non che una sostituzione sia avvenuta: la stessa
modifica, per le due strade, e una delle due lascia dietro un'unità che nessuno
ha scavato.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.assets import InMemoryAssetStore                        # noqa: E402
from app.contract import invoke                                  # noqa: E402
from app.tools import (build_registry, make_create_su, make_update_su,  # noqa: E402
                       unit_id_for)
from app.writer import FieldRefused, LocalWriter, addressable     # noqa: E402

ORCID = "0000-0002-1825-0097"
ALTRO = "0000-0001-5109-3700"


@pytest.fixture
def writer(tmp_path):
    return LocalWriter(str(tmp_path / "scavo.em.json"), study="Saggio B")


def unit(writer, number="12", **slots):
    """Una US che esiste, creata dal tool che la crea."""
    result = make_create_su(writer).handler({"us": number, **slots}, ORCID)
    assert result.ok, result.message
    return result.data["node_id"]


def nodes_of(writer):
    """Ciò che è finito sul disco, riletto — non ciò che il writer dice."""
    section = _section(writer)
    return {n["id"]: n for n in section.get("nodes") or []}


def _section(writer):
    doc = json.loads(writer.path.read_text(encoding="utf-8"))
    return next(iter(doc["graphs"].values()))


# ── 1 · UN AGGIORNAMENTO È UN AGGIORNAMENTO ─────────────────────────────────

def test_an_update_lands_the_fields_on_the_unit_that_exists(writer):
    node_id = unit(writer)
    result = make_update_su(writer).handler(
        {"us": "12", "fields": {"definizione": "strato di crollo",
                                "colore": "bruno"}}, ORCID)
    assert result.ok, result.message

    stored = nodes_of(writer)[node_id]
    assert stored["data"]["definizione"] == "strato di crollo"
    assert stored["data"]["colore"] == "bruno"
    assert set(result.data["updated"]) == {"data.definizione", "data.colore"}


def test_an_update_on_a_unit_that_is_not_there_is_refused(writer):
    """La frase che la persona legge, e NIENTE creato."""
    unit(writer, "12")                       # c'è la 12, non la 21
    before = set(nodes_of(writer))

    result = make_update_su(writer).handler(
        {"us": "21", "fields": {"definizione": "strato"}}, ORCID)

    assert not result.ok
    assert "non è in questo grafo" in result.message
    assert "Creala prima" in result.message
    assert set(nodes_of(writer)) == before, (
        "un aggiornamento rifiutato ha comunque lasciato qualcosa nel grafo")


def test_the_same_change_as_add_node_would_have_invented_a_unit(writer):
    """IL CANCELLO DI QUESTO CAPITOLO, e verifica l'EFFETTO.

    La stessa correzione sulla stessa unità inesistente, per le due strade.
    `update_field` rifiuta e non lascia niente; `add_node` — che è ciò che
    `create_su` e `writer.apply` usano — la conia. Se un giorno le due strade
    diventassero equivalenti, questo test è il posto dove si scopre.
    """
    from s3dgraphy.crdt import apply_op_to_section

    unit(writer, "12")
    fantasma = unit_id_for("21")

    # LA STRADA DI `update_su`
    with pytest.raises(FieldRefused):
        writer.update(fantasma, {"definizione": "strato"}, author=ORCID)
    assert fantasma not in nodes_of(writer)

    # LA STRADA DI `add_node`, sulla stessa sezione, con lo stesso id
    section = {"nodes": [], "edges": []}
    outcome = apply_op_to_section(section, {
        "op": "add_node", "id": fantasma,
        "node": {"id": fantasma, "node_type": "US",
                 "data": {"definizione": "strato"}}, "author": ORCID})
    assert outcome.applied and outcome.reason == "added", (
        "add_node non ha creato: allora il rifiuto di update_field non "
        "dimostra più niente e questo capitolo non ha una ragione")
    assert fantasma in {n["id"] for n in section["nodes"]}


def test_an_update_does_not_touch_who_created_the_unit(writer):
    """La proprietà per cui un aggiornamento non è una creazione, nel dato.

    `created_by` e `created_at` restano di chi ha scavato; si muove
    `modified_*`. Se un aggiornamento riscrivesse la creazione, il grafo
    direbbe che l'unità è stata trovata da chi ha corretto una virgola.
    """
    node_id = unit(writer)
    before = dict(nodes_of(writer)[node_id]["data"])

    make_update_su(writer).handler(
        {"us": "12", "fields": {"colore": "bruno"}}, ALTRO)

    after = nodes_of(writer)[node_id]["data"]
    assert after["created_by"] == before["created_by"] == ORCID
    assert after["created_at"] == before["created_at"]
    assert after.get("modified_by") == ALTRO
    assert after["colore"] == "bruno"


# ── 2 · come i campi vengono indirizzati ────────────────────────────────────

def test_field_names_are_prefixed_where_the_crdt_requires_it():
    """`apply_op_to_section` rifiuta ciò che non è `name`, `description` o
    `data.*`. Il prefisso si mette in UN posto, e questo è il test di quel
    posto: un writer che mandasse `sito` raccoglierebbe un rifiuto per campo e
    riferirebbe un salvataggio riuscito."""
    assert addressable("sito") == "data.sito"
    assert addressable("data.sito") == "data.sito"
    assert addressable("description") == "description"
    assert addressable("name") == "name"
    with pytest.raises(ValueError):
        addressable("  ")


def test_a_field_the_crdt_cannot_address_is_refused_by_the_crdt(writer):
    """La prova che il prefisso serve DAVVERO: senza, la scrittura non atterra.

    Una guardia che non morde dà lo stesso verde di una che funziona, quindi
    qui il campo si manda a mano SENZA prefisso e si guarda l'effetto.
    """
    from s3dgraphy.crdt import apply_op_to_section

    node_id = unit(writer)
    section = _section(writer)

    from s3dgraphy.editorial import now_iso
    stamp = now_iso()

    nudo = apply_op_to_section(section, {"op": "update_field",
                                         "node_id": node_id,
                                         "field": "sito", "value": "Cencelle",
                                         "author": ORCID, "ts": stamp})
    assert not nudo.applied
    assert "not an addressable field" in nudo.reason

    prefisso = apply_op_to_section(section, {"op": "update_field",
                                             "node_id": node_id,
                                             "field": "data.sito",
                                             "value": "Cencelle",
                                             "author": ORCID, "ts": stamp})
    assert prefisso.applied, (
        "col prefisso deve atterrare, altrimenti la prova sopra non misura il "
        "prefisso ma qualcos'altro")


def test_an_operation_without_a_timestamp_is_dead_on_arrival(writer):
    """LA TRAPPOLA CHE HA ROTTO LA PRIMA VERSIONE DI `update`, inchiodata.

    `op_clock` — misurato — costruisce `Clock(ts=None)` per un'operazione senza
    `ts`, e un orologio senza istante PERDE contro qualunque stato timbrato.
    La prima `update` non timbrava, e ogni campo tornava `stale` contro
    un'unità creata due righe prima: un salvataggio che riferiva successo e non
    scriveva niente.

    Qui si guarda l'EFFETTO delle due strade sullo stesso campo.
    """
    from s3dgraphy.crdt import apply_op_to_section, op_clock
    from s3dgraphy.editorial import now_iso

    assert op_clock({"op": "update_field", "author": ORCID}).ts is None

    node_id = unit(writer)
    section = _section(writer)
    base = {"op": "update_field", "node_id": node_id,
            "field": "data.colore", "value": "bruno", "author": ORCID}

    senza = apply_op_to_section(section, dict(base))
    assert not senza.applied and senza.reason == "stale"

    con = apply_op_to_section(section, dict(base, ts=now_iso()))
    assert con.applied, "col timbro deve atterrare"

    # …e il writer vero timbra: è la prova che la lezione è nel codice
    result = make_update_su(writer).handler(
        {"us": "12", "fields": {"consistenza": "friabile"}}, ORCID)
    assert result.ok
    assert result.data["updated"] == ["data.consistenza"], result.data


def test_emptying_a_field_is_a_value_of_its_own(writer):
    """`None` svuota — e «svuotato» non è «mai avuto», che è la ragione per cui
    il CRDT tiene un tombstone di campo."""
    node_id = unit(writer)
    make_update_su(writer).handler(
        {"us": "12", "fields": {"colore": "bruno"}}, ORCID)
    assert nodes_of(writer)[node_id]["data"]["colore"] == "bruno"

    make_update_su(writer).handler(
        {"us": "12", "fields": {"colore": None}}, ORCID)
    assert not nodes_of(writer)[node_id]["data"].get("colore")


# ── 3 · quello che questo tool NON fa ───────────────────────────────────────

@pytest.mark.parametrize("vietato", ["name", "id", "node_type"])
def test_it_refuses_to_rename_a_unit(writer, vietato):
    """`name` è indirizzabile dal CRDT e qui è rifiutato di proposito:
    rinominare un'unità tocca ogni posto che la nomina."""
    unit(writer)
    result = make_update_su(writer).handler(
        {"us": "12", "fields": {vietato: "qualcosa"}}, ORCID)
    assert not result.ok
    assert vietato in result.message
    assert "un altro atto" in result.message


def test_no_fields_is_a_question_not_a_write(writer):
    unit(writer)
    result = make_update_su(writer).handler({"us": "12", "fields": {}}, ORCID)
    assert not result.ok
    assert "che cosa cambiare" in result.message


def test_no_unit_number_is_refused(writer):
    result = make_update_su(writer).handler(
        {"fields": {"colore": "bruno"}}, ORCID)
    assert not result.ok


def test_an_update_with_nobody_behind_it_is_refused_by_the_core(writer):
    """La quarta refusal del contratto vale anche per l'ottavo tool, e vale
    PRIMA che l'handler parta: il grafo non viene nemmeno letto."""
    node_id = unit(writer)
    before = dict(nodes_of(writer)[node_id]["data"])

    result = invoke(make_update_su(writer),
                    {"us": "12", "fields": {"colore": "bruno"}}, None)
    assert not result.ok
    assert result.data["reason"] == "no-author"
    assert nodes_of(writer)[node_id]["data"] == before


def test_the_slots_the_core_requires_are_declared(writer):
    """`fields` è obbligatorio nello schema, quindi la refusal «mi manca» del
    contratto scatta da sé invece che nell'handler."""
    result = invoke(make_update_su(writer), {"us": "12"}, ORCID)
    assert not result.ok
    assert result.data["reason"] == "missing-slots"
    assert "fields" in result.data["missing"]


# ── 4 · l'atto resta registrato, e i conflitti si dicono ────────────────────

def test_the_act_is_recorded_as_an_update_and_not_as_a_creation(writer):
    """Il D7 dice `update_su`. È come si legge, tre anni dopo, che quel valore
    è stato corretto e non trovato."""
    unit(writer)
    result = make_update_su(writer).handler(
        {"us": "12", "fields": {"colore": "bruno"}}, ORCID)

    d7 = [n for n in nodes_of(writer).values()
          if n.get("node_type") == "dtc_process"]
    tools = {n["data"]["tool"] for n in d7}
    assert tools == {"create_su", "update_su"}

    # …e il delta del risultato non dichiara nodi, perché non ne ha creati
    assert result.delta.nodes == []
    assert result.delta.process["data"]["tool"] == "update_su"
    assert result.delta.author == ORCID


def test_a_field_the_room_keeps_is_reported_and_does_not_lose_the_others(writer):
    """Un campo `stale` è il merge CHE FUNZIONA, non un errore.

    Interrompere una scheda al primo `stale` butterebbe undici campi buoni per
    uno. Qui se ne manda uno già scritto più di recente da qualcun altro,
    insieme a uno nuovo, e si pretende che il nuovo atterri e che l'altro venga
    detto.
    """
    node_id = unit(writer)

    # qualcun altro scrive `colore` adesso
    writer.update(node_id, {"colore": "grigio"}, author=ALTRO)

    # noi arriviamo con un valore vecchio per `colore` e uno nuovo per `misure`
    from s3dgraphy.crdt import apply_op_to_section
    section = _section(writer)
    stale = apply_op_to_section(section, {
        "op": "update_field", "node_id": node_id, "field": "data.colore",
        "value": "bruno", "author": ORCID, "ts": "2000-01-01T00:00:00Z"})
    assert not stale.applied and stale.reason == "stale", (
        f"il caso non è stato costruito: {stale.reason}")

    result = make_update_su(writer).handler(
        {"us": "12", "fields": {"misure": "0,25 m"}}, ORCID)
    assert result.ok
    assert nodes_of(writer)[node_id]["data"]["misure"] == "0,25 m"
    assert nodes_of(writer)[node_id]["data"]["colore"] == "grigio"


def test_a_value_that_is_already_that_is_not_reported_as_a_conflict(writer):
    """`idempotent` e `stale` non sono la stessa risposta.

    Trovato nel giro vero: una scheda salvata subito dopo la creazione
    riportava «la stanza ha un valore più recente» per `area`, quando il valore
    era IDENTICO. Dire a una persona che ha perso una modifica che non ha
    perso è peggio che tacere.
    """
    node_id = unit(writer, "12", sito="Cencelle", area="1")
    result = make_update_su(writer).handler(
        {"us": "12", "fields": {"area": "1", "colore": "bruno"}}, ORCID)
    assert result.ok
    assert result.data["updated"] == ["data.colore"]
    assert result.data["already"] == ["data.area"]
    assert result.data["not_applied"] == []
    assert "già così" in result.message
    assert "più di recente" not in result.message


# ── 5 · l'ottavo, e nessun nono per sbaglio ─────────────────────────────────

def test_update_su_is_in_the_registry_with_a_writing_descriptor(writer):
    registry = build_registry(writer, InMemoryAssetStore())
    found = registry.get("update_su")
    assert found is not None
    assert found.writes is True, (
        "un tool che scrive deve dichiararlo, altrimenti la refusal «senza "
        "autore» del contratto non scatta")
    assert found.service == "s3dgraphy"
