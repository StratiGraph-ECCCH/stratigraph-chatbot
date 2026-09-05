"""StratiField siede al tavolo della stanza, invece di imbucare lettere.

════════════════════════════════════════════════════════════════════════════════
## L'INVARIANTE, E LA MISURA CHE DICEVA CHE NON C'ERA

Fino al 26 settembre `RoomWriter._send_ops` faceva `with connect(...)`: apre,
entra, manda, chiude. **Una connessione per consegna.** Il websocket usato come
mezzo di trasporto e non come presenza — e fra una consegna e l'altra nessuno
nella stanza sapeva che StratiField esistesse.

È la stessa cosa del difetto di persistenza del relay, vista dall'altro lato: il
client che avrebbe dovuto chiedere `request_save` se n'era già andato. Non aveva
dimenticato di chiedere — **non c'era più nessuno a chiedere**.

> **Una connessione non è una presenza.** Aprire, mandare e chiudere assomiglia
> a partecipare fino al momento in cui qualcuno ti cerca.

Il primo test di questo file è il cancello dell'invariante: due consegne, **una**
connessione. Con il codice di ieri sarebbero due, e il numero lo dice.
"""

from __future__ import annotations

import pathlib
import sys
import time

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

pytest.importorskip("websockets", reason="serve il client websockets")

from app.session import RoomSession, SessionClosed, SessionRefused  # noqa: E402
from app.writer import RoomRefused                                  # noqa: E402
from tests.test_room_writer_wire import FakeRelay, _delta, _writer   # noqa: E402


# ═══ 1 · il posto a sedere ═══════════════════════════════════════════════════

def test_due_consegne_una_sola_connessione():
    """IL CANCELLO DELL'INVARIANTE, e il numero è la prova.

    Con il codice del 25 settembre questo test dice `2`. Non «è più efficiente»:
    è che con `2` StratiField non è nella stanza fra una consegna e l'altra, e
    la stanza non sa che c'è."""
    with FakeRelay() as relay:
        writer = _writer(relay)
        writer.apply(_delta())
        writer.apply(_delta())
        assert relay.connections == 1, (
            f"{relay.connections} connessioni per due consegne: "
            f"è un corrispondente, non un seduto")
        assert writer.session.seated is True
        writer.close()
    assert writer.session.seated is False


def test_fra_una_consegna_e_laltra_resta_dentro():
    """Il tempo che passa non chiude il posto: è la differenza fra «sono qui» e
    «ero passato»."""
    with FakeRelay() as relay:
        writer = _writer(relay)
        writer.apply(_delta())
        time.sleep(0.3)
        assert writer.session.seated is True
        assert writer.session.uptime() >= 0.3
        writer.close()


def test_il_ciclo_di_vita_e_esplicito():
    """Qualcuno apre, qualcuno chiude. E un processo che finisce senza chiudere
    è il caso normale del telefono, non un difetto: il socket cade e la stanza
    se ne accorge da sola — la rete del relay (`app/keeping.py` di là) salva
    quando l'ultimo che sa scrivere esce."""
    with FakeRelay() as relay:
        writer = _writer(relay)
        assert writer.session.seated is False, "pigro: non si entra per niente"
        writer.apply(_delta())
        assert writer.session.seated is True
        writer.close()
        assert writer.session.seated is False
        writer.close()          # chiudere due volte non è un errore


# ═══ 2 · la dichiarazione al join ════════════════════════════════════════════

def test_dichiara_che_dei_salvataggi_si_occupa_il_server():
    """§2.3 · e **questo è lo stato di stanotte, non una scelta di
    architettura**.

    StratiField dichiara `saves_itself: False` perché stanotte la rete del relay
    è la cosa che lo tiene. La forma definitiva è che StratiField sta dentro e
    la stanza sa che c'è — quello che questo file misura sopra."""
    with FakeRelay() as relay:
        writer = _writer(relay)
        writer.apply(_delta())
        writer.close()
    assert relay.declarations == [{"saves_itself": False}]


def test_la_dichiarazione_precede_la_prima_operazione():
    """Il relay decide con essa se stendere la rete: una dichiarazione che
    arrivasse dopo la prima operazione arriverebbe dopo la decisione."""
    with FakeRelay() as relay:
        writer = _writer(relay)
        writer.apply(_delta())
        writer.close()
    tipi = [m.get("type") for m in relay.received]
    assert tipi[0] == "client_info", tipi
    assert "op" in tipi and tipi.index("client_info") < tipi.index("op")


# ═══ 3 · la caduta è normale ═════════════════════════════════════════════════

def test_la_caduta_e_il_rientro_senza_duplicati():
    """La rete cade a metà del lavoro. La consegna dopo rientra da sola, e i
    numeri dicono che non si è duplicato niente: le stesse tre operazioni
    arrivano due volte e il grafo non si muove, perché `add_node` FONDE e
    `add_edge` rifiuta il ripetuto — la convergenza è della libreria, non di
    questo client."""
    with FakeRelay() as relay:
        writer = _writer(relay)
        writer.apply(_delta())
        assert relay.connections == 1
        prime = len(relay.ops)

        relay.drop_all()                       # ← la galleria
        for _ in range(50):
            if not writer.session.seated:
                break
            time.sleep(0.02)
        assert writer.session.seated is False, "il lettore si è accorto della caduta"

        writer.apply(_delta())                 # rientra da solo
        assert relay.connections == 2, "una riconnessione, non una terza sessione"
        assert len(relay.ops) == prime * 2
        writer.close()


def test_lo_snapshot_del_join_e_di_QUESTA_sessione():
    """Dopo una riconnessione ne arriva uno nuovo, e il vecchio è vecchio.

    Tenere il precedente sarebbe peggio che non averne: un documento di
    mezz'ora fa somiglia abbastanza a quello di adesso da non insospettire
    nessuno."""
    with FakeRelay() as relay:
        writer = _writer(relay)
        writer.apply(_delta())
        primo = writer.session.snapshot
        assert primo is not None

        relay.drop_all()
        for _ in range(50):
            if not writer.session.seated:
                break
            time.sleep(0.02)
        writer.apply(_delta())
        assert writer.session.snapshot is not None
        assert writer.session.snapshot is not primo, (
            "lo snapshot di prima della caduta è sopravvissuto alla caduta")
        writer.close()


def test_la_risposta_si_riconosce_non_si_conta():
    """LO SFASAMENTO DI UNO, e il caso che lo fa scattare.

    Con una connessione per consegna non potevano esserci `op_result` vecchi in
    coda: la connessione era appena nata. Con una sessione tenuta sì, e un
    `op_result` di troppo fa leggere a ogni operazione la risposta di quella
    prima — l'ultima resta per aria e la consegna torna a chi l'ha chiesta come
    riuscita.

    È successo davvero mentre si scriveva questo file: il finto relay rispondeva
    `op_result` anche a `client_info`, e il test della caduta falliva quattro
    volte su cinque con «5 operazioni invece di 4». Qui si rimette la risposta
    di troppo a mano, e la consegna deve arrivare intera lo stesso."""
    with FakeRelay() as relay:
        writer = _writer(relay)
        writer.apply(_delta())
        # una risposta orfana, come quella che il finto relay mandava per errore
        writer.session._answers.put({
            "v": 2, "type": "op_result", "source": "em-server",
            "payload": {"applied": True, "op": {"op": "add_node",
                                                "id": "QUALCUN-ALTRO"}}})
        quante = len(relay.ops)
        writer.apply(_delta())
        assert len(relay.ops) == quante * 2, "una consegna è rimasta per aria"
        assert writer.session.heard.get("skipped", 0) >= 1, (
            "la risposta orfana è stata scartata in silenzio")
        writer.close()


def test_una_stanza_che_non_risponde_non_viene_martellata():
    """Backoff: un telefono in tasca non deve bussare a una stanza giù dieci
    volte al secondo."""
    sessione = RoomSession(lambda: "ws://127.0.0.1:9/ws", timeout=0.5,
                           backoff=0.5)
    with pytest.raises(SessionClosed):
        sessione.open()
    inizio = time.monotonic()
    with pytest.raises(SessionClosed) as secondo:
        sessione.open()
    assert time.monotonic() - inizio < 0.2, "il secondo tentativo non ha nemmeno provato"
    assert "riprovo fra" in str(secondo.value)


def test_il_posto_si_riprende_da_solo():
    """«Seduto» deve durare più del primo singhiozzo.

    IL CASO CHE LO FA SCATTARE, ed è successo davvero stanotte: riavviando il
    relay, la sessione di StratiField cadeva e **non tornava fino alla consegna
    successiva** — su un telefono fermo, il giorno dopo. Una stanza che elenca
    fra i presenti solo chi ha appena parlato non sta elencando i presenti.

    Qui la connessione cade e nessuno consegna niente: il posto deve tornare da
    solo."""
    with FakeRelay() as relay:
        writer = _writer(relay)
        writer.apply(_delta())
        writer.session.keep_seated()
        assert relay.connections == 1

        relay.drop_all()                       # ← e nessuno consegna niente
        for _ in range(120):                   # il sorvegliante guarda ogni 1s
            if relay.connections >= 2 and writer.session.seated:
                break
            time.sleep(0.05)
        assert relay.connections == 2, "il posto non è stato ripreso"
        assert writer.session.seated is True

        # E UNA SECONDA VOLTA, che è il caso che ha smascherato il difetto:
        # la riapertura passava dalla chiusura interna, che spegneva l'intento
        # del sorvegliante. Dal vivo si riprendeva il posto una volta sola e poi
        # la stanza diceva «nessuno» per sempre.
        relay.drop_all()
        for _ in range(120):
            if relay.connections >= 3 and writer.session.seated:
                break
            time.sleep(0.05)
        assert relay.connections == 3, "il posto si riprende una volta sola"
        writer.close()


def test_una_porta_chiusa_non_si_riapre_insistendo():
    """Il sorvegliante distingue un singhiozzo da una regola: un ruolo in sola
    lettura non si aggira riprovando, e insistere sarebbe bussare a una porta
    che qualcuno ha chiuso apposta."""
    with FakeRelay(can_write=False) as relay:
        writer = _writer(relay)
        writer.session.keep_seated()
        time.sleep(1.5)
        assert relay.connections <= 2, (
            f"{relay.connections} tentativi contro una porta chiusa")
        writer.close()


# ═══ 4 · il lettore, e la ragione per cui esiste ═════════════════════════════

def test_una_sessione_seduta_e_muta_non_si_tappa():
    """LA MISURA CHE HA IMPOSTO IL THREAD.

    `websockets` 17.1 ha `max_queue=16`: una sessione tenuta aperta che non
    legge si riempie dopo sedici frame, la finestra TCP si chiude e **il server
    si blocca mentre manda a noi**. In una stanza dove qualcuno lavora, sedici
    frame sono minuti.

    Qui la stanza urla venticinque volte — oltre la coda — mentre StratiField
    non fa niente, e poi si consegna: se il lettore non ci fosse, la consegna
    non arriverebbe."""
    with FakeRelay() as relay:
        writer = _writer(relay)
        writer.apply(_delta())
        relay.shout(25)
        time.sleep(0.4)
        assert writer.session.heard.get("presence", 0) >= 25, writer.session.heard

        writer.apply(_delta())                 # …e si continua a lavorare
        assert relay.connections == 1
        writer.close()


def test_quello_che_gli_altri_scrivono_si_conta_e_si_butta():
    """Il limite dichiarato, con la riga che lo mostra: i frame `op` degli altri
    partecipanti vengono letti e **non consegnati a nessuno**. La scheda aperta
    sul telefono non si aggiorna da sola.

    Sedersi è la precondizione di quella funzione, non la funzione. `heard` la
    conta, così il limite è un numero e non una frase in una docstring."""
    with FakeRelay(noise=True) as relay:
        writer = _writer(relay)
        writer.apply(_delta())
        time.sleep(0.2)
        assert writer.session.heard.get("presence", 0) >= 1
        writer.close()


# ═══ 5 · una porta chiusa non è una rete che manca ═══════════════════════════

def test_il_ruolo_in_sola_lettura_e_un_rifiuto_non_un_buco_di_rete():
    """Le due cose hanno rimedi opposti: una rete che manca si aggira scrivendo
    nel container locale e sincronizzando dopo; un ruolo in sola lettura no —
    scrivere lì sotto nasconderebbe una regola che la stanza ha applicato
    correttamente, e la nota tornerebbe a fallire per sempre.

    Trovato rompendo `test_a_read_only_role_is_refused_at_the_door_before_any_op`
    mentre si estraeva la sessione: senza `SessionRefused` la porta chiusa si
    era travestita da rete assente."""
    assert issubclass(SessionRefused, SessionClosed)
    with FakeRelay(can_write=False) as relay:
        writer = _writer(relay)
        with pytest.raises(RoomRefused):
            writer.apply(_delta())
