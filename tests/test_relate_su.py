"""Il nono tool: «la 12 copre la 18» — l'intento che mancava.

Trovato marcando la scheda ICCD in `stratigraph-templates` (22 settembre):
nessuno dei sette intenti permetteva di registrare un rapporto stratigrafico a
voce, e per questo le dieci caselle dei rapporti di quella scheda sono rimaste
`unknown` — marcarle `trench` senza un intento che le copra sarebbe stato
inventare il criterio.

**La cosa che questo file difende sopra tutte:** gli inversi NON esistono come
tipi di arco. Misurato in `s3Dgraphy_connections_datamodel.json` 1.6.13:
`is_after` c'è, `is_before` no; `cuts` c'è, `is_cut_by` no. Esistono solo come
etichetta `reverse` per LEGGERE un arco al contrario. Quindi «la 12 è coperta
dalla 18» si registra **scambiando i capi**, e una freccia messa al rovescio è
l'unico errore in stratigrafia che cambia la sequenza.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.assets import InMemoryAssetStore                        # noqa: E402
from app.contract import invoke                                  # noqa: E402
from app.intent import extract_relation, understand              # noqa: E402
from app.tools import (RELATIONS, build_registry, edge_id_for,    # noqa: E402
                       make_create_su, make_relate_su, unit_id_for)
from app.writer import LocalWriter                               # noqa: E402

ORCID = "0000-0002-1825-0097"


@pytest.fixture
def writer(tmp_path):
    return LocalWriter(str(tmp_path / "scavo.em.json"), study="Saggio B")


@pytest.fixture
def registry(writer):
    return build_registry(writer, InMemoryAssetStore())


def units(writer, *numbers):
    for n in numbers:
        assert make_create_su(writer).handler({"us": str(n)}, ORCID).ok


def graph_of(writer):
    doc = json.loads(writer.path.read_text(encoding="utf-8"))
    return next(iter(doc["graphs"].values()))


def edges_of(writer):
    return graph_of(writer).get("edges") or []


# ── 1 · IL DATAMODEL, misurato — la ragione per cui esiste lo scambio ───────

def test_the_inverse_edge_types_do_not_exist():
    """Se un giorno `is_before` comparisse nel datamodel, lo scambio dei capi
    diventerebbe una scelta invece che una necessità — e questo test è il posto
    dove la cosa va ridiscussa, non scoperta."""
    import pathlib

    import s3dgraphy

    base = pathlib.Path(s3dgraphy.__file__).parent
    model = json.loads(
        (base / "JSON_config" / "s3Dgraphy_connections_datamodel.json")
        .read_text(encoding="utf-8"))
    declared = model["edge_types"]

    assert model["s3Dgraphy_connections_model_version"] == "1.6.13"
    for present in ("is_after", "cuts", "fills", "abuts", "is_bonded_to",
                    "is_physically_equal_to", "has_same_time"):
        assert present in declared, present
    for absent in ("is_before", "is_overlain_by", "is_cut_by"):
        assert absent not in declared, (
            f"{absent} è comparso: gli inversi ora esistono, e la mappa di "
            f"`RELATIONS` va ridiscussa invece che scambiare i capi")


def test_every_verb_maps_to_a_type_the_datamodel_declares():
    """Nessun tipo inventato. Un arco con un `edge_type` che il datamodel non
    conosce è la cosa che il recinto di questo repository vieta."""
    import pathlib

    import s3dgraphy

    base = pathlib.Path(s3dgraphy.__file__).parent
    declared = json.loads(
        (base / "JSON_config" / "s3Dgraphy_connections_datamodel.json")
        .read_text(encoding="utf-8"))["edge_types"]
    for verb, (edge_type, _direction) in RELATIONS.items():
        assert edge_type in declared, f"{verb} → {edge_type} non esiste"


def test_the_map_is_the_same_one_the_ecosystem_already_uses():
    """Lo stesso rapporto, detto a voce o importato da una tabella, deve
    diventare LO STESSO arco: altrimenti la porta da cui è entrato si vede nel
    grafo. La mappa di riferimento è quella di
    `pyarchinit-mini/pyarchinit_mini/connector/us_ops.py`."""
    assert RELATIONS["copre"][0] == "is_after"
    assert RELATIONS["taglia"][0] == "cuts"
    assert RELATIONS["riempie"][0] == "fills"
    assert RELATIONS["si appoggia a"][0] == "abuts"
    assert RELATIONS["si lega a"][0] == "is_bonded_to"
    assert RELATIONS["uguale a"][0] == "is_physically_equal_to"
    assert RELATIONS["contemporaneo a"][0] == "has_same_time"


# ── 2 · LE REGOLE, senza modello — che sul campo è un martedì ──────────────

@pytest.mark.parametrize("sentence, expected", [
    ("la 12 copre la 18", {"us": "12", "relation": "copre", "other": "18"}),
    ("US 12 copre US 18", {"us": "12", "relation": "copre", "other": "18"}),
    ("la 12 è coperta dalla 18",
     {"us": "12", "relation": "coperta da", "other": "18"}),
    ("la 3 è tagliata dalla 7",
     {"us": "3", "relation": "tagliata da", "other": "7"}),
    ("la 5 riempie la 9", {"us": "5", "relation": "riempie", "other": "9"}),
    ("la 5 è riempita dalla 9",
     {"us": "5", "relation": "riempita da", "other": "9"}),
    ("la 12 si appoggia alla 11",
     {"us": "12", "relation": "si appoggia a", "other": "11"}),
    ("la 12 si lega alla 18",
     {"us": "12", "relation": "si lega a", "other": "18"}),
    ("la 12 è uguale alla 21",
     {"us": "12", "relation": "uguale a", "other": "21"}),
    ("la 12 è contemporanea alla 18",
     {"us": "12", "relation": "contemporanea a", "other": "18"}),
    ("la 12 è posteriore alla 18",
     {"us": "12", "relation": "posteriore a", "other": "18"}),
    ("la 12 è anteriore alla 18",
     {"us": "12", "relation": "anteriore a", "other": "18"}),
])
def test_the_field_phrase_routes_and_fills_all_three_slots(registry, sentence,
                                                           expected):
    understood = understand(sentence, registry)          # NO model passed
    assert understood.tool == "relate_su", understood.as_dict()
    assert understood.via == "rules", (
        "riconosciuto dal modello: sul campo il modello può non esserci")
    assert understood.slots == expected


def test_the_feminine_forms_are_the_ones_people_actually_say(registry):
    """Un'unità stratigrafica è femminile: «la 12 è copertA dalla 18».

    Le forme al femminile erano nella mappa e NON nella lista degli intenti,
    perché le due liste erano scritte a mano, e quelle frasi non venivano
    riconosciute affatto. Ora la lista È la mappa. Questo test è ciò che
    impedisce alle due di divergere di nuovo.
    """
    descriptor = registry.get("relate_su")
    for feminine in ("coperta da", "tagliata da", "riempita da",
                     "contemporanea a"):
        assert feminine in RELATIONS
        assert feminine in descriptor.intents, (
            f"«{feminine}» è nella mappa e non fra gli intenti: la frase non "
            f"verrebbe riconosciuta")


def test_a_sentence_with_one_number_fills_nothing_rather_than_guessing():
    assert extract_relation("la 4 gli si appoggia") is None
    assert extract_relation("crea la us 12") is None


def test_a_verb_nobody_declared_is_not_invented():
    assert extract_relation("la 12 somiglia alla 18") is None


def test_the_longest_verb_wins():
    """«si appoggia a» deve battere «appoggia a», altrimenti il verbo cambia."""
    found = extract_relation("la 12 si appoggia alla 11")
    assert found["relation"] == "si appoggia a"


# ── 3 · L'ARCO che ne esce ─────────────────────────────────────────────────

def test_copre_lands_one_edge_in_the_canonical_direction(writer):
    units(writer, 12, 18)
    result = make_relate_su(writer).handler(
        {"us": "12", "other": "18", "relation": "copre"}, ORCID)
    assert result.ok, result.message

    edges = edges_of(writer)
    assert len(edges) == 1
    assert edges[0]["source"] == "US12"
    assert edges[0]["target"] == "US18"
    assert edges[0]["edge_type"] == "is_after"


def test_the_inverse_swaps_the_ends_and_does_not_invent_a_type(writer):
    """IL CUORE DEL TOOL. «La 12 è coperta dalla 18» vuol dire che la 18 è
    posteriore alla 12, quindi l'arco parte da 18."""
    units(writer, 12, 18)
    result = make_relate_su(writer).handler(
        {"us": "12", "other": "18", "relation": "coperta da"}, ORCID)
    assert result.ok, result.message

    edge = edges_of(writer)[0]
    assert edge["edge_type"] == "is_after", "non si inventa un tipo inverso"
    assert edge["source"] == "US18", "i capi non sono stati scambiati"
    assert edge["target"] == "US12"
    assert result.data["direction"] == "swap"
    assert result.data["said"] == "coperta da"


def test_the_two_ways_of_saying_it_produce_the_SAME_edge(writer):
    """«12 copre 18» e «18 è coperta dalla 12» sono lo stesso fatto.

    Un grafo che li registra come due archi conta due rapporti dove ce n'è uno,
    e la matrice li disegna entrambi.
    """
    units(writer, 12, 18)
    tool = make_relate_su(writer)
    assert tool.handler({"us": "12", "other": "18", "relation": "copre"},
                        ORCID).ok
    assert tool.handler({"us": "18", "other": "12", "relation": "coperta da"},
                        ORCID).ok
    assert len(edges_of(writer)) == 1, edges_of(writer)


@pytest.mark.parametrize("verb", ["uguale a", "si lega a", "contemporaneo a"])
def test_a_symmetric_relation_is_one_edge_whichever_end_you_name(writer, verb):
    """I capi si ORDINANO. Senza, i simmetrici sarebbero il solo posto che
    raddoppia ancora — che è precisamente il difetto che `us_ops._oriented`
    documenta di aver evitato dall'altra parte."""
    units(writer, 12, 18)
    tool = make_relate_su(writer)
    assert tool.handler({"us": "12", "other": "18", "relation": verb}, ORCID).ok
    assert tool.handler({"us": "18", "other": "12", "relation": verb}, ORCID).ok
    assert len(edges_of(writer)) == 1, edges_of(writer)


def test_the_edge_id_follows_the_convention_the_ecosystem_composes(writer):
    """`source__type__target` — così un arco detto a voce e lo stesso arco
    disegnato a mano in EMStudio SONO un arco."""
    units(writer, 12, 18)
    make_relate_su(writer).handler(
        {"us": "12", "other": "18", "relation": "copre"}, ORCID)
    assert edges_of(writer)[0]["id"] == "US12__is_after__US18"
    assert edge_id_for("US12", "is_after", "US18") == "US12__is_after__US18"


def test_saying_it_twice_does_not_double_the_arrow(writer):
    units(writer, 12, 18)
    tool = make_relate_su(writer)
    tool.handler({"us": "12", "other": "18", "relation": "copre"}, ORCID)
    tool.handler({"us": "12", "other": "18", "relation": "copre"}, ORCID)
    assert len(edges_of(writer)) == 1


# ── 4 · quello che rifiuta ─────────────────────────────────────────────────

def test_an_edge_to_a_unit_that_does_not_exist_is_refused(writer):
    """Un arco verso un id che nessuno può risolvere è peggio di un arco che
    manca: la matrice lo disegna, e la freccia punta nel vuoto."""
    units(writer, 12)
    result = make_relate_su(writer).handler(
        {"us": "12", "other": "18", "relation": "copre"}, ORCID)
    assert not result.ok
    assert "18" in result.message
    assert result.data["missing"] == ["18"]
    assert edges_of(writer) == []


def test_both_missing_units_are_named(writer):
    result = make_relate_su(writer).handler(
        {"us": "12", "other": "18", "relation": "copre"}, ORCID)
    assert not result.ok
    assert result.data["missing"] == ["12", "18"]


def test_a_unit_cannot_be_in_a_relation_with_itself(writer):
    units(writer, 12)
    result = make_relate_su(writer).handler(
        {"us": "12", "other": "12", "relation": "copre"}, ORCID)
    assert not result.ok
    assert "se stessa" in result.message
    assert edges_of(writer) == []


def test_an_unknown_verb_is_refused_and_says_what_it_knows(writer):
    units(writer, 12, 18)
    result = make_relate_su(writer).handler(
        {"us": "12", "other": "18", "relation": "somiglia a"}, ORCID)
    assert not result.ok
    assert "somiglia a" in result.message
    assert "copre" in result.message and "uguale a" in result.message


def test_one_unit_alone_is_a_question_not_a_guess(writer):
    units(writer, 12)
    result = make_relate_su(writer).handler(
        {"us": "12", "relation": "copre"}, ORCID)
    assert not result.ok
    assert "due unità" in result.message


def test_a_relation_with_nobody_behind_it_is_refused(writer):
    units(writer, 12, 18)
    result = invoke(make_relate_su(writer),
                    {"us": "12", "other": "18", "relation": "copre"}, None)
    assert not result.ok
    assert result.data["reason"] == "no-author"
    assert edges_of(writer) == []


# ── 5 · l'atto resta registrato ────────────────────────────────────────────

def test_the_act_records_what_was_said_and_not_only_what_was_written(writer):
    """Chi rilegge deve poter vedere che «coperta da» è diventata un `is_after`
    a capi scambiati, e non un tipo inverso che non esiste."""
    units(writer, 12, 18)
    result = make_relate_su(writer).handler(
        {"us": "12", "other": "18", "relation": "coperta da"}, ORCID)

    d7 = [n for n in graph_of(writer)["nodes"]
          if n.get("node_type") == "dtc_process"
          and n["data"].get("tool") == "relate_su"]
    assert len(d7) == 1
    assert "coperta da" in d7[0]["description"]
    assert d7[0]["data"]["created_by"] == ORCID
    assert result.delta.author == ORCID
    assert result.delta.edges[0]["edge_type"] == "is_after"
