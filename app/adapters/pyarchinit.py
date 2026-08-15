"""PyArchInit → the graph, through the contract.

PyArchInit(-mini) is where a great many Italian excavations already record their
stratigraphy: a SQLite/PostGIS database with about twenty forms and a REST API.
It **owns** that record; this adapter does not try to take it over. What it does
is bring a US record into the shared graph so the interpretation, the photos and
the 3D can converge on it — §5's convergence, not a migration.

The record shape below is PyArchInit's `us_table` as its REST export gives it:
`sito`, `area`, `us`, `d_stratigrafica`, `d_interpretativa`, `descrizione`,
`interpretazione`, `periodo_iniziale`… The adapter reads the handful that map
onto a stratigraphic unit and **leaves the rest alone** — a field it does not
understand is not dropped silently, it is carried into the unit's data so
nothing is lost on the way in.

The key is `(sito, area, us)`, because a unit number is only unique inside its
area: two trenches both have a US 1, and merging them would be the worst kind of
silent data loss.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..contract import (Slot, ToolDescriptor, ToolRegistry, ToolResult, invoke)

#: What PyArchInit calls a thing → what EM calls it. Only the fields that have a
#: meaning here; everything else rides along in `extra`.
FIELD_MAP = {
    "us": "us",
    "d_stratigrafica": "definition",
    "d_interpretativa": "interpretation",
    "descrizione": "description",
    "interpretazione": "reading",
    "periodo_iniziale": "period_from",
    "periodo_finale": "period_to",
}

#: The fields that identify WHERE the unit is. A number alone does not.
KEY_FIELDS = ("sito", "area", "us")


def unit_key(record: Dict[str, Any]) -> str:
    """`sito/area/us` — because two trenches both have a US 1."""
    return "/".join(str(record.get(f) or "").strip() for f in KEY_FIELDS)


def slots_from_record(record: Dict[str, Any]) -> Dict[str, Any]:
    """A PyArchInit US record → the slots the contract declares.

    Everything the map does not name is kept under `extra`: a field this
    adapter does not understand is still somebody's data, and dropping it on
    the way in would make the graph a lossy copy of the database.
    """
    slots: Dict[str, Any] = {}
    for source, target in FIELD_MAP.items():
        value = record.get(source)
        if value not in (None, ""):
            slots[target] = str(value).strip()
    extra = {k: v for k, v in record.items()
             if k not in FIELD_MAP and k not in KEY_FIELDS
             and v not in (None, "")}
    if extra:
        slots["extra"] = extra
    for field in KEY_FIELDS:
        if record.get(field) not in (None, ""):
            slots[field] = str(record[field]).strip()
    return slots


def ingest_record(registry: ToolRegistry, record: Dict[str, Any],
                  author: Optional[str]) -> ToolResult:
    """One US record → one act, through the ordinary tool."""
    slots = slots_from_record(record)
    return invoke(registry.route("create_su"), slots, author,
                  registry=registry)


def register(registry: ToolRegistry) -> ToolDescriptor:
    """Plug PyArchInit in: a descriptor, a handler, the core untouched."""

    def handler(slots: Dict[str, Any], author: Optional[str]) -> ToolResult:
        records = slots.get("records")
        if isinstance(records, dict):
            records = [records]
        if not isinstance(records, list) or not records:
            return ToolResult(ok=False,
                              message="Non ho ricevuto nessun record PyArchInit.")
        results: List[ToolResult] = []
        for record in records:
            if not isinstance(record, dict):
                continue
            results.append(ingest_record(registry, record, author))
        if not results:
            return ToolResult(ok=False, message="Nessun record leggibile.")
        from ..contract import GraphDelta
        nodes: List[Dict[str, Any]] = []
        edges: List[Dict[str, Any]] = []
        for one in results:
            nodes.extend(one.delta.nodes)
            edges.extend(one.delta.edges)
        written = sum(1 for one in results if one.ok)
        return ToolResult(
            ok=written > 0,
            message=(f"Da PyArchInit: {written} unità su {len(results)}."
                     if written != len(results)
                     else f"Da PyArchInit: {written} unità."),
            delta=GraphDelta(nodes=nodes, edges=edges, author=author),
            data={"records": len(results), "written": written,
                  "keys": [unit_key(r) for r in records if isinstance(r, dict)]})

    return registry.register(ToolDescriptor(
        name="ingest_pyarchinit",
        intents=["importa da pyarchinit", "prendi le us da pyarchinit",
                 "record pyarchinit"],
        input_schema=[Slot("records", "id", True, "i record US")],
        description="Un record US di PyArchInit diventa un'unità nel grafo "
                    "condiviso, senza togliere a PyArchInit il suo ruolo.",
        service="rest", handler=handler))
