"""Cosa fa il servizio quando la stanza non c'è — misurato prima di ripararlo.

════════════════════════════════════════════════════════════════════════════════
## PERCHÉ QUESTO FILE ESISTE, E UN ERRORE MIO DA CORREGGERE

Nel referto del 26 settembre ho scritto:

> «Misurato durante la galleria: `update()` ha sollevato `SessionClosed:
> gaierror`, **non ha scritto localmente**.»

**La misura era vera e la conclusione era sbagliata**, perché la sonda
costruiva `RoomWriter(base, room, token)` — cioè **senza `fallback`** — e il
servizio vero non è configurato così: `writer_from_env` passa sempre un
`LocalWriter`. Avevo misurato una configurazione che nessuno usa e l'avevo
riportata come il comportamento del servizio.

È la stessa forma dell'errore che il prompt di stanotte si attribuisce sul
ponte del browser: **una cosa che vale per una strada non vale per tutte, e
prima di appoggiarcisi si guarda chi la chiama.**

Quindi questo file misura la tabella per intero, nelle due configurazioni, e
resta come il posto dove quella domanda ha una risposta invece di una memoria.
"""

from __future__ import annotations

import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

pytest.importorskip("websockets", reason="serve il client websockets")

from app.contract import GraphDelta                        # noqa: E402
from app.writer import LocalWriter, RoomWriter, RoomRefused  # noqa: E402

ORCID = "0000-0002-1825-0097"
#: una porta chiusa: nessuno ascolta, e `connect` fallisce subito
NESSUNO = "http://127.0.0.1:9"


def _delta():
    return GraphDelta(nodes=[{"id": "US12", "node_type": "US", "name": "US 12"}],
                      edges=[], process=None, author=ORCID)


def _local(tmp_path) -> LocalWriter:
    return LocalWriter(str(tmp_path / "scavo.em.json"), study="Scavo")


def _has(local: LocalWriter, node_id: str) -> bool:
    return local.has_node(node_id)


# ── la tabella ──────────────────────────────────────────────────────────────

def test_apply_senza_ripiego_INGOIA_lerrore(tmp_path):
    """`apply` con `fallback=None` non solleva e non scrive: torna e basta.

    È l'esito peggiore dei tre, ed è quello che nessuno vedrebbe. Resta
    `degraded` con la ragione, che è l'unico posto dove la cosa si nota."""
    writer = RoomWriter(NESSUNO, "stanza", "tok", timeout=1.0)
    writer.apply(_delta())              # nessuna eccezione
    assert writer.degraded is True
    assert "gaierror" in (writer.last_refusal or "") or writer.last_refusal


def test_apply_col_ripiego_scrive_nel_container_locale(tmp_path):
    """La configurazione VERA del servizio. Il lavoro si posa sul disco del
    nodo, e — fino a stanotte — da lì non partiva più."""
    local = _local(tmp_path)
    writer = RoomWriter(NESSUNO, "stanza", "tok", timeout=1.0, fallback=local)
    writer.apply(_delta())
    assert writer.degraded is True
    assert _has(local, "US12"), "il lavoro non è nemmeno nel container locale"


def test_update_senza_ripiego_SOLLEVA(tmp_path):
    """La riga che il referto di ieri riportava come «il comportamento del
    servizio». Lo è solo senza `fallback`, che è la configurazione che il
    servizio non ha mai."""
    writer = RoomWriter(NESSUNO, "stanza", "tok", timeout=1.0)
    with pytest.raises(Exception) as caduta:
        writer.update("US12", {"definizione": "strato"}, author=ORCID)
    assert not isinstance(caduta.value, RoomRefused), (
        "una rete che manca non è un rifiuto")
    assert writer.degraded is True


def test_update_col_ripiego_scrive_nel_container_locale(tmp_path):
    local = _local(tmp_path)
    local.apply(_delta())               # l'unità esiste già, localmente
    writer = RoomWriter(NESSUNO, "stanza", "tok", timeout=1.0, fallback=local)
    esiti = writer.update("US12", {"definizione": "strato di crollo"},
                          author=ORCID)
    assert writer.degraded is True
    assert esiti and all(e.get("applied") for e in esiti)
    assert local.node("US12")["data"]["definizione"] == "strato di crollo"


@pytest.mark.parametrize("chiamata", [
    lambda w: w.node("US12"),
    lambda w: w.has_node("US12"),
    lambda w: w.study_name(),
    lambda w: w.count_units(),
    lambda w: w.answer("cosa abbiamo registrato"),
])
def test_le_letture_non_sollevano_mai(tmp_path, chiamata):
    """Le letture ricadono in silenzio, con e senza ripiego. È corretto — una
    domanda senza risposta non deve rompere una scheda — e va detto, perché
    vuol dire che **una lettura non segnala mai che la stanza non c'è**."""
    local = _local(tmp_path)
    chiamata(RoomWriter(NESSUNO, "s", "t", timeout=1.0, fallback=local))
    chiamata(RoomWriter(NESSUNO, "s", "t", timeout=1.0))


def test_la_tabella_in_una_riga(tmp_path):
    """IL RIASSUNTO, eseguito invece che scritto in una docstring.

        percorso            senza fallback        con fallback (il servizio)
        ─────────────────────────────────────────────────────────────────────
        apply               ingoia, degraded      container locale, degraded
        update              SOLLEVA               container locale, degraded
        node / has_node     None, in silenzio     container locale
        study_name          il room_id            container locale
        count_units         0                     container locale
        answer              una frase vuota       container locale

    Il codice sceglie fra «solleva» e «si posa altrove» **in base a quale
    eccezione è passata di lì**, che non è una scelta: è un caso. `apply` ha un
    `except Exception` che ingoia, `update` ne ha uno che rialza quando manca
    il ripiego.
    """
    local = _local(tmp_path)
    con = RoomWriter(NESSUNO, "s", "t", timeout=1.0, fallback=local)
    senza = RoomWriter(NESSUNO, "s", "t", timeout=1.0)

    con.apply(_delta())                       # non solleva
    senza.apply(_delta())                     # non solleva
    assert _has(local, "US12") is True

    con.update("US12", {"colore": "bruno"}, author=ORCID)   # non solleva
    with pytest.raises(Exception):
        senza.update("US12", {"colore": "bruno"}, author=ORCID)
