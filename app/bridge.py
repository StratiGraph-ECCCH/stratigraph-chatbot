"""Il ponte: quello che si è scritto fuori dalla stanza torna dentro.

════════════════════════════════════════════════════════════════════════════════
## IL BUCO CHE QUESTO FILE CHIUDE

Da ieri StratiField ha un posto a sedere nella stanza. Sotto non aveva un ponte:
quando la sessione cadeva — e sul telefono cade per mestiere — il lavoro finiva
nel container locale del nodo, **e da lì non partiva più**. Nessuno lo
cancellava, nessuno lo consegnava, e la scheda tornava a chi l'aveva dettata
come riuscita.

Un lavoro che si posa in un posto da cui non può partire è peggio di un errore,
perché assomiglia a un successo.

La coda che faceva da ponte era quella del **browser** (`web/index.html`,
`sg.queue.v1` in `localStorage`). Esisteva, funzionava, era provata — e non era
dove serviva. *Una cosa che esiste per una strada non esiste per tutte.*

════════════════════════════════════════════════════════════════════════════════
## LE TRE DECISIONI, E L'ARGOMENTO DI CIASCUNA

### 1 · La coda sta SU DISCO, accanto al container locale

La domanda che decide: *deve sopravvivere alla morte del processo, o solo a una
caduta di rete?*

Alla morte del processo. Non è prudenza generica, è osservato: il servizio viene
riavviato di continuo — tre volte in una notte sola, il 26 settembre, e sono
riavvii ordinari (`docker compose up -d`, un aggiornamento, un nodo che perde
corrente). E il `docker-compose` mette `EM_CHATBOT_CONTAINER` sotto il volume
dei dati con questa esatta motivazione: *«so a node that loses power keeps what
was recorded»*.

Una coda in memoria metterebbe **il lavoro** su disco — nel container locale — e
**la notizia che deve viaggiare** nella RAM. È il difetto di oggi spostato di un
metro: il lavoro sopravvive e nessuno sa più che deve partire.

Quindi un file accanto al container, in append, una riga per operazione.

### 2 · In coda entrano LE OPERAZIONI, non l'intento

Le operazioni portano un `ts` timbrato da questo client, ed è quello che il CRDT
usa per ordinare. Rimandarle **pari pari** significa che la stanza applica
esattamente ciò che avrebbe applicato se la rete ci fosse stata.

Ricostruire dall'intento vorrebbe dire ritimbrare, e una nota dettata in trincea
alle 10 e sincronizzata alle 18 entrerebbe nel grafo **come le 18**. È il difetto
contro cui `RoomWriter.apply` mette in guardia da solo, nel commento più lungo
di quel file: *«il tempo sì, l'identità no»* — il relay onora il `ts` del client
e butta il suo `author`.

L'identità invece **non** viaggia nella coda: la mette il token al momento della
consegna, come sempre. Una nota rimasta in coda tre giorni e consegnata da chi
ha in mano il telefono oggi porta l'ora di quando è stata dettata e la firma di
chi la consegna — e questo è un limite dichiarato, non una svista: vedi in fondo.

### 3 · Il container locale RESTA, e non è una copia divergente

È la domanda che oggi non aveva risposta.

Il container locale non è la coda: è **la copia che il nodo ha dello studio**, ed
è quella che risponde a `study_name`, `count_units` e `answer` quando la stanza
non c'è. Toglierne il lavoro dopo la consegna renderebbe muto l'assistente sulle
cose che ha appena registrato.

E «divergente» è la parola sbagliata: le stesse operazioni, con lo stesso `ts`,
applicate nei due posti **convergono** — è la proprietà per cui esiste un CRDT.
Non è un'affermazione: `tests/test_il_ponte.py` confronta i due documenti dopo
la consegna e guarda cosa differisce.

════════════════════════════════════════════════════════════════════════════════
## COSA QUESTO FILE NON FA

**Non parla con nessuno.** Tiene un file e lo consegna a una funzione che gli
viene passata. La via verso la stanza resta una sola (`writer.py`, e
`tests/test_one_write_path.py` la tiene).

**Non compone operazioni.** Le riceve già fatte. Quel confine è lo stesso che
`tools.py` rispetta: costruire un delta e scriverlo sono due mestieri.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import logging
import os
import pathlib
import threading
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

log = logging.getLogger("stratigraph.bridge")

#: Le ragioni per cui una consegna vuol dire «c'era già».
#:
#: **E la sottigliezza sta nel fatto che due di queste arrivano con
#: `applied: True`**, misurato sul relay vero rimandando la stessa coda due
#: volte: `add_node` su un id esistente FONDE e torna `applied: True, merged`,
#: mentre `update_field` ripetuto torna `applied: False, idempotent`.
#:
#: Contare le prime come «consegnate» darebbe «consegnate 2, già arrivate 2» per
#: una coda in cui **non è cambiato niente**. Le due cose si guardano insieme, e
#: la seconda traversata di una coda identica dice «già arrivate 4», che è la
#: frase vera.
GIA_ARRIVATE = frozenset({"idempotent", "merged", "already"})


class Bridge:
    """Una coda durevole di operazioni che aspettano la stanza.

    Append-only mentre si accumula, riscritta per intero quando si consegna: una
    coda che si può solo allungare e accorciare dalla testa non ha bisogno di un
    indice, e un file di poche decine di righe non ha bisogno di un database.
    """

    def __init__(self, path: str) -> None:
        self.path = pathlib.Path(path)
        #: dentro il processo
        self._lock = threading.Lock()
        #: e FRA processi. Non è teoria: stanotte, sulla dev-stack, due processi
        #: hanno condiviso questa coda senza che nessuno l'avesse previsto — il
        #: servizio che si era appena rialzato e uno script di sonda, tutti e
        #: due con lo stesso `EM_CHATBOT_CONTAINER`. È andata bene per caso.
        #:
        #: `threading.Lock` non esclude un altro processo, e `deliver` è una
        #: lettura-modifica-scrittura: A legge cinque, B ne aggiunge una, A
        #: consegna tre e riscrive con le sue due — **l'operazione di B è
        #: sparita**, e nessuno se ne accorge. `flock` chiude quella finestra.
        self._guard = self.path.with_suffix(self.path.suffix + ".lock")
        #: perché l'ultima consegna si è fermata, se si è fermata. Una coda che
        #: non si svuota e non dice perché è una palude.
        self.last_refusal: Optional[str] = None

    # ── tenere ──────────────────────────────────────────────────────────────

    @contextlib.contextmanager
    def _exclusive(self):
        """Uno alla volta, anche fra processi diversi.

        Il lucchetto è un file a parte e non la coda: `flock` su un file che
        viene sostituito da `rename` non protegge niente — il secondo processo
        prenderebbe il lucchetto di un inode che non è più la coda. Un file suo
        non si sposta mai.
        """
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._guard, "a+") as chiave:
                fcntl.flock(chiave.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(chiave.fileno(), fcntl.LOCK_UN)

    def keep(self, ops: Iterable[Dict[str, Any]], *, why: str = "") -> int:
        """Metti in coda, in ordine. Torna quante ne ha prese.

        Scrive e **sincronizza**: una coda che vive nel buffer del sistema
        operativo sopravvive alla morte del processo e non a quella del nodo, e
        la seconda è il caso per cui esiste.
        """
        righe = [json.dumps({"op": op, "why": why}, ensure_ascii=False)
                 for op in ops]
        if not righe:
            return 0
        with self._exclusive():
            with open(self.path, "a", encoding="utf-8") as handle:
                handle.write("\n".join(righe) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        return len(righe)

    # ── guardare ────────────────────────────────────────────────────────────

    def pending(self) -> List[Dict[str, Any]]:
        """Le operazioni in attesa, in ordine.

        Una riga illeggibile **non ferma la coda e non sparisce**: si salta e si
        conta. Perdere il resto della coda per un byte storto sarebbe il difetto
        che questo file esiste per chiudere, in miniatura.
        """
        if not self.path.is_file():
            return []
        with self._exclusive():
            return self._read()

    def _read(self) -> List[Dict[str, Any]]:
        """Le righe, SENZA prendere il lucchetto: lo tiene già chi chiama.

        Separata da `pending` perché `deliver` deve leggere e riscrivere sotto
        **lo stesso** lucchetto, e prenderlo due volte lascerebbe aperta la
        finestra che il lucchetto esiste per chiudere.
        """
        if not self.path.is_file():
            return []
        fuori: List[Dict[str, Any]] = []
        rotte = 0
        for riga in self.path.read_text(encoding="utf-8").splitlines():
            if not riga.strip():
                continue
            try:
                voce = json.loads(riga)
                fuori.append(voce["op"])
            except (ValueError, KeyError, TypeError):
                rotte += 1
        if rotte:
            log.warning("bridge: %d righe illeggibili in %s, saltate",
                        rotte, self.path)
        return fuori

    def __len__(self) -> int:
        return len(self.pending())

    def describe(self) -> Dict[str, Any]:
        quante = len(self)
        return {"pending": quante, "path": str(self.path),
                "stuck_because": self.last_refusal if quante else None}

    # ── consegnare ──────────────────────────────────────────────────────────

    def deliver(self, send: Callable[[Dict[str, Any]], Tuple[bool, str]]
                ) -> Dict[str, Any]:
        """Consegna in ordine, e **fermati al primo che non passa**.

        `send(op)` torna `(consegnata, ragione)`.

        L'ORDINE NON SI ROMPE. Saltare quella che non passa e provare la
        successiva riordinerebbe operazioni che il CRDT ordina per orologio: due
        scritture sullo stesso campo arriverebbero al contrario, e la seconda
        perderebbe contro la prima. Meglio una coda ferma che una coda
        riordinata — e la coda ferma **si vede** (`describe`).

        Un rifiuto che dice «c'è già» **è** una consegna riuscita: la nota è
        arrivata, magari da un altro giro. Si toglie dalla coda e si conta a
        parte, perché «consegnate 4, già arrivate 2» e «consegnate 6» sono due
        fatti diversi.
        """
        with self._exclusive():
            return self._deliver(send)

    def _deliver(self, send: Callable[[Dict[str, Any]], Tuple[bool, str]]
                 ) -> Dict[str, Any]:
        in_attesa = self._read()
        if not in_attesa:
            self.last_refusal = None
            return {"delivered": 0, "already": 0, "left": 0, "stopped": None}

        consegnate = gia = 0
        fermo: Optional[str] = None
        for indice, op in enumerate(in_attesa):
            try:
                passata, ragione = send(op)
            except Exception as exc:      # noqa: BLE001 — la rete è ricaduta
                fermo = f"{type(exc).__name__}: {exc}"
                in_attesa = in_attesa[indice:]
                break
            gia_cera = str(ragione or "").strip().lower() in GIA_ARRIVATE
            if passata:
                # `merged` arriva con `applied: True` e vuol dire «c'era già»
                if gia_cera:
                    gia += 1
                else:
                    consegnate += 1
                continue
            if gia_cera:
                gia += 1
                continue
            fermo = f"{op.get('op')} {op.get('id') or op.get('node_id')}: {ragione}"
            in_attesa = in_attesa[indice:]
            break
        else:
            in_attesa = []

        self._rewrite(in_attesa)
        self.last_refusal = fermo
        esito = {"delivered": consegnate, "already": gia,
                 "left": len(in_attesa), "stopped": fermo}
        if consegnate or gia or fermo:
            log.info("bridge: %s", esito)
        return esito

    def _rewrite(self, resto: List[Dict[str, Any]]) -> None:
        """Sotto il lucchetto di chi chiama — vedi `_read`."""
        if not resto:
            self.path.unlink(missing_ok=True)
            return
        temporaneo = self.path.with_suffix(self.path.suffix + ".tmp")
        temporaneo.write_text(
            "\n".join(json.dumps({"op": op}, ensure_ascii=False)
                      for op in resto) + "\n", encoding="utf-8")
        temporaneo.replace(self.path)


def bridge_for(container_path: str) -> Bridge:
    """La coda che accompagna un container locale.

    Accanto e non dentro: il container è un `em.json` che l'ecosistema intero
    sa leggere, e infilarci una coda di operazioni ne farebbe un formato nostro.
    """
    base = pathlib.Path(container_path)
    return Bridge(str(base.with_suffix(base.suffix + ".pending.jsonl")))
