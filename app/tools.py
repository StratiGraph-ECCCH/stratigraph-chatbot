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

from typing import Any, Dict, List, Optional, Tuple

from . import authorship
from .contract import (GraphDelta, Slot, ToolDescriptor, ToolRegistry,
                       ToolResult, stable_id)

#: The DTC term for "this was made by that act". Not a word invented here: it is
#: the one `s3dgraphy.publication` already uses for a genesis event.
DTC_PROCESS = "dtc_process"

def unit_id_for(number: str) -> str:
    """The id of a stratigraphic unit, from its number — in ONE place.

    `create_su` has always used `f"US{number}"`, a readable id rather than a
    `stable_id` hash, and that is kept: it is what is already in every graph
    this assistant has written, and re-minting would orphan them.

    IT LIVES HERE because `update_su` needs the SAME answer. Written twice it
    was wrong immediately: the second copy said `stable_id("us", number)`, and
    every update was refused with «non è in questo grafo» against a unit that
    had just been created two lines above. Caught by a test; it would have been
    a scheda that silently refused to save.

    KNOWN LIMIT, inherited and declared: the id does not carry `sito`/`area`,
    though `create_su` stores both in `data`. So two areas with a unit «1» are
    one node. That is pre-existing behaviour of this service, not a decision
    taken tonight, and changing it re-mints ids.
    """
    return f"US{str(number).strip()}"


#: What `update_su` will not change, though the CRDT would let it. `name` is
#: derived from the unit number and is mentioned in other people's nodes; `id`
#: is the identity itself. Refused by name rather than silently dropped, because
#: a form that sent one and got a success would have been told a lie.
_NOT_UPDATABLE = frozenset({"name", "id", "node_type"})


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
        unit_id = unit_id_for(number)

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


# ── 1bis · update_su — THE EIGHTH, and the first one a SCHEDA needs ─────────

def make_update_su(graph_writer) -> ToolDescriptor:
    """Correct or complete a unit that already exists.

    ## WHY THIS COULD NOT BE `create_su` WITH MORE SLOTS

    Because of one measured line. `s3dgraphy.crdt.apply_op_to_section`:

        if kind == "add_node":
            existing = by_id.get(node_id)
            if existing is None:
                nodes.append(payload)
                return OpResult(True, "added", node_id)

    **`add_node` on an id that is not there CREATES it.** So a scheda that
    corrected «US 21» when the unit in the graph is «US 12» — a mistyped number,
    a form opened against the wrong study — would not fail: it would quietly
    mint a new unit with one field in it, under the author's name, and report a
    success. `update_field` refuses the same thing with «node '…' is not here»,
    and that refusal **is** the difference between correcting a record and
    inventing one.

    So this tool is not a convenience over `create_su`. It is the only verb that
    can say «this unit already exists and I am changing it», and a scheda — which
    is opened on a unit far more often than it creates one — needs exactly that.

    ## WHAT IT DOES NOT DO

    * **it does not create.** No `add_node` for the unit, ever. If the unit is
      not there the act is refused and the person is told to create it first;
    * **it does not rename.** `name` is addressable by the CRDT and is
      deliberately refused here: «US 12» is derived from the number, and renaming
      a unit is a different act with different consequences (every mention of it
      elsewhere). Refused by name, so nobody has to wonder;
    * **it does not decide what a field means.** The names arrive from the
      scheda's definition (`app/scheda.py`), which read them from the standard.
      This tool addresses them and nothing more.
    """

    def handler(slots: Dict[str, Any], author: Optional[str]) -> ToolResult:
        number = str(slots.get("us") or "").strip()
        if not number:
            return ToolResult(ok=False, message="Mi manca il numero dell'unità.")

        fields = slots.get("fields")
        if not isinstance(fields, dict) or not fields:
            return ToolResult(
                ok=False,
                message=f"Non mi hai detto che cosa cambiare sulla US {number}.")

        refused = sorted(k for k in fields if str(k).strip() in _NOT_UPDATABLE)
        if refused:
            return ToolResult(
                ok=False,
                message=(f"Non cambio {', '.join(refused)} da qui: rinominare "
                         f"un'unità è un altro atto, perché tocca ogni posto "
                         f"che la nomina."))

        unit_id = unit_id_for(number)
        process = _process_node("update_su", author, unit_id,
                                f"US {number}: {len(fields)} campi aggiornati")

        # ── CHI HA COMPOSTO OGNI VALORE, accanto al valore ──────────────────
        #
        # Scritta nella STESSA operazione dei campi, non in un secondo giro:
        # un valore e la sua autorialità che atterrano separatamente sono due
        # scritture di cui una può fallire, e un campo AI senza il suo
        # marcatore ha l'aria di un campo qualunque — che è precisamente ciò
        # che non deve avere.
        #
        # Il default è `human`: chi non dice niente ha scritto lui. Marcare AI
        # per difetto attribuirebbe a una macchina il lavoro di chi scava.
        marks = authorship.marks_for(fields, slots.get("authored_by"),
                                     model=slots.get("model"))
        try:
            outcomes = graph_writer.update(unit_id, {**fields, **marks},
                                           author=author, process=process)
        except Exception as exc:                                 # noqa: BLE001
            # `FieldRefused` («non esiste») and `RoomRefused` («non puoi
            # scrivere») both arrive here, and both already carry a sentence
            # meant for a person: it is passed on rather than replaced.
            return ToolResult(ok=False, message=str(exc),
                              data={"us": number, "node_id": unit_id})

        # I marcatori di autorialità NON si contano fra i campi: una persona
        # che ha compilato due caselle deve leggere «2 campi aggiornati», non
        # quattro.
        def _is_mark(name: str) -> bool:
            return f".{authorship.PREFIX}." in f".{name}"

        landed = [o for o in outcomes
                  if o.get("applied") and not _is_mark(o["field"])]
        # `idempotent` E `stale` NON sono la stessa risposta, e trattarli
        # insieme diceva una cosa falsa. Trovato nel giro vero: una scheda
        # salvata subito dopo la creazione riportava «la stanza ha un valore
        # più recente» per `area`, quando il valore era **identico** — il
        # merge aveva risposto `idempotent`, cioè «ce l'ho già così».
        #
        #   idempotent → il valore è già quello. Non è un conflitto e non si
        #                dice: nessuno ha perso niente.
        #   stale      → qualcun altro ha scritto quella casella più
        #                recentemente. QUESTO si dice, perché è la cosa che
        #                una persona più ha bisogno di sapere.
        already = [o for o in outcomes
                   if not o.get("applied") and not _is_mark(o["field"])
                   and o.get("reason") == "idempotent"]
        held = [o for o in outcomes
                if not o.get("applied") and not _is_mark(o["field"])
                and o.get("reason") != "idempotent"]
        # A field the room kept somebody else's value for is NOT a failure of
        # this act, and it is not silence either: it is said, because two people
        # writing the same box is the thing a person most needs to know about.
        message = f"US {number}: {len(landed)} campi aggiornati."
        if already:
            message += f" {len(already)} erano già così."
        if held:
            message += (f" {len(held)} non applicati (qualcun altro li ha "
                        f"scritti più di recente): "
                        f"{', '.join(o['field'] for o in held)}.")

        return ToolResult(
            ok=True, message=message,
            # The delta carries the ACT, not the nodes: nothing was created, and
            # a delta claiming a node would be a claim that something was.
            delta=GraphDelta(process=process, author=author),
            data={"us": number, "node_id": unit_id,
                  "updated": [o["field"] for o in landed],
                  "already": [o["field"] for o in already],
                  "not_applied": held})

    return ToolDescriptor(
        name="update_su",
        intents=["aggiorna la scheda", "correggi la us", "modifica la us",
                 "aggiorna la us", "correggi la scheda"],
        input_schema=[
            Slot("us", "string", True, "il numero dell'unità da aggiornare"),
            Slot("fields", "id", True,
                 "i campi da cambiare, per nome — quelli della definizione "
                 "della scheda; un valore nullo svuota la casella"),
            Slot("authored_by", "id", False,
                 "chi ha COMPOSTO ciascun valore: `human` o `ai`. Assente "
                 "vuol dire human, perché chi non dice niente ha scritto lui"),
            Slot("model", "string", False,
                 "quale modello, per i campi marcati `ai` — è ciò che resta "
                 "leggibile dopo che una persona li ha validati"),
        ],
        description="Aggiorna i campi di un'unità stratigrafica che esiste già.",
        service="s3dgraphy", handler=handler)


# ── 1ter · relate_su — L'INTENTO CHE MANCAVA ────────────────────────────────
#
# Trovato marcando la scheda ICCD in `stratigraph-templates` il 22 settembre:
# **nessuno dei sette intenti permetteva di registrare un rapporto
# stratigrafico a voce.** Non era una dimenticanza — `create_su` mette
# `rapporti` di pyArchInit sotto `extra`, fra le cose «merely carried», e per
# un ingest ha ragione. Ma una persona in trincea dice «la 12 copre la 18»
# tutto il giorno, e non aveva un modo di dirlo a questo servizio.
#
# Per questo le dieci caselle dei rapporti della US ICCD sono rimaste
# `unknown` in quella definizione: marcarle `trench` senza un intento che le
# copra sarebbe stato inventare il criterio. Quando questo tool esiste, il
# marcatore si mette **là**, nella definizione, non qui.

#: I verbi che una persona dice, e l'arco che ne esce. MISURATO contro
#: `s3Dgraphy_connections_datamodel.json` — versione **1.6.13**, 54 tipi
#: dichiarati:
#:
#:     is_after  overlies  cuts  fills  abuts  is_bonded_to
#:     is_physically_equal_to  has_same_time            → PRESENTI
#:     is_before  is_overlain_by  is_cut_by             → ASSENTI
#:
#: **Gli inversi non esistono come tipi di arco.** Esistono solo come etichetta
#: `reverse` per LEGGERE un arco al contrario. Quindi «la 12 è coperta dalla
#: 18» non si registra con un arco inverso: si registra **scambiando i capi**,
#: ed è il motivo per cui la terza voce di ogni coppia qui sotto è `swap`.
#:
#: LA MAPPA È LA STESSA di `pyarchinit-mini/pyarchinit_mini/connector/us_ops.py`
#: (commit `9fb8777`), deliberatamente: lo stesso rapporto detto a voce o
#: importato da una tabella deve diventare **lo stesso arco**, altrimenti la
#: porta da cui è entrato si vede nel grafo.
#:
#: `overlies` ESISTE e NON è scelto: sarebbe la relazione fisica di COPRE con
#: la sua mappatura AP11, ma l'adattatore già nell'ecosistema usa `is_after`, e
#: due porte che scrivono due tipi per la stessa frase è la divergenza che si
#: scopre sei mesi dopo. Se un giorno si passa a `overlies`, si passa nei due
#: repository insieme.
RELATIONS: Dict[str, Tuple[str, str]] = {
    # ── il verbo canonico, e il suo inverso che scambia i capi ──────────────
    "copre": ("is_after", "forward"),
    "coperto da": ("is_after", "swap"),
    "coperta da": ("is_after", "swap"),
    "posteriore a": ("is_after", "forward"),
    "anteriore a": ("is_after", "swap"),
    "taglia": ("cuts", "forward"),
    "tagliato da": ("cuts", "swap"),
    "tagliata da": ("cuts", "swap"),
    "riempie": ("fills", "forward"),
    "riempito da": ("fills", "swap"),
    "riempita da": ("fills", "swap"),
    "si appoggia a": ("abuts", "forward"),
    "appoggia a": ("abuts", "forward"),
    "gli si appoggia": ("abuts", "swap"),
    # ── simmetrici: i capi si ORDINANO, così due modi di dirlo sono un arco ─
    "si lega a": ("is_bonded_to", "symmetric"),
    "uguale a": ("is_physically_equal_to", "symmetric"),
    "contemporaneo a": ("has_same_time", "symmetric"),
    "contemporanea a": ("has_same_time", "symmetric"),
}


def edge_id_for(source: str, edge_type: str, target: str) -> str:
    """`source__type__target` — la convenzione, non un'invenzione.

    `EMStudio/frontend/src/crdt.ts:622` compone esattamente questo quando un
    arco arriva senza id, e `us_ops.edge_id` fa lo stesso. Usare la stessa
    convenzione vuol dire che un arco detto a voce e lo stesso arco disegnato a
    mano nell'editor **sono un arco**, e il secondo si fonde invece di
    raddoppiare la freccia.
    """
    return f"{source}__{edge_type}__{target}"


def make_relate_su(graph_writer) -> ToolDescriptor:
    """«La 12 copre la 18» — un rapporto stratigrafico, a voce.

    Le due unità devono ESISTERE. Un arco fra due id che nessuno può risolvere
    è peggio di un arco che manca: la matrice lo disegna, e la freccia punta
    nel vuoto. Quindi si controlla, e se una delle due non c'è si dice quale.
    """

    def handler(slots: Dict[str, Any], author: Optional[str]) -> ToolResult:
        left = str(slots.get("us") or "").strip()
        right = str(slots.get("other") or "").strip()
        said = str(slots.get("relation") or "").strip().lower()

        if not left or not right:
            return ToolResult(
                ok=False,
                message="Mi servono due unità: «la 12 copre la 18».")
        if left == right:
            return ToolResult(
                ok=False,
                message=f"La US {left} non può essere in rapporto con se stessa.")

        mapping = RELATIONS.get(said)
        if mapping is None:
            return ToolResult(
                ok=False,
                message=(f"Non conosco il rapporto «{said}». So: "
                         + ", ".join(sorted(RELATIONS)) + "."))
        edge_type, direction = mapping

        source_n, target_n = left, right
        if direction == "swap":
            source_n, target_n = right, left
        source, target = unit_id_for(source_n), unit_id_for(target_n)
        if direction == "symmetric":
            # I capi si ORDINANO, così «12 uguale a 18» e «18 uguale a 12»
            # producono lo stesso id e il secondo si fonde. Senza questo i
            # simmetrici sarebbero il solo posto che raddoppia ancora.
            source, target = min(source, target), max(source, target)

        missing = [n for n, node_id in ((source_n, source), (target_n, target))
                   if not graph_writer.has_node(node_id)]
        if missing:
            return ToolResult(
                ok=False,
                message=(f"Non trovo la US {' e la US '.join(missing)} in questo "
                         f"grafo. Un arco verso un'unità che non c'è disegna "
                         f"una freccia nel vuoto: crea prima l'unità."),
                data={"missing": missing})

        edge = {"id": edge_id_for(source, edge_type, target),
                "source": source, "target": target, "edge_type": edge_type}
        process = _process_node(
            "relate_su", author, edge["id"],
            f"US {left} {said} US {right}, detto sul campo")
        delta = GraphDelta(edges=[edge], process=process, author=author)
        graph_writer.apply(delta)

        return ToolResult(
            ok=True,
            message=f"Registrato: US {left} {said} US {right}.",
            delta=delta,
            data={"edge_id": edge["id"], "edge_type": edge_type,
                  "source": source, "target": target,
                  # Cosa è stato DETTO, oltre a cosa è stato scritto: chi
                  # rilegge deve poter vedere che «coperta da» è diventata un
                  # `is_after` a capi scambiati e non un tipo inverso.
                  "said": said, "direction": direction})

    return ToolDescriptor(
        name="relate_su",
        # LE FRASI SONO LE CHIAVI DELLA MAPPA, non una seconda lista.
        #
        # Scritte a mano erano subito divergenti: la mappa conosceva «coperta
        # da», «riempita da» e «contemporanea a» — le forme al femminile, che
        # sono quelle che si dicono di una US — e la lista degli intenti no,
        # quindi «la 12 è coperta dalla 18» non veniva riconosciuta affatto.
        # Trovato provando le frasi vere, non leggendo il codice.
        intents=sorted(RELATIONS) + ["rapporto fra", "metti in rapporto"],
        input_schema=[
            Slot("us", "string", True, "la prima unità — quella che fa l'azione"),
            Slot("other", "string", True, "la seconda unità"),
            Slot("relation", "string", True,
                 "il rapporto, nelle parole dette: copre, tagliato da, "
                 "uguale a…"),
        ],
        description="Registra un rapporto stratigrafico fra due unità.",
        service="s3dgraphy", handler=handler)


# ── 1quater · validate_field — la conferma umana ────────────────────────────

def make_validate_field(graph_writer) -> ToolDescriptor:
    """Una persona guarda un campo che il modello ha composto, e se lo assume.

    **LA VALIDAZIONE TRASFERISCE L'AUTORIALITÀ**: da quel momento il campo è di
    chi l'ha confermato, e il grafo lo dice. Resta scritto che il valore
    l'aveva proposto una macchina (`composed_by`), perché cancellarlo
    trasformerebbe una validazione in una riscrittura della storia.

    Non tocca il VALORE. Correggere un campo è `update_su`, e sono due atti
    diversi: «ho letto e va bene» non è «ho cambiato». Un tool che facesse
    entrambe le cose renderebbe impossibile distinguerli nel record.
    """

    def handler(slots: Dict[str, Any], author: Optional[str]) -> ToolResult:
        number = str(slots.get("us") or "").strip()
        wanted = slots.get("fields")
        if isinstance(wanted, str):
            wanted = [wanted]
        if not number or not wanted:
            return ToolResult(
                ok=False,
                message="Mi servono l'unità e quali campi hai controllato.")

        unit_id = unit_id_for(number)
        if not graph_writer.has_node(unit_id):
            return ToolResult(
                ok=False,
                message=f"Non trovo la US {number} in questo grafo.")

        # Lo STATO DI PRIMA, letto dal grafo: la validazione conserva come il
        # valore era arrivato, e per conservarlo bisogna averlo letto.
        before = graph_writer.node(unit_id) or {}
        at = _now()
        marks: Dict[str, Any] = {}
        skipped: List[str] = []
        for field in wanted:
            said = authorship.read(before, field)
            if said["by"] != authorship.AI or said["validated"]:
                # Un campo che nessun modello ha composto non ha bisogno di
                # essere validato, e dirgli di sì sarebbe mettere una spunta
                # accanto a un'affermazione che nessuno ha messo in dubbio.
                skipped.append(field)
                continue
            marks[authorship.field_key(field)] = authorship.validated(
                said, by=author or "", at=at)

        if not marks:
            return ToolResult(
                ok=True,
                message=(f"Su US {number} non c'è niente da validare: "
                         f"{', '.join(skipped)} "
                         f"{'non è' if len(skipped) == 1 else 'non sono'} "
                         f"stat{'o' if len(skipped) == 1 else 'i'} "
                         f"compost{'o' if len(skipped) == 1 else 'i'} da un "
                         f"modello."),
                data={"us": number, "validated": [], "skipped": skipped})

        validated_names = [f for f in wanted if authorship.field_key(f) in marks]
        process = _process_node(
            "validate_field", author, unit_id,
            f"US {number}: {len(marks)} campi validati da una persona")
        try:
            graph_writer.update(unit_id, marks, author=author, process=process)
        except Exception as exc:                                 # noqa: BLE001
            return ToolResult(ok=False, message=str(exc))

        return ToolResult(
            ok=True,
            message=f"US {number}: {len(marks)} campi validati.",
            delta=GraphDelta(process=process, author=author),
            data={"us": number, "validated": validated_names,
                  "skipped": skipped})

    return ToolDescriptor(
        name="validate_field",
        intents=["valida", "confermo", "va bene così", "ho controllato",
                 "valida il campo", "confermo la scheda"],
        input_schema=[
            Slot("us", "string", True, "il numero dell'unità"),
            Slot("fields", "id", True, "i campi controllati"),
        ],
        description="Conferma i campi che un modello ha composto: "
                    "l'autorialità passa alla persona.",
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
    registry.register(make_update_su(graph_writer))
    registry.register(make_relate_su(graph_writer))
    registry.register(make_validate_field(graph_writer))
    registry.register(make_which_project(graph_writer))
    registry.register(make_attach_photo(graph_writer, asset_store))
    registry.register(make_ingest_photos(graph_writer, asset_store))
    registry.register(make_query_kg(graph_writer))
    registry.register(make_build_model(graph_writer, asset_store))
    registry.register(make_open_in_emstudio(graph_writer, asset_store))
    return registry
