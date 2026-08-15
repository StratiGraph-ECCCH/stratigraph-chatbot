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

import base64
from typing import Any, Dict, List, Optional

from fastapi import (APIRouter, Body, FastAPI, File, Form, HTTPException,
                     Request, UploadFile)
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

from .assets import ASSET_STORE
from .assets import describe as asset_describe
from .auth import AuthDependency, authenticator, principal_orcid
from .contract import ToolResult, invoke
from .intent import understand
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

__version__ = "0.1.0.dev0"

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
#: field card's commands, which is what the MVP needs; a model is loaded on the
#: node when there is one, and `/health` says whether it was.
INTENT_MODEL = None

v1 = APIRouter(prefix="/v1", dependencies=[AuthDependency])
public = APIRouter()


# ── health ────────────────────────────────────────────────────────────────────

class Health(BaseModel):
    ok: bool = True
    service: str = "stratigraph-chatbot"
    version: str
    auth: str = "dev-no-auth"
    #: WHERE what you say ends up. `local container` means the others will see
    #: it at the next sync, not now — and an operator has to be able to tell.
    writes_to: str = "local container"
    asset_store: str = "memory"
    speech: str = "passthrough"
    intent_model: bool = False
    #: The registry IS the documentation: what this node can do, by name.
    tools: List[Dict[str, Any]] = Field(default_factory=list)


def _health() -> Health:
    return Health(
        version=__version__,
        auth=authenticator.settings.describe(),
        writes_to=writer_describe(WRITER),
        asset_store=asset_describe(ASSET_STORE),
        speech=stt_describe(STT),
        intent_model=INTENT_MODEL is not None,
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


# ── the tools, declared ───────────────────────────────────────────────────────

@public.get("/v1/tools", tags=["contract"])
def list_tools() -> Dict[str, Any]:
    """The registry, in full — the interoperability surface, readable.

    Unauthenticated on purpose: a partner writing an adapter needs to see what
    the contract looks like on a node, and a descriptor names capabilities, not
    data. What is behind them is protected; what they ARE is public.
    """
    return {"tools": [d.as_dict() for d in REGISTRY.list()],
            "intents": REGISTRY.intents()}


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
                 photo: Optional[UploadFile] = File(default=None)) -> Answer:
    """Audio in — transcribed on the node, then exactly as `/say`.

    A photo may ride along, because that is how the gesture actually happens:
    somebody takes a picture and says what it is of, in one act. Making them two
    requests would mean the device has to hold state between them, in a place
    where the network drops.
    """
    raw = await audio.read()
    try:
        transcript = STT.transcribe(raw)
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


@public.get("/sw.js", tags=["device"])
def service_worker() -> Any:
    from pathlib import Path
    worker = Path(__file__).resolve().parent.parent / "web" / "sw.js"
    if not worker.is_file():
        raise HTTPException(status_code=404, detail="no service worker")
    return FileResponse(worker, media_type="application/javascript")


app.include_router(public)
app.include_router(v1)
