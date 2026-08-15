"""Transcript → intent + slots, with a rule fallback that always works.

Two parsers behind one function, and the order is the design:

1. **rules** — a small, explicit matcher over the intents the registry declares.
   Deterministic, instant, no model, and it is what the tests use. It is also
   what runs when the node has no model loaded, which on a dig is a Tuesday;
2. **the LLM seam** (N5) — the provider-neutral seam already built for the
   narrative AI-authoring: a local model on the Field Computing Node parses the
   sentence the rules could not. Provider-neutral because the node decides what
   it runs, and no call leaves the excavation.

The rules go **first**, not last. A field card's commands are a closed
vocabulary somebody designed on purpose ("crea una nuova scheda"), and asking a
model to interpret a phrase that matches exactly would be slower, less
predictable and occasionally wrong. The model is for the sentences a person
actually says when their hands are dirty.

**The registry is what it chooses from.** Both parsers are given the tools that
EXIST, so neither can route to a capability this node does not have — an
assistant confidently answering with a tool nobody installed is worse than one
saying it cannot.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol

from .contract import ToolRegistry


@dataclass
class Intent:
    """What was understood. `tool` is None when nothing was."""

    intent: str = ""
    tool: Optional[str] = None
    slots: Dict[str, Any] = field(default_factory=dict)
    #: Which parser answered — `rules` or `llm`. Reported because an operator
    #: debugging a field node needs to know whether the model was involved.
    via: str = "none"
    #: What was heard, kept so a caller can show it and a person can correct it.
    transcript: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {"intent": self.intent, "tool": self.tool, "slots": self.slots,
                "via": self.via, "transcript": self.transcript}


class IntentModel(Protocol):
    """The N5 seam, seen from here: a sentence and the available tools in, a
    `{intent, slots}` out. Anything that satisfies this is a provider."""

    def parse(self, transcript: str, tools: List[Dict[str, Any]]
              ) -> Optional[Dict[str, Any]]:
        ...


# ── the rules ────────────────────────────────────────────────────────────────
#
# Slot extraction is deliberately narrow: a unit NUMBER, because that is the one
# value the MVP commands carry. Widening it is adding a pattern, not rewriting a
# parser — and a pattern that grabbed too much would put the wrong number in a
# record.

#: "US 12", "unità 12", "la 12", "scheda numero 12" — and the bare digits at the
#: end of a sentence, which is what people actually say.
_US_PATTERNS = (
    re.compile(r"\bu\.?\s*s\.?\s*(\d{1,5})\b", re.I),
    re.compile(r"\bunit[aà]\s+(?:stratigrafica\s+)?(\d{1,5})\b", re.I),
    re.compile(r"\bscheda\s+(?:numero\s+)?(\d{1,5})\b", re.I),
    re.compile(r"\bnumero\s+(\d{1,5})\b", re.I),
    re.compile(r"\b(\d{1,5})\s*$"),
)


def extract_us(transcript: str) -> Optional[str]:
    """The unit number in a sentence, or None. Never a guess."""
    for pattern in _US_PATTERNS:
        found = pattern.search(transcript or "")
        if found:
            return found.group(1)
    return None


def _normalise(text: str) -> str:
    return re.sub(r"[^\w\s]", " ", (text or "").lower()).strip()


def rules_parse(transcript: str, registry: ToolRegistry) -> Optional[Intent]:
    """Match the transcript against the intents the registry declares.

    Longest declared intent first, so "crea una nuova scheda" wins over "scheda"
    — a shorter phrase contained in a longer one must not steal it.
    """
    said = _normalise(transcript)
    if not said:
        return None

    candidates: List[tuple] = []
    for descriptor in registry.list():
        for phrase in descriptor.intents:
            key = _normalise(phrase)
            if key and key in said:
                candidates.append((len(key), phrase, descriptor))
    if not candidates:
        return None
    candidates.sort(key=lambda c: -c[0])
    _, phrase, descriptor = candidates[0]

    slots: Dict[str, Any] = {}
    if any(s.name == "us" for s in descriptor.input_schema):
        number = extract_us(transcript)
        if number:
            slots["us"] = number
    return Intent(intent=phrase, tool=descriptor.name, slots=slots,
                  via="rules", transcript=transcript)


# ── the seam ─────────────────────────────────────────────────────────────────

def llm_parse(transcript: str, registry: ToolRegistry,
              model: Optional[IntentModel]) -> Optional[Intent]:
    """Ask the local model, and believe it only if it names a tool that EXISTS.

    The check is the point: a model that hallucinated `dig_trench` would
    otherwise produce a confident answer about a capability nobody installed.
    """
    if model is None:
        return None
    try:
        answer = model.parse(transcript, [d.as_dict() for d in registry.list()])
    except Exception:                              # noqa: BLE001
        # A model that fell over must not end the conversation: the rules
        # already had their turn, and "I did not understand" is a fine answer.
        return None
    if not isinstance(answer, dict):
        return None
    name = str(answer.get("tool") or answer.get("intent") or "")
    descriptor = registry.route(name)
    if descriptor is None:
        return None
    slots = answer.get("slots")
    return Intent(intent=name, tool=descriptor.name,
                  slots=dict(slots) if isinstance(slots, dict) else {},
                  via="llm", transcript=transcript)


def understand(transcript: str, registry: ToolRegistry, *,
               model: Optional[IntentModel] = None) -> Intent:
    """Rules first, the model second, and an honest nothing third."""
    found = rules_parse(transcript, registry)
    if found is not None:
        return found
    found = llm_parse(transcript, registry, model)
    if found is not None:
        return found
    return Intent(via="none", transcript=transcript)
