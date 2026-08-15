"""ATRIUM (ARC) → the graph, through the contract.

ATRIUM is a web app where a field archaeologist dictates a **context sheet** and
edits the transcript; it exports CSV. Its fields — *Ctx Number*, *Description*,
*Interpretation*, *Recording* — are the shape below, and this adapter is the
whole of what connecting it takes.

**It does not import ATRIUM and ATRIUM does not import us.** It takes a dict in
their shape and produces slots in ours; the live REST integration is a
deployment matter with ARC. What is proved here is that when that call exists,
the thing on this side of it is thirty lines.

Two decisions worth naming:

* **the description and the interpretation stay apart.** ATRIUM records them as
  two fields because they are two acts — what is there, and what somebody thinks
  it means — and flattening them into one blob would lose the distinction that
  makes a sheet worth reviewing;
* **the recording is a RESOURCE, not a transcript pasted into a field.** The
  audio (or its transcript file) goes to the object store and the unit points at
  it, so the evidence for the sentence is one click from the sentence.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..contract import (GraphDelta, Slot, ToolDescriptor, ToolRegistry,
                        ToolResult, invoke)

#: The ATRIUM fields this adapter reads, and what each becomes. Written down
#: because a partner reading this file should see the mapping without running
#: it — and because a field they add later is a line here, not a rewrite.
FIELD_MAP = {
    "Ctx Number": "us",
    "Description": "description",
    "Interpretation": "interpretation",
    "Recording": "recording",
}


def _pick(sheet: Dict[str, Any], *names: str) -> Optional[Any]:
    """Read a field however ATRIUM happened to spell it in that export."""
    for name in names:
        for key in (name, name.lower(), name.replace(" ", "_").lower()):
            if key in sheet and sheet[key] not in (None, ""):
                return sheet[key]
    return None


def slots_from_sheet(sheet: Dict[str, Any]) -> Dict[str, Any]:
    """An ATRIUM sheet → the slots `create_su` declares.

    Nothing is invented: a sheet with no context number produces no `us`, and
    the contract then ASKS for it rather than guessing — which is the same
    refusal a person speaking into the microphone would get.
    """
    number = _pick(sheet, "Ctx Number", "ctx", "context")
    slots: Dict[str, Any] = {}
    if number not in (None, ""):
        slots["us"] = str(number).strip()
    description = _pick(sheet, "Description")
    interpretation = _pick(sheet, "Interpretation")
    if description:
        slots["description"] = str(description).strip()
    if interpretation:
        slots["interpretation"] = str(interpretation).strip()
    return slots


def ingest_sheet(registry: ToolRegistry, sheet: Dict[str, Any],
                 author: Optional[str], *,
                 recording: Optional[bytes] = None) -> List[ToolResult]:
    """One ATRIUM sheet → the acts it implies, in order.

    Returns one result per act, because a sheet can half-succeed (the unit is
    written, the recording is not) and a single boolean would hide which.
    """
    results: List[ToolResult] = []
    slots = slots_from_sheet(sheet)

    created = invoke(registry.route("create_su"), slots, author,
                     registry=registry)
    results.append(created)
    if not created.ok:
        return results          # no unit, nothing to attach to

    if recording:
        results.append(invoke(
            registry.route("attach_photo_to_su"),
            {"us": slots.get("us"), "photo": recording,
             "filename": f"ATRIUM · registrazione US {slots.get('us')}",
             "media_type": str(_pick(sheet, "Recording media type")
                               or "audio/mpeg")},
            author, registry=registry))
    return results


def register(registry: ToolRegistry, *, source: str = "ATRIUM") -> ToolDescriptor:
    """Plug ATRIUM in: one descriptor, one handler, and the core untouched."""

    def handler(slots: Dict[str, Any], author: Optional[str]) -> ToolResult:
        sheet = slots.get("sheet")
        if not isinstance(sheet, dict):
            return ToolResult(ok=False,
                              message="Non ho ricevuto una scheda ATRIUM.")
        results = ingest_sheet(registry, sheet, author,
                               recording=slots.get("recording"))
        nodes: List[Dict[str, Any]] = []
        edges: List[Dict[str, Any]] = []
        for one in results:
            nodes.extend(one.delta.nodes)
            edges.extend(one.delta.edges)
        ok = all(one.ok for one in results)
        return ToolResult(
            ok=ok,
            # The message is the LAST act's when something failed, because that
            # is the one the person has to do something about.
            message=(f"Scheda {source} acquisita: "
                     + "; ".join(one.message for one in results)),
            delta=GraphDelta(nodes=nodes, edges=edges, author=author),
            data={"acts": len(results),
                  "ok": [one.ok for one in results]})

    return registry.register(ToolDescriptor(
        name="ingest_atrium_sheet",
        intents=["acquisisci una scheda atrium", "scheda atrium",
                 "importa da atrium"],
        input_schema=[Slot("sheet", "id", True, "la scheda ATRIUM"),
                      Slot("recording", "bytes", False, "la registrazione")],
        description="Una scheda di contesto ATRIUM diventa una US nel grafo, "
                    "con la sua registrazione come risorsa.",
        service="rest", handler=handler))
