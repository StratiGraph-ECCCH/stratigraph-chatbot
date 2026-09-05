"""Le parole che mancavano — un campo detto a voce.

Il 21 settembre si è visto che nessuno dei sette intenti permetteva di dire un
rapporto stratigrafico. **La scoperta era più larga**: non c'era un modo di dire
nemmeno `definizione` — che è obbligatoria — né le quote, né le misure. Sulla US
ICCD i campi da trincea erano **8 su 59**: non una scheda semplificata, una
scheda quasi vuota.

**Il collo di bottiglia non era il formato, erano le parole.**

E non è servito un tool nuovo: `update_su` scrive già qualunque campo. Mancava
il VOCABOLARIO che porta una frase a un campo.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.assets import InMemoryAssetStore                        # noqa: E402
from app.intent import extract_spoken_field, understand          # noqa: E402
from app.tools import (SPOKEN_FIELDS, build_registry,             # noqa: E402
                       make_create_su, make_update_su)
from app.writer import LocalWriter                               # noqa: E402

ORCID = "0000-0002-1825-0097"


@pytest.fixture
def writer(tmp_path):
    w = LocalWriter(str(tmp_path / "scavo.em.json"), study="Saggio B")
    assert make_create_su(w).handler({"us": "12"}, ORCID).ok
    return w


@pytest.fixture
def registry(writer):
    return build_registry(writer, InMemoryAssetStore())


# ── 1 · LE FRASI, riconosciute DALLE REGOLE e senza modello ────────────────

@pytest.mark.parametrize("said, field, value", [
    ("la us 12 è uno strato di crollo", "definizione", "strato di crollo"),
    ("la definizione della us 12 è muro", "definizione", "muro"),
    ("us 12 quota 145,30", "quote", "145,30"),
    ("la quota della us 7 è -1,25", "quote", "-1,25"),
    ("la us 12 misura 2 per 1,5 metri", "misure", "2 per 1,5 metri"),
    ("il colore della us 12 è bruno scuro", "colore", "bruno scuro"),
    ("la consistenza della us 12 è friabile", "consistenza", "friabile"),
])
def test_a_field_said_out_loud_reaches_update_su(registry, said, field, value):
    understood = understand(said, registry)              # NESSUN modello
    assert understood.tool == "update_su", understood.as_dict()
    assert understood.via == "rules", (
        "riconosciuto dal modello: sul campo il modello può non esserci, ed è "
        "un martedì")
    assert understood.slots["fields"] == {field: value}


def test_the_unit_number_comes_out_of_the_same_sentence(registry):
    assert understand("la us 12 è uno strato", registry).slots["us"] == "12"
    assert understand("la quota della us 7 è -1,25",
                      registry).slots["us"] == "7"


# ── 2 · LA VIRGOLA È IL DATO, e la prima versione la buttava ───────────────

def test_the_value_keeps_its_punctuation():
    """`_normalise` toglie la punteggiatura per far combaciare le frasi, e su un
    valore la distrugge. La prima versione prendeva la coda dal testo
    normalizzato: «quota 145,30» diventava «145 30».

    Su una quota e su una misura **la virgola è il dato**, e sono precisamente
    i due campi che questa serata aggiunge.
    """
    assert extract_spoken_field("us 12 quota 145,30")["value"] == "145,30"
    assert extract_spoken_field("la us 12 misura 2 per 1,5 metri")["value"] \
        == "2 per 1,5 metri"
    assert extract_spoken_field("la quota è -1,25")["value"] == "-1,25"


def test_that_the_punctuation_check_would_catch_the_old_bug():
    """Una guardia che non morde dà lo stesso verde di una che funziona.

    Si applica al valore la stessa normalizzazione che la prima versione
    applicava, e si pretende che il risultato sia DIVERSO — altrimenti il test
    sopra passerebbe anche col difetto.
    """
    from app.intent import _normalise

    value = extract_spoken_field("us 12 quota 145,30")["value"]
    assert _normalise(value) != value, (
        "la normalizzazione non cambia più questo valore: il test sopra non "
        "sta più misurando il difetto che ha trovato")
    assert _normalise(value) == "145 30"


# ── 3 · quello che NON riconosce ───────────────────────────────────────────

def test_a_phrase_with_nothing_after_it_is_a_question_not_a_write():
    """«definizione» detto da solo è una domanda."""
    assert extract_spoken_field("definizione") is None
    assert extract_spoken_field("la quota è") is None


def test_a_word_nobody_declared_is_not_invented():
    assert extract_spoken_field("la us 12 pesa 4 chili") is None


def test_the_longest_phrase_wins():
    """«la definizione è» deve battere «definizione», altrimenti il valore
    comincerebbe con « è »."""
    assert extract_spoken_field("la definizione è muro")["value"] == "muro"


def test_the_phrases_and_the_map_are_ONE_list(registry):
    """Due elenchi scritti a mano divergono — l'ho già pagato con le forme al
    femminile di `relate_su`, che la mappa conosceva e gli intenti no."""
    declared = registry.get("update_su").intents
    for phrases in SPOKEN_FIELDS.values():
        for phrase in phrases:
            assert phrase in declared, phrase


# ── 4 · e la frase ATTERRA nel grafo ───────────────────────────────────────

def test_the_sentence_becomes_a_field_in_the_graph(writer, registry):
    from app.contract import invoke

    understood = understand("la us 12 è uno strato di crollo", registry)
    result = invoke(registry.get(understood.tool), understood.slots, ORCID,
                    registry=registry)
    assert result.ok, result.message

    node = writer.node("US12")
    assert node["data"]["definizione"] == "strato di crollo"
    assert result.data["updated"] == ["data.definizione"]


def test_a_spoken_field_is_authored_by_the_person_not_by_a_model(writer,
                                                                 registry):
    """Le regole non compongono: riconoscono. Una frase capita da una regola è
    verbatim, e il campo resta di chi l'ha detto."""
    from app.authorship import read
    from app.contract import invoke

    understood = understand("il colore della us 12 è bruno", registry)
    assert understood.via == "rules"
    invoke(registry.get("update_su"), understood.slots, ORCID, registry=registry)

    said = read(writer.node("US12"), "colore")
    assert said["by"] == "human"
    assert said["validated"] is False


def test_two_things_said_in_two_sentences_are_two_fields(writer, registry):
    from app.contract import invoke

    for phrase in ("la us 12 è uno strato di crollo",
                   "la us 12 misura 2 per 1,5 metri"):
        understood = understand(phrase, registry)
        assert invoke(registry.get("update_su"), understood.slots, ORCID,
                      registry=registry).ok

    data = writer.node("US12")["data"]
    assert data["definizione"] == "strato di crollo"
    assert data["misure"] == "2 per 1,5 metri"
