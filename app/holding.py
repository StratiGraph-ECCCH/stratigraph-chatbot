"""Chi tiene il nodo — perché un nodo di campo sta in mano a una persona sola.

════════════════════════════════════════════════════════════════════════════════
## LA MISURA CHE DECIDE, E NON È UN'OPINIONE

Lo scrivano è **un singleton** costruito all'avvio (`main.py:146`). Misurato
stanotte con due firme diverse sullo stesso nodo, attraverso il percorso vero
(due bearer con ORCID diversi, autenticatore in modo enforcing):

    dev     → 200 · Ho creato la US 10.     id(WRITER) 0x10acf07d0
    viewer  → 200 · Ho creato la US 20.     id(WRITER) 0x10acf07d0

    nel container locale:
      US10   created_by=0000-0002-1825-0097
      US20   created_by=0000-0001-5109-3700

Quindi **sul container locale l'autore è già giusto per ciascuno**: `_author`
lo prende dal token a ogni richiesta. La frase «il nodo scrive come una persona
sola» è falsa lì, ed è una buona notizia perché restringe il problema.

**Ma nella stanza è vera, e questo è il fatto che governa la notte.** Il relay
non guarda l'autore che il client dichiara — lo dice la sua docstring, letta e
non dedotta (`stratigraph-server/app/ws.py:23`):

    «The **author of every operation is the token's identity**, never what the
    client wrote in the message — an author a client can declare is an author
    anybody can borrow»

e il token è quello del **nodo**. Quindi su un nodo tenuto da Anna, una scheda
dettata da Marco entra nella stanza **firmata Anna** — e con **i permessi di
Anna**, perché `authorize(room, author, …)` decide il ruolo dallo stesso
identificativo. Se Anna è in sola lettura, Marco non scrive; se Anna è owner,
Marco scrive come owner.

Due identità che possono contraddirsi nello stesso atto: `data.created_by` nel
payload dice Marco, la provenienza dell'operazione nella stanza dice Anna.

**Per questo «un nodo, una persona alla volta» non è una comodità: è l'unica
configurazione onesta.** Non è una restrizione che aggiungiamo — è una
restrizione che c'era già, taciuta, e stanotte diventa una frase che si legge
invece di una sorpresa che si scopre.

════════════════════════════════════════════════════════════════════════════════
## LA PRESA, E PERCHÉ HA UNA SCADENZA D'INERZIA

Una presa senza scadenza è un blocco: chi tiene il nodo se ne va con il proprio
telefono in tasca, e il nodo resta suo per sempre. In una tenda di cantiere
quella è la fine della giornata di qualcun altro.

Una presa che chiunque può strappare non è una presa.

Quindi: la presa dura finché **chi la tiene lavora**, e ogni atto autenticato la
rinfresca. Dopo `EM_NODE_IDLE` secondi di silenzio (venti minuti di default —
la pausa che separa «sta scavando» da «ha messo giù il telefono») **chiunque può
prenderlo**, e la risposta lo dice invece di farlo di nascosto:

    «Questo nodo era tenuto da 0000-…-0097, fermo da 34 minuti. L'hai preso tu.»

Chi arriva mentre l'altro sta ancora lavorando **non lo prende**, e legge una
frase con dentro chi e da quanto — non un errore generico.

════════════════════════════════════════════════════════════════════════════════
## LA PRESA VIVE NEL PROCESSO, E NON SU DISCO

Deliberato, e l'argomento è che **la presa e la credenziale hanno la stessa
vita**. Il token della stanza non tocca mai il disco (`handoff.py`, e la regola
di tutta la settimana: *un token su disco è un token che trapela*); una presa
durevole senza il suo token sarebbe un nodo che al riavvio dichiara una stanza
in cui non può più scrivere.

La conseguenza, dichiarata invece che scoperta: **un riavvio libera il nodo** e
lo riporta alla configurazione dell'ambiente. È la direzione sicura in cui
sbagliare — un nodo libero si prende, un nodo bloccato no — e si vede in
`/health`.

Quello che invece **resta su disco è il lavoro**: le code per stanza
(`bridge.py`) e la dispensa (`spool.py`). Un riavvio libera la presa e non perde
una riga, ed è esattamente la divisione giusta.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass, replace
from typing import Any, Dict, Optional

#: Dopo quanti secondi di silenzio un nodo si considera «messo giù». Venti
#: minuti: più corto e si strappa il nodo di mano a chi sta compilando una
#: scheda lunga, più lungo e chi arriva in tenda aspetta senza capire.
#: Configurabile perché la pausa giusta la conosce lo scavo, non questo file.
IDLE_SECONDS = int(os.environ.get("EM_NODE_IDLE", "1200"))


class HeldByAnother(RuntimeError):
    """Il nodo è in mano a qualcun altro, e la frase dice a chi e da quanto.

    Un'eccezione sua e non un `ValueError`: chi la cattura deve poter
    rispondere **409** e non 400. Non è una richiesta malformata — è una
    richiesta giusta fatta al momento sbagliato.
    """

    def __init__(self, message: str, grip: "Grip") -> None:
        super().__init__(message)
        self.grip = grip


@dataclass(frozen=True)
class Grip:
    """Chi tiene il nodo, da quando, e dove lo sta facendo scrivere."""

    who: str
    since: str
    last_seen: str
    server: Optional[str] = None
    room: Optional[str] = None


def _now() -> str:
    from s3dgraphy.editorial import now_iso
    return now_iso()


def _minuti(seconds: float) -> str:
    minuti = int(seconds // 60)
    if minuti < 1:
        return "meno di un minuto"
    if minuti == 1:
        return "un minuto"
    return f"{minuti} minuti"


class Holding:
    """La presa del nodo. In memoria, con un lucchetto, e niente altro.

    `threading.Lock` e non `flock`: qui si difende da due richieste
    concorrenti nello **stesso** processo, che è l'unico posto dove questa
    cosa esiste. Il caso fra processi non si pone perché la presa non è su
    disco — e se un giorno lo fosse, il lucchetto giusto sarebbe quello di
    `bridge.py`, non questo.
    """

    def __init__(self, *, idle_after: int = IDLE_SECONDS) -> None:
        self.idle_after = idle_after
        self._lock = threading.Lock()
        self._grip: Optional[Grip] = None
        #: l'orologio dell'inerzia è MONOTONO e non quello del muro: un nodo di
        #: campo prende l'ora da NTP quando la rete torna, e un salto
        #: all'indietro renderebbe «fermo da 34 minuti» un numero negativo.
        #: `since` e `last_seen` restano leggibili per una persona; il conto lo
        #: fa questo.
        self._seen_at: float = 0.0

    # ── guardare ────────────────────────────────────────────────────────────

    def _idle(self) -> float:
        import time
        return time.monotonic() - self._seen_at

    def holder(self) -> Optional[Grip]:
        """Chi lo tiene adesso, o `None` se è libero.

        «Libero» include **fermo da troppo**: una presa scaduta non è una presa,
        e mostrarla come tale nasconderebbe che il nodo si può prendere.
        """
        with self._lock:
            if self._grip is None:
                return None
            return None if self._idle() >= self.idle_after else self._grip

    def stale(self) -> Optional[Grip]:
        """La presa scaduta, se ce n'è una. Serve per DIRLO a chi subentra."""
        with self._lock:
            if self._grip is None or self._idle() < self.idle_after:
                return None
            return self._grip

    # ── prendere e lasciare ─────────────────────────────────────────────────

    def take(self, who: str, *, server: Optional[str] = None,
             room: Optional[str] = None) -> Dict[str, Any]:
        """Prendi il nodo. Alza `HeldByAnother` se è in mano a qualcuno che lavora.

        Torna un dizionario con la presa e **la frase da dire**, perché chi
        subentra a un nodo fermo deve leggerlo: subentrare in silenzio è la
        stessa sorpresa, girata dall'altra parte.
        """
        import time

        chi = str(who or "").strip()
        if not chi:
            raise ValueError(
                "un nodo non si prende senza identità: serve una firma "
                "verificata, perché quello che si scrive nella stanza porta "
                "il nome di chi tiene il nodo.")
        with self._lock:
            corrente = self._grip
            fermo = self._idle() if corrente is not None else 0.0
            if (corrente is not None and corrente.who != chi
                    and fermo < self.idle_after):
                raise HeldByAnother(
                    f"Questo nodo è in mano a {corrente.who}, che lo sta usando "
                    f"(ultimo atto {_minuti(fermo)} fa). Nella stanza tutto "
                    f"quello che si scrive da qui porta il suo nome, quindi non "
                    f"si può essere in due: aspetta che lo lasci, oppure fra "
                    f"{_minuti(self.idle_after - fermo)} lo puoi prendere.",
                    corrente)
            subentro = (corrente is not None and corrente.who != chi)
            adesso = _now()
            self._grip = Grip(
                who=chi,
                since=corrente.since if (corrente and corrente.who == chi)
                else adesso,
                last_seen=adesso, server=server, room=room)
            self._seen_at = time.monotonic()
            if subentro:
                detto = (f"Questo nodo era tenuto da {corrente.who}, fermo da "
                         f"{_minuti(fermo)}. L'hai preso tu.")
            elif corrente is None:
                detto = "Il nodo era libero. Adesso è tuo."
            else:
                detto = "Il nodo era già tuo."
            return {"grip": self._grip, "message": detto,
                    "took_over_from": corrente.who if subentro else None}

    def touch(self, who: str) -> None:
        """Rinfresca la presa di chi sta lavorando. Nessun effetto per gli altri.

        Chiamata da ogni atto autenticato: è **il lavoro** che tiene il nodo, non
        una dichiarazione. Un nodo che si tenesse per aver detto una volta «è
        mio» sarebbe di nuovo un blocco.
        """
        import time

        with self._lock:
            if self._grip is not None and self._grip.who == str(who or "").strip():
                self._grip = replace(self._grip, last_seen=_now())
                self._seen_at = time.monotonic()

    def release(self, who: str) -> bool:
        """Lascia il nodo. Torna `False` se non era tuo (e non lo tocca).

        Non alza: lasciare una cosa che non si tiene non è un errore di nessuno,
        ed è quello che fa un'interfaccia che chiude una scheda due volte.
        """
        with self._lock:
            if self._grip is None:
                return False
            if self._grip.who != str(who or "").strip():
                return False
            self._grip = None
            return True

    def pointed_at(self, *, server: Optional[str], room: Optional[str]) -> None:
        """Aggiorna dove sta scrivendo chi tiene il nodo."""
        with self._lock:
            if self._grip is not None:
                self._grip = replace(self._grip, server=server, room=room)

    # ── dirlo ───────────────────────────────────────────────────────────────

    def describe(self, *, reveal: bool = False) -> Dict[str, Any]:
        """Lo stato della presa.

        `reveal` decide se ci va **il nome**. `/health` è pubblica — la serve un
        nodo che chiunque sulla stessa rete può interrogare — e «chi sta
        lavorando in questa tenda adesso» non è una cosa da pubblicare a chi non
        ha nemmeno firmato. Chi ha firmato lo vede: è la stessa persona che, se
        prova a prendere il nodo, legge il nome nel rifiuto.
        """
        with self._lock:
            grip, fermo = self._grip, self._idle()
        if grip is None:
            return {"held": False, "state": "libero"}
        scaduta = fermo >= self.idle_after
        out: Dict[str, Any] = {
            "held": not scaduta,
            "state": "libero (l'ultima presa è scaduta)" if scaduta else "tenuto",
            "idle_seconds": int(fermo),
            "since": grip.since,
        }
        if reveal:
            out["who"] = grip.who
            out["room"] = grip.room
            out["server"] = grip.server
        return out


def describe(holding: "Holding") -> str:
    """Una riga per `/health`, senza nomi."""
    stato = holding.describe()
    if not stato.get("held"):
        return str(stato["state"])
    return (f"tenuto (ultimo atto {int(stato['idle_seconds'])}s fa, "
            f"preso il {stato['since']})")
