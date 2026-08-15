"""THE TOOL CONTRACT — the base everything else plugs into.

This is the centre of gravity of the whole assistant (design note §10), and the
one file to get right. Everything above it (speech, intent, the web app) and
everything below it (s3Dgraphy, the object store, a partner's REST API) is
replaceable. This is not: it is the shape that lets a partner add a capability
by **writing a descriptor and a thin adapter** instead of re-architecting
anything.

    a tool declares WHAT IT ANSWERS, WHAT IT NEEDS, and WHAT IT CHANGES.

Three parts, and each is a decision:

**`ToolDescriptor`** — the declaration. `intents` are the phrases the router may
map to it; `input_schema` are the slots the intent has to fill; `handler` is the
service that does the work — a library call, a REST endpoint, a local function.
One tool = one service. A descriptor that did two things would be a small
framework, and the point of the contract is that there is no framework.

**`ToolRegistry`** — the plug board. `register` / `list` / `route`. Nothing more,
because a registry that also decided, retried or transformed would become the
place where behaviour hides.

**`invoke`** — the act. It runs the handler and returns a `ToolResult` carrying
two things that are NOT the same and are both required:

* a **graph delta**, DTC-attributed (`crmdig:D7`, author = the ORCID of the
  token), because a field assistant that wrote unattributed data would produce a
  record nobody can defend;
* a **spoken message**, because the person hearing it has their hands in the
  soil and cannot read a JSON response.

**What this file will not do.** It will not invent an author (an author comes
from a verified token, or the invocation is refused), it will not swallow a
failure (a tool that could not act says so, and the message says it in words),
and it will not guess an intent (an unrecognised one is a clean *I cannot do
that*, never an exception and never a nearest-match).

Offline-first, on the Field Computing Node: nothing here calls out, and nothing
here needs a network to be tested.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Protocol

#: The namespace for deterministic ids minted by a tool invocation. Shared with
#: the command channel's `cmd_id` reasoning: the same act asked twice is the same
#: act, and a random id would make idempotence impossible to even define.
TOOL_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL,
                            "https://w3id.org/stratigraph/chatbot/tool")


def stable_id(*parts: str) -> str:
    """A deterministic id from what the act is ABOUT, not from when it ran."""
    return str(uuid.uuid5(TOOL_NAMESPACE, "|".join(str(p) for p in parts)))


# ── what a tool changes ──────────────────────────────────────────────────────

@dataclass
class GraphDelta:
    """What a tool did to the graph — the DTC-attributed transformation.

    A delta, not a whole graph: the assistant writes into a **shared room** where
    other people are working, and handing back a document would mean deciding
    what happened to their edits. The delta is applied by whoever owns the graph
    (the room, or the local container when the node is offline).

    `author` is an ORCID and it is not optional in practice — `invoke` refuses to
    run a writing tool without one. It is typed as optional only so a read-only
    tool (`which_project`, `query_kg`) can return an empty delta without
    pretending somebody authored a question.
    """

    nodes: List[Dict[str, Any]] = field(default_factory=list)
    edges: List[Dict[str, Any]] = field(default_factory=list)
    #: The ORCID the write is attributed to. The identity of the TOKEN, never a
    #: field the client filled in.
    author: Optional[str] = None
    #: The `crmdig:D7` process node that records the act — what was made, by
    #: whom, when, from what. Part of the delta, not a side channel.
    process: Optional[Dict[str, Any]] = None

    @property
    def writes(self) -> bool:
        return bool(self.nodes or self.edges)

    def as_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"nodes": self.nodes, "edges": self.edges}
        if self.author:
            out["author"] = self.author
        if self.process:
            out["process"] = self.process
        return out


@dataclass
class ToolResult:
    """What came back: whether it worked, what changed, and what to SAY.

    `message` is the field the field archaeologist actually receives — read out
    loud by the device. It is required even on failure, and especially then: a
    tool that failed silently would leave somebody believing a unit was recorded.
    """

    ok: bool
    message: str
    delta: GraphDelta = field(default_factory=GraphDelta)
    #: Free-form, for a caller that wants more than the sentence (the PWA shows
    #: the new US number; a test asserts on it).
    data: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {"ok": self.ok, "message": self.message,
                "delta": self.delta.as_dict(), "data": self.data}


# ── what a tool IS ───────────────────────────────────────────────────────────

class ToolHandler(Protocol):
    """The service behind a tool.

    Takes the filled slots and the author, returns a result. Everything a
    handler needs beyond that — a graph, a store, an HTTP client — it closes
    over, because the contract must not grow a dependency-injection scheme: the
    moment it does, a partner writing an adapter has to learn ours.
    """

    def __call__(self, slots: Dict[str, Any], author: Optional[str]) -> ToolResult:
        ...


@dataclass
class Slot:
    """One thing a tool needs before it can act.

    `required` is what makes a conversation possible: the router can see that
    `create_su` wants a number, and ask for it, instead of failing.
    """

    name: str
    kind: str = "string"        # string · number · bytes · id
    required: bool = True
    description: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "kind": self.kind,
                "required": self.required, "description": self.description}


@dataclass
class ToolDescriptor:
    """A tool, declared.

    This is the whole interoperability surface. A partner adds ATRIUM,
    PyArchInit, a shape recogniser or a 3D handoff by filling this in and
    writing a handler — nothing in the assistant changes.
    """

    name: str
    #: The phrases and labels the router may map to this tool. Written in the
    #: language of the field card, because that card IS the specification.
    intents: List[str]
    input_schema: List[Slot] = field(default_factory=list)
    #: What comes back, in one phrase — for `/health` and for a partner reading
    #: the registry to see what exists.
    output: str = "graph-delta"
    handler: Optional[ToolHandler] = None
    description: str = ""
    #: Which kind of service is behind it. Declared rather than inferred: an
    #: operator debugging a field node wants to know whether a failure is ours
    #: or somebody's endpoint.
    service: str = "local"      # local · s3dgraphy · rest · mcp
    #: False for a tool that only reads. `invoke` refuses to run a WRITING tool
    #: without an author; a question needs no attribution.
    writes: bool = True

    def as_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "intents": list(self.intents),
            "input_schema": [s.as_dict() for s in self.input_schema],
            "output": self.output,
            "description": self.description,
            "service": self.service,
            "writes": self.writes,
        }

    def missing_slots(self, slots: Dict[str, Any]) -> List[str]:
        """Which required slots the intent did not fill.

        Reported rather than defaulted. A field assistant that invented a unit
        number because it did not hear one would put a wrong number in a record
        that outlives the excavation.
        """
        return [s.name for s in self.input_schema
                if s.required and slots.get(s.name) in (None, "", [])]


# ── the plug board ───────────────────────────────────────────────────────────

class ToolRegistry:
    """Register, list, route. Deliberately nothing else."""

    def __init__(self) -> None:
        self._tools: Dict[str, ToolDescriptor] = {}

    def register(self, descriptor: ToolDescriptor) -> ToolDescriptor:
        """Add a tool. Registering the same NAME twice is an error, not a
        silent replacement: on a field node, a tool quietly shadowed by a
        partner's adapter is a bug nobody can see."""
        if not descriptor.name:
            raise ValueError("a tool descriptor needs a name")
        if descriptor.name in self._tools:
            raise ValueError(
                f"a tool named {descriptor.name!r} is already registered — "
                f"replacing it silently would hide whichever one loses")
        self._tools[descriptor.name] = descriptor
        return descriptor

    def list(self) -> List[ToolDescriptor]:
        """Every tool, by name. The registry IS the documentation."""
        return [self._tools[name] for name in sorted(self._tools)]

    def get(self, name: str) -> Optional[ToolDescriptor]:
        return self._tools.get(name)

    def route(self, intent: str) -> Optional[ToolDescriptor]:
        """The tool that answers this intent, or **None**.

        None, not an exception and not a nearest match. "I cannot do that" is a
        perfectly good answer for an assistant to give, and a fuzzy match would
        occasionally act on a sentence nobody meant — which, on a dig, means a
        wrong record in the graph.

        Matching is on the tool's declared intents and on its own name, both
        case-insensitively: a caller that already knows the tool it wants
        should not have to look up a synonym.
        """
        wanted = (intent or "").strip().lower()
        if not wanted:
            return None
        for descriptor in self._tools.values():
            if descriptor.name.lower() == wanted:
                return descriptor
            if any(i.strip().lower() == wanted for i in descriptor.intents):
                return descriptor
        return None

    def intents(self) -> Dict[str, str]:
        """intent → tool name. What the intent parser is given so it can choose
        from what EXISTS rather than from what a model remembers."""
        out: Dict[str, str] = {}
        for descriptor in self._tools.values():
            for intent in descriptor.intents:
                out.setdefault(intent.strip().lower(), descriptor.name)
        return out


# ── the act ──────────────────────────────────────────────────────────────────

#: The message an assistant gives when it did not understand. One sentence,
#: honest, and it names what it CAN do — because "I don't know" with no way
#: forward is the answer that makes people stop using a tool.
UNKNOWN_INTENT = "Non so fare questa cosa."


def invoke(descriptor: Optional[ToolDescriptor], slots: Dict[str, Any],
           author: Optional[str], *, registry: Optional[ToolRegistry] = None
           ) -> ToolResult:
    """Run a tool and come back with something sayable.

    Four refusals before the handler is reached, and each is a decision about
    what a field assistant must never do:

    1. **no tool** → *I cannot do that*, with what it can do. Not an exception:
       failing to understand is a normal turn in a conversation;
    2. **no handler** → the tool is declared but not wired. Said plainly, because
       a descriptor without an adapter is a real state during integration and
       pretending otherwise wastes a partner's afternoon;
    3. **missing slots** → named. The assistant asks for the unit number rather
       than inventing one;
    4. **a writing tool with no author** → refused. Everything this assistant
       writes is attributed to a verifiable person; an unattributed record is
       one nobody can defend later, and that is the whole reason ORCID is in
       the design.

    A handler that raises is caught and reported: on a dig, one failing tool must
    not take the assistant down.
    """
    if descriptor is None:
        known = ""
        if registry is not None:
            names = [d.name for d in registry.list()]
            if names:
                known = " So fare: " + ", ".join(names) + "."
        return ToolResult(ok=False, message=UNKNOWN_INTENT + known)

    if descriptor.handler is None:
        return ToolResult(
            ok=False,
            message=f"Lo strumento «{descriptor.name}» è dichiarato ma non "
                    f"ancora collegato a un servizio.",
            data={"tool": descriptor.name, "reason": "no-handler"})

    missing = descriptor.missing_slots(slots)
    if missing:
        return ToolResult(
            ok=False,
            message="Mi manca " + ", ".join(missing) + ".",
            data={"tool": descriptor.name, "missing": missing})

    if descriptor.writes and not author:
        return ToolResult(
            ok=False,
            message="Non posso scrivere senza sapere chi sei: serve "
                    "un'identità verificata.",
            data={"tool": descriptor.name, "reason": "no-author"})

    try:
        result = descriptor.handler(slots, author)
    except Exception as exc:                       # noqa: BLE001 — see docstring
        return ToolResult(
            ok=False,
            message=f"«{descriptor.name}» non è riuscito: {exc}",
            data={"tool": descriptor.name, "reason": "handler-failed",
                  "error": str(exc)})

    # The author is stamped HERE, on the way out, and not left to the handler.
    # A handler that forgot would produce an unattributed write, and this is the
    # one place that sees every write.
    if result.ok and result.delta.writes and not result.delta.author:
        result.delta.author = author
    result.data.setdefault("tool", descriptor.name)
    return result
