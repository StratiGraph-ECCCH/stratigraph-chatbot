"""The seven tools — the first clients of the contract.

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
| "costruisci il modello 3D di questa US" | `build_model` | the node reconstructs; a model and its provenance appear |

The sixth is the newest and the odd one out: it is the only tool whose service
is not this process. It ASKS the node (`/v1/photogrammetry`) and reads the answer
back — which is what a voice should do with an act that takes minutes and needs
an engine.

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
        # `description` is the library's own field (the datamodel maps it to
        # crm:P3_has_note), so it is passed to the CONSTRUCTOR and not bolted on.
        from s3dgraphy.graph import Graph
        from s3dgraphy.nodes import StratigraphicUnit

        description = str(slots.get("description") or "").strip()
        scratch = Graph(graph_id="chatbot-scratch")
        scratch.add_node(StratigraphicUnit(unit_id, name=f"US {number}",
                                           description=description))
        made = next(n for n in scratch.nodes if n.node_id == unit_id)

        node = {"id": made.node_id, "node_type": made.node_type,
                "name": made.name,
                "data": {"created_by": author, "created_at": _now()}}
        if made.description:
            node["description"] = made.description

        # ── the interpretation, and why it lives where it does ──────────────
        #
        # Measured first (the datamodel, not a guess): `description` exists on a
        # stratigraphic unit; an INTERPRETATION does not — the `functional/telic`
        # qualia are about function, which is a different claim.
        #
        # So the decision, taken deliberately and stated here so nobody has to
        # rediscover it: a dictated interpretation is a **field note**, and it
        # goes in `data["interpretation"]`. It is NOT made a PropertyNode,
        # because a PropertyNode carries an evidence chain (source → extractor →
        # property) and a sentence spoken into a microphone has none —
        # manufacturing one would put a paradata chain in the graph that nobody
        # built.
        #
        # One place, not two: this field is what somebody said in the trench;
        # when that reading acquires evidence it becomes a property WITH its
        # chain, and that is a different act, done at the desk.
        interpretation = str(slots.get("interpretation") or "").strip()
        if interpretation:
            node["data"]["interpretation"] = interpretation

        # ── everything the adapters carried and nobody mapped ───────────────
        #
        # PyArchInit's `rapporti`, its `unita_misura`, whatever ATRIUM adds next
        # release. Kept under one key rather than spread across `data`, so a
        # reader can always tell what this service UNDERSTOOD from what it
        # merely carried. Dropping it would make the graph a lossy copy of
        # somebody's database, which is the one thing an ingest must not be.
        extra = slots.get("extra")
        if isinstance(extra, dict) and extra:
            node["data"]["source_fields"] = dict(extra)

        # Where the record came from, when the caller knows: a unit number is
        # only unique inside its area, and losing that makes two trenches one.
        for key in ("sito", "area"):
            value = slots.get(key)
            if value not in (None, ""):
                node["data"][key] = str(value).strip()
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
        input_schema=[
            Slot("us", "string", True, "il numero dell'unità"),
            # Optional, all of them: a unit dictated in three words is still a
            # unit. What the adapters carry, this now honours.
            Slot("description", "string", False, "cosa c'è (crm:P3_has_note)"),
            Slot("interpretation", "string", False,
                 "cosa si pensa che sia — nota di campo, non ancora una "
                 "property con la sua catena di evidenza"),
            Slot("extra", "id", False,
                 "i campi che l'adattatore non mappa: portati, non buttati"),
            Slot("sito", "string", False, "il sito, quando il record lo dice"),
            Slot("area", "string", False, "l'area: una US è unica dentro la sua"),
        ],
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


# ── 6 · build_model ──────────────────────────────────────────────────────────

def make_build_model(graph_writer, asset_store) -> ToolDescriptor:
    """"costruisci il modello 3D di questa US dalle foto" — said out loud.

    The voice does not reconstruct anything. It ASKS the node, which is the
    whole point of the three layers: the meaning of the act is s3Dgraphy's, the
    engine and the store are StratiGraph Server's, and what lives here is one
    sentence turned into one request and one answer read back to somebody whose
    hands are in the soil.

    **It refuses off a room, and says why.** Reconstruction needs the engine and
    the object store, both of which are the NODE's. A field assistant writing to
    its own local container has photographs and no engine — telling somebody
    "I'll build it" and silently doing nothing is the worst of the three
    possible answers.

    **It does not wait.** The engine takes minutes; a person standing over a
    trench does not hold a phone to their ear for them. The tool reports the job
    id, which is what the endpoint's 202 means. Asking «a che punto è il
    modello» OUT LOUD is a tool of its own and is not built here — the poll is
    `GET /v1/photogrammetry/{job_id}` for now, and saying otherwise would be
    promising a sentence that does nothing.

    `asset_store` is taken and not used, deliberately: every tool in this
    registry has the same signature, and the photographs are ALREADY in the
    node's store (that is what `ingest_photos` did). Staging them again from
    here would upload the same bytes twice.
    """

    def handler(slots: Dict[str, Any], author: Optional[str]) -> ToolResult:
        number = str(slots.get("us") or "").strip()
        cluster = str(slots.get("cluster") or "").strip()
        if not number and not cluster:
            return ToolResult(ok=False,
                              message="Di quale US devo costruire il modello?")
        unit_id = f"US{number}" if number else ""
        target = cluster or unit_id

        room = getattr(graph_writer, "room_id", None)
        caller = getattr(graph_writer, "call", None)
        if not room or caller is None:
            return ToolResult(
                ok=False,
                message="Da qui non posso: il modello lo costruisce il nodo, e "
                        "questo assistente sta scrivendo sul contenitore locale. "
                        "Collegati a una stanza e riprova.",
                data={"reason": "no-room", "target": target})

        mode = str(slots.get("mode") or "local").strip().lower()
        payload: Dict[str, Any] = {"room_id": room, "cluster": target,
                                   "mode": mode}
        if unit_id:
            payload["subject"] = unit_id
        gcps = slots.get("gcps")
        if gcps:
            # a control set arrives as data (from a survey, an import), never
            # dictated: pixels and coordinates are not things anybody says
            payload["gcps"] = gcps
            payload["mode"] = "absolute"

        answer = caller("/v1/photogrammetry", payload)
        if answer is None:
            return ToolResult(
                ok=False,
                message="Non riesco a raggiungere il nodo. Le foto sono al "
                        "sicuro: riprova quando torna la rete.",
                data={"reason": "unreachable", "target": target})
        if answer.get("detail"):
            # the endpoint's own refusal, read out in its own words rather than
            # replaced by a friendlier one that says less
            return ToolResult(ok=False,
                              message=f"Il nodo ha rifiutato: {answer['detail']}",
                              data={"reason": "refused", "target": target,
                                    "detail": answer["detail"]})

        job_id = str(answer.get("job_id") or "")
        count = int(answer.get("image_count") or 0)
        where = ("georeferenziato" if payload["mode"] == "absolute"
                 else "in coordinate locali")
        # NOT promising a phrase that does nothing: asking «a che punto è il
        # modello» out loud needs a tool of its own, and it is not this one. The
        # job id is reported instead, which is what the node's own poll takes.
        return ToolResult(
            ok=True,
            message=(f"Ho avviato la ricostruzione di {target} da {count} foto, "
                     f"{where}. Ci vogliono alcuni minuti; il lavoro è "
                     f"{job_id[:8]}."),
            data={"job_id": job_id, "target": target, "room": room,
                  "mode": payload["mode"], "image_count": count,
                  "status": answer.get("status")})

    return ToolDescriptor(
        name="build_model",
        intents=["costruisci il modello 3d", "costruisci il modello",
                 "fai il modello 3d", "ricostruisci la us",
                 "modello 3d di questa us", "modello dalle foto"],
        input_schema=[Slot("us", "string", False, "il numero dell'unità"),
                      Slot("cluster", "string", False,
                           "l'acquisizione o la risorsa da cui partire"),
                      Slot("mode", "string", False,
                           "local (scala) o absolute (georeferenziato con GCP)"),
                      Slot("gcps", "object", False,
                           "i punti di controllo, se ci sono")],
        description="Le foto già caricate diventano un modello 3D sul nodo, "
                    "con la sua provenienza nel grafo.",
        service="rest", handler=handler)


# ── 7 · open_in_emstudio ─────────────────────────────────────────────────────

def make_open_in_emstudio(graph_writer, asset_store) -> ToolDescriptor:
    """"apri questa stanza in EMStudio" — the round-trip, from the trench.

    Not a transfer and not an export: the graph lives in the ROOM, so opening it
    elsewhere is another client joining the same room. Somebody standing over a
    unit says this, and the person at the laptop finds the study already there.

    **The link names a place and never a permission.** It comes from the node's
    own handoff contract (`GET /v1/rooms/{id}/open`), asked for the room this
    assistant is connected to, and EMStudio signs itself in when it opens.

    Reads nothing and writes nothing — `writes=False`, so the core's no-author
    refusal does not fire: asking where a room can be opened is not an act on
    the record.
    """

    def handler(slots: Dict[str, Any], author: Optional[str]) -> ToolResult:
        room = getattr(graph_writer, "room_id", None)
        reader = getattr(graph_writer, "read", None)
        if not room or reader is None:
            return ToolResult(
                ok=False,
                message="Non sono in una stanza: non c'è niente da aprire "
                        "altrove. Questo nodo sta scrivendo sul contenitore "
                        "locale.",
                data={"reason": "no-room"})

        answer = reader(f"/v1/rooms/{room}/open")
        if answer is None:
            return ToolResult(
                ok=False,
                message="Non riesco a raggiungere il nodo per chiedere come si "
                        "apre la stanza. Riprova quando torna la rete.",
                data={"reason": "unreachable", "room": room})
        if answer.get("detail"):
            return ToolResult(ok=False,
                              message=f"Il nodo ha rifiutato: {answer['detail']}",
                              data={"reason": "refused", "room": room})

        card = (answer.get("tools") or {}).get("emstudio") or {}
        # The browser door when the deployment hosts a web build, the desktop
        # scheme otherwise — the same two doors the room browser offers, and the
        # same rule: no door at all beats a door that fails after the click.
        browser = card.get("browser")
        scheme = card.get("scheme") or answer.get("scheme")
        link = browser or scheme
        if not link:
            return ToolResult(
                ok=False,
                message="Questo nodo non sa dire come aprire EMStudio.",
                data={"reason": "no-door", "room": room})

        where = "nel browser" if browser else "in EMStudio"
        return ToolResult(
            ok=True,
            message=f"Ecco il link per aprire la stanza {room} {where}. "
                    f"Non contiene nessun token: EMStudio ti fa entrare da sé.",
            data={"room": room, "link": link,
                  "kind": "browser" if browser else "scheme",
                  "web": answer.get("web"),
                  "carries_token": bool(answer.get("carries_token"))})

    return ToolDescriptor(
        name="open_in_emstudio",
        intents=["apri questa stanza in emstudio", "apri in emstudio",
                 "apri lo studio sul computer", "passa a emstudio"],
        input_schema=[],
        description="Il link per aprire la STESSA stanza in EMStudio — il grafo "
                    "è della stanza, quindi non si trasferisce niente.",
        service="rest", writes=False, handler=handler)


# ── the five, registered ─────────────────────────────────────────────────────

def build_registry(graph_writer, asset_store) -> ToolRegistry:
    """The registry. Adding a partner's capability is one more line here plus a
    descriptor — which is the whole claim of the contract, and `build_model`
    (2026-08-29) is the first time somebody else's capability was added by
    exactly those two lines."""
    registry = ToolRegistry()
    registry.register(make_create_su(graph_writer))
    registry.register(make_which_project(graph_writer))
    registry.register(make_attach_photo(graph_writer, asset_store))
    registry.register(make_ingest_photos(graph_writer, asset_store))
    registry.register(make_query_kg(graph_writer))
    registry.register(make_build_model(graph_writer, asset_store))
    registry.register(make_open_in_emstudio(graph_writer, asset_store))
    return registry
