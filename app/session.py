"""Un posto a sedere nella stanza, invece di una lettera imbucata.

════════════════════════════════════════════════════════════════════════════════
## L'INVARIANTE, E COSA C'ERA AL SUO POSTO

StratiField **siede** al tavolo della stanza. Non è un corrispondente che spedisce
consegne: è un partecipante che tiene una sessione, che ha presenza, che è lì
anche quando non sta scrivendo niente. È la stessa postura di EMStudio, ed è
quello che rende sensata la parola «stanza».

Fino al 26 settembre non era così. `RoomWriter._send_ops` faceva:

    with connect(self._ws_url(), ...) as socket:      # apre
        self._drain_join(socket)                      # entra
        for op in ops: ...                            # manda
                                                      # e chiude

Una connessione per consegna. Il websocket usato come mezzo di trasporto, non
come presenza. Fra una consegna e l'altra StratiField non era nella stanza, e
nessuno nella stanza sapeva che esistesse.

**Ed è la stessa cosa del difetto di persistenza, vista dall'altro lato.** Il
relay dichiarava che salvare è compito del client (`request_save`); il client
che avrebbe dovuto chiederlo se n'era già andato. Non aveva dimenticato di
chiedere: non c'era più nessuno a chiedere.

> **Una connessione non è una presenza.** Aprire, mandare e chiudere assomiglia
> a partecipare fino al momento in cui qualcuno ti cerca.

════════════════════════════════════════════════════════════════════════════════
## PERCHÉ C'È UN THREAD, E PERCHÉ È PICCOLO

Tenere aperto un socket senza leggerlo non è tenerlo aperto: è riempirlo.
`websockets` 17.1 ha `max_queue=16` — misurato, non letto in un changelog — e
quando la coda di ricezione è piena il lettore si ferma, la finestra TCP si
chiude e **il server si blocca mentre manda a noi**. Una stanza dove qualcuno
lavora produce presenze, operazioni altrui e `snapshot_written`: sedici frame
sono minuti, non ore.

Quindi qualcuno deve leggere sempre. Un thread demone che consuma il socket è la
cosa più piccola che lo fa **senza riscrivere `RoomWriter` da sincrono ad
asincrono** — che sarebbe stata la strada larga, e una notte a sé.

Il thread fa una cosa sola: legge e smista. Le risposte che qualcuno sta
aspettando (`op_result`, `denied`, `error`, `snapshot`) vanno in una coda; tutto
il resto — presenza, operazioni degli altri, `snapshot_written` — si conta e si
butta. **Contare e buttare, non ignorare**: `heard` dice quanto traffico è
passato, ed è la differenza fra una sessione viva e una che sembra viva.

════════════════════════════════════════════════════════════════════════════════
## QUELLO CHE QUESTO FILE ANCORA NON FA, DETTO QUI E NON ALTROVE

**Non consegna a nessuno ciò che gli altri scrivono.** I frame `op` degli altri
partecipanti vengono letti e buttati: la scheda aperta sul telefono non si
aggiorna da sola se qualcuno modifica la stessa unità da EMStudio. Sedersi è la
precondizione di quella funzione, non la funzione — e costruirla stanotte
avrebbe voluto dire decidere come una scheda mezza compilata reagisce a una
modifica altrui, che è una domanda per E.D.

Misurabile, non dichiarato: `heard["op"]` conta esattamente quei frame.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
from typing import Any, Callable, Dict, Optional

log = logging.getLogger("stratigraph.session")

#: I frame che qualcuno può stare aspettando. Tutto il resto è notizia.
ANSWERS = frozenset({"op_result", "denied", "error", "snapshot"})

#: Le tre frame del join, sempre le stesse tre e in quest'ordine.
JOIN_FRAMES = 3


class SessionClosed(RuntimeError):
    """La sessione non c'è, e riaprirla non è compito di chi l'ha chiamata."""


class SessionRefused(SessionClosed):
    """La stanza ha detto di no, e **non è un problema di rete**.

    La distinzione esiste perché le due cose hanno rimedi opposti: una rete che
    manca si aggira scrivendo nel container locale e sincronizzando dopo, un
    ruolo in sola lettura no — scrivere lì sotto nasconderebbe una regola che la
    stanza ha applicato correttamente, e la nota tornerebbe a fallire per
    sempre. Tenute insieme, la seconda si sarebbe travestita da prima: trovato
    rompendo `test_a_read_only_role_is_refused_at_the_door_before_any_op`.
    """


class RoomSession:
    """Una connessione alla stanza, tenuta, con un lettore che la drena.

    Il ciclo di vita è esplicito: `open()`, poi quante consegne servono, poi
    `close()`. **Un processo che finisce senza chiudere è il caso normale del
    telefono, non un difetto** — la stanza se ne accorge da sola quando il
    socket cade, e il salvataggio dell'ultimo che esce è la rete sotto.
    """

    def __init__(self, url: Callable[[], str], *, timeout: float = 10.0,
                 saves_itself: bool = False,
                 backoff: float = 1.0, backoff_max: float = 30.0) -> None:
        #: una FUNZIONE e non una stringa: il token scade, e una sessione che
        #: si riapre deve poter chiedere l'indirizzo aggiornato invece di
        #: ricomporre quello con cui era nata.
        self._url = url
        self.timeout = timeout
        #: §2.3 · quello che questa sessione dichiara al relay. Stanotte è
        #: `False`: dei salvataggi si occupa il server. **È lo stato di
        #: stanotte, non una scelta di architettura.**
        self.saves_itself = saves_itself
        self._socket: Any = None
        self._reader: Optional[threading.Thread] = None
        #: il sorvegliante che RIPRENDE il posto quando cade. Senza, «seduto»
        #: vorrebbe dire «seduto finché non succede niente»: misurato stanotte
        #: riavviando il server — StratiField spariva dalla stanza e non
        #: tornava fino alla consegna successiva, che su un telefono fermo può
        #: essere il giorno dopo.
        self._keeper: Optional[threading.Thread] = None
        self._opening = threading.Lock()
        self._answers: "queue.Queue[Dict[str, Any]]" = queue.Queue()
        self._sending = threading.Lock()
        self._closing = threading.Event()
        self._snapshot: Optional[Dict[str, Any]] = None
        self._opened_at: Optional[float] = None
        #: quanto traffico è passato, per tipo. Una sessione seduta e muta e
        #: una sessione morta si assomigliano; questo le distingue.
        self.heard: Dict[str, int] = {}
        #: l'attesa prima di riprovare, che raddoppia. Una stanza giù non va
        #: martellata da un telefono in tasca.
        self._backoff = backoff
        self._backoff_base = backoff
        self._backoff_max = backoff_max
        self._not_before = 0.0
        self.last_refusal: Optional[str] = None
        #: ogni quanto il sorvegliante guarda se il posto c'è ancora. Non è un
        #: intervallo di riconnessione — quello lo decide il backoff — è la
        #: frequenza con cui si ACCORGE.
        self._watch_every = 1.0
        self._wanted = False

    # ── stato ───────────────────────────────────────────────────────────────

    @property
    def seated(self) -> bool:
        return self._socket is not None and not self._closing.is_set()

    @property
    def snapshot(self) -> Optional[Dict[str, Any]]:
        """Lo snapshot del join **di questa sessione**.

        Dopo una riconnessione ne arriva uno nuovo e questo viene sostituito:
        tenere il vecchio sarebbe peggio che non averne — un documento di
        mezz'ora fa somiglia abbastanza a quello di adesso da non insospettire
        nessuno.
        """
        return self._snapshot

    def uptime(self) -> Optional[float]:
        return None if self._opened_at is None else time.monotonic() - self._opened_at

    # ── aprire e chiudere ───────────────────────────────────────────────────

    def open(self) -> Dict[str, Any]:
        """Entra nella stanza e resta. Torna l'`host_info` del relay.

        Serializzata: due thread che entrano insieme — una consegna e il
        sorvegliante — aprirebbero due posti e ne perderebbero uno.
        """
        with self._opening:
            return self._open()

    def _open(self) -> Dict[str, Any]:
        from websockets.sync.client import connect

        if time.monotonic() < self._not_before:
            raise SessionClosed(
                f"la stanza non risponde: riprovo fra "
                f"{self._not_before - time.monotonic():.0f}s")
        self.close(quiet=True)
        self._closing.clear()
        try:
            socket = connect(self._url(), open_timeout=self.timeout,
                             close_timeout=self.timeout, max_size=None)
        except Exception as exc:      # noqa: BLE001
            self._back_off()
            raise SessionClosed(f"{type(exc).__name__}: {exc}") from None

        host: Dict[str, Any] = {}
        try:
            # LE TRE FRAME DEL JOIN SI LEGGONO QUI, prima del thread: sono le
            # uniche il cui ORDINE conta, e leggerle da un lettore asincrono
            # vorrebbe dire fare la stessa cosa con un semaforo in più.
            for _ in range(JOIN_FRAMES):
                message = self._read(socket)
                kind = message.get("type")
                if kind == "snapshot":
                    self._snapshot = message.get("payload") or {}
                elif kind == "host_info":
                    host = message.get("payload") or {}
                    if host.get("can_write") is False:
                        socket.close()
                        # LA FRASE È QUELLA DI PRIMA, parola per parola: è la
                        # stessa porta, spostata di file. Cambiarla avrebbe
                        # cambiato ciò che una persona legge per una ragione
                        # che non la riguarda.
                        raise SessionRefused(
                            f"this room is read-only for you "
                            f"(role {host.get('role') or 'unknown'})")
        except SessionClosed:
            raise
        except Exception as exc:      # noqa: BLE001
            socket.close()
            self._back_off()
            raise SessionClosed(f"join fallito: {type(exc).__name__}: {exc}") from None

        # …e SUBITO DOPO la dichiarazione, prima di qualunque consegna: il relay
        # decide con essa se stendere la rete, e una dichiarazione che arriva
        # dopo la prima operazione sarebbe arrivata dopo la decisione.
        socket.send(json.dumps({"v": 2, "type": "client_info",
                                "source": "stratifield",
                                "payload": {"saves_itself": self.saves_itself}}))

        self._socket = socket
        self._opened_at = time.monotonic()
        self._backoff = self._backoff_base
        self._not_before = 0.0
        self.last_refusal = None
        self._reader = threading.Thread(target=self._drain, name="room-session",
                                        daemon=True)
        self._reader.start()
        return host

    def keep_seated(self) -> None:
        """Riprendi il posto da solo, quando cade.

        **Senza questo, «seduto» dura fino al primo singhiozzo.** Misurato
        stanotte: riavviando il relay, la sessione di StratiField cadeva e non
        tornava fino alla consegna successiva — su un telefono fermo, il giorno
        dopo. Una stanza che elenca fra i presenti solo chi ha appena parlato
        non sta elencando i presenti.

        Il sorvegliante non forza niente: rispetta il backoff, e si ferma
        quando qualcuno chiude la sessione per davvero.
        """
        if self._keeper is not None and self._keeper.is_alive():
            return
        self._wanted = True
        self._keeper = threading.Thread(target=self._stay, name="room-seat",
                                        daemon=True)
        self._keeper.start()

    def _stay(self) -> None:
        while self._wanted:
            if not self.seated:
                try:
                    self.open()
                except SessionRefused:
                    # una porta chiusa non si riapre insistendo: è una regola,
                    # non un singhiozzo di rete
                    self._wanted = False
                    return
                except Exception:     # noqa: BLE001 — riproverà col backoff
                    pass
            time.sleep(self._watch_every)

    def close(self, *, quiet: bool = False) -> None:
        # `quiet` è la chiusura INTERNA — quella che `_open` fa per ripulire
        # prima di riaprire — e **non deve togliere la volontà di stare
        # seduti**. Trovato dal vivo: riavviando il relay, il sorvegliante
        # riapriva una volta sola e poi si fermava, perché la sua stessa
        # riapertura passava di qui e gli spegneva l'intento. Dalla stanza si
        # vedeva «nessuno», che è precisamente ciò che questo codice esiste per
        # evitare.
        if not quiet:
            self._wanted = False
        self._closing.set()
        socket, self._socket = self._socket, None
        self._opened_at = None
        if socket is not None:
            try:
                socket.close()
            except Exception:         # noqa: BLE001 — chiudere non deve fallire
                pass
        reader, self._reader = self._reader, None
        if reader is not None and reader is not threading.current_thread():
            reader.join(timeout=1.0)
        if not quiet:
            log.info("room session closed")

    def _back_off(self) -> None:
        self._not_before = time.monotonic() + self._backoff
        self._backoff = min(self._backoff * 2, self._backoff_max)

    # ── il lettore ──────────────────────────────────────────────────────────

    def _drain(self) -> None:
        """Legge sempre, smista, e non decide niente.

        Se il socket cade, il thread esce e la sessione risulta chiusa: la
        prossima consegna la riapre. **La caduta è normale, non eccezionale** —
        in modalità telefono è il caso frequente.
        """
        socket = self._socket
        while not self._closing.is_set() and socket is not None:
            try:
                message = self._read(socket)
            except Exception:         # noqa: BLE001 — chiusa, caduta, o timeout
                break
            kind = str(message.get("type") or "?")
            self.heard[kind] = self.heard.get(kind, 0) + 1
            if kind in ANSWERS:
                self._answers.put(message)
        # non `close()`: quello lo fa chi possiede la sessione. Qui si segna
        # soltanto che il posto a sedere non c'è più.
        self._socket = None
        self._opened_at = None

    @staticmethod
    def _read(socket: Any) -> Dict[str, Any]:
        raw = socket.recv()
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode("utf-8", "replace")
        try:
            message = json.loads(raw)
        except (TypeError, ValueError):
            return {}
        return message if isinstance(message, dict) else {}

    # ── parlare ─────────────────────────────────────────────────────────────

    def send(self, kind: str, payload: Dict[str, Any]) -> None:
        socket = self._socket
        if socket is None or self._closing.is_set():
            raise SessionClosed("non sei nella stanza")
        with self._sending:
            socket.send(json.dumps({"v": 2, "type": kind, "source": "stratifield",
                                    "payload": payload}))

    def await_answer(self, wanted: str = "op_result",
                     timeout: Optional[float] = None,
                     matches: Optional[Callable[[Dict[str, Any]], bool]] = None
                     ) -> Dict[str, Any]:
        """La risposta che si stava aspettando, saltando le altre.

        ── PERCHÉ C'È `matches`, E NON BASTA IL TIPO ────────────────────────

        Con una connessione per consegna si mandava un'operazione e si leggeva
        fino al primo `op_result`: non potevano essercene di vecchi, perché la
        connessione era appena nata. Con una sessione tenuta sì — e trovato
        misurando, non ragionando: un `op_result` di troppo in coda faceva
        leggere a ogni operazione la risposta di quella prima, e l'ultima
        restava per aria. Il test della caduta falliva **quattro volte su
        cinque** con «5 operazioni invece di 4», che è il modo in cui uno
        sfasamento di uno si presenta.

        Quindi la risposta si riconosce, non si conta. Le altre si buttano e si
        contano in `heard["skipped"]`, perché una risposta scartata in silenzio
        è come non averla mai ricevuta.
        """
        deadline = time.monotonic() + (self.timeout if timeout is None else timeout)
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise TimeoutError(f"nessuna risposta «{wanted}» dalla stanza")
            try:
                message = self._answers.get(timeout=left)
            except queue.Empty:
                raise TimeoutError(f"nessuna risposta «{wanted}» dalla stanza") from None
            kind = message.get("type")
            if kind in ("denied", "error"):
                return message
            if kind == wanted and (matches is None or matches(message)):
                return message
            self.heard["skipped"] = self.heard.get("skipped", 0) + 1
