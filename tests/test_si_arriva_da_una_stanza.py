"""Si arriva da una stanza — cosa dice il link, e cosa si trova.

════════════════════════════════════════════════════════════════════════════════
## IL COLLEGAMENTO C'ERA GIÀ. MANCAVA COSA SUCCEDE DOPO IL CLIC

`handoff.browser_url` del server costruisce `<web>/?server=…&room=…` dal 5
settembre, e `EM_FIELD_ASSISTANT_URL` è valorizzata nel dev-stack. Misurato nel
DOM vero il 6 ottobre, arrivando su quell'indirizzo:

    url letto     {server: "https://em.localhost:8443", room: "scavo"}
    room-name     «container locale»
    #index        non esisteva

**I due parametri non li leggeva nessuno.** L'unico che la pagina guardava era
`?token=`, la porta di servizio del banco. Quindi chi arriva da `/em/rooms/` —
che una stanza l'ha già scelta — atterrava sul microfono, e per riprendere in
mano una US doveva aprire la colonna e riscrivere a mano il nome della stanza
che era già nell'indirizzo.

## LE DUE REGOLE CHE SONO VINCOLI E NON CONSIGLI

**Il link porta un posto, mai un modo.** Chi decide come si vede è la finestra
che arriva. Un `&mode=` si rifiuta **a voce**, come fa `FORBIDDEN` in
`app/handoff.py`: accettarne uno insegna a chi ha costruito il link che mandarlo
funziona, e da quel momento il contratto non ha più la proprietà.

**Arrivare senza `room` resta come adesso.** Un nodo di campo che parte headless
su una stanza nota non ha una finestra e non deve cambiare comportamento.

## LE GUARDIE, E CHE PAGLIAIO GUARDANO (§3)

La prima forza — **eseguire il programma** — è disponibile per tutto ciò che
conta qui: `arrivo.js` e `indice.js` sono puri e si importano con node
(`sorgenti.esegui`). Il documento si interroga con `sorgenti.dentro`. Nessuna
prova qui sotto cerca una parola dentro un sorgente letto come testo.
"""

from __future__ import annotations

import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import sorgenti                                        # noqa: E402

pytest.importorskip("fastapi", reason="needs the [dev] extra")

from fastapi.testclient import TestClient              # noqa: E402

from app.contract import GraphDelta                    # noqa: E402
from app.tools import number_from_unit_id, unit_id_for  # noqa: E402
from app.writer import LocalWriter, units_of           # noqa: E402

WEB = pathlib.Path(__file__).resolve().parent.parent / "web"
needs_node = pytest.mark.skipif(
    not sorgenti.HA_NODE,
    reason="node non è installato: l'arrivo resta non sorvegliato")


def _arrivo(queries):
    return sorgenti.esegui(f"""
const m = await import("./web/arrivo.js");
const out = {{}};
for (const q of {json.dumps(list(queries))}) out[q] = m.readArrival(q);
console.log(JSON.stringify(out));
""")


# ═══ 1 · IL LINK PORTA UN POSTO ══════════════════════════════════════════════

@needs_node
def test_I_DUE_PARAMETRI_DEL_SERVER_si_leggono():
    """La forma esatta che `handoff.browser_url` costruisce."""
    letto = _arrivo(["?server=https%3A%2F%2Fem.localhost%3A8443%2F&room=scavo"])
    solo = letto["?server=https%3A%2F%2Fem.localhost%3A8443%2F&room=scavo"]
    assert solo["server"] == "https://em.localhost:8443"
    assert solo["room"] == "scavo"
    assert solo["refused"] == [] and solo["ignored"] == []


@needs_node
def test_UN_MODO_NEL_LINK_SI_RIFIUTA_a_voce():
    """E in tutte le grafie in cui qualcuno lo scriverebbe.

    A voce e non in silenzio: ignorarlo lascerebbe chi ha costruito il link a
    credere che un giorno funzionerà.
    """
    grafie = ["?room=r&mode=phone", "?room=r&MODE=phone",
              "?room=r&sg-mode=desktop", "?room=r&SG_Mode=tablet",
              "?room=r&sgmode=phone"]
    letto = _arrivo(grafie)
    for q in grafie:
        assert letto[q]["refused"], q
        assert letto[q]["room"] == "r", "…e la stanza si legge lo stesso"


@needs_node
def test_E_IL_MODO_NON_SI_LEGGE_NEMMENO_per_sbaglio():
    """La prova che il rifiuto non è solo un'etichetta: `readArrival` non ha
    nessun campo in cui un modo possa finire."""
    letto = _arrivo(["?room=r&mode=phone"])["?room=r&mode=phone"]
    assert set(letto) == {"server", "room", "refused", "ignored"}
    assert "phone" not in json.dumps(letto)


@needs_node
def test_QUELLO_CHE_NON_E_NELLA_LISTA_CHIUSA_non_si_legge():
    """Una lista chiusa invece di un divieto aperto: così un parametro che
    nessuno ha ancora inventato non ha bisogno di una politica.

    `token` **c'è**, e non nasce stanotte: è la porta di servizio del banco,
    dichiarata in `index.html` («it is therefore a HAZARD in a screenshot —
    which is exactly why it is a bench door»). Toglierla sarebbe stato rompere
    un meccanismo documentato per fare un punto.
    """
    letto = _arrivo(["?room=r&token=abc&pippo=1&utm_source=x"])
    solo = letto["?room=r&token=abc&pippo=1&utm_source=x"]
    assert solo["ignored"] == ["pippo", "utm_source"]
    assert solo["refused"] == []


# ═══ 2 · I TRE ESITI ═════════════════════════════════════════════════════════

@needs_node
def test_ARRIVARE_SENZA_STANZA_non_cambia_niente():
    """Il vincolo di §2: un nodo headless su una stanza nota non ha una
    finestra e non deve cambiare comportamento."""
    detto = sorgenti.esegui("""
const m = await import("./web/arrivo.js");
console.log(JSON.stringify([
  m.arrivalPlan(m.readArrival(""), "scavo"),
  m.arrivalPlan(m.readArrival("?token=abc"), ""),
  m.arrivalPlan(null, "scavo"),
]));
""")
    assert [p["do"] for p in detto] == ["nothing", "nothing", "nothing"]


@needs_node
def test_SE_IL_NODO_SCRIVE_GIA_LI_si_atterra_su_cosa_c_e():
    detto = sorgenti.esegui("""
const m = await import("./web/arrivo.js");
console.log(JSON.stringify(m.arrivalPlan(m.readArrival("?room=scavo"), "scavo")));
""")
    assert detto["do"] == "index" and detto["room"] == "scavo"


@needs_node
def test_E_SE_SCRIVE_ALTROVE_LO_DECIDE_UNA_PERSONA():
    """Ripuntare da soli vorrebbe dire togliere un nodo condiviso a chi ce
    l'ha, in silenzio, per aver aperto un link. `holding.py` esiste perché
    «una persona per volta» sia una frase e non una sorpresa."""
    detto = sorgenti.esegui("""
const m = await import("./web/arrivo.js");
console.log(JSON.stringify([
  m.arrivalPlan(m.readArrival("?room=scavo"), "aiano"),
  m.arrivalPlan(m.readArrival("?room=scavo"), ""),
]));
""")
    assert [p["do"] for p in detto] == ["offer", "offer"]


# ═══ 3 · LE SCHEDE DELLA STANZA ══════════════════════════════════════════════

def _documento(nodes):
    return {"graphs": {"scavo": {"graph_id": "scavo", "nodes": nodes,
                                 "edges": []}}}


def test_LE_UNITA_SI_ELENCANO_col_numero_che_gli_attrezzi_vogliono():
    """Il numero non si indovina: lo dà l'inverso di `unit_id_for`, che vive
    accanto a lui."""
    doc = _documento([
        {"id": unit_id_for("12"), "node_type": "US", "name": "US 12",
         "description": "crollo di tegole",
         "data": {"created_at": "2026-09-06T09:00:00Z", "created_by": "anna",
                  "definizione": "crollo"}},
    ])
    (uno,) = units_of(doc)
    assert uno["number"] == "12"
    assert uno["name"] == "US 12"
    #: `description` sta sul NODO e non in `data`: contarlo lì e non qui direbbe
    #: che un'unità dettata in tre parole è vuota
    assert uno["fields"] == 2
    assert uno["field_names"] == ["definizione", "description"]


def test_I_TIMBRI_NON_SONO_CAMPI_scritti_da_qualcuno():
    """Contare gli orologi direbbe che una scheda vuota è piena."""
    doc = _documento([
        {"id": "US13", "node_type": "US", "name": "US 13",
         "data": {"created_at": "x", "created_by": "y", "modified_at": "z",
                  "modified_by": "w", "field_clocks": {}, "graph_id": "scavo"}},
    ])
    (uno,) = units_of(doc)
    assert uno["fields"] == 0 and uno["field_names"] == []


def test_UNA_FRASE_NON_E_UNA_SCHEDA():
    """I messaggi della conversazione portano il marcatore di volatilità: sono
    nella stanza e non sono unità."""
    from app import conversazione
    doc = _documento([
        {"id": "US13", "node_type": "US", "name": "US 13", "data": {}},
        conversazione.message_node("la 12 taglia la 14", node_id="m1"),
    ])
    assert [u["id"] for u in units_of(doc)] == ["US13"]


def test_UN_TOMBSTONE_NON_E_UN_UNITA():
    doc = _documento([
        {"id": "US13", "node_type": "US", "name": "US 13", "data": {}},
        {"id": "US14", "node_type": "US", "name": "US 14",
         "data": {"removed": {"ts": "2026-09-06T10:00:00Z", "by": "anna"}}},
    ])
    assert [u["id"] for u in units_of(doc)] == ["US13"]


def test_UN_UNITA_IMPORTATA_NON_HA_UN_NUMERO_e_lo_dice():
    """Succede con i grafi convertiti. Proporre una scheda su un numero
    indovinato sarebbe la stessa famiglia di errori del numero mistypato che
    `update_su` esiste per rifiutare."""
    doc = _documento([
        {"id": "n0-3f2a", "node_type": "US", "name": "unità 7 (importata)",
         "data": {}},
    ])
    (uno,) = units_of(doc)
    assert uno["number"] == ""
    #: …ma se il NOME ha la forma che `create_su` dà, il numero si legge da lì
    doc2 = _documento([{"id": "n0-3f2a", "node_type": "US", "name": "US 7",
                        "data": {}}])
    assert units_of(doc2)[0]["number"] == "7"


def test_L_INVERSO_E_DAVVERO_L_INVERSO():
    for numero in ("1", "12", "3014", "1a", "12b"):
        assert number_from_unit_id(unit_id_for(numero)) == numero


def test_I_DUE_SCRIVANI_RISPONDONO_LA_STESSA_COSA(tmp_path):
    """Un nodo che parte headless e uno che arriva da un link non sono due
    superfici diverse: `units_of` è una sola funzione per i due scrivani."""
    w = LocalWriter(str(tmp_path / "c.em.json"))
    w.apply(GraphDelta(nodes=[
        {"id": unit_id_for("12"), "node_type": "US", "name": "US 12",
         "description": "crollo", "data": {"created_at": "x"}}]))
    (uno,) = w.units()
    assert uno == units_of(json.loads((tmp_path / "c.em.json").read_text()))[0]


# ═══ 4 · LA ROTTA ════════════════════════════════════════════════════════════

@pytest.fixture
def nodo(tmp_path, monkeypatch):
    from app import main
    w = LocalWriter(str(tmp_path / "c.em.json"))
    w.apply(GraphDelta(nodes=[
        {"id": unit_id_for("12"), "node_type": "US", "name": "US 12",
         "description": "crollo", "data": {"created_at": "x"}}]))
    monkeypatch.setattr(main, "WRITER", w)
    return main


def test_LA_ROTTA_LEGGE_E_BASTA(nodo):
    client = TestClient(nodo.app)
    letto = client.get("/v1/room/units").json()
    assert letto["total"] == 1
    assert letto["units"][0]["number"] == "12"
    assert letto["room"] is None, "il container locale non è una stanza"
    #: non esiste la gemella che scrive: un'unità si crea con `create_su`
    assert client.post("/v1/room/units", json={}).status_code == 405


def test_E_QUANDO_LA_STANZA_NON_RISPONDE_lo_dice(monkeypatch):
    from app import main

    class Muta:
        base_url = "http://nowhere.invalid"
        room_id = "scavo"

        def units(self):
            raise OSError("nessuna rete")

    monkeypatch.setattr(main, "WRITER", Muta())
    risposta = TestClient(main.app).get("/v1/room/units")
    assert risposta.status_code == 502
    assert "non riesco a leggere" in risposta.json()["detail"].lower()


# ═══ 5 · LA SUPERFICIE ═══════════════════════════════════════════════════════

def test_L_ELENCO_ATTERRA_NELLA_COLONNA_CENTRALE_e_non_nel_cassetto():
    """È dove atterra chi arriva da una stanza, cioè la prima cosa che vede. Il
    cassetto è il posto delle cose che si fanno una volta.

    Il DOCUMENTO, contando i tag (`sorgenti.dentro`): `<main>` contiene già
    sezioni annidate, e una fetta fino alla prima chiusura direbbe che l'elenco
    non c'è — il falso positivo che il 5 ottobre è scattato davvero.
    """
    pagina = (WEB / "index.html").read_text(encoding="utf-8")
    lavoro = sorgenti.dentro(pagina, '<main class="work"')
    assert '<section id="index"' in lavoro
    colonna = sorgenti.dentro(pagina, '<nav class="sidenav"')
    assert 'id="index-list"' not in colonna


def test_ZERO_COLORI_LETTERALI_e_i_token_esistono():
    import re
    testo = (WEB / "indice.js").read_text(encoding="utf-8")
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", testo)
    css = (WEB / "shell.css").read_text(encoding="utf-8")
    blocco = css[css.index("/* ── cosa c'è già in questa stanza"):]
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b|rgba?\(", blocco)
    tema = "".join(p.read_text(encoding="utf-8")
                   for p in (WEB / "brand").glob("*.css"))
    for token in set(re.findall(r"var\((--sg-[\w-]+)", blocco)):
        assert f"{token}:" in tema, f"{token} non è dichiarato dal tema"


def test_UNO_E_MOLTI_SONO_DUE_FRASI():
    """«1 campi» è una traduzione che nessuna lingua accetta. Stesso schema di
    `room.left.one` / `room.left.many`."""
    pagina = (WEB / "index.html").read_text(encoding="utf-8")
    for chiave in ("index.fields.one", "index.fields.many",
                   "index.total.one", "index.total.many"):
        assert pagina.count(f'"{chiave}"') == 2, chiave
