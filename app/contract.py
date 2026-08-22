"""THE TOOL CONTRACT — the chatbot's BINDING to the shared one.

This file used to hold the contract itself (design note §10). It no longer does,
and the reason is the whole point of that note: *a tool declares what it answers,
what it needs, and what it changes* turned out to be the same shape a
**connector** needs — EMtools in Blender, a Heriverse viewer, a Tropy import — so
the shape now lives in one place, **`s3dgraphy.contract.core`**, where the long
rationale lives too. Two consumers specialise it and neither owns it: the study's
side has `ConnectorRegistry`, the field assistant has `ToolRegistry`. Keeping a
second copy here would have produced two contracts that agree today and diverge
on the first refusal somebody adds to one of them.

So what remains here is a **binding**, and it is deliberately three things and
nothing else:

* the chatbot's **names** for the one shape (`ToolDescriptor`, `ToolRegistry`,
  `ToolResult`, `GraphDelta`, `ToolHandler`, `Slot`) — they are the same objects,
  not subclasses, which is what makes the fork gone rather than hidden;
* the chatbot's **words**. The four refusals are the contract's; the sentences are
  the consumer's, and this consumer answers an archaeologist out loud, in Italian,
  with their hands in the soil. They are passed to the shared `invoke` as a
  `Refusals` instance — never re-hardcoded in a second copy of the function;
* the chatbot's **id namespace**. `stable_id` keeps minting under
  `TOOL_NAMESPACE`, so nothing already coined changes.

Offline-first, on the Field Computing Node: nothing here calls out, and nothing
here needs a network to be tested.
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, Optional

try:
    from s3dgraphy.contract.core import (  # noqa: F401  (re-exported below)
        Delta, Descriptor, Handler, Refusals, Registry, Result, Slot,
        invoke as _core_invoke, stable_id as _core_stable_id)
except ImportError as exc:                                 # pragma: no cover
    # Said in one sentence, at import time, because a version floor cannot
    # express "dev14 plus two commits": the contract landed inside the 1.6.0.dev14
    # line, so a wheel that satisfies the pin may still not carry it. Better a
    # sentence naming the fix than an AttributeError three frames deep.
    raise ImportError(
        "this s3dgraphy has no `s3dgraphy.contract.core`: the chatbot's tool "
        "contract is now a binding to the shared one. Install s3Dgraphy from a "
        "build that carries `s3dgraphy.contract` (the local checkout, or the "
        "first release after 1.6.0.dev14)."
    ) from exc


# ── the chatbot's names for the one shape ────────────────────────────────────
#
# Aliases, not subclasses. `ToolDescriptor is Descriptor` — a test asserts it,
# because a subclass would be a fork that had learnt to look like a binding.

ToolDescriptor = Descriptor
ToolRegistry = Registry
ToolResult = Result
GraphDelta = Delta
ToolHandler = Handler
# `Slot` and `Refusals` travel under their own names: they already read right
# from a field-assistant call site.


# ── the chatbot's ids ────────────────────────────────────────────────────────

#: The namespace for deterministic ids minted by a tool invocation. Shared with
#: the command channel's `cmd_id` reasoning: the same act asked twice is the same
#: act, and a random id would make idempotence impossible to even define. It stays
#: the CHATBOT's namespace — the core has its own, and switching would silently
#: re-mint every id this assistant has ever produced.
TOOL_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL,
                            "https://w3id.org/stratigraph/chatbot/tool")


def stable_id(*parts: str) -> str:
    """A deterministic id from what the act is ABOUT, not from when it ran."""
    return _core_stable_id(*parts, namespace=TOOL_NAMESPACE)


# ── the chatbot's words ──────────────────────────────────────────────────────

#: The message an assistant gives when it did not understand. One sentence,
#: honest, and it names what it CAN do — because "I don't know" with no way
#: forward is the answer that makes people stop using a tool.
UNKNOWN_INTENT = "Non so fare questa cosa."

#: The four refusals, in the language the person on the dig speaks. The refusals
#: themselves are the contract's (they happen in one place, before any handler is
#: reached, which is the only way "no write without an author" can be true of a
#: system rather than of a diligent adapter); these are the sentences.
REFUSALS = Refusals(
    unknown=UNKNOWN_INTENT,
    known_prefix=" So fare: ",
    no_handler="Lo strumento «{name}» è dichiarato ma non ancora collegato "
               "a un servizio.",
    missing="Mi manca {slots}.",
    no_author="Non posso scrivere senza sapere chi sei: serve "
              "un'identità verificata.",
    failed="«{name}» non è riuscito: {error}",
)


def invoke(descriptor: Optional[ToolDescriptor], slots: Dict[str, Any],
           author: Optional[str], *, registry: Optional[ToolRegistry] = None,
           refusals: Refusals = REFUSALS) -> ToolResult:
    """Run a tool and come back with something sayable.

    The shared invocation, with this consumer's sentences. The four refusals, the
    author stamped on the way out and the handler's exception caught all live in
    `s3dgraphy.contract.core.invoke` — one place that sees every act.

    The only thing added here is the chatbot's own word for a fact the core
    records under its own: the core files the acting op under `data["op"]`, this
    assistant has always called it `data["tool"]`, and `/say` hands `data`
    verbatim to a device somebody is holding. Both keys, so no client outside
    this repository has to be found and changed.
    """
    result = _core_invoke(descriptor, slots, author, registry=registry,
                         refusals=refusals)
    if "op" in result.data:
        result.data.setdefault("tool", result.data["op"])
    return result


__all__ = ["GraphDelta", "Refusals", "REFUSALS", "Slot", "ToolDescriptor",
           "ToolHandler", "ToolRegistry", "ToolResult", "TOOL_NAMESPACE",
           "UNKNOWN_INTENT", "invoke", "stable_id"]
