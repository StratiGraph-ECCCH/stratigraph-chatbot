"""Dire una frase alla stanza, e rileggerla — dal nodo di campo.

La conversazione è il posto dove si decide, e questo repo ne ha la metà che
sta in mano a chi scava. Quello che si prova qui:

* **la cucitura dichiarata**: la forma di un messaggio è definita nel server e
  tre stringhe sono ricopiate qui. Una prova per parte le fissa, così una
  divergenza è rossa e non silenziosa;
* **non c'è dove scrivere un autore**, e non è una svista;
* **il regalo dell'offline**: una frase è un nodo, e un nodo lo scrivano lo sa
  già mettere in coda quando non c'è rete;
* **la metà che non si può promettere**: senza rete si dice e non si rilegge.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

pytest.importorskip("fastapi", reason="needs the [dev] extra")

from fastapi.testclient import TestClient          # noqa: E402

from app import conversazione                      # noqa: E402
from app.contract import GraphDelta                # noqa: E402


# ── la cucitura, fissata ────────────────────────────────────────────────────

def test_LA_FORMA_DI_UN_MESSAGGIO_e_quella_del_server():
    """Le tre stringhe ricopiate, scritte a mano qui.

    Il gemello è `stratigraph-server/tests/test_cosa_ci_siamo_detti.py`, che le
    fissa dall'altra parte. Due prove che dicono lo stesso letterale sono il
    prezzo di due repo che non si importano — e sono meglio di una convenzione
    che nessuno controlla.
    """
    assert conversazione.INJECTOR == "chat"
    assert conversazione.SAID == "said"
    assert conversazione.NODE_TYPE == "UnknownNode"
    #: la quarta si IMPORTA, perché è la parola su cui poggia il confine della
    #: pubblicazione e non deve poter divergere
    assert conversazione.VOLATILE_KEY == "aux_volatile"
    from s3dgraphy.contract import VOLATILE_KEY
    assert conversazione.VOLATILE_KEY is VOLATILE_KEY


def test_UN_MESSAGGIO_E_UN_NODO_VOLATILE():
    nodo = conversazione.message_node("la 12 taglia la 14", node_id="m1")
    assert nodo["node_type"] == "UnknownNode"
    assert nodo["data"]["said"] == "la 12 taglia la 14"
    assert nodo["data"]["aux_volatile"] == "chat"
    assert conversazione.is_message(nodo)
    #: e il nodo di un'unità NON lo è
    assert not conversazione.is_message(
        {"id": "US12", "node_type": "US", "data": {}})


def test_NON_C_E_DOVE_SCRIVERE_UN_AUTORE():
    """Né nel nodo né nel modello della rotta. Un campo che non esiste è più
    forte di un campo ignorato."""
    nodo = conversazione.message_node("una frase", node_id="m1")
    assert "created_by" not in nodo["data"]
    assert "author" not in nodo and "author" not in nodo["data"]
    from app.main import Detto
    assert set(Detto.model_fields) == {"said"}


# ── il regalo dell'offline, e la metà che non si può promettere ─────────────

class _StanzaMuta:
    """Uno scrivano che non arriva alla stanza — cioè un fosso senza campo."""

    base_url = "http://nowhere.invalid"
    room_id = "scavo"

    def __init__(self):
        self.in_coda = []

    def apply(self, delta):
        self.in_coda.append(delta)

    def conversation(self):
        raise OSError("nessuna rete")


ANNA = "0000-0002-1825-0097"


@pytest.fixture
def muto(monkeypatch):
    from app import main
    scrivano = _StanzaMuta()
    monkeypatch.setattr(main, "WRITER", scrivano)
    #: una firma, perché senza nome questo nodo non lascia dire niente — che è
    #: il test qui sotto
    monkeypatch.setattr(main, "_author", lambda request: ANNA)
    return scrivano


def test_SENZA_UN_NOME_NON_SI_DICE_NIENTE(monkeypatch):
    """Una riga di conversazione senza autore è peggio di una riga in meno.

    In modo sviluppo non c'è identità, e il nodo rifiuta invece di scrivere una
    frase che nella stanza comparirebbe senza il nome di nessuno.
    """
    from app import main
    monkeypatch.setattr(main, "WRITER", _StanzaMuta())
    risposta = TestClient(main.app).post("/v1/room/chat", json={"said": "ciao"})
    assert risposta.status_code == 403
    assert "nome di nessuno" in risposta.json()["detail"]


def test_UNA_FRASE_DETTA_SENZA_RETE_non_si_perde(muto):
    """Lo scrivano la prende, come prende una fotografia: la coda è sua.

    Questo test non prova la coda (la provano quelli di `writer.py`): prova che
    dire una frase **passa dallo scrivano** e non da una via nuova, che è la
    ragione per cui la coda c'è gratis.
    """
    from app.main import app
    client = TestClient(app)
    risposta = client.post("/v1/room/chat", json={"said": "la 12 taglia la 14"})
    assert risposta.status_code == 200, risposta.text
    assert len(muto.in_coda) == 1
    delta = muto.in_coda[0]
    assert isinstance(delta, GraphDelta)
    assert len(delta.nodes) == 1
    assert delta.nodes[0]["data"]["said"] == "la 12 taglia la 14"


def test_E_SENZA_RETE_NON_SI_RILEGGE_e_lo_dice(muto):
    from app.main import app
    client = TestClient(app)
    risposta = client.get("/v1/room/chat")
    assert risposta.status_code == 502
    assert "non ha risposto" in risposta.json()["detail"]


def test_UN_NODO_CHE_SCRIVE_IN_LOCALE_non_ha_una_conversazione(monkeypatch):
    """«Non c'è niente» e «non c'è una stanza» sono due frasi diverse."""
    from app import main

    class Locale:
        pass

    monkeypatch.setattr(main, "WRITER", Locale())
    risposta = TestClient(main.app).get("/v1/room/chat")
    assert risposta.status_code == 409
    assert "contenitore locale" in risposta.json()["detail"]


def test_UNA_FRASE_VUOTA_NON_E_UNA_FRASE(muto):
    from app.main import app
    risposta = TestClient(app).post("/v1/room/chat", json={"said": "   "})
    assert risposta.status_code == 400
    assert muto.in_coda == []


# ── la superficie ───────────────────────────────────────────────────────────

WEB = pathlib.Path(__file__).resolve().parent.parent / "web"


def test_IL_PANNELLO_STA_DIETRO_UN_TOCCO():
    """La regola del telefono: quello che si fa sempre sta davanti, quello che
    si fa quando ci si ferma sta dietro."""
    pagina = (WEB / "index.html").read_text(encoding="utf-8")
    assert 'id="chat-open"' in pagina
    assert 'id="chat" class="chat" hidden' in pagina


def test_ZERO_COLORI_LETTERALI_nella_superficie_nuova():
    """Il tema decide i colori, e una superficie nuova non fa eccezione."""
    import re
    for nome in ("chat.js",):
        testo = (WEB / nome).read_text(encoding="utf-8")
        assert not re.search(r"#[0-9a-fA-F]{3,8}\b", testo)
    css = (WEB / "shell.css").read_text(encoding="utf-8")
    blocco = css[css.index("/* ── cosa ci siamo detti"):]
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b|rgba?\(", blocco)
    #: e i token che usa esistono davvero nel tema
    tema = "".join(p.read_text(encoding="utf-8")
                   for p in (WEB / "brand").glob("*.css"))
    for token in re.findall(r"var\((--sg-[\w-]+)", blocco):
        assert f"{token}:" in tema, f"{token} non è dichiarato dal tema"


def test_LA_SUPERFICIE_DICE_COSA_NON_PROMETTE():
    """Senza rete si dice e non si rilegge, e il pannello lo scrive."""
    pagina = (WEB / "index.html").read_text(encoding="utf-8")
    for chiave in ("chat.unreachable", "chat.queued", "chat.boundary"):
        assert f'"{chiave}"' in pagina
    #: in tutt'e due le lingue
    assert pagina.count('"chat.boundary"') == 2
