"""The five MVP tools — the first clients of the contract.

Grown from Elisa Dalla Longa's field card (design note §4): a thick forex card
with colour-coded voice commands, an accessibility artefact that doubles as the
command specification. The commands on it ARE the intents below, in the words a
person says with their hands in the soil.

| said in the field | tool | what changes |
|---|---|---|
| "crea una nuova scheda" | `create_su` | a StratigraphicUnit in the graph |
| "in che progetto sto lavorando" | `which_project` | nothing — it answers |
| "questa foto è per la US 12" | `attach_photo_to_su` | bytes in the store, a resource on the unit |
| "ti passo delle foto" | `ingest_photos` | bytes in the store, queued to a unit |
| "cosa abbiamo registrato nel saggio B" | `query_kg` | nothing — it answers from the graph |

**Every write goes through s3Dgraphy.** Not "mostly": the domain rule about what
a stratigraphic unit is, and what a resource attached to one means, lives in the
library and is not restated here. This module builds the delta by asking the
library to do the act on a scratch graph and reading what appeared — which is
also why a change in the library's node shape does not silently diverge from
what the field assistant writes.

**Everything is attributed.** The author is the ORCID of the token, stamped by
`invoke`; the act itself is a `crmdig:D7` process node, the same genesis record
the promotion arc uses. A record without a hand behind it is one nobody can
defend three years later, when it matters.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .contract import (GraphDelta, Slot, ToolDescriptor, ToolRegistry,
                       ToolResult, stable_id)

#: The DTC term for "this was made by that act". Not a word invented here: it is
#: the one `s3dgraphy.publication` already uses for a genesis event.
DTC_PROCESS = "dtc_process"


def _now() -> str:
    from s3dgraphy.editorial import now_iso
    return now_iso()


def _process_node(kind: str, author: Optional[str], about: str,
                  detail: str = "") -> Dict[str, Any]:
    """The `crmdig:D7` that records the act.

    Deterministic id from what the act is ABOUT, so the same act asked twice is
    the same act — which is what makes a retry on a flaky field network safe.
    """
    node_id = stable_id(kind, about)
    return {
        "id": node_id,
        "node_type": DTC_PROCESS,
        "name": kind,
        "description": detail or f"{kind} · {about}",
        "data": {"created_by": author, "created_at": _now(),
                 "tool": kind, "source": "stratigraph-chatbot"},
    }


# ── 1 · create_su ────────────────────────────────────────────────────────────

def make_create_su(graph_writer) -> ToolDescriptor:
    """A new stratigraphic unit, by voice.

    `graph_writer` is how this node writes: a room client, or the local
    container when the excavation is offline. The tool does not know which, and
    that is the point of §5 — convergence on the graph, not coupling.
    """

    def handler(slots: Dict[str, Any], author: Optional[str]) -> ToolResult:
        number = str(slots.get("us") or "").strip()
        unit_id = f"US{number}"

        # The library decides what a StratigraphicUnit IS. We ask it, then read
        # what it produced — rather than writing a dict shaped like one.
        from s3dgraphy.graph import Graph
        from s3dgraphy.nodes import StratigraphicUnit

        scratch = Graph(graph_id="chatbot-scratch")
        scratch.add_node(StratigraphicUnit(unit_id, name=f"US {number}"))
        made = next(n for n in scratch.nodes if n.node_id == unit_id)

        node = {"id": made.node_id, "node_type": made.node_type,
                "name": made.name,
                "data": {"created_by": author, "created_at": _now()}}
        process = _process_node("create_su", author, unit_id,
                                f"US {number} creata a voce sul campo")
        delta = GraphDelta(nodes=[node], process=process, author=author)

        existed = graph_writer.has_node(unit_id)
        if not existed:
            graph_writer.apply(delta)
        return ToolResult(
            ok=True,
            # Said out loud. "US 12 creata" is what a person needs to hear to
            # know the record exists and keep digging.
            message=(f"US {number} già presente, non l'ho creata di nuovo."
                     if existed else f"Ho creato la US {number}."),
            delta=GraphDelta() if existed else delta,
            data={"us": number, "node_id": unit_id, "created": not existed})

    return ToolDescriptor(
        name="create_su",
        intents=["crea una nuova scheda", "nuova scheda", "nuova unità",
                 "nuova us", "crea una us"],
        input_schema=[Slot("us", "string", True, "il numero dell'unità")],
        description="Una nuova unità stratigrafica nel grafo condiviso.",
        service="s3dgraphy", handler=handler)


# ── 2 · which_project ────────────────────────────────────────────────────────

def make_which_project(graph_writer) -> ToolDescriptor:
    """The question that orients somebody who has been digging for six hours."""

    def handler(slots: Dict[str, Any], author: Optional[str]) -> ToolResult:
        study = graph_writer.study_name()
        if not study:
            return ToolResult(
                ok=True,
                message="Non sto lavorando su nessuno studio: questo nodo non "
                        "ha ancora un progetto aperto.",
                data={"study": None})
        units = graph_writer.count_units()
        return ToolResult(
            ok=True,
            message=f"Stai lavorando su «{study}», "
                    f"{units} unità registrate finora.",
            data={"study": study, "units": units})

    return ToolDescriptor(
        name="which_project",
        intents=["in che progetto sto lavorando", "che progetto è questo",
                 "dove sono", "quale progetto"],
        description="Legge lo studio attivo e risponde a voce.",
        service="s3dgraphy", writes=False, handler=handler)


# ── 3 · attach_photo_to_su ───────────────────────────────────────────────────

def make_attach_photo(graph_writer, asset_store) -> ToolDescriptor:
    """A photo becomes a resource of a unit.

    Two acts, in this order and not the other: the bytes go to the store FIRST,
    then the graph points at them. If the node dies in between, there is an
    orphan object in a bucket — recoverable. The other order would put a
    reference in a shared graph to bytes that do not exist, which every client
    would then fail to load.
    """

    def handler(slots: Dict[str, Any], author: Optional[str]) -> ToolResult:
        number = str(slots.get("us") or "").strip()
        photo = slots.get("photo")
        if not isinstance(photo, (bytes, bytearray)) or not photo:
            return ToolResult(ok=False,
                              message="Non ho ricevuto nessuna foto.")
        unit_id = f"US{number}"
        if not graph_writer.has_node(unit_id):
            # Not an error, and not a silent creation either: saying it is what
            # lets somebody fix the number they just spoke.
            return ToolResult(
                ok=False,
                message=f"Non trovo la US {number}. Creala prima, "
                        f"o dimmi un altro numero.",
                data={"us": number, "reason": "unknown-unit"})

        stored = asset_store.put(bytes(photo),
                                 str(slots.get("media_type") or "image/jpeg"))
        digest = stored["sha256"]
        resource_id = f"{unit_id}.photo.{digest[:12]}"

        resource = {
            "id": resource_id, "node_type": "resource",
            "name": str(slots.get("filename") or f"foto US {number}"),
            "data": {"url": stored.get("url") or stored["ref"],
                     "checksum": stored["ref"], "residency": "reference",
                     "media_type": stored.get("media_type"),
                     "created_by": author, "created_at": _now()},
        }
        edge = {"id": f"{unit_id}__has_linked_resource__{resource_id}",
                "source": unit_id, "target": resource_id,
                "edge_type": "has_linked_resource"}
        process = _process_node("attach_photo_to_su", author,
                                f"{unit_id}:{digest[:12]}",
                                f"foto legata alla US {number}")
        delta = GraphDelta(nodes=[resource], edges=[edge], process=process,
                           author=author)
        graph_writer.apply(delta)
        return ToolResult(
            ok=True,
            message=f"Foto allegata alla US {number}.",
            delta=delta,
            data={"us": number, "resource_id": resource_id,
                  "sha256": stored["ref"], "created": stored.get("created")})

    return ToolDescriptor(
        name="attach_photo_to_su",
        intents=["questa foto è per la us", "questa foto va sulla us",
                 "allega la foto alla us", "foto per la us"],
        input_schema=[Slot("us", "string", True, "il numero dell'unità"),
                      Slot("photo", "bytes", True, "i byte dell'immagine"),
                      Slot("filename", "string", False, "il nome del file"),
                      Slot("media_type", "string", False, "il tipo MIME")],
        description="La foto va nell'object store e diventa una risorsa "
                    "dell'unità.",
        service="s3dgraphy", handler=handler)


# ── 4 · ingest_photos ────────────────────────────────────────────────────────

def make_ingest_photos(graph_writer, asset_store) -> ToolDescriptor:
    """Several photos at once, queued to a unit.

    The same act as `attach_photo_to_su`, repeated — and it reuses that tool's
    handler rather than growing a second write path, because two implementations
    of "a photo belongs to a unit" would eventually disagree about what one is.
    """
    single = make_attach_photo(graph_writer, asset_store)

    def handler(slots: Dict[str, Any], author: Optional[str]) -> ToolResult:
        photos = slots.get("photos") or []
        if not isinstance(photos, list) or not photos:
            return ToolResult(ok=False, message="Non ho ricevuto nessuna foto.")
        nodes: List[Dict[str, Any]] = []
        edges: List[Dict[str, Any]] = []
        stored = 0
        for index, photo in enumerate(photos):
            one = single.handler({**slots, "photo": photo,
                                  "filename": f"foto {index + 1} "
                                              f"US {slots.get('us')}"}, author)
            if not one.ok:
                return one            # the first refusal is the answer
            nodes.extend(one.delta.nodes)
            edges.extend(one.delta.edges)
            stored += 1
        number = str(slots.get("us") or "").strip()
        process = _process_node("ingest_photos", author,
                                f"US{number}:{stored}",
                                f"{stored} foto in coda alla US {number}")
        return ToolResult(
            ok=True,
            message=f"Ho messo {stored} foto sulla US {number}.",
            delta=GraphDelta(nodes=nodes, edges=edges, process=process,
                             author=author),
            data={"us": number, "stored": stored})

    return ToolDescriptor(
        name="ingest_photos",
        intents=["ti passo delle foto", "prendo delle foto",
                 "queste foto sono per la us"],
        input_schema=[Slot("us", "string", True, "il numero dell'unità"),
                      Slot("photos", "bytes", True, "le immagini")],
        description="Più foto nello store, in coda a un'unità.",
        service="s3dgraphy", handler=handler)


# ── 5 · query_kg ─────────────────────────────────────────────────────────────

def make_query_kg(graph_writer) -> ToolDescriptor:
    """A question, answered from the graph, out loud.

    Deliberately small: it answers what the graph plainly says (how many units,
    what is in an epoch or an activity, what a unit is). A retrieval model over
    the documentation is ARC's Document Analysis, and it plugs in as its OWN
    tool — which is exactly what the contract is for.
    """

    def handler(slots: Dict[str, Any], author: Optional[str]) -> ToolResult:
        question = str(slots.get("question") or "").strip()
        answer = graph_writer.answer(question)
        return ToolResult(ok=True, message=answer,
                          data={"question": question})

    return ToolDescriptor(
        name="query_kg",
        intents=["cosa abbiamo registrato", "quante unità",
                 "cosa c'è", "dimmi"],
        input_schema=[Slot("question", "string", False, "la domanda")],
        description="Una risposta parlata, letta dal grafo.",
        service="s3dgraphy", writes=False, handler=handler)


# ── the five, registered ─────────────────────────────────────────────────────

def build_registry(graph_writer, asset_store) -> ToolRegistry:
    """The MVP registry. Adding a partner's capability is one more line here
    plus a descriptor — which is the whole claim of the contract."""
    registry = ToolRegistry()
    registry.register(make_create_su(graph_writer))
    registry.register(make_which_project(graph_writer))
    registry.register(make_attach_photo(graph_writer, asset_store))
    registry.register(make_ingest_photos(graph_writer, asset_store))
    registry.register(make_query_kg(graph_writer))
    return registry
