"""Il ponte: quello che si scrive fuori dalla stanza torna dentro.

════════════════════════════════════════════════════════════════════════════════
## IL CASO CHE LO FA SCATTARE

Da ieri StratiField ha un posto a sedere. Sotto non aveva niente: quando la
sessione cadeva, il lavoro finiva nel container locale del nodo e **da lì non
partiva più**. Nessuno lo cancellava, nessuno lo consegnava, e la scheda tornava
a chi l'aveva dettata come riuscita.

Il primo test di questo file spegne il ponte e rimisura la perdita; il secondo è
lo stesso giro con il ponte acceso. È il cancello che verifica l'effetto della
rottura.
"""

from __future__ import annotations

import json
import pathlib
import sys
import time

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

pytest.importorskip("websockets", reason="serve il client websockets")

from app.bridge import Bridge, bridge_for                   # noqa: E402
from app.contract import GraphDelta                         # noqa: E402
from app.writer import LocalWriter, RoomWriter              # noqa: E402
from tests.test_room_writer_wire import FakeRelay           # noqa: E402

ORCID = "0000-0002-1825-0097"
CHIUSO = "http://127.0.0.1:9"          # nessuno ascolta


def _delta(node_id="US12", ts=None):
    return GraphDelta(nodes=[{"id": node_id, "node_type": "US",
                              "name": f"US {node_id}"}],
                      edges=[], process=None, author=ORCID)


def _rientra(writer, relay):
    """La stanza torna, e il backoff si azzera.

    Il backoff è VERO e va tolto a mano qui: dopo un tentativo fallito la
    sessione aspetta un secondo prima di riprovare, che nella vita reale è
    niente — il rientro succede alla consegna dopo, minuti più tardi — e in un
    test sono mille millisecondi di attesa per ogni caso. Azzerarlo è la
    scorciatoia; fingere il backoff non lo sarebbe."""
    writer.base_url = f"http://127.0.0.1:{relay.port}"
    writer.session._not_before = 0.0
    writer.session._backoff = writer.session._backoff_base


def _scrivano(tmp_path, relay=None, *, con_ponte=True):
    local = LocalWriter(str(tmp_path / "scavo.em.json"), study="Scavo")
    base = f"http://127.0.0.1:{relay.port}" if relay else CHIUSO
    ponte = bridge_for(local.path) if con_ponte else None
    w = RoomWriter(base, "stanza", "tok", timeout=2.0, fallback=local,
                   bridge=ponte)
    return w, local


# ═══ 1 · l'effetto della rottura ═════════════════════════════════════════════

def test_senza_ponte_il_lavoro_resta_dove_non_puo_partire(tmp_path):
    """La perdita di ieri, rimisurata: il lavoro c'è, è sul disco del nodo, e
    non esiste niente che sappia che deve viaggiare."""
    with FakeRelay() as relay:
        writer, local = _scrivano(tmp_path, con_ponte=False)   # stanza chiusa
        writer.apply(_delta())
        assert writer.degraded is True
        assert local.has_node("US12"), "posato nel container locale"

        _rientra(writer, relay)                                # la stanza torna
        writer.apply(_delta("US99"))                           # una consegna nuova
        arrivati = {op.get("id") for op in relay.ops}
        assert "US99" in arrivati
        assert "US12" not in arrivati, "senza ponte non torna, ed è il difetto"


def test_col_ponte_lo_stesso_giro_rientra(tmp_path):
    """Identico riga per riga, tranne `con_ponte`."""
    with FakeRelay() as relay:
        writer, local = _scrivano(tmp_path, con_ponte=True)
        writer.apply(_delta())
        assert writer.degraded is True
        assert len(writer.bridge) == 1, "in coda"
        assert local.has_node("US12"), "e anche a portata di mano"

        _rientra(writer, relay)
        writer.apply(_delta("US99"))
        arrivati = [op.get("id") for op in relay.ops]
        assert arrivati == ["US12", "US99"], (
            "il ponte passa PRIMA della roba nuova, o l'ordine si rompe")
        assert len(writer.bridge) == 0
        writer.close()


# ═══ 2 · i numeri del rientro ════════════════════════════════════════════════

def test_niente_duplicati_e_lo_dicono_i_numeri(tmp_path):
    """`applied` / `idempotent`, non «ok».

    Il relay finto risponde `applied: false, reason: 'stale'` quando gli si dice
    di farlo; qui invece si consegna DUE volte la stessa cosa a un relay che
    applica, e si guarda cosa arriva sul filo: le stesse operazioni, con lo
    stesso `ts`. Il grafo non si muove perché è la libreria a deciderlo — le
    misure vere sull'`idempotent` del relay sono nel referto."""
    with FakeRelay() as relay:
        writer, _ = _scrivano(tmp_path)
        writer.apply(_delta())                       # stanza chiusa → in coda
        primo_ts = writer.bridge.pending()[0]["ts"]

        _rientra(writer, relay)
        writer.apply(_delta())                       # la STESSA, ora online
        sul_filo = relay.ops
        assert len(sul_filo) == 2, "una dal ponte, una nuova"
        assert sul_filo[0]["id"] == sul_filo[1]["id"] == "US12"
        assert sul_filo[0]["ts"] == primo_ts, (
            "il ponte ha ritimbrato: una nota delle 10 entrerebbe come le 18")
        writer.close()


def test_in_coda_entrano_le_operazioni_non_lintento(tmp_path):
    """La decisione, verificata sul dato: in coda c'è la forma del filo — verbo,
    id, nodo, e **l'istante del client** — non «crea una US»."""
    writer, _ = _scrivano(tmp_path)
    writer.apply(_delta())
    voce = writer.bridge.pending()[0]
    assert voce["op"] == "add_node" and voce["id"] == "US12"
    assert "ts" in voce and voce["ts"].endswith("Z")
    assert "node" in voce
    # e NON l'identità: quella la mette il token alla consegna
    assert "author" not in voce


# ═══ 3 · sopravvive alla morte del processo ══════════════════════════════════

def test_la_coda_sopravvive_al_processo(tmp_path):
    """La decisione: **su disco**, non in memoria.

    Il servizio viene riavviato di continuo — tre volte in una notte sola il 26
    settembre. Una coda in memoria metterebbe il lavoro su disco (nel container
    locale) e la notizia che deve partire nella RAM: è il difetto di ieri
    spostato di un metro.

    Qui il processo non muore davvero, ma l'oggetto sì: si ricostruisce dal
    percorso, che è esattamente cosa fa un riavvio."""
    writer, local = _scrivano(tmp_path)
    writer.apply(_delta())
    percorso = writer.bridge.path
    assert percorso.is_file()

    del writer
    rinato = Bridge(str(percorso))               # ← il riavvio
    in_attesa = rinato.pending()
    assert len(in_attesa) == 1 and in_attesa[0]["id"] == "US12"


def test_una_coda_scritta_da_un_processo_la_consegna_un_altro(tmp_path):
    """E la consegna la fa chi trova la coda, non chi l'ha scritta."""
    primo, local = _scrivano(tmp_path)
    primo.apply(_delta())
    assert len(primo.bridge) == 1

    with FakeRelay() as relay:
        secondo, _ = _scrivano(tmp_path, relay)   # stesso container, stessa coda
        assert len(secondo.bridge) == 1, "la coda è quella di prima"
        secondo.apply(_delta("US99"))
        assert [op.get("id") for op in relay.ops] == ["US12", "US99"]
        secondo.close()


# ═══ 4 · l'ordine non si rompe ═══════════════════════════════════════════════

def test_un_rifiuto_ferma_la_coda_e_tiene_il_resto(tmp_path):
    """Saltare quella che non passa riordinerebbe operazioni che il CRDT ordina
    per orologio: due scritture sullo stesso campo arriverebbero al contrario, e
    la seconda perderebbe contro la prima.

    Meglio una coda ferma che una coda riordinata — e la coda ferma **si
    vede**."""
    ponte = Bridge(str(tmp_path / "coda.jsonl"))
    ponte.keep([{"op": "add_node", "id": "A", "ts": "2026-09-27T10:00:00Z"},
                {"op": "update_field", "node_id": "B", "field": "x",
                 "ts": "2026-09-27T10:01:00Z"},
                {"op": "add_node", "id": "C", "ts": "2026-09-27T10:02:00Z"}])

    visti = []

    def manda(op):
        visti.append(op.get("id") or op.get("node_id"))
        if op.get("node_id") == "B":
            return False, "node 'B' is not here"
        return True, "added"

    esito = ponte.deliver(manda)
    assert visti == ["A", "B"], "si è fermato al primo che non passa"
    assert esito["delivered"] == 1 and esito["left"] == 2
    assert "not here" in esito["stopped"]
    resto = [op.get("id") or op.get("node_id") for op in ponte.pending()]
    assert resto == ["B", "C"], "il resto è rimasto, in ordine"
    assert ponte.describe()["stuck_because"], "e la coda ferma lo dice"


def test_un_rifiuto_che_dice_ce_gia_e_una_consegna_riuscita(tmp_path):
    """`add_node` su un id esistente FONDE (`merged`), `update_field` ripetuto
    torna `idempotent`. La nota è arrivata: si toglie dalla coda e si conta a
    parte, perché «consegnate 1, già arrivate 2» e «consegnate 3» sono due fatti
    diversi."""
    ponte = Bridge(str(tmp_path / "coda.jsonl"))
    ponte.keep([{"op": "add_node", "id": "A"}, {"op": "add_node", "id": "B"},
                {"op": "update_field", "node_id": "C", "field": "x"}])
    esito = ponte.deliver(lambda op: (False, "idempotent")
                          if op.get("id") != "A" else (True, "added"))
    assert esito == {"delivered": 1, "already": 2, "left": 0, "stopped": None}
    assert ponte.pending() == []


def test_anche_un_MERGED_e_una_cosa_che_cera_gia(tmp_path):
    """LA SOTTIGLIEZZA, misurata sul relay vero rimandando la stessa coda due
    volte: `add_node` su un id esistente **fonde** e torna `applied: True,
    merged`, mentre `update_field` ripetuto torna `applied: False, idempotent`.

    Contare il primo come «consegnato» darebbe «consegnate 2, già arrivate 2»
    per una coda in cui non è cambiato niente — e la prima misura dal vivo ha
    detto esattamente quello, prima che questa riga esistesse."""
    ponte = Bridge(str(tmp_path / "coda.jsonl"))
    ponte.keep([{"op": "add_node", "id": "A"},
                {"op": "update_field", "node_id": "A", "field": "x"}])
    esito = ponte.deliver(lambda op: (True, "merged") if op.get("id")
                          else (False, "idempotent"))
    assert esito["delivered"] == 0 and esito["already"] == 2


def test_una_riga_illeggibile_non_porta_via_la_coda(tmp_path):
    """Perdere il resto per un byte storto sarebbe il difetto che questo file
    chiude, in miniatura."""
    percorso = tmp_path / "coda.jsonl"
    ponte = Bridge(str(percorso))
    ponte.keep([{"op": "add_node", "id": "A"}])
    with open(percorso, "a", encoding="utf-8") as handle:
        handle.write("{questo non è json\n")
    ponte.keep([{"op": "add_node", "id": "B"}])
    assert [op["id"] for op in ponte.pending()] == ["A", "B"]


# ═══ 5 · il container locale dopo la consegna ════════════════════════════════

def test_il_container_locale_e_PARZIALE_non_divergente(tmp_path):
    """La domanda che oggi non aveva risposta, e la parola giusta è la seconda.

    Il container locale non è la coda: è la copia che il nodo ha dello studio, e
    risponde a `study_name`, `count_units` e `answer` quando la stanza non c'è.
    Toglierne il lavoro dopo la consegna renderebbe muto l'assistente sulle cose
    che ha appena registrato.

    **Divergente** vorrebbe dire che i due dicono cose diverse sullo stesso
    campo, e servirebbe una fusione. **Parziale** vuol dire che uno è un
    sottoinsieme dell'altro, e basta rileggere. È la seconda, misurata dal vivo
    il 27 settembre su `US600`:

        locale: {"origin": "stratifield", "colore": "bruno-rossastro"}
        stanza: {"definizione": "strato di crollo", "origin": "stratifield",
                 "colore": "bruno-rossastro"}

    Manca `definizione` perché quel campo era stato scritto **online**, ed era
    andato dritto nella stanza senza passare dal container. Niente si
    contraddice: quello che c'è localmente c'è anche là, con lo stesso valore.

    La conseguenza va detta perché è quella che si sente usando l'assistente:
    `answer`, `count_units` e `study_name` leggono il container, quindi
    **offline il nodo sa rispondere solo su quello che ha scritto lui**.
    Misurato sulla stessa sonda: 4 unità nel container, 10 nella stanza."""
    with FakeRelay() as relay:
        writer, local = _scrivano(tmp_path)
        writer.apply(_delta())                          # stanza chiusa
        _rientra(writer, relay)
        writer.apply(_delta("US99"))                    # rientro: il ponte passa
        writer.close()

    qui = local.node("US12")
    assert qui is not None, "il container locale l'ha ancora"

    # …e quello che è andato sul filo è la stessa unità, non una diversa
    consegnata = next(op for op in relay.ops if op.get("id") == "US12")
    nodo = consegnata["node"]
    assert nodo["id"] == qui["id"]
    assert nodo["node_type"] == qui["node_type"]
    assert nodo["name"] == qui["name"]
    timbri = {"created_at", "created_by", "modified_at", "modified_by",
              "field_clocks"}
    contenuto = {k: v for k, v in (qui.get("data") or {}).items()
                 if k not in timbri}
    la = {k: v for k, v in (nodo.get("data") or {}).items() if k not in timbri}
    # SOTTOINSIEME, non uguaglianza: quello che il container ha, la stanza ce
    # l'ha uguale. Il contrario no, ed è il punto.
    assert contenuto.items() <= la.items() or la.items() <= contenuto.items()
    for chiave, valore in contenuto.items():
        if chiave in la:
            assert la[chiave] == valore, f"i due si contraddicono su {chiave}"


def test_quello_che_e_stato_scritto_ONLINE_non_e_nel_container(tmp_path):
    """La prova della parzialità, sul caso che la fa vedere.

    Un campo scritto mentre la stanza c'era va dritto nella stanza; il container
    del nodo non lo vede passare. Dopo, offline, l'assistente non sa che
    esiste."""
    with FakeRelay() as relay:
        writer, local = _scrivano(tmp_path, relay)      # ONLINE fin da subito
        writer.apply(_delta("US42"))
        writer.close()
    assert not local.has_node("US42"), (
        "se un giorno il container prendesse anche le scritture online, questo "
        "diventa rosso — e sarebbe una scelta, non un caso")


# ═══ 6 · due processi, una coda ═════════════════════════════════════════════

def test_senza_lucchetto_una_operazione_sparisce(tmp_path):
    """IL DIFETTO, ricostruito passo per passo.

    Non è teoria: stanotte, sulla dev-stack, **due processi hanno condiviso
    questa coda senza che nessuno l'avesse previsto** — il servizio che si era
    appena rialzato e uno script di sonda, tutti e due con lo stesso
    `EM_CHATBOT_CONTAINER`. È andata bene per caso.

    `deliver` è una lettura-modifica-scrittura. Qui si eseguono i suoi tre passi
    a mano, con un'aggiunta in mezzo — che è esattamente cosa fa l'altro
    processo — e si guarda cosa resta."""
    a = Bridge(str(tmp_path / "coda.jsonl"))
    b = Bridge(str(tmp_path / "coda.jsonl"))      # ← l'altro processo
    a.keep([{"op": "add_node", "id": f"A{i}"} for i in range(3)])

    letto = a._read()                              # A legge: tre
    b.keep([{"op": "add_node", "id": "B1"}])       # B ne aggiunge una
    a._rewrite(letto[2:])                          # A ne consegna due e riscrive

    resto = [op["id"] for op in a.pending()]
    assert resto == ["A2"], resto
    assert "B1" not in resto, "questo test esiste per dire che B1 sparisce"


def test_col_lucchetto_non_sparisce(tmp_path):
    """LA RIPARAZIONE, con la concorrenza vera: un thread che aggiunge mentre
    la consegna è in corso. `flock` lo fa aspettare, e la sua operazione resta.

    Il lucchetto è un file a parte e non la coda: `flock` su un file che viene
    sostituito da `rename` non protegge niente — il secondo processo prenderebbe
    il lucchetto di un inode che non è più la coda."""
    import threading

    percorso = str(tmp_path / "coda.jsonl")
    a = Bridge(percorso)
    b = Bridge(percorso)
    a.keep([{"op": "add_node", "id": f"A{i}"} for i in range(3)])

    entrato = threading.Event()
    aggiunta = []

    def altro_processo():
        entrato.wait(2)
        b.keep([{"op": "add_node", "id": "B1"}])   # aspetta il lucchetto
        aggiunta.append(True)

    thread = threading.Thread(target=altro_processo, daemon=True)
    thread.start()

    consegnate = []

    def manda(op):
        consegnate.append(op["id"])
        entrato.set()                              # l'altro prova a entrare
        time.sleep(0.05)
        return (True, "added") if op["id"] != "A2" else (False, "not here")

    esito = a.deliver(manda)
    thread.join(timeout=3)
    assert aggiunta == [True], "l'altro processo non è mai entrato"
    assert esito["delivered"] == 2 and esito["left"] == 1
    resto = [op["id"] for op in a.pending()]
    assert resto == ["A2", "B1"], f"B1 è sparita: {resto}"


# ═══ 7 · la coda ferma si vede ═══════════════════════════════════════════════

def test_health_dice_quanto_aspetta_e_perche(tmp_path):
    """Una coda che non si svuota è un lavoro che non è ancora arrivato a
    nessuno, e finché nessuno la nomina somiglia a un servizio che funziona."""
    from app.writer import describe

    writer, _ = _scrivano(tmp_path)
    assert "bridge" not in describe(writer), "vuota, non c'è niente da dire"

    writer.apply(_delta())
    detto = describe(writer)
    assert "bridge: 1 waiting" in detto, detto
    assert "degraded" in detto

    writer.bridge.keep([{"op": "add_node", "id": "Z"}])
    writer.bridge.deliver(lambda op: (False, "node 'Z' is not here"))
    assert "stuck on" in describe(writer)


def test_health_dice_anche_quando_il_ponte_NON_ce(tmp_path):
    """«Nessun ponte» è una configurazione, non un dettaglio."""
    from app.writer import describe

    writer, _ = _scrivano(tmp_path, con_ponte=False)
    assert "no bridge" in describe(writer)


# ═══ 8 · il recinto ══════════════════════════════════════════════════════════

def test_il_ponte_non_parla_con_nessuno():
    """Tiene un file e lo consegna a una funzione che gli viene passata. La via
    verso la stanza resta una sola."""
    import app.bridge as modulo
    source = pathlib.Path(modulo.__file__).read_text(encoding="utf-8")
    codice = "\n".join(l for l in source.splitlines()
                       if not l.lstrip().startswith(("#", "*")))
    for vietato in ("websockets", "connect(", "urlopen", "RoomSession"):
        assert vietato not in codice, f"il ponte parla: {vietato}"
