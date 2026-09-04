"""stratigraph-chatbot — the field assistant, as a service.

*An orchestrator.* Voice or text comes in, an intent comes out, a tool acts, and
what changes is a **DTC-attributed write on the shared graph**. It does not
rebuild the pieces that exist: ARC's ATRIUM captures voice, Whisper transcribes
on the Field Computing Node, PyArchInit and iDAI.field record contexts, Tropy
and the object store hold media. What was missing — and what this is — is the
**convergence and orchestration layer** (design note §1).

The centre of gravity is `contract.py`, not this file. Everything here is
plumbing over it: a route that takes audio or text, the engine that turns it
into words, the parser that turns words into an intent, the registry that turns
an intent into an act. Read that file first; this one only wires.

**Three rules, inherited and not re-litigated:**

1. **the domain lives in s3Dgraphy.** What a stratigraphic unit is, what a
   resource attached to one means — the library decides, and the tools ask it;
2. **the author is the token's.** Never a field the client filled in. A record
   without a verifiable hand behind it is one nobody can defend;
3. **offline-first.** The node is the host. Nothing here calls out to a cloud,
   and everything works with no network beyond the trench — the room adds reach,
   it is not a precondition.

`/health` says what this node can actually do: which tools are registered, which
speech engine is listening, whether an intent model is loaded, where writes are
going. On a dig, "is what I just said reaching the others?" is the first
question anybody asks, and it deserves an answer that is one GET away.
"""

from __future__ import annotations

import pathlib

import base64
from typing import Any, Dict, List, Optional

from fastapi import (APIRouter, Body, FastAPI, File, Form, HTTPException,
                     Request, UploadFile)
from fastapi.responses import FileResponse, HTMLResponse, Response
from pydantic import BaseModel, Field

from .assets import ASSET_STORE
from .assets import describe as asset_describe
from .auth import AuthDependency, authenticator, principal_orcid
from .contract import ToolResult, invoke
from .intent import COMMAND_LANGUAGE, understand
from .intent import describe as intent_describe
from .intent import intent_model_from_env
from .speech import describe as stt_describe
from .speech import stt_from_env
from .tools import build_registry
from .writer import describe as writer_describe
from .writer import writer_from_env

try:
    import s3dgraphy  # noqa: F401  — the domain; a clear failure beats a mystery
except ImportError as exc:  # pragma: no cover
    raise RuntimeError(
        "stratigraph-chatbot needs s3dgraphy importable: pip install s3dgraphy "
        f"(or -e ../s3Dgraphy). {exc}") from exc

# THE VERSION, and the convention is not this service's to invent.
#
#     <major EM>.<minor EM>.<the tool's own iteration>
#
# The first two segments declare which Extended Matrix language this build
# speaks; the third is its own history. **A tool cannot be more stable than the
# language it speaks**: while s3Dgraphy is `1.6.0.devN`, so is this.
#
# Measured, not assumed: s3Dgraphy is `1.6.0.dev17`, and EM-blender-tools
# already carries `1.6.0-dev.8` beside an s3dgraphy wheel of `1.6.0.dev16`, so
# the convention existed and was adopted half-way. `0.1.0.dev0` meant this
# service had never been versioned at all.
#
# The third segment is the COORDINATE OF A TEST REPORT: «on dev2 this no longer
# happens» is information, «on the latest version» is not.
__version__ = "1.6.0.dev1"

app = FastAPI(
    title="stratigraph-chatbot",
    version=__version__,
    summary="The StratiGraph field assistant: voice → intent → tool → a "
            "DTC-attributed write on the shared graph.",
    description=__doc__,
)

#: Built at import, so a misconfiguration fails at STARTUP rather than in a
#: trench. The order matters: the writer may refuse (a room with no token).
WRITER = writer_from_env()
STT = stt_from_env()
REGISTRY = build_registry(WRITER, ASSET_STORE)

#: The intent model is OPTIONAL and absent by default. The rules answer the
#: field card's commands, which is what the MVP needs; a model is used on the
#: node when the node names one, and `/health` says WHICH.
#:
#: Built at import for the reason `WRITER` and `STT` are: a HALF configuration
#: must refuse where somebody is watching, not on the first dictation in a
#: trench. Until 2026-09-02 this was a bare `None` and nothing could set it —
#: `/health` declared a capability that had no switch.
#:
#: The order is untouched and stays untouched: **rules first, model second.** The
#: field vocabulary is closed and designed on purpose; asking a model to
#: interpret a sentence that matches it exactly would be slower, less
#: predictable, and occasionally wrong. And the model still chooses only among
#: the tools the registry declares (`llm_parse`).
INTENT_MODEL = intent_model_from_env()

v1 = APIRouter(prefix="/v1", dependencies=[AuthDependency])
public = APIRouter()


# ── health ────────────────────────────────────────────────────────────────────

class Health(BaseModel):
    ok: bool = True
    service: str = "stratigraph-chatbot"
    version: str
    #: WHICH graph language this process is actually running. The other two
    #: services of the stack publish it already; this one did not, and that is
    #: how three images installing three different specs went unnoticed. They
    #: share em.json files and one vocabulary — a version that differs is a study
    #: the catalogue indexes differently from how the server wrote it.
    s3dgraphy: str = ""
    auth: str = "dev-no-auth"
    #: WHERE what you say ends up. `local container` means the others will see
    #: it at the next sync, not now — and an operator has to be able to tell.
    writes_to: str = "local container"
    asset_store: str = "memory"
    speech: str = "passthrough"
    #: Kept, and DERIVED from the line below: a probe that only ever asked
    #: "is there one?" must not break the day the answer got longer.
    intent_model: bool = False
    #: WHICH engine is interpreting, and which model — or which variable would
    #: name one. A boolean was not enough (design note §4): that string is
    #: PROVENANCE, and a datum without the name of the engine that produced it
    #: is a datum nobody can argue about later. Same shape as `speech` above.
    intent: str = "rules only"
    #: The node's AI capabilities, in the shape design note §4 asks for: a name,
    #: a state, the engine when there is one, and what would configure it when
    #: there is not. This is what `/v1/node`'s public reduction forwards, so a
    #: surface never keeps a list of its own.
    capabilities: List["Capability"] = Field(default_factory=list)
    #: The language this node's command vocabulary is written in. A surface
    #: localised into another language still has to show ITS examples, or it
    #: offers a phrase the node would refuse.
    command_language: str = COMMAND_LANGUAGE
    #: Whether a dictation can be accepted AT ALL. False when no identity
    #: provider is configured: the tools would refuse the write anyway ("Non
    #: posso scrivere senza sapere chi sei"), so a field page that offered an
    #: input box would be collecting words it could never attribute.
    accepts_dictation: bool = True
    #: …and when it cannot, WHAT IS IN THE WAY, by name. Empty otherwise.
    missing: List[str] = Field(default_factory=list)
    #: The registry IS the documentation: what this node can do, by name.
    tools: List[Dict[str, Any]] = Field(default_factory=list)


class Capability(BaseModel):
    """One AI capability of this node, as design note §4 asks it to be said."""

    name: str
    #: `absent` — this node does not do it · `configured` — it names an engine
    #: and the configuration is coherent · `active` — it is loaded here.
    #:
    #: There is no `active` for `intent`, and that is honest rather than lazy:
    #: knowing it would mean reaching the model's endpoint, and a health probe
    #: that makes a network call at import is a health probe that can hang. A
    #: model that does not answer surfaces on FIRST USE, with a line in the log
    #: and an «I did not understand» — the rules having already had their turn.
    state: str = "absent"
    #: which engine and which model. Never empty: when nothing is configured it
    #: says so, because "" would read as a missing field.
    engine: str = ""
    #: the variable(s) that would configure it, when it is absent
    missing: List[str] = Field(default_factory=list)


def _capabilities() -> List[Capability]:
    """What this node can do, and what would make it able.

    ONE builder, so `/health` and — through the room server's probe — the public
    reduction in `/v1/node` cannot tell two different stories. A probe and a gate
    that disagree send two people looking in two places.
    """
    from .intent import INTENT_ENDPOINT_VAR, INTENT_MODEL_VAR

    speech_engine = stt_describe(STT)
    on_node = not speech_engine.startswith("passthrough")
    capabilities = [
        Capability(
            name="speech",
            state="active" if on_node else "absent",
            engine=speech_engine,
            missing=[] if on_node else ["EM_CHATBOT_WHISPER_MODEL"],
        ),
        Capability(
            name="intent",
            state="configured" if INTENT_MODEL is not None else "absent",
            engine=intent_describe(INTENT_MODEL),
            missing=([] if INTENT_MODEL is not None
                     else [INTENT_MODEL_VAR, INTENT_ENDPOINT_VAR]),
        ),
    ]
    # …and the one configuration that can send an excavation's words off the
    # site is SAID here as well as logged at startup. Design note §5: silent is
    # what makes it an incident.
    local = getattr(INTENT_MODEL, "local", True)
    if INTENT_MODEL is not None and not local:
        capabilities[-1].missing.append(
            "WARNING: the intent endpoint is NOT local — every dictated "
            "sentence leaves this node")
    return capabilities


def _identity_gaps() -> List[str]:
    """What stands between this node and an ATTRIBUTABLE dictation, by name.

    The habit this follows is the one `auth.py` set when it refuses to start on
    a half-configured realm: say what is not there, rather than behave oddly.
    A field page that meets a bare gate cannot tell «sign in» from «somebody
    forgot a variable», and the person meeting it is standing in a trench.

    Empty when this node enforces tokens — there is then nothing in the way.
    """
    settings = authenticator.settings
    if getattr(settings, "enforcing", False):
        return []
    gaps: List[str] = []
    if not getattr(settings, "issuer", ""):
        gaps.append("OIDC_ISSUER (or TOKEN_ENDPOINT)")
    if not getattr(settings, "audience", ""):
        gaps.append("OIDC_AUDIENCE (or CLIENT_ID_em)")
    if getattr(settings, "anon_declared", False):
        # Not a missing variable — a declared one, and it is still in the way:
        # an anonymous dictation has nobody to attribute.
        gaps.append("EM_CHATBOT_ALLOW_ANON is on, and an anonymous dictation "
                    "has no author")
    return gaps


def _s3dgraphy_version() -> str:
    try:
        import s3dgraphy

        return str(getattr(s3dgraphy, "__version__", "") or "")
    except Exception:                                  # noqa: BLE001
        return ""


def _health() -> Health:
    return Health(
        version=__version__,
        s3dgraphy=_s3dgraphy_version(),
        auth=authenticator.settings.describe(),
        writes_to=writer_describe(WRITER),
        asset_store=asset_describe(ASSET_STORE),
        speech=stt_describe(STT),
        intent_model=INTENT_MODEL is not None,
        intent=intent_describe(INTENT_MODEL),
        capabilities=_capabilities(),
        accepts_dictation=bool(authenticator.settings.enforcing),
        missing=_identity_gaps(),
        tools=[{"name": d.name, "intents": d.intents, "writes": d.writes,
                "service": d.service} for d in REGISTRY.list()],
    )


@public.get("/health", response_model=Health, tags=["meta"])
def health() -> Health:
    return _health()


@public.get("/v1/health", response_model=Health, tags=["meta"])
def health_v1() -> Health:
    """Same answer as `/health`. A probe belongs to the infrastructure and must
    not have to be edited the day the API is versioned."""
    return _health()


# ── how a browser signs in ────────────────────────────────────────────────────

class AuthConfig(BaseModel):
    """What a BROWSER needs to sign in against this node's realm.

    Public by construction — an issuer and a client id are not secrets, and the
    one thing that would be (a client secret) does not exist for this client: the
    field page is a PUBLIC OIDC client and uses PKCE instead.

    **Why this node answers it rather than the room server.** StratiGraph Server
    exposes the same document, and behind Caddy it is one fetch away at
    `/em/v1/auth-config`. Reaching for it would mean this page knowing that the
    room server lives under `/em` — a second deployment fact, true today,
    invisible when it stops being true, and wrong in the development loop where
    the page is served bare at `:8020`. The page derives everything from its own
    URL (`new URL(".", location.href)`); this route is what makes that enough.
    The SHAPE is deliberately the room server's, field for field, so a client
    that speaks to one speaks to the other.
    """

    #: the realm, e.g. `https://sso.example.org/realms/em`. Empty when this node
    #: runs in dev-no-auth, and the page then SAYS so instead of offering a
    #: sign-in that cannot work.
    issuer: str = ""
    #: the PUBLIC client the browser authenticates as
    client_id: str = ""
    #: where the IdP sends the browser back. Advertised so a deployment can be
    #: read from one place, but the page computes its OWN from the document's
    #: URL — it is served bare at `:8020/` and under `/chat/` behind the proxy —
    #: and sends that. The two must agree with what the realm allows.
    redirect_uri: str = ""
    #: derived from the issuer the way Keycloak lays them out, for the reason
    #: `auth.py` gives about the issuer and the JWKS: two URLs that must agree
    #: are two URLs that will one day disagree.
    authorization_endpoint: str = ""
    token_endpoint: str = ""
    #: the one that closes the shared-device trap. Without it "esci" only
    #: forgets a token, and the next sign-in walks back in on Keycloak's cookie
    #: as the previous person — a tablet that changes hands without changing
    #: author.
    end_session_endpoint: str = ""
    scope: str = "openid profile email"
    #: False when this node enforces nothing. The page then shows the gate with
    #: the honest sentence — a node that cannot attribute must not be offered a
    #: dictation box, because `contract.py` would refuse the write anyway.
    enforcing: bool = False
    #: When it is False, WHAT IS IN THE WAY, by name — the same list `/health`
    #: publishes, from the same helper, so the gate and the probe cannot tell
    #: two different stories. A bare gate leaves the person in front of it
    #: unable to tell «sign in» from «somebody forgot a variable».
    missing: List[str] = Field(default_factory=list)


@public.get("/v1/auth-config", response_model=AuthConfig, tags=["meta"])
def auth_config() -> AuthConfig:
    """How a browser signs in to THIS node. No secret, by construction.

    `EM_CHATBOT_CLIENT_ID` names the public client. It defaults to `em-console`,
    which is the stack's existing PUBLIC browser client, rather than inventing a
    second one: a realm object that does not exist yet produces a Sign in that
    fails at the last step, and this stack already argues (in `auth.py`, about
    the issuer and the JWKS) that two spellings of one thing are two things that
    will disagree. A deployment that wants the field client to be its own realm
    client sets the variable; what it must NOT be is `CLIENT_ID_em`, which is
    confidential and does not do this flow.

    Whichever client it is, the realm must list this page's URL among its valid
    redirect URIs — bare at `:8020/` in development, `…/chat/` behind the node's
    Caddy. That is configuration, not code.
    """
    import os

    settings = authenticator.settings
    issuer = str(getattr(settings, "issuer", "") or "")
    client_id = os.environ.get("EM_CHATBOT_CLIENT_ID", "em-console").strip()
    return AuthConfig(
        issuer=issuer,
        client_id=client_id if issuer else "",
        redirect_uri=os.environ.get("EM_CHATBOT_REDIRECT_URI", "").strip(),
        authorization_endpoint=(f"{issuer}/protocol/openid-connect/auth"
                                if issuer else ""),
        token_endpoint=(f"{issuer}/protocol/openid-connect/token"
                        if issuer else ""),
        end_session_endpoint=(f"{issuer}/protocol/openid-connect/logout"
                              if issuer else ""),
        scope=os.environ.get("EM_CHATBOT_SCOPE",
                             "openid profile email").strip(),
        enforcing=bool(getattr(settings, "enforcing", False)),
        missing=_identity_gaps(),
    )


# ── the tools, declared ───────────────────────────────────────────────────────

@public.get("/v1/tools", tags=["contract"])
def list_tools() -> Dict[str, Any]:
    """The registry, in full — the interoperability surface, readable.

    Unauthenticated on purpose: a partner writing an adapter needs to see what
    the contract looks like on a node, and a descriptor names capabilities, not
    data. What is behind them is protected; what they ARE is public.
    """
    return {"tools": [d.as_dict() for d in REGISTRY.list()],
            "intents": REGISTRY.intents(),
            # …and WHICH LANGUAGE those phrases are in. Without it a client can
            # read the vocabulary and not know that it is a vocabulary — that
            # these are the words, not a translation of the words.
            "command_language": COMMAND_LANGUAGE}


# ── the schede, as DATA ──────────────────────────────────────────────────────
#
# THE CONSTRAINT THIS SERVES, and it is a hard one (E.D., 5 September): a new
# definition must reach the telephone **without a release of the app**. If
# showing the Spanish sheet meant rebuilding the front-end, the format would not
# be travelling as data — it would be getting compiled into the code.
#
# So: a directory of definitions, listed and served. Dropping a file in makes it
# appear. The JS renderer reads what comes back and draws the module; nothing
# here draws anything, and nothing here knows what a field means to the graph.

@public.get("/v1/schede", tags=["scheda"])
def list_schede() -> Dict[str, Any]:
    """Which definitions this node can serve.

    Unauthenticated for the same reason as `/v1/tools`: a definition names a
    STANDARD, not somebody's excavation. What is behind a scheda is protected;
    which schede exist is public — and a partner writing a client needs to be
    able to see it.
    """
    from . import scheda as schede

    found = schede.available()
    return {
        "schede": [{"id": s.id,
                    "languages": s.languages,
                    "standard": {k: s.standard.get(k) for k in
                                 ("authority", "code", "version", "invented")},
                    "fields": len(s.fields),
                    # The three counts, so a client can SEE that a definition
                    # has not been decided yet instead of discovering it as an
                    # empty phone form.
                    "recorded_in": s.counts()}
                   for s in found],
        # Said out loud rather than left to be inferred from an empty list: a
        # node with no definitions is not broken, it is a node that takes
        # dictation. Absent means the assistant is exactly what it was.
        "serving": schede.schede_dir() is not None,
    }


@public.get("/v1/schede/{scheda_id}", tags=["scheda"])
def get_scheda(scheda_id: str, lang: str = "it") -> Dict[str, Any]:
    """ONE definition, in ONE language, as the data the module is drawn from.

    `lang` is not a preference with a fallback: a language the definition does
    not declare is a **400**. The alternative is serving a label nobody wrote
    for that standard, which is the measured defect this whole arc exists to
    avoid (`pdf_export` in pyarchinit-mini printed «Notifica» where the sheet
    says FLOTTAZIONE).
    """
    from . import scheda as schede

    found = schede.find(scheda_id)
    if found is None:
        raise HTTPException(
            status_code=404,
            detail=f"questo nodo non serve una scheda «{scheda_id}»")
    try:
        return found.for_browser(lang)
    except schede.SchedaError as problem:
        raise HTTPException(status_code=400, detail=str(problem)) from problem


# ── the act ───────────────────────────────────────────────────────────────────

class Say(BaseModel):
    """What the device sends when it already has words."""
    transcript: str = ""
    #: Slots the device knows and the sentence cannot carry — the photo it just
    #: took, the GPS fix. Never the author.
    slots: Dict[str, Any] = Field(default_factory=dict)


class Answer(BaseModel):
    ok: bool
    #: The sentence to read out loud. Present on every path, including failure.
    message: str
    #: WHAT WAS HEARD. Only interesting on `/listen`, where the node did the
    #: transcribing and the device has no idea what it sent — `intent.py` keeps
    #: it for exactly this ("so a caller can show it and a person can correct
    #: it"), and until now nothing carried it back out.
    said: str = ""
    intent: Optional[str] = None
    tool: Optional[str] = None
    slots: Dict[str, Any] = Field(default_factory=dict)
    via: str = "none"
    data: Dict[str, Any] = Field(default_factory=dict)


def _author(request: Request) -> Optional[str]:
    """Who is speaking — from the token. In dev mode there is no identity, and
    the tools then refuse to write, which is the correct outcome: a node with
    auth off must not produce records attributed to nobody."""
    principal = authenticator.require_token(request)
    if principal.get("em_dev_mode"):
        return None
    return principal_orcid(principal)


def _run(transcript: str, slots: Dict[str, Any], author: Optional[str]) -> Answer:
    understood = understand(transcript, REGISTRY, model=INTENT_MODEL)
    descriptor = REGISTRY.route(understood.tool or "")
    merged = {**understood.slots, **{k: v for k, v in slots.items()
                                     if v is not None}}
    result: ToolResult = invoke(descriptor, merged, author, registry=REGISTRY)
    return Answer(ok=result.ok, message=result.message,
                  said=understood.transcript or "",
                  intent=understood.intent or None, tool=understood.tool,
                  slots={k: v for k, v in merged.items()
                         if not isinstance(v, (bytes, bytearray))},
                  via=understood.via, data=result.data)


@v1.post("/say", response_model=Answer, tags=["assistant"])
def say(request: Request, body: Say = Body(...)) -> Answer:
    """A sentence in, an act and a sentence out.

    The whole assistant in one route, on purpose: a field device should have one
    thing to call, and everything that varies (which tool, which engine) varies
    behind it rather than in the client's code.
    """
    return _run(body.transcript, body.slots, _author(request))


@v1.post("/listen", response_model=Answer, tags=["assistant"])
async def listen(request: Request,
                 audio: UploadFile = File(...),
                 us: Optional[str] = Form(default=None),
                 language: Optional[str] = Form(default=None),
                 photo: Optional[UploadFile] = File(default=None)) -> Answer:
    """Audio in — transcribed on the node, then exactly as `/say`.

    A photo may ride along, because that is how the gesture actually happens:
    somebody takes a picture and says what it is of, in one act. Making them two
    requests would mean the device has to hold state between them, in a place
    where the network drops.

    `language` is WHICH language to transcribe as, and it was not being passed at
    all: the call fell through to the engine's default, which used to be a
    hard-coded `"it"`. A wrong transcription language does not fail — it produces
    words, the wrong ones, and the fault presents itself as "the assistant
    misunderstands me", which sends somebody looking in the wrong place for an
    afternoon. Absent means "let the engine decide", which for Whisper is
    detection; it no longer means "assume Italian".

    The caller that knows is the page: it sends the language its NODE speaks
    (`command_language`), not the one its own chrome is drawn in.
    """
    raw = await audio.read()
    try:
        transcript = (STT.transcribe(raw, language=language)
                      if language else STT.transcribe(raw))
    except Exception as exc:                       # noqa: BLE001
        raise HTTPException(
            status_code=501,
            detail=f"this node cannot transcribe audio: {exc}") from None
    slots: Dict[str, Any] = {}
    if us:
        slots["us"] = us
    if photo is not None:
        slots["photo"] = await photo.read()
        slots["filename"] = photo.filename
        slots["media_type"] = photo.content_type
    return _run(transcript, slots, _author(request))


class PhotoBody(BaseModel):
    """A photo the device already has, base64'd — the PWA's path when it is
    sending a picture with a sentence rather than a recording."""
    transcript: str = ""
    us: Optional[str] = None
    photo_base64: str = ""
    filename: Optional[str] = None
    media_type: str = "image/jpeg"


@v1.post("/photo", response_model=Answer, tags=["assistant"])
def photo(request: Request, body: PhotoBody = Body(...)) -> Answer:
    try:
        raw = base64.b64decode(body.photo_base64 or "", validate=True)
    except Exception:                              # noqa: BLE001
        raise HTTPException(status_code=400,
                            detail="photo_base64 is not base64") from None
    slots: Dict[str, Any] = {"photo": raw, "media_type": body.media_type}
    if body.us:
        slots["us"] = body.us
    if body.filename:
        slots["filename"] = body.filename
    return _run(body.transcript or "questa foto è per la US", slots,
                _author(request))


# ── the device ────────────────────────────────────────────────────────────────

@public.get("/", response_class=HTMLResponse, tags=["device"])
def device() -> Any:
    """The field client, served by the node itself (design note §6).

    A PWA rather than a native app: ATRIUM is already a web app, the ecosystem
    is web-first, and one codebase reaches phones and tablets on both platforms
    with camera, microphone and GPS through the browser. The heavy AI stays on
    the node; the device stays thin.
    """
    from pathlib import Path
    page = Path(__file__).resolve().parent.parent / "web" / "index.html"
    if not page.is_file():
        return HTMLResponse("<h1>stratigraph-chatbot</h1>"
                            "<p>Il client di campo non è installato su questo "
                            "nodo.</p>", status_code=200)
    return FileResponse(page, media_type="text/html")


#: What never goes in the precache, whatever is on disk. Two kinds of thing:
#: the worker itself (the browser fetches it outside the cache, and caching it
#: is how a worker becomes impossible to update) and anything that is not part
#: of the shell.
_NOT_SHELL = {"sw.js"}
_SHELL_SUFFIXES = {".html", ".css", ".js", ".mjs", ".woff2", ".woff", ".svg",
                   ".png", ".webmanifest", ".json"}


def _shell_files(web: pathlib.Path) -> List[str]:
    """Everything under `web/` the device needs to open with no signal.

    GENERATED, and the prompt's §4 asks for exactly this: *«se dividi, la cache
    list si genera, non si scrive a mano»*. A file present is a file cached —
    so splitting the front-end into modules cannot leave one of them out of the
    cache, which is the failure that only shows up in a trench.

    `"./"` first, because the shell is the page and the page is served at the
    app's root rather than as `index.html`.
    """
    found = ["./"]
    for path in sorted(web.rglob("*")):
        if not path.is_file() or path.name in _NOT_SHELL:
            continue
        if path.suffix.lower() not in _SHELL_SUFFIXES:
            continue
        relative = path.relative_to(web).as_posix()
        if relative == "index.html":
            continue                       # already there as "./"
        found.append(f"./{relative}")
    return found


@public.get("/sw.js", tags=["device"])
def service_worker() -> Any:
    """The service worker, with its precache list and cache name SUBSTITUTED.

    Two things are filled in, and both are things a person forgets:

    * **the file list**, from what is on disk (`_shell_files`);
    * **the cache name**, from a digest of those files' bytes. A hand-bumped
      version is a step somebody skips, and skipping it leaves a device serving
      yesterday's page from cache with no way to notice — an offline bug that
      looks like it works.

    Served as text rather than `FileResponse` because it is now rendered. The
    file on disk stays a valid worker on its own (the placeholders have literal
    fallbacks), so it can still be read and reasoned about without a server.
    """
    import hashlib
    import json as _json

    web = pathlib.Path(__file__).resolve().parent.parent / "web"
    worker = web / "sw.js"
    if not worker.is_file():
        raise HTTPException(status_code=404, detail="no service worker")

    files = _shell_files(web)
    digest = hashlib.sha256()
    for relative in files:
        candidate = web / relative.removeprefix("./")
        if candidate.is_file():
            digest.update(candidate.read_bytes())
        else:
            digest.update(relative.encode("utf-8"))
    source = worker.read_text(encoding="utf-8")
    source = source.replace("__SHELL_FILES__", _json.dumps(files))
    source = source.replace("__SHELL_VERSION__", digest.hexdigest()[:12])
    return Response(content=source, media_type="application/javascript")


# The BRAND, served beside the page that asks for it. Static files, no route of
# their own: they are the vendored copy from `stratigraph-brand/` (see
# `sync-brand.sh`), and the service worker precaches them so the assistant opens
# looking like itself on a device that has never had signal.
#
# Mounted rather than listed one by one because the set is data, not code:
# adding a font weight to the brand should not mean editing this file.
_BRAND = pathlib.Path(__file__).resolve().parent.parent / "web" / "brand"
if _BRAND.is_dir():
    from fastapi.staticfiles import StaticFiles
    app.mount("/brand", StaticFiles(directory=str(_BRAND)), name="brand")

app.include_router(public)
app.include_router(v1)
