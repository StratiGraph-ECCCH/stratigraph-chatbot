"""Where a tool's delta actually lands — the room, or the node's own container.

Design note §5: the assistant is **not** integrated inside EMStudio. It is
another client of the shared room on the Field Computing Node, exactly like
EMStudio and EMtools. It writes units and photos to the shared graph; EMStudio
joins the same room and sees them appear. **Convergence on the graph, not
coupling** — and the difference matters, because a coupling would mean the field
assistant could only work when the desk application is running.

Two implementations of one seam, and which one is used is a property of the
excavation rather than a setting somebody chose:

* **`RoomWriter`** — there is an StratiGraph Server on the node. The delta goes onto the
  wire as CRDT operations, and EMStudio sees the unit appear while the person
  who spoke it is still holding the trowel;
* **`LocalWriter`** — there is no room (a trench with no network, a node not yet
  started). The delta goes into the node's own container, and syncs later. This
  is not a degraded mode: offline-first means the field case is the base case,
  and the room is what adds reach.

Both answer the same three questions a tool asks — *does this unit exist*, *what
study is this*, *what does the graph say* — so a tool never learns which one it
is talking to.

**Nothing here decides anything about stratigraphy.** The delta arrives built by
the tools (which asked s3Dgraphy); this puts it somewhere and reads it back.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol

from .contract import GraphDelta
from .session import RoomSession, SessionClosed, SessionRefused

#: The wire version this client speaks (ADR-002 / WIRE 2). Stated here rather
#: than imported: the chatbot does not depend on StratiGraph Server's package, and
#: a mismatch is answered by the relay with a sentence naming both numbers.
WIRE = 2


class RoomRefused(RuntimeError):
    """The room said no — a role that may not write, a stale operation, a wire
    it does not speak.

    Its own exception, and NOT the same thing as an unreachable node: a refusal
    is the room applying a rule correctly, and quietly writing the delta to the
    local container instead would hide that rule and produce two copies of a
    study that disagree. Unreachable falls back; refused is raised.
    """


#: The fields a CRDT operation can address, MEASURED rather than assumed:
#: `s3dgraphy.crdt.apply_op_to_section` refuses anything that is not `name`,
#: `description`, or a `data.` path —
#:
#:     if not name or (name != "name" and name != "description"
#:                     and not name.startswith("data.")):
#:         return OpResult(False, f"'{name}' is not an addressable field")
#:
#: so a writer that sent `sito` instead of `data.sito` would collect a refusal
#: per field and report a successful save. The prefixing happens in ONE place
#: (`addressable`) for that reason.
_DIRECT_FIELDS = ("name", "description")
_DATA_PREFIX = "data."


def addressable(name: str) -> str:
    """A field name as an operation must spell it.

    `descrizione` → `data.descrizione`; `description` → `description`. Not a
    convenience: the CRDT's own refusal list is the authority, and a second
    spelling of it somewhere else would be a rule that agrees today.
    """
    clean = str(name or "").strip()
    if not clean:
        raise ValueError("a field with no name cannot be addressed")
    if clean in _DIRECT_FIELDS or clean.startswith(_DATA_PREFIX):
        return clean
    return f"{_DATA_PREFIX}{clean}"


class FieldRefused(RuntimeError):
    """An `update_field` the room would not apply — and which one, and why.

    Separate from `RoomRefused` because the two need different answers: a role
    that may not write is about the person, a field the room refused is about
    one value. `outcomes` carries them per field so a form can mark the boxes
    that did not land instead of showing one red banner over a page.
    """

    def __init__(self, message: str, outcomes: List[Dict[str, Any]]) -> None:
        super().__init__(message)
        self.outcomes = outcomes


def _now() -> str:
    """The instant a field write claims. From s3Dgraphy, like everything else.

    MEASURED, and it is why this function exists at all:

        def op_clock(op):
            return Clock(ts=(str(op["ts"]) if op.get("ts") else None), …)

    **An operation with no `ts` carries a clock with no timestamp**, and a
    clock with no timestamp LOSES against any stamped state. The first version
    of `update` left it out and every single field came back `stale` against a
    unit created two lines earlier — a save that reported success and wrote
    nothing. It is the contract's own rule seen from the writing side: if you
    write a field, stamp it.
    """
    from s3dgraphy.editorial import now_iso
    return now_iso()


def _absent(outcomes: List[Dict[str, Any]]) -> bool:
    """Did the room (or the container) say the node is not here?"""
    return any(not o["applied"] and "is not here" in str(o.get("reason") or "")
               for o in outcomes)


def _raise_if_absent(node_id: str, outcomes: List[Dict[str, Any]]) -> None:
    """«node '…' is not here» is not a failure to report quietly.

    It means somebody is correcting a unit that does not exist — a mistyped
    number, or a form opened against a study it does not belong to — and the
    honest answer is a refusal, not a save that wrote nothing. Read from the
    CRDT's own `reason` string rather than pre-checked, so a unit removed
    between the check and the write is caught too.

    A `stale` or `idempotent` field is NOT this: those are the merge working,
    and they come back in the outcomes for the form to show.
    """
    if _absent(outcomes):
        raise FieldRefused(
            f"L'unità «{node_id}» non è in questo grafo: non posso aggiornare "
            f"una scheda che non esiste. Creala prima.", outcomes)


class GraphWriter(Protocol):
    def apply(self, delta: GraphDelta) -> None: ...
    #: THE SECOND VERB OF THE SAME SEAM, and why it has to exist.
    #:
    #: `apply` puts NODES and EDGES somewhere: it is the shape
    #: `s3dgraphy.contract.core.Delta` can express (`nodes`, `edges`, `author`,
    #: `process`, `volatile` — measured, there is no room for a field write).
    #: An UPDATE is not a set of nodes, it is a set of field writes with their
    #: own clocks, and the difference is not cosmetic: `add_node` on an id that
    #: is not there CREATES it, while `update_field` refuses with
    #: «node '…' is not here». That refusal is the whole distinction between
    #: correcting a unit and inventing one.
    #:
    #: So the seam grew a verb rather than the delta growing a field: the
    #: `Delta` is the SHARED contract's and changing it is another repository's
    #: decision (reported in tonight's end-of, not taken here), while
    #: `GraphWriter` is this file's own protocol.
    #:
    #: NOT a second road to the graph: both verbs are on this one seam, both are
    #: implemented by both writers, and nothing else in `app/` opens a socket.
    def update(self, node_id: str, fields: Dict[str, Any], *,
               author: Optional[str],
               process: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]: ...
    def has_node(self, node_id: str) -> bool: ...
    #: THE READING VERB. Validating a field has to know how the value arrived
    #: — a validation that did not read the previous authorship would erase
    #: the memory that a model had proposed it. `has_node` answers «is it
    #: there»; this answers «what does it say».
    def node(self, node_id: str) -> Optional[Dict[str, Any]]: ...
    def study_name(self) -> Optional[str]: ...
    def count_units(self) -> int: ...
    def answer(self, question: str) -> str: ...


_STRAT_PREFIXES = ("US", "USV", "USD", "SF", "VSF", "RSF", "ser", "TSU", "UL",
                   "USN", "BR", "SE")


def _is_unit(node: Dict[str, Any]) -> bool:
    return str(node.get("node_type") or "").startswith(_STRAT_PREFIXES)


class LocalWriter:
    """The node's own container — the offline case, which is the base case.

    A file, written atomically, holding one em.json container. When the room
    comes back the container is merged in by the ordinary machinery (dated
    field-level merge): nothing here has to reconcile anything, which is the
    whole reason the CRDT exists.
    """

    def __init__(self, path: str, *, study: str = "Scavo") -> None:
        self.path = Path(path)
        self._study = study
        self._lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write({
                "header": {"format": "em.json", "version": "1.0",
                           "visibility": "restricted", "title": study},
                "graphs": {"scavo": {"graph_id": "scavo", "name": study,
                                     "nodes": [], "edges": []}},
                "active_graph_id": "scavo",
            })

    # ── the file ─────────────────────────────────────────────────────────────

    def _read(self) -> Dict[str, Any]:
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _write(self, doc: Dict[str, Any]) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        tmp.replace(self.path)

    def _section(self, doc: Dict[str, Any]) -> Dict[str, Any]:
        graphs = doc.get("graphs") or {}
        key = doc.get("active_graph_id") or next(iter(graphs), "scavo")
        return graphs.setdefault(key, {"graph_id": key, "nodes": [],
                                       "edges": []})

    # ── the seam ─────────────────────────────────────────────────────────────

    def apply(self, delta: GraphDelta) -> None:
        with self._lock:
            doc = self._read()
            section = self._section(doc)
            nodes = section.setdefault("nodes", [])
            edges = section.setdefault("edges", [])
            by_id = {n.get("id"): n for n in nodes}
            for node in delta.nodes + ([delta.process] if delta.process else []):
                if node.get("id") in by_id:
                    by_id[node["id"]].update(node)   # idempotent by id
                else:
                    nodes.append(node)
                    by_id[node["id"]] = node
            known = {e.get("id") for e in edges}
            for edge in delta.edges:
                if edge.get("id") not in known:
                    edges.append(edge)
            self._write(doc)

    def update(self, node_id: str, fields: Dict[str, Any], *,
               author: Optional[str],
               process: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """Field writes into the node's own container — through the CRDT itself.

        `s3dgraphy.crdt.apply_op_to_section` is called rather than reimplemented,
        which is the same choice `tools.py` makes when it asks the library to
        perform an act on a scratch graph and reads what appeared. The clocks,
        the last-writer-wins per field, the tombstone of an emptied field and the
        refusal for a node that is not here are the library's — and a local save
        that merged by hand would diverge from the room on the first conflict,
        which is the one place the two must agree.
        """
        from s3dgraphy.crdt import apply_op_to_section

        outcomes: List[Dict[str, Any]] = []
        with self._lock:
            doc = self._read()
            section = self._section(doc)
            stamp = _now()

            # THE FIELDS FIRST, AND THE D7 LAST — and the order is the point.
            #
            # The first version recorded the act before attempting it, and a
            # refused update left behind a `dtc_process` node saying «US 21: 1
            # campi aggiornati» about a unit that does not exist. **A record of
            # an act that did not happen is worse than no record**: it is a
            # claim nobody can defend, in the one place built to hold claims
            # somebody can. Caught by a test that counted the nodes after a
            # refusal.
            for name, value in fields.items():
                op = {"op": "update_field", "node_id": node_id,
                      "field": addressable(name), "author": author,
                      "ts": stamp}
                if value is None:
                    op["remove"] = True
                else:
                    op["value"] = value
                result = apply_op_to_section(section, op)
                outcomes.append({"field": addressable(name),
                                 "applied": bool(result.applied),
                                 "reason": result.reason})

            if _absent(outcomes):
                # NOTHING is written: `doc` is a value read from the file and
                # dropping it on the floor is how a local writer rolls back.
                _raise_if_absent(node_id, outcomes)

            if process:
                apply_op_to_section(section, {"op": "add_node",
                                              "id": process["id"],
                                              "node": process,
                                              "author": author, "ts": stamp})
            self._write(doc)
        return outcomes

    def has_node(self, node_id: str) -> bool:
        section = self._section(self._read())
        return any(n.get("id") == node_id for n in section.get("nodes") or [])

    def node(self, node_id: str) -> Optional[Dict[str, Any]]:
        section = self._section(self._read())
        return next((n for n in section.get("nodes") or []
                     if n.get("id") == node_id), None)

    def study_name(self) -> Optional[str]:
        doc = self._read()
        header = doc.get("header") or {}
        return (header.get("title")
                or self._section(doc).get("name") or self._study)

    def count_units(self) -> int:
        section = self._section(self._read())
        return sum(1 for n in section.get("nodes") or [] if _is_unit(n))

    def answer(self, question: str) -> str:
        """What the graph plainly says. Small on purpose — a retrieval model
        over the documentation is a DIFFERENT tool, and the contract is what
        lets it be one."""
        section = self._section(self._read())
        nodes = section.get("nodes") or []
        units = [n for n in nodes if _is_unit(n)]
        asked = (question or "").lower()

        if "quant" in asked:
            if not units:
                return "Non è ancora stata registrata nessuna unità."
            return (f"Sono registrate {len(units)} unità: "
                    + ", ".join(str(u.get("name") or u.get("id"))
                                for u in units[:8])
                    + ("…" if len(units) > 8 else "") + ".")
        if "foto" in asked or "immagin" in asked:
            photos = [n for n in nodes if n.get("node_type") == "resource"]
            return (f"Ci sono {len(photos)} risorse allegate."
                    if photos else "Non c'è ancora nessuna foto.")
        if not units:
            return (f"Su «{self.study_name()}» non è ancora stata registrata "
                    f"nessuna unità.")
        return (f"Su «{self.study_name()}» ci sono {len(units)} unità. "
                f"L'ultima è {units[-1].get('name') or units[-1].get('id')}.")


class RoomWriter:
    """An StratiGraph Server room: what is written here appears in EMStudio, live.

    The delta becomes CRDT operations on the wire the ecosystem already speaks
    (WIRE 2 / ADR-002). Nothing about the protocol is invented here — this is a
    client, and being a client rather than a peer is the point of §5.

    **Fixed 2026-08-29, and the bug is worth keeping written down.** This used
    to POST each operation to `/v1/rooms/{room}/op` — an endpoint that has never
    existed. Room operations travel on the room's WebSocket and always have
    (`app/ws.py`, ADR-002): every write from a field node failed against a real
    server, silently falling back to the local container, and the only reason
    nothing looked broken is that the fallback is good. Found by reading, not by
    a bug report, which is the worst way for it to have been found.

    The socket is opened per act and closed after it. A field assistant speaks in
    bursts minutes apart, and a connection held open across them would be a
    presence claim ("somebody is in this room") that is not true — presence is
    ephemeral by design, and a phone in a pocket must not appear in the roster.
    P4.3's persistent client is EMStudio's job, not this one's.
    """

    def __init__(self, base_url: str, room_id: str, token: str, *,
                 fallback: Optional[GraphWriter] = None,
                 timeout: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.room_id = room_id
        self._token = token          # in memory only, never on disk
        self.timeout = timeout
        #: When the room cannot be reached, the delta must not be lost: it goes
        #: to the node's own container and syncs later. Offline-first means the
        #: network is the optional part.
        self.fallback = fallback
        #: IL POSTO A SEDERE. Aperto pigramente alla prima consegna e poi
        #: TENUTO: fino al 26 settembre ogni consegna apriva e chiudeva una
        #: connessione, e fra una consegna e l'altra questo servizio non era
        #: nella stanza — nessuno nella stanza sapeva che esistesse.
        #:
        #: L'indirizzo è una funzione e non una stringa perché il token scade e
        #: una sessione che si riapre deve poter chiedere quello aggiornato.
        self.session = RoomSession(self._ws_url, timeout=timeout,
                                   saves_itself=False)
        self.degraded = False
        #: WHY the last write did not go through, if it did not. `describe()`
        #: reads it: "degraded" without a reason is a status light with no label.
        self.last_refusal: Optional[str] = None

    def _post(self, path: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        import urllib.error
        import urllib.request

        request = urllib.request.Request(
            f"{self.base_url}{path}", method="POST",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self._token}"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as answer:
                raw = answer.read()
                return json.loads(raw) if raw else {}
        except (urllib.error.URLError, OSError):
            return None

    def call(self, path: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """One POST to the node's own API, with the token this session holds.

        Exposed because a room is not only somewhere to write NODES: the
        processing connector (`/v1/photogrammetry`) is a capability of the same
        node, reached with the same identity and the same refusals. `None` means
        the node could not be reached — the caller says so out loud rather than
        pretending something is running.
        """
        return self._post(path, payload)

    def read(self, path: str) -> Optional[Dict[str, Any]]:
        """One GET, for polling a job the node is running."""
        import urllib.error
        import urllib.request

        request = urllib.request.Request(
            f"{self.base_url}{path}", method="GET",
            headers={"Authorization": f"Bearer {self._token}"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as answer:
                raw = answer.read()
                return json.loads(raw) if raw else {}
        except (urllib.error.URLError, OSError):
            return None

    # ── the room's own wire ──────────────────────────────────────────────
    #
    # WIRE 2 (ADR-002): `{"v": 2, "type": …, "payload": {…}}`. The relay stamps
    # the author from the TOKEN and drops any the client wrote, so nothing here
    # names an author — that is the property the merge depends on (P4.1b), and a
    # client that filled it in would be lying to everyone downstream.

    #: what the relay sends on JOIN, in this order, always: a client knows it has
    #: arrived without counting (`ws.py`: "three frames, always the same three")
    JOIN_FRAMES = ("host_info", "snapshot", "presence")

    def _ws_url(self) -> str:
        base = self.base_url
        if base.startswith("https://"):
            base = "wss://" + base[len("https://"):]
        elif base.startswith("http://"):
            base = "ws://" + base[len("http://"):]
        # the token goes in the query because a browser cannot set a header on a
        # WebSocket and the relay accepts both — this client could use the header,
        # and uses the query so there is ONE spelling to debug across clients
        return f"{base}/v1/rooms/{self.room_id}/ws?token={self._token}"

    def apply(self, delta: GraphDelta) -> None:
        """Write the delta into the room, over the socket the ecosystem speaks.

        One connection for the whole act, one `op` frame per change, and each
        one's `op_result` read before the next is sent. Reading the ack is the
        difference between "sent" and "landed": the relay answers
        `applied: false` for a stale or refused operation, and a client that did
        not look would report a success the room never had.

        Any failure — unreachable, refused, denied by role — falls back to the
        local container. Offline-first means the network is the optional part,
        and a photograph somebody took must not be lost to a dropped connection.
        """
        # ── UN SOLO OROLOGIO PER UN SOLO ORDINAMENTO ────────────────────────
        #
        # `apply` non timbrava: le sue operazioni partivano senza `ts`, e il
        # relay ne metteva uno suo. `update` invece timbra col clock di QUESTO
        # client. Due orologi decidono un ordinamento, e il risultato si è visto
        # nel giro vero del 23 settembre — una scheda che crea l'unità e la
        # riempie nella stessa richiesta:
        #
        #     created_at   2026-09-04T21:51:08Z   (payload, clock del client)
        #     modified_at  2026-09-04T21:51:09Z   (op, clock del relay)
        #     → i cinque campi tornano tutti `stale`, contro un nodo creato
        #       nella stessa richiesta, e la scheda non salva NIENTE
        #
        # Il campo senza orologio proprio ricade sul timbro del NODO (la regola
        # del fallback di P4.1), quindi un nodo timbrato un secondo avanti
        # rifiuta ogni campo che arriva con l'ora giusta.
        #
        # Timbrare qui non risolve la sincronia fra macchine diverse — quello è
        # l'ADR-003, ed è di WP6 — ma toglie il caso in cui **lo stesso client,
        # nella stessa richiesta**, si contraddice da solo.
        #
        # IL TEMPO SÌ, L'IDENTITÀ NO, e la differenza non è nostra: il relay fa
        # `pop("author")` e `setdefault("ts")`. Cioè un `ts` del client viene
        # ONORATO — deve esserlo, perché una nota dettata in trincea alle 10 e
        # sincronizzata alle 18 porta le 10 — mentre un `author` del client
        # viene buttato, perché l'identità è quella del TOKEN e un client che la
        # dichiarasse starebbe mentendo a valle. La prima versione di questa
        # riga mandava anche l'autore; l'ha fermata
        # `test_no_author_is_sent_because_the_relay_takes_it_from_the_token`,
        # che esisteva già ed è precisamente il test che serviva.
        stamp = _now()
        ops: List[Dict[str, Any]] = []
        for node in delta.nodes + ([delta.process] if delta.process else []):
            ops.append({"op": "add_node", "id": node["id"], "node": node,
                        "ts": stamp})
        for edge in delta.edges:
            ops.append({"op": "add_edge", "id": edge.get("id"),
                        "source": edge["source"], "target": edge["target"],
                        "edge_type": edge.get("edge_type"), "ts": stamp})
        if not ops:
            return

        try:
            self._send_ops(ops)
        except RoomRefused as refusal:
            # The room said no, and that is NOT a network problem: writing the
            # delta locally would hide a rule the room applied correctly. Said,
            # and the caller decides.
            self.degraded = True
            self.last_refusal = str(refusal)
            raise
        except Exception as exc:                      # unreachable, timeout, TLS
            self.degraded = True
            self.last_refusal = f"{type(exc).__name__}: {exc}"
            if self.fallback is not None:
                self.fallback.apply(delta)
            return
        self.degraded = False
        self.last_refusal = None

    def _send_ops(self, ops: List[Dict[str, Any]]) -> None:
        """Consegna DENTRO la sessione già aperta, e leggi ogni ack.

        Era: apri, entra, manda, chiudi. Adesso la sessione c'è già — o si apre
        adesso e resta — e la consegna passa da lì.
        """
        self._seated()
        for op in ops:
            self.session.send("op", op)
            self._result_of(op)

    # ── il posto a sedere ───────────────────────────────────────────────────

    def _seated(self) -> None:
        """Assicura la sessione, riaprendola se è caduta.

        **La caduta è normale, non eccezionale**: in modalità telefono la rete
        va e viene, e ogni consegna può trovarsi il posto vuoto. Chi chiama non
        se ne accorge — se non si riesce a rientrare, l'eccezione risale e il
        chiamante ricade sul container locale, che è il ponte già costruito.
        """
        if self.session.seated:
            return
        try:
            host = self.session.open()
        except SessionRefused as refusal:
            # UNA PORTA CHIUSA NON È UNA RETE CHE MANCA. `apply` ricade sul
            # container locale per qualunque eccezione che non sia `RoomRefused`,
            # e senza questa traduzione un ruolo in sola lettura sarebbe
            # diventato una scrittura locale che nessuno avrebbe mai potuto
            # consegnare.
            raise RoomRefused(str(refusal)) from None
        host = host
        # QUELLO CHE IL RELAY DICE ALLA PORTA, tenuto: da stanotte contiene
        # anche se dei salvataggi si occupa lui (`keeping.host_keeps`).
        self.host_info = host

    def _result_of(self, op: Dict[str, Any]) -> None:
        """L'ack di QUESTA operazione, o l'eccezione che dice perché no."""
        payload = self._outcome_of(op)
        if payload.get("applied"):
            return
        raise RoomRefused(
            f"the room did not apply {op.get('op')} {op.get('id')}: "
            f"{payload.get('reason') or 'no reason given'}")

    def _outcome_of(self, op: Dict[str, Any]) -> Dict[str, Any]:
        def is_ours(message: Dict[str, Any]) -> bool:
            """L'`op_result` di QUESTA operazione e non di un'altra.

            Il relay rimanda l'operazione dentro la risposta (`payload.op`), e
            questa è la ragione per cui quel campo vale la pena: in una sessione
            tenuta le risposte possono accumularsi, e contare invece di
            riconoscere produce uno sfasamento di uno — misurato."""
            answered = (message.get("payload") or {}).get("op")
            if not isinstance(answered, dict):
                # un relay che non rimanda l'operazione: si torna a contare, che
                # è quello che si faceva prima e che qui è il ripiego onesto
                return True
            return (answered.get("op") == op.get("op")
                    and answered.get("id") == op.get("id")
                    and answered.get("node_id") == op.get("node_id")
                    and answered.get("field") == op.get("field"))

        message = self.session.await_answer("op_result", matches=is_ours)
        payload = message.get("payload") or {}
        kind = message.get("type")
        if kind == "denied":
            raise RoomRefused(payload.get("reason")
                              or "the room refused the write")
        if kind == "error":
            raise RoomRefused(payload.get("detail") or "the room errored")
        return payload

    def close(self) -> None:
        """Lascia il tavolo. Esplicito, perché il ciclo di vita lo è."""
        self.session.close()

    def _drain_join(self, socket: Any) -> Optional[Dict[str, Any]]:
        """Read the three join frames, BELIEVE the first, and KEEP the snapshot.

        `host_info.can_write` is the room telling you at the door what you may
        do. Checking it turns "the assistant said it saved and nothing appeared"
        into a sentence somebody can act on — and it costs one field of a frame
        we have to read anyway.

        AND THE SNAPSHOT IS RETURNED rather than discarded. It was being thrown
        away, and the cost of that showed up in the live round of 23 September:
        validating a field in a ROOM answered «non trovo la US 3015 in questo
        grafo», because `has_node`/`node` fell through to the local container —
        which of course does not have a unit that was written to the room. The
        room sends its whole document at the door; not reading it meant opening
        a second, impossible question.
        """
        snapshot = None
        for _ in self.JOIN_FRAMES:
            message = self._recv(socket)
            if message.get("type") == "snapshot":
                snapshot = message.get("payload") or {}
                continue
            if message.get("type") != "host_info":
                continue
            payload = message.get("payload") or {}
            if payload.get("can_write") is False:
                raise RoomRefused(
                    f"this room is read-only for you (role "
                    f"{payload.get('role') or 'unknown'})")
        return snapshot

    def _await_result(self, socket: Any, op: Dict[str, Any]) -> None:
        """Read until THIS operation is answered.

        The relay fans out other people's operations and presence on the same
        socket, so the answer is not necessarily the next frame. Skipping until
        `op_result` is what makes this correct in a room with somebody else in it
        — and a room with somebody else in it is the whole point of a room.
        """
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            message = self._recv(socket)
            kind = message.get("type")
            payload = message.get("payload") or {}
            if kind == "op_result":
                if payload.get("applied"):
                    return
                raise RoomRefused(
                    f"the room did not apply {op.get('op')} {op.get('id')}: "
                    f"{payload.get('reason') or 'no reason given'}")
            if kind == "denied":
                raise RoomRefused(payload.get("reason")
                                  or "the room refused the write")
            if kind == "error":
                raise RoomRefused(payload.get("detail") or "the room errored")
            # anything else is somebody else's news: not ours to act on
        raise TimeoutError(
            f"no answer from the room for {op.get('op')} {op.get('id')} "
            f"within {self.timeout}s")

    @staticmethod
    def _recv(socket: Any) -> Dict[str, Any]:
        raw = socket.recv()
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode("utf-8", "replace")
        try:
            message = json.loads(raw)
        except (TypeError, ValueError):
            return {}
        return message if isinstance(message, dict) else {}

    # Reads go to the fallback when there is one: on a field node the local
    # container is the copy that is always there.
    def update(self, node_id: str, fields: Dict[str, Any], *,
               author: Optional[str],
               process: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """Field writes into the room — one connection, one ack read per field.

        THE DIFFERENCE FROM `apply`, and it is why this does not reuse
        `_await_result`: for a creation a refused operation is a failure, and
        raising is right. For an update a refused field is often the merge
        WORKING — `stale` means somebody else wrote that box more recently and
        the room is correct to keep their value; `idempotent` means the value is
        already what we are sending. Aborting a whole scheda on the first
        `stale` would throw away eleven good fields because of one.

        So the outcomes are COLLECTED and handed back, and only two things
        raise: a node that is not there (`_raise_if_absent` — that is an update
        of something that does not exist, not a merge) and a refusal about the
        person rather than the value (`denied`, which `_await_outcome` lets
        through as `RoomRefused`).

        Unreachable falls back to the local container, exactly as `apply` does:
        offline-first means the network is the optional part.
        """
        # ONE stamp for the whole act, not one per field: a scheda saved in one
        # gesture is one moment, and giving twelve fields twelve timestamps
        # would invent an order between boxes that nobody filled in sequence.
        stamp = _now()
        ops: List[Dict[str, Any]] = []
        for name, value in fields.items():
            op: Dict[str, Any] = {"op": "update_field", "node_id": node_id,
                                  "field": addressable(name), "ts": stamp}
            if value is None:
                op["remove"] = True
            else:
                op["value"] = value
            ops.append(op)
        if not ops:
            return []
        # …and the D7 LAST, for the reason `LocalWriter.update` states at
        # length: an act that the room refuses must not leave a record saying it
        # happened. Sent last means never sent at all in that case.
        if process:
            ops.append({"op": "add_node", "id": process["id"], "node": process,
                        "ts": stamp})

        try:
            outcomes = self._send_updates(ops)
        except RoomRefused as refusal:
            self.degraded = True
            self.last_refusal = str(refusal)
            raise
        except Exception as exc:                      # unreachable, timeout, TLS
            self.degraded = True
            self.last_refusal = f"{type(exc).__name__}: {exc}"
            if self.fallback is not None:
                return self.fallback.update(node_id, fields, author=author,
                                            process=process)
            raise
        self.degraded = False
        self.last_refusal = None
        _raise_if_absent(node_id, outcomes)
        return outcomes

    def _send_updates(self, ops: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Come sopra, ma un campo rifiutato NON solleva: torna nell'esito."""
        self._seated()
        outcomes: List[Dict[str, Any]] = []
        for op in ops:
            if op["op"] == "add_node" and _absent(outcomes):
                # the unit is not in the room: the act did not happen, so
                # its record does not get sent. The caller raises.
                break
            self.session.send("op", op)
            payload = self._outcome_of(op)
            if op["op"] == "update_field":
                outcomes.append({"field": op["field"],
                                 "applied": bool(payload.get("applied")),
                                 "reason": payload.get("reason")})
            elif not payload.get("applied"):
                # the D7 that records the act. A refused process node means
                # the act itself did not get recorded, and an unattributable
                # change is the one thing this service does not produce.
                raise RoomRefused(
                    f"the room did not record the act: "
                    f"{payload.get('reason') or 'no reason given'}")
        return outcomes

    def _await_outcome(self, socket: Any, op: Dict[str, Any]) -> Dict[str, Any]:
        """Read until THIS operation is answered, and RETURN the answer.

        `_await_result`'s twin, and deliberately a twin rather than a flag on
        it: that one's contract is «raise unless it landed» and `apply` depends
        on exactly that. What is shared is the skipping — the relay fans out
        other people's operations and presence on the same socket, so the answer
        is not necessarily the next frame.
        """
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            message = self._recv(socket)
            kind = message.get("type")
            payload = message.get("payload") or {}
            if kind == "op_result":
                return payload
            if kind == "denied":
                # about the PERSON, not the value: it aborts.
                raise RoomRefused(payload.get("reason")
                                  or "the room refused the write")
            if kind == "error":
                raise RoomRefused(payload.get("detail") or "the room errored")
        raise TimeoutError(
            f"no answer from the room for {op.get('op')} "
            f"{op.get('node_id') or op.get('id')} within {self.timeout}s")

    def has_node(self, node_id: str) -> bool:
        """Asks the ROOM first — see `node` for the round trip that showed why."""
        return self.node(node_id) is not None

    def _snapshot_node(self, node_id: str) -> Optional[Dict[str, Any]]:
        """Un nodo, letto dal documento della stanza.

        **Questa docstring diceva il contrario fino al 26 settembre**, e vale la
        pena tenerne la frase: «one socket per read is the same posture the
        writes take: this assistant is a CORRESPONDENT, it does not keep a seat
        in the room, and holding a socket open to answer a question later would
        put a phone in a pocket into the roster».

        Era coerente e sbagliata. Un telefono in tasca **deve** stare nel
        roster: è quello che rende sensata la parola «stanza», ed è l'invariante
        di progetto. Adesso la sessione c'è, e la domanda si fa da dentro:
        `request_snapshot` invece di una connessione nuova.
        """
        self._seated()
        self.session.send("request_snapshot", {})
        answer = self.session.await_answer("snapshot")
        doc = ((answer.get("payload") or {}).get("doc")) or {}
        for section in (doc.get("graphs") or {}).values():
            for node in section.get("nodes") or []:
                if node.get("id") == node_id:
                    return node
        return None

    def node(self, node_id: str) -> Optional[Dict[str, Any]]:
        """From the ROOM, and only from the local container when it cannot.

        Until 23 September this read the local container always, and the
        consequence was measured rather than imagined: validating a field on a
        unit that lives in the room answered «non trovo la US 3015 in questo
        grafo». The room is where the unit is; the room is what gets asked.

        Unreachable falls back, like everything else here: offline-first means
        the network is the optional part.
        """
        try:
            found = self._snapshot_node(node_id)
        except Exception:                             # unreachable, refused
            return self.fallback.node(node_id) if self.fallback else None
        if found is not None:
            return found
        return self.fallback.node(node_id) if self.fallback else None

    def study_name(self) -> Optional[str]:
        return self.fallback.study_name() if self.fallback else self.room_id

    def count_units(self) -> int:
        return self.fallback.count_units() if self.fallback else 0

    def answer(self, question: str) -> str:
        if self.fallback:
            return self.fallback.answer(question)
        return "Non riesco a leggere il grafo da qui."


def writer_from_env(environ: Optional[Dict[str, str]] = None) -> GraphWriter:
    """A room when the node names one, the local container otherwise.

    Never silent in either direction: `/health` says which is answering, because
    "did what I just said reach the others?" is the first question anybody asks
    about a field assistant.
    """
    env = dict(environ if environ is not None else os.environ)
    local = LocalWriter(env.get("EM_CHATBOT_CONTAINER")
                        or "data/scavo.em.json",
                        study=env.get("EM_CHATBOT_STUDY") or "Scavo")

    # A HANDOFF LINK is the way in now (`app/handoff.py`): one string, no
    # credential, and the node signs in for itself. It wins over the split
    # variables because it is the thing a person was HANDED — if both are set,
    # the link is the more recent intention.
    link = (env.get("EM_CHATBOT_HANDOFF") or "").strip()
    if link:
        from .handoff import HandoffError, parse as parse_handoff
        try:
            where = parse_handoff(link)
        except HandoffError as exc:
            raise RuntimeError(
                f"EM_CHATBOT_HANDOFF is not a handoff link: {exc}") from None
        token = (env.get("EM_CHATBOT_TOKEN") or "").strip()
        if not token:
            # Deliberately NOT signing in from here: `writer_from_env` is called
            # at import on a node that may be headless, and opening a browser as
            # a side effect of a module load is the kind of thing that hangs a
            # service at boot. The sign-in belongs to the command that follows a
            # link (`handoff.writer_from_link`), where a person is present.
            raise RuntimeError(
                "a handoff link is configured but no token: run the assistant's "
                "sign-in (it follows the link and gets one), or set "
                "EM_CHATBOT_TOKEN for a headless node.")
        return RoomWriter(where["server"], where["room"], token, fallback=local)

    base = (env.get("EM_SERVER_URL") or "").strip()
    room = (env.get("EM_CHATBOT_ROOM") or "").strip()
    token = (env.get("EM_CHATBOT_TOKEN") or "").strip()
    if base and room:
        if not token:
            raise RuntimeError(
                "a room is configured but no token: the assistant writes as a "
                "verified person or it does not write. Set EM_CHATBOT_TOKEN, "
                "or unset EM_SERVER_URL to work on the local container.")
        return RoomWriter(base, room, token, fallback=local)
    return local


def describe(writer: Any) -> str:
    if isinstance(writer, RoomWriter):
        # "degraded" with no reason is a status light with no label: the first
        # question anybody asks a field node is WHY, and `/health` is where they
        # ask it.
        state = ""
        if writer.degraded:
            why = f": {writer.last_refusal}" if writer.last_refusal else ""
            state = f" (degraded, writing locally{why})"
        return f"room {writer.room_id} at {writer.base_url}{state}"
    if isinstance(writer, LocalWriter):
        return f"local container ({writer.path})"
    return type(writer).__name__
