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

import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol

from .contract import ToolRegistry


log = logging.getLogger("stratigraph-chatbot.intent")


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


#: TWO units and the verb between them — «la 12 copre la 18».
#:
#: The slot extraction above is deliberately narrow (a unit NUMBER), and this is
#: the widening the note there anticipated: *«widening it is adding a pattern,
#: not rewriting a parser»*. It is added because a rapporto stratigrafico was
#: the one thing a person says all day and this service could not hear — found
#: by marking the ICCD sheet in `stratigraph-templates`, where the ten
#: relationship boxes had to stay `unknown` for want of an intent here.
#:
#: The verb is looked up against `tools.RELATIONS`, which is the map measured
#: against the connections datamodel. **The lookup is not done here**: this
#: returns what was SAID, and the tool decides what it means. A parser that
#: also decided the edge type would be a second place where a relationship's
#: meaning lives.
_RELATION_PATTERN = re.compile(r"(\d{1,5})\s+([^\d]+?)\s*(\d{1,5})")


def extract_relation(transcript: str) -> Optional[Dict[str, str]]:
    """`{us, relation, other}` from a sentence, or None. Never a guess.

    The middle of the sentence is matched against the KNOWN verbs, longest
    first, so «si appoggia a» wins over «appoggia a» and a phrase that contains
    no verb we know returns None rather than the first number and a shrug.

    Longest-first matters for the inverses too: «coperta dalla 18» contains
    «coperta da», and that must win over nothing at all — an inverse recorded
    as a forward relation would put the arrow the wrong way round, which is the
    one error in stratigraphy that changes the sequence.
    """
    from .tools import RELATIONS

    said = _normalise(transcript)
    found = _RELATION_PATTERN.search(said)
    if not found:
        return None
    left, middle, right = found.group(1), found.group(2), found.group(3)
    for verb in sorted(RELATIONS, key=len, reverse=True):
        if verb in middle:
            return {"us": left, "relation": verb, "other": right}
    return None


#: UN CAMPO DETTO A VOCE — «la definizione è strato di crollo».
#:
#: La terza estensione dell'estrazione, dopo il numero di unità e la coppia di un
#: rapporto, e l'ultima che il commento in cima prevedeva: *«widening it is
#: adding a pattern, not rewriting a parser»*.
#:
#: Il VALORE è ciò che segue la frase, verbatim: non si normalizza, non si
#: incolonna in un vocabolario, non si corregge. Una parola detta in trincea è
#: quella che la persona ha detto — e se un giorno passerà da un modello, sarà
#: quel modello a doverlo dichiarare (`authored_by`), non questo parser a
#: deciderlo di nascosto.
#: IL VALORE SI PRENDE DAL TRASCRITTO GREZZO, non da quello normalizzato.
#:
#: `_normalise` toglie la punteggiatura — serve a far combaciare le frasi — e su
#: un valore la distrugge: «quota 145,30» diventava «145 30» e «2 per 1,5 metri»
#: diventava «2 per 1 5 metri». Su una quota e su una misura la virgola È il
#: dato, e questi sono precisamente i due campi che questa serata aggiunge.
#:
#: Quindi le frasi si cercano con una regex sul testo COME È STATO DETTO, e il
#: valore è la coda, verbatim.
_SPOKEN_JOIN = r"(?:\s+d\w+)?(?:\s+(?:la|il|lo))?(?:\s+u\.?\s*s\.?\s*\d{1,5})?(?:\s+(?:è|e|sono|:))?"


def _spoken_patterns():
    """Le frasi del vocabolario, compilate. Costruite dalla mappa e non scritte
    a mano: due elenchi divergono, ed è già successo."""
    from .tools import SPOKEN_FIELDS

    out = []
    for field, phrases in SPOKEN_FIELDS.items():
        for phrase in sorted(phrases, key=len, reverse=True):
            words = r"\s+".join(re.escape(w) for w in phrase.split())
            out.append((len(phrase), field, re.compile(
                words + _SPOKEN_JOIN + r"\s+(?P<value>\S.*?)\s*$", re.I)))
    out.sort(key=lambda item: -item[0])
    return out


#: Le parole che UNISCONO il nome del campo al suo valore. Da sole non sono un
#: valore, e la prima versione le prendeva per tale.
_CONNECTORS = frozenset({"e", "è", "sono", "la", "il", "lo", "di", "del",
                         "della", "dello"})

_SPOKEN = None


def extract_spoken_field(transcript: str) -> Optional[Dict[str, str]]:
    """`{field, value}` da una frase, o None. Mai un'invenzione.

    Le frasi si provano dalla PIÙ LUNGA: «la definizione è» deve battere
    «definizione». E una frase riconosciuta senza niente dopo torna None —
    «definizione» detto da solo è una domanda, non una scrittura.
    """
    global _SPOKEN
    if _SPOKEN is None:
        _SPOKEN = _spoken_patterns()
    said = (transcript or "").strip()
    if not said:
        return None
    for _length, field, pattern in _SPOKEN:
        found = pattern.search(said)
        if found:
            value = found.group("value").strip(" .,;:")
            # UNA CONGIUNZIONE NON È UN VALORE. «la quota è», detto e basta,
            # faceva combaciare «quota» e prendeva «è» come valore — scriveva
            # `data.quote = "è"` nel grafo di qualcuno. Una frase riconosciuta
            # senza niente dopo è una domanda, non una scrittura.
            if value and _normalise(value) not in _CONNECTORS:
                return {"field": field, "value": value}
    return None


#: The language the rules — and therefore this NODE — actually understand.
#:
#: The intent vocabulary is a phrasebook somebody designed on purpose ("crea una
#: nuova scheda", "questa foto è per la US"), and it is Italian. That is a fact
#: about this node, not a default to be embarrassed about; what WOULD be wrong is
#: leaving it unsaid, because then a page localised into Polish shows a Polish
#: example of a command this node would refuse — a lie in the one place where
#: somebody is standing in a trench with their hands full.
#:
#: So it is DECLARED, and `/v1/tools` and `/health` publish it, the way the node
#: already publishes which speech engine and which tools it has. The page's
#: chrome follows the reader; the conversation follows the node.
#:
#: Teaching a node a second command language means a second phrasebook beside
#: the tool descriptors, and it belongs to the node. It is not a page's to fake.
COMMAND_LANGUAGE = "it"


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
    declared = {s.name for s in descriptor.input_schema}
    # Dispatched on the slots the descriptor DECLARES, not on the tool's name:
    # a tool that wants two units and a verb says so in its schema, and the
    # parser never learns a name. Same shape as the `us` line below it, which
    # has worked that way since the MVP.
    if {"other", "relation"} <= declared:
        pair = extract_relation(transcript)
        if pair:
            slots.update(pair)
    # Un campo detto a voce riempie lo slot `fields`, che è quello che
    # `update_su` prende — nessun tool nuovo per una cosa che un tool fa già.
    if "fields" in declared and "fields" not in slots:
        spoken = extract_spoken_field(transcript)
        if spoken:
            slots["fields"] = {spoken["field"]: spoken["value"]}
    if "us" in declared and "us" not in slots:
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


# ── the model, and the lever that turns it on ────────────────────────────────
#
# The seam above (`IntentModel`) has existed since the MVP and `llm_parse` was
# careful from the start. What was missing was the LEVER: `/health` declared
# `intent_model` as a boolean and nothing could set it. A capability that
# declares itself but cannot be configured is a promise with no switch — the one
# place where E.D.'s rule was not carried through:
#
#   > If the node has an AI you have functions; if it does not, you do not — and
#   > the surface says so.
#
# So this follows `speech.py` gesture for gesture: config-gated, never chosen in
# silence, **a half-configuration refuses with a sentence** instead of falling
# back, and nothing heavy is imported at module load — a node that never uses a
# model must not carry its dependencies.
#
# WHICH PROVIDER. The seam is provider-neutral on purpose (the node decides what
# it runs), and what is implemented here is the one road a Field Computing Node
# can actually walk offline: **an OpenAI-compatible local endpoint**, which covers
# Ollama and llama.cpp — and llamafile, and vLLM — without tying us to any of
# them. Anything else is refused BY NAME, saying what this build cannot do,
# rather than pretending.
#
# AND THERE IS NO DEFAULT ENDPOINT. That is not an omission: a default would be
# somebody's cloud, and design note §5 forbids exactly that — «an excavation's
# audio leaving for a third-party provider because the local model was not there
# is an incident, not a fallback». A model with no endpoint refuses.

#: `<provider>:<model>` — e.g. `openai:llama3.2`. The bare `<model>` is accepted
#: and means `openai:`, because that is the only provider this build speaks and
#: an operator should not have to write a word that has one value.
INTENT_MODEL_VAR = "EM_CHATBOT_INTENT_MODEL"
#: Where that model answers. Required, and deliberately without a default.
INTENT_ENDPOINT_VAR = "EM_CHATBOT_INTENT_ENDPOINT"
#: How long one intent call may take. Short: this sits between a person speaking
#: and a card appearing, and the rules have already had their turn — a model that
#: needs longer than this has effectively not answered.
INTENT_TIMEOUT_VAR = "EM_CHATBOT_INTENT_TIMEOUT"

#: The providers this build can actually speak. One, and it is a family rather
#: than a vendor.
PROVIDERS = ("openai",)

#: What the model is told. Short and closed on purpose: it must choose among the
#: tools the registry declares and answer JSON, and `llm_parse` refuses anything
#: that names a tool which does not exist — so a hallucination costs a shrug and
#: not a wrong record.
SYSTEM_PROMPT = (
    "You route a field archaeologist's dictated sentence to one of the tools "
    "listed below. Answer with JSON only: "
    '{"tool": "<one of the tool names>", "slots": {...}} '
    "— or {\"tool\": null} when none of them fits. Never invent a tool name. "
    "The slots you may fill are the ones each tool declares."
)


class IntentModelError(RuntimeError):
    """The configuration is half-done. Raised at STARTUP, where somebody is
    watching, and never converted into a quiet fallback."""


def _is_local(endpoint: str) -> bool:
    """Is this endpoint on the node, or on its LAN?

    Design note §5: nothing may silently leave the excavation. This does not
    decide anything by itself — it decides what the node SAYS (loudly, at
    startup and in what it publishes), because refusing outright is E.D.'s call
    and not this module's. See the report of 2026-09-02.

    Conservative on purpose: when the host cannot be read, the answer is «not
    local». A wrong «local» would silence exactly the warning that matters.
    """
    import ipaddress
    import urllib.parse

    host = (urllib.parse.urlsplit(endpoint).hostname or "").strip().lower()
    if not host:
        return False
    if host in ("localhost", "127.0.0.1", "::1") or host.endswith(".local"):
        return True
    # a bare hostname with no dots is a LAN name (the node's own `fcn`, say)
    if "." not in host and ":" not in host:
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False          # a name that resolves somewhere: not our business
    return bool(address.is_loopback or address.is_private or address.is_link_local)


class OpenAICompatibleIntentModel:
    """An intent router over a local OpenAI-compatible `/chat/completions`.

    `urllib` and not a client library: this is one POST with a JSON body, and a
    dependency for that would be a dependency a node carries whether or not it
    ever loads a model. Imported inside the call for the same reason.
    """

    provider = "openai"

    def __init__(self, model: str, endpoint: str, timeout: float = 8.0):
        self.model = model
        self.endpoint = endpoint.rstrip("/")
        self.timeout = timeout

    @property
    def local(self) -> bool:
        return _is_local(self.endpoint)

    def describe(self) -> str:
        where = self.endpoint if self.local else f"{self.endpoint} — NOT LOCAL"
        return f"openai-compatible ({self.model} @ {where})"

    def parse(self, transcript: str, tools: List[Dict[str, Any]]
              ) -> Optional[Dict[str, Any]]:
        """A sentence and the available tools in, `{tool, slots}` out or None.

        Every failure returns None rather than raising: `llm_parse` already
        treats a model that fell over as «I did not understand», and the rules
        have had their turn. What must NOT happen here is an exception that ends
        somebody's dictation.
        """
        import json
        import urllib.error
        import urllib.request

        catalogue = [
            {"name": tool.get("name"), "intents": tool.get("intents"),
             "slots": tool.get("slots")}
            for tool in tools
        ]
        body = json.dumps({
            "model": self.model,
            "temperature": 0,
            "messages": [
                {"role": "system",
                 "content": SYSTEM_PROMPT + "\n\nTools:\n"
                            + json.dumps(catalogue, ensure_ascii=False)},
                {"role": "user", "content": transcript},
            ],
        }).encode("utf-8")
        request = urllib.request.Request(
            f"{self.endpoint}/chat/completions", data=body,
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as answer:
                payload = json.loads(answer.read())
        except (urllib.error.URLError, OSError, ValueError) as exc:
            log.warning("intent model at %s did not answer (%s) — the rules had "
                        "their turn and the assistant will say it did not "
                        "understand", self.endpoint, exc)
            return None
        try:
            content = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            log.warning("intent model at %s answered a shape this build does "
                        "not know", self.endpoint)
            return None
        # A local model often wraps its JSON in prose or a fence. Reading the
        # first `{...}` is not sloppiness: refusing an otherwise good answer
        # because it arrived inside a code fence would make the capability look
        # broken on half the models an operator might install.
        text = str(content).strip()
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            return json.loads(text[start:end + 1])
        except ValueError:
            return None


def intent_model_from_env(environ: Optional[Dict[str, str]] = None
                          ) -> Optional[IntentModel]:
    """The model when the node names one, `None` otherwise.

    Raises :class:`IntentModelError` on a HALF configuration — a model with no
    endpoint, an endpoint with no model, an unknown provider — because falling
    back would leave an operator believing dictation is being interpreted on
    their node when it is not. Same refusal `speech.py` makes, and same reason.
    """
    env = dict(environ if environ is not None else os.environ)
    spec = (env.get(INTENT_MODEL_VAR) or "").strip()
    endpoint = (env.get(INTENT_ENDPOINT_VAR) or "").strip()

    if not spec:
        if endpoint:
            raise IntentModelError(
                f"{INTENT_ENDPOINT_VAR} is set but {INTENT_MODEL_VAR} is not, so "
                f"this node names a place and no model. Refusing to start rather "
                f"than running with the rules only while somebody believes a "
                f"model is answering. Set {INTENT_MODEL_VAR} "
                f"(e.g. openai:llama3.2), or unset both.")
        return None

    provider, _, model = spec.partition(":")
    if not model:
        provider, model = "openai", provider     # a bare model name
    provider = provider.strip().lower()
    model = model.strip()
    if provider not in PROVIDERS:
        raise IntentModelError(
            f"{INTENT_MODEL_VAR}={spec!r} names the provider {provider!r}, which "
            f"this build cannot speak. It speaks: {', '.join(PROVIDERS)} — an "
            f"OpenAI-compatible endpoint, which is what Ollama, llama.cpp, "
            f"llamafile and vLLM all expose. Saying so beats pretending.")
    if not model:
        raise IntentModelError(
            f"{INTENT_MODEL_VAR}={spec!r} names no model.")
    if not endpoint:
        raise IntentModelError(
            f"{INTENT_MODEL_VAR} names {model!r} but {INTENT_ENDPOINT_VAR} is "
            f"not set, and there is NO DEFAULT — a default would be somebody's "
            f"cloud, and an excavation's words must not leave it because a "
            f"variable was missing (design note §5). Point it at the model on "
            f"this node, e.g. http://127.0.0.1:11434/v1")

    timeout = 8.0
    raw_timeout = (env.get(INTENT_TIMEOUT_VAR) or "").strip()
    if raw_timeout:
        try:
            timeout = float(raw_timeout)
        except ValueError:
            raise IntentModelError(
                f"{INTENT_TIMEOUT_VAR}={raw_timeout!r} is not a number of "
                f"seconds.") from None

    engine = OpenAICompatibleIntentModel(model, endpoint, timeout)
    if engine.local:
        log.info("intent: %s", engine.describe())
    else:
        # LOUD, and in two places (here and what the node publishes): this is the
        # one configuration that can send an excavation's words off the site.
        log.warning(
            "intent: the model endpoint %s is NOT local (not loopback, not a "
            "private address, not a .local name). Every sentence somebody "
            "dictates will leave this node. Design note §5 calls that an "
            "incident, not a fallback — if it is deliberate, it is at least now "
            "on the record.", endpoint)
    return engine


def describe(model: Optional[IntentModel]) -> str:
    """For `/health`: WHICH engine is interpreting, not whether one is.

    A boolean was not enough (design note §4): that string is provenance, and a
    datum without the name of the engine that produced it is a datum nobody can
    argue about later.

    WHAT THIS NO LONGER SAYS, and why. It used to name the two variables here
    too, because when it was written the node published them nowhere else. It
    does now — `capabilities[].missing`, which the room server forwards and the
    node's front door renders — and carrying them in both places made the front
    door read them out twice, once inside this sentence and once under «needs».
    Measured on screen on 2026-09-02.

    So the division is: this says WHICH ENGINE, `missing` says WHAT WOULD
    CONFIGURE ONE. One fact, one field.
    """
    if model is None:
        return "rules only"
    described = getattr(model, "describe", None)
    if callable(described):
        return str(described())
    return type(model).__name__
