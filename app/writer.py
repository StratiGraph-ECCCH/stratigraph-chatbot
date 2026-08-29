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


class GraphWriter(Protocol):
    def apply(self, delta: GraphDelta) -> None: ...
    def has_node(self, node_id: str) -> bool: ...
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

    def has_node(self, node_id: str) -> bool:
        section = self._section(self._read())
        return any(n.get("id") == node_id for n in section.get("nodes") or [])

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
        ops: List[Dict[str, Any]] = []
        for node in delta.nodes + ([delta.process] if delta.process else []):
            ops.append({"op": "add_node", "id": node["id"], "node": node})
        for edge in delta.edges:
            ops.append({"op": "add_edge", "id": edge.get("id"),
                        "source": edge["source"], "target": edge["target"],
                        "edge_type": edge.get("edge_type")})
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
        """Open, join, send, read each ack, close."""
        from websockets.sync.client import connect

        with connect(self._ws_url(), open_timeout=self.timeout,
                     close_timeout=self.timeout,
                     max_size=None) as socket:
            self._drain_join(socket)
            for op in ops:
                socket.send(json.dumps({"v": WIRE, "type": "op",
                                        "payload": op}))
                self._await_result(socket, op)

    def _drain_join(self, socket: Any) -> None:
        """Read the three join frames, and BELIEVE the first one.

        `host_info.can_write` is the room telling you at the door what you may
        do. Checking it turns "the assistant said it saved and nothing appeared"
        into a sentence somebody can act on — and it costs one field of a frame
        we have to read anyway.
        """
        for _ in self.JOIN_FRAMES:
            message = self._recv(socket)
            if message.get("type") != "host_info":
                continue
            payload = message.get("payload") or {}
            if payload.get("can_write") is False:
                raise RoomRefused(
                    f"this room is read-only for you (role "
                    f"{payload.get('role') or 'unknown'})")

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
    def has_node(self, node_id: str) -> bool:
        return self.fallback.has_node(node_id) if self.fallback else False

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
