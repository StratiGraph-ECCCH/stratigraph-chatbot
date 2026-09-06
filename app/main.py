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
import contextlib
import logging
import os
import pathlib

from typing import Any, Dict, List, Optional

from fastapi import (APIRouter, Body, FastAPI, File, Form, HTTPException,
                     Request, UploadFile)
from fastapi.responses import FileResponse, HTMLResponse, Response
from pydantic import BaseModel, Field

from . import assets, handoff
from .assets import describe as asset_describe
from .bridge import bridge_for, queues_beside
from .holding import HeldByAnother, Holding
from .holding import describe as holding_describe
from .spool import describe as larder_describe
from .spool import shared_name, spool_from_env
from .auth import _TOKEN_SUFFIX as TOKEN_SUFFIX
from .auth import AuthDependency, authenticator, principal_orcid
from .contract import ToolResult, invoke
from .intent import COMMAND_LANGUAGE, understand
from .intent import describe as intent_describe
from .intent import intent_model_from_env
from .speech import describe as stt_describe
from .speech import stt_from_env
from .tools import build_registry
from .writer import LocalWriter, RoomWriter
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

log = logging.getLogger("stratigraph.chatbot")


@contextlib.asynccontextmanager
async def _lifespan(_app: FastAPI):
    """Sedersi all'avvio, e alzarsi quando il servizio finisce.

    **All'avvio e non alla prima consegna**, ed è tutta la differenza fra un
    seduto e un corrispondente: se il posto si prendesse alla prima operazione,
    un assistente aperto e fermo — che è la maggior parte del tempo di un
    telefono in cantiere — non sarebbe nella stanza, e nessuno lì dentro
    saprebbe che c'è.

    **Non può impedire l'avvio.** La stanza può essere giù, il token scaduto, il
    telefono senza campo: sono i casi normali, non guasti di configurazione. Ci
    si siede alla prima occasione utile (`_seated`), e nel frattempo il servizio
    lavora sul container locale — che è il ponte già costruito.
    """
    sit = getattr(WRITER, "_seated", None)
    if sit is not None:
        try:
            sit()
            log.info("seduti nella stanza")
        except Exception as exc:      # noqa: BLE001
            log.info("non seduti (per ora): %s", exc)
        # …e si RESTA seduti: il sorvegliante riprende il posto quando cade,
        # che è quello che succede a ogni riavvio del relay e a ogni buco di
        # rete. Senza, «seduto» durerebbe fino al primo singhiozzo.
        seat = getattr(getattr(WRITER, "session", None), "keep_seated", None)
        if seat is not None:
            seat()
    yield
    leave = getattr(WRITER, "close", None)
    if leave is not None:
        try:
            leave()
        except Exception:             # noqa: BLE001 — alzarsi non deve fallire
            pass


app = FastAPI(
    lifespan=_lifespan,
    title="stratigraph-chatbot",
    version=__version__,
    summary="The StratiGraph field assistant: voice → intent → tool → a "
            "DTC-attributed write on the shared graph.",
    description=__doc__,
)

#: Built at import, so a misconfiguration fails at STARTUP rather than in a
#: trench. The order matters: the writer may refuse (a room with no token).
#: LA DISPENSA, costruita UNA VOLTA e data a tutti e due — lo scrivano, che
#: decide quando i byte salgono, e i tool, che ce li mettono. Due istanze sulla
#: stessa directory sarebbero due processi con la stessa coda: il difetto che il
#: `flock` di `bridge.py` esiste per chiudere, riaperto un metro più in là.
#:
#: E costruita QUI e non dentro `writer_from_env` per una ragione misurata
#: stanotte: senza stanza lo scrivano è un `LocalWriter`, che non ha una
#: dispensa — e un nodo senza stanza è esattamente il nodo di campo, cioè
#: quello che ne ha più bisogno. La prima versione la prendeva dal writer e
#: `/health` diceva «nessuna» con la variabile impostata.
LARDER = spool_from_env()

#: IL CONTAINER LOCALE, costruito qui e passato: dal 2 ottobre lo scrivano si
#: può sostituire a caldo (`POST /v1/room`), e ogni scrivano nuovo deve ricevere
#: **questo** container e non costruirsene un secondo sullo stesso file.
LOCAL = LocalWriter(os.environ.get("EM_CHATBOT_CONTAINER")
                    or "data/scavo.em.json",
                    study=os.environ.get("EM_CHATBOT_STUDY") or "Scavo")
WRITER = writer_from_env(spool=LARDER, local=LOCAL)

#: CHI TIENE IL NODO. Vuota all'avvio anche quando l'ambiente punta già a una
#: stanza: un nodo configurato dal dispiegamento non è «in mano» a nessuno, e
#: dichiararlo tenuto impedirebbe alla prima persona che arriva di prenderlo.
HOLDING = Holding()
STT = stt_from_env()

#: LO STORE CHE I TOOL VEDONO. Se questo nodo ha una dispensa è quella, e non
#: MinIO: `Spool` implementa lo stesso `AssetStore`, quindi nessun tool sa la
#: differenza — cambia solo QUANDO si parla con la rete. Un nodo di scrivania
#: senza dispensa continua a scrivere diritto nel bucket, che è la cosa giusta
#: quando la rete è sotto il tavolo.
#: E `assets.ASSET_STORE` si LEGGE solo se non c'è una dispensa: leggerlo è
#: quello che lo costruisce, e costruirlo è un giro di rete. Un nodo di campo
#: con la dispensa non deve chiedere niente a nessuno per accendersi.
STORE = LARDER if LARDER is not None else assets.ASSET_STORE
REGISTRY = build_registry(WRITER, STORE)

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
    #: LA DISPENSA: i byte che non hanno ancora raggiunto lo store condiviso.
    #: Detta anche quando è vuota, perché «nessuna dispensa» è una
    #: configurazione — su un nodo di campo vuol dire che una foto scattata
    #: senza rete si perde — e non un dettaglio.
    larder: str = "nessuna (i byte vanno diritti allo store: senza rete si perdono)"
    #: SE IL NODO È IN MANO A QUALCUNO, e da quanto è fermo. **Senza il nome**:
    #: `/health` è pubblica, e «chi sta lavorando in questa tenda adesso» non si
    #: dà a chi non si è nemmeno presentato. Il nome lo legge chi ha firmato, su
    #: `GET /v1/room` — e chi prova a prendere un nodo occupato lo trova nel
    #: rifiuto, che è il posto in cui serve.
    held: str = "libero"
    #: LE CODE CHE QUESTO NODO HA SUL DISCO, una per stanza, con quanto c'è
    #: dentro. Da quando le code sono per stanza esiste un modo nuovo di perdere
    #: del lavoro — ripuntare via da una stanza e dimenticarsene — e l'unica
    #: difesa è che si veda.
    queues: List[Dict[str, Any]] = Field(default_factory=list)
    #: IL SERVER CHE QUESTO NODO USEREBBE, se chi punta dice solo una stanza.
    #: Un indirizzo non è un permesso — è la stessa cosa che il link di consegna
    #: porta in chiaro — e senza, la pagina dovrebbe chiedere di scrivere due
    #: campi per fare una cosa sola.
    server_hint: str = ""
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
        # Con la dispensa, lo store condiviso si NOMINA dalla configurazione e
        # non si costruisce: costruirlo è un giro di rete, e una spia che si
        # blocca quando la rete manca è la spia che si spegne quando serve.
        asset_store=(asset_describe(assets.ASSET_STORE) if LARDER is None
                     else shared_name()),
        larder=larder_describe(LARDER),
        held=holding_describe(HOLDING),
        queues=queues_beside(LOCAL.path),
        server_hint=(getattr(WRITER, "base_url", "")
                     or (os.environ.get("EM_SERVER_URL") or "").strip()),
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
    orcid = principal_orcid(principal)
    # ...e OGNI ATTO AUTENTICATO RINFRESCA LA PRESA. È il lavoro che tiene il
    # nodo, non una dichiarazione: un nodo tenuto per aver detto una volta «e'
    # mio» tornerebbe a essere un blocco. Qui e non altrove perché questa
    # funzione È «un atto autenticato di questa persona» — la chiamano le
    # cinque rotte che scrivono, e nessun'altra.
    if orcid:
        HOLDING.touch(orcid)
    return orcid


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


class SchedaIn(BaseModel):
    """A filled scheda, on its way to the tools that already exist."""

    #: The unit the scheda is about. Not derived from the values, even when a
    #: field of the definition holds it: WHICH unit a record is about is the
    #: caller's statement, and reading it out of a box would make a typo in that
    #: box silently address a different unit.
    us: str = ""
    #: `{field id: value}`, with the ids the DEFINITION declares. Anything else
    #: is refused by `scheda.slots_for` — a form is not a way to put arbitrary
    #: keys into `data`.
    values: Dict[str, Any] = Field(default_factory=dict)
    #: `{field id: "human"|"ai"}`. Absent means human: whoever said nothing
    #: wrote it themselves.
    authored_by: Dict[str, str] = Field(default_factory=dict)
    #: Which model composed the `ai` ones — what stays legible after a person
    #: validates them.
    model: str = ""
    #: True the first time, so a unit that does not exist yet gets created
    #: before its boxes are filled. Declared by the caller rather than guessed
    #: from whether the unit is there: «create it if missing» is how a mistyped
    #: number becomes a new unit, which is the whole reason `update_su` exists.
    create: bool = False


@v1.post("/scheda/{scheda_id}", response_model=Answer, tags=["scheda"])
def submit_scheda(scheda_id: str, request: Request,
                  body: SchedaIn = Body(...)) -> Answer:
    """A filled scheda becomes the SAME act a voice would have produced.

    THE POINT OF THE WHOLE ARC, in one route: this does not write to the graph.
    It turns a form into the slots of `create_su` / `update_su` and hands them
    to `invoke`, exactly as `/say` hands it what a sentence produced. The
    browser talks to the service; the service talks to the tools; the tools talk
    to the writer — and `tests/test_one_write_path.py` keeps that the only road.

    So there is no new write path here, and there is no second place that knows
    what a field means: `app/scheda.py` reads the definition, and the definition
    came from the standard.
    """
    from . import scheda as schede

    found = schede.find(scheda_id)
    if found is None:
        raise HTTPException(
            status_code=404,
            detail=f"questo nodo non serve una scheda «{scheda_id}»")
    if not (body.us or "").strip():
        raise HTTPException(
            status_code=400,
            detail="una scheda è di un'unità: manca il numero")

    author = _author(request)
    try:
        slots = schede.slots_for(found, body.values, us=body.us)
    except schede.SchedaError as problem:
        raise HTTPException(status_code=400, detail=str(problem)) from problem

    # A unit has to exist before its boxes can be filled — `update_field`
    # refuses a node that is not there, which is the property that makes an
    # update an update. So a first save is two acts, in this order, and the
    # SECOND one is what the answer reports: the creation is a precondition, the
    # filling is what the person did.
    if body.create:
        made = invoke(REGISTRY.get("create_su"),
                      {"us": slots["us"]}, author, registry=REGISTRY)
        if not made.ok:
            return Answer(ok=False, message=made.message, tool="create_su",
                          data=made.data)

    slots["authored_by"] = dict(body.authored_by)
    if body.model:
        slots["model"] = body.model
    result: ToolResult = invoke(REGISTRY.get("update_su"), slots, author,
                                registry=REGISTRY)
    return Answer(ok=result.ok, message=result.message, tool="update_su",
                  slots={"us": slots["us"],
                         "fields": sorted(slots["fields"])},
                  data=result.data)


class ValidateIn(BaseModel):
    us: str = ""
    fields: List[str] = Field(default_factory=list)


@v1.post("/validate", response_model=Answer, tags=["scheda"])
def validate(request: Request, body: ValidateIn = Body(...)) -> Answer:
    """A person confirms the boxes a model composed. One gesture, one act."""
    result: ToolResult = invoke(
        REGISTRY.get("validate_field"),
        {"us": body.us, "fields": list(body.fields)},
        _author(request), registry=REGISTRY)
    return Answer(ok=result.ok, message=result.message, tool="validate_field",
                  data=result.data)


@v1.post("/listen", response_model=Answer, tags=["assistant"])
async def listen(request: Request,
                 audio: UploadFile = File(...),
                 us: Optional[str] = Form(default=None),
                 language: Optional[str] = Form(default=None),
                 photo: Optional[UploadFile] = File(default=None),
                 photo_sha256: Optional[str] = Form(default=None)) -> Answer:
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
        if photo_sha256:
            slots["sha256"] = photo_sha256
    return _run(transcript, slots, _author(request))


class PhotoBody(BaseModel):
    """A photo the device already has, base64'd — the PWA's path when it is
    sending a picture with a sentence rather than a recording."""
    transcript: str = ""
    us: Optional[str] = None
    photo_base64: str = ""
    filename: Optional[str] = None
    media_type: str = "image/jpeg"
    #: IL DIGEST CHE IL MITTENTE SI ASPETTA — `sha256:<hex>`, o il solo hex.
    #: Facoltativo, e verificato quando c'è: un base64 troncato a un multiplo
    #: di 4 si decodifica in mezza foto con un riferimento valido, e senza
    #: questa riga nessuno può più accorgersene. Chi non lo manda passa come
    #: prima, dichiaratamente (vedi `spool.verify`).
    sha256: str = ""


@v1.post("/photo", response_model=Answer, tags=["assistant"])
def photo(request: Request, body: PhotoBody = Body(...)) -> Answer:
    try:
        raw = base64.b64decode(body.photo_base64 or "", validate=True)
    except Exception:                              # noqa: BLE001
        raise HTTPException(status_code=400,
                            detail="photo_base64 is not base64") from None
    slots: Dict[str, Any] = {"photo": raw, "media_type": body.media_type}
    if body.sha256:
        slots["sha256"] = body.sha256
    if body.us:
        slots["us"] = body.us
    if body.filename:
        slots["filename"] = body.filename
    return _run(body.transcript or "questa foto è per la US", slots,
                _author(request))




def _room_credential(bearer: str, server: str) -> tuple:
    """Il token con cui questo nodo entrera' nella stanza, e da dove viene.

    ════════════════════════════════════════════════════════════════════════════
    ## PERCHÉ NON SI INOLTRA IL TOKEN DI CHI CHIAMA

    È la strada che verrebbe in mente per prima e **non si fa**, e la ragione e'
    un attacco concreto, non un principio: il server della stanza lo sceglie il
    LINK, e un link lo può scrivere chiunque. «Incolla questo» con dentro
    `server=https://qualcosa.esempio` e questo nodo consegnerebbe il token della
    persona che ha firmato a chi ha scritto il link.

    Uno scambio non ha quella forma: il realm conia un token **per un pubblico
    che conosce lui** (`EM_ROOM_AUDIENCE`), e un link che nomina un altro server
    ottiene un token che a quel server non serve.

    Due strade, in quest'ordine, e ognuna dice cosa comporta:

    1. **lo scambio al realm** — il lavoro nella stanza è di chi ha firmato qui.
       È la strada giusta e ha bisogno di tre variabili di configurazione
       (`handoff.EXCHANGE_KEYS`), che sono del nodo e non di una persona.
    2. **il token dell'ambiente** — c'è già, su un nodo configurato headless, e
       funziona. Ma nella stanza il lavoro risulterà **di chi ha configurato il
       nodo**, perché il relay prende l'identità dal token e non dal payload.
       Si usa e **si dichiara nella risposta**: è il comportamento che questo
       nodo ha sempre avuto, e stanotte smette di essere taciuto.

    Senza nessuna delle due si alza `NoCredential` con dentro cosa manca. Non si
    ripiega su niente: un nodo che scrivesse «a nome tuo» con un token altrui
    direbbe una cosa falsa in un grafo che qualcuno dovra' difendere.
    """
    if not handoff.exchange_missing():
        # DERIVATO DALL'ISSUER, come fa `auth.py` nella direzione opposta: una
        # variabile sola configura tutte e due e non possono contraddirsi. Se
        # questo nodo non ha un issuer si chiede al server della stanza, che e'
        # l'unico altro posto che lo sa.
        issuer = (authenticator.settings.issuer or "").rstrip("/")
        endpoint = (issuer + TOKEN_SUFFIX) if issuer else (
            (handoff.auth_config(server) or {}).get("token_endpoint"))
        if not endpoint:
            raise handoff.NoCredential(
                "lo scambio è configurato ma non so a quale token endpoint "
                "chiederlo: manca OIDC_ISSUER (o TOKEN_ENDPOINT) su questo nodo.")
        return handoff.exchange(bearer, token_endpoint=endpoint), (
            "scambiato al realm: nella stanza il lavoro è tuo")

    dellambiente = (os.environ.get("EM_CHATBOT_TOKEN") or "").strip()
    if dellambiente:
        return dellambiente, (
            f"il token dell'ambiente: nella stanza il lavoro risulterà di "
            f"{_token_orcid(dellambiente) or 'chi ha configurato questo nodo'}, "
            f"non tuo. Per scrivere a nome tuo servono "
            f"{', '.join(handoff.exchange_missing())}.")

    raise handoff.NoCredential(
        "questo nodo non ha come presentarsi a una stanza. Due strade: "
        f"configurare lo scambio al realm ({', '.join(handoff.EXCHANGE_KEYS)}), "
        "che fa risultare il lavoro di chi firma; oppure dargli un "
        "EM_CHATBOT_TOKEN, che lo fa risultare del dispiegamento.")


def _token_orcid(token: str) -> Optional[str]:
    """L'ORCID dichiarato da un token, **senza verificarlo**.

    Solo per una frase, e la frase dice di chi risulterà il lavoro. Non è una
    decisione di fiducia — a verificare ci pensa il relay — ed è il token di
    questo nodo, non di uno sconosciuto.
    """
    import json
    try:
        corpo = token.split(".")[1]
        corpo += "=" * (-len(corpo) % 4)
        return json.loads(base64.urlsafe_b64decode(corpo)).get("orcid")
    except Exception:                              # noqa: BLE001
        return None



# ── la porta si apre da dentro ────────────────────────────────────────────────
#
# LA META' CHE MANCAVA. `handoff.writer_from_link` esisteva dal 14 agosto e non
# la chiamava nessuno: il nodo si puntava a una stanza **solo** all'avvio,
# dall'ambiente, e una persona che apriva l'assistente e firmava non poteva
# dirgli dove scrivere. La firma diceva CHI parla e non DOVE arriva.
#
# E la regola del docstring di `writer_from_env` resta, parola per parola:
# *«opening a browser as a side effect of a module load is the kind of thing
# that hangs a service at boot»*. Qui non si carica un modulo — qui c'è una
# persona che ha appena premuto un bottone, che è esattamente il posto che
# quella riga indicava.

class PointAt(BaseModel):
    """Dove il nodo deve scrivere. Un link, oppure le due cose che ci sono
    dentro — perché un tablet in tenda non sempre ha da dove incollare."""

    link: str = ""
    server: str = ""
    room: str = ""


def _bearer(request: Request) -> str:
    """La firma di chi chiama, cruda.

    Serve per una cosa sola: chiedere al realm un token PER LA STANZA a nome di
    questa persona (`handoff.exchange`). Non viene mai scritta, mai registrata,
    e non finisce in nessun ambiente di processo — vedi `_repoint`.
    """
    raw = request.headers.get("authorization") or ""
    return raw.split(" ", 1)[1].strip() if raw.lower().startswith("bearer ") else ""


def _who(request: Request) -> str:
    """Chi chiede di prendere il nodo, o un rifiuto.

    Un nodo non si prende senza identità: quello che si scrive da qui, nella
    stanza, portera' il nome di chi lo tiene, e un nodo tenuto da nessuno
    scriverebbe a nome di nessuno.
    """
    orcid = _author(request)
    if not orcid:
        raise HTTPException(
            status_code=403,
            detail="Questo nodo non chiede una firma (auth in modo dev), quindi "
                   "non sa a nome di chi lo prenderesti. Nella stanza tutto "
                   "quello che si scrive da qui porta il nome di chi tiene il "
                   "nodo: senza un nome, non si punta a niente.")
    return orcid


def _swap(new_writer) -> None:
    """Metti lo scrivano nuovo al posto di quello vecchio, e chiudi il vecchio.

    Tre righe, e sono le stesse tre dell'avvio: il registro LEGA lo scrivano ai
    dieci descrittori al momento in cui li costruisce, quindi ripuntare il nodo
    vuol dire ricostruire il registro. Non è una furbizia — è la stessa
    riga 158 di questo file, chiamata una seconda volta.

    Una richiesta già in volo tiene il riferimento vecchio e finisce dove era
    diretta. È la cosa giusta: una scheda a meta' non cambia stanza sotto le
    mani di chi la sta salvando.
    """
    global WRITER, REGISTRY
    vecchio = WRITER
    WRITER = new_writer
    REGISTRY = build_registry(new_writer, STORE)
    leave = getattr(vecchio, "close", None)
    if leave is not None and vecchio is not new_writer:
        try:
            leave()                    # alzarsi dalla stanza di prima
        except Exception:              # noqa: BLE001
            pass


def _room_state(*, reveal: bool = False) -> Dict[str, Any]:
    """Dove scrive il nodo, chi lo tiene, e cosa è rimasto indietro."""
    stato: Dict[str, Any] = {
        "writes_to": writer_describe(WRITER),
        "room": getattr(WRITER, "room_id", None),
        "server": getattr(WRITER, "base_url", None),
        "holding": HOLDING.describe(reveal=reveal),
        "queues": queues_beside(LOCAL.path),
    }
    return stato


@v1.get("/room", tags=["room"])
def room_state(request: Request) -> Dict[str, Any]:
    """In quale stanza scrive questo nodo, e chi lo tiene.

    Sotto `/v1` e non in `/health` perché **il nome di chi tiene il nodo lo
    vede chi ha firmato**. `/health` è pubblica — la serve un nodo che chiunque
    sulla stessa rete interroga — e «chi sta lavorando in questa tenda adesso»
    non è una cosa da dare a chi non si è presentato. Il FATTO (tenuto,
    libero) sta anche li'; il nome sta qui.
    """
    _author(request)                   # firma valida, o 401 dall'autenticatore
    return _room_state(reveal=True)


@v1.post("/room", tags=["room"])
def point_at(request: Request, body: PointAt = Body(...)) -> Dict[str, Any]:
    """Punta questo nodo a una stanza, a nome di chi chiama.

    ## L'ORDINE DEI QUATTRO PASSI, e perché è questo

    1. **la presa**, prima di tutto. È l'esclusione: due persone che puntano lo
       stesso nodo insieme sono il difetto, non una corsa da arbitrare dopo.
    2. **la credenziale**, poi. Il token della stanza si chiede al realm A NOME
       di chi ha firmato qui (`handoff.exchange`), e mai si inoltra il suo pari
       pari: un token coniato per questo nodo, presentato altrove, è il
       *confused deputy* da manuale.
    3. **la porta**, e si prova PRIMA di scambiare lo scrivano. Ripuntare un
       nodo a una stanza che non risponde lo lascerebbe fermo in un posto dove
       non può scrivere — e la frase del relay («questa stanza è in sola
       lettura per te») è precisamente quello che chi chiede deve leggere.
    4. **lo scambio**, per ultimo, quando tutto il resto è andato.

    Se il passo 2 o il 3 falliscono e la presa è stata presa adesso, **si
    lascia**: aver preso un nodo e non averne ottenuto niente non è tenerlo.
    """
    who = _who(request)
    try:
        dove = (handoff.parse(body.link) if body.link.strip()
                else {"server": body.server.strip().rstrip("/"),
                      "room": body.room.strip()})
    except handoff.HandoffError as storto:
        raise HTTPException(status_code=400, detail=str(storto)) from None
    if not dove.get("server") or not dove.get("room"):
        raise HTTPException(
            status_code=400,
            detail="serve un link di consegna, oppure un server e una stanza.")

    gia_mio = HOLDING.holder() is not None and HOLDING.holder().who == who
    try:
        presa = HOLDING.take(who, server=dove["server"], room=dove["room"])
    except HeldByAnother as altro:
        raise HTTPException(status_code=409, detail=str(altro)) from None
    except ValueError as vuoto:
        raise HTTPException(status_code=403, detail=str(vuoto)) from None

    def lascia_se_serve():
        if not gia_mio:
            HOLDING.release(who)

    # ── 2 e 3, insieme: si prova la porta con il token che si è ottenuto ────
    bearer = _bearer(request)
    try:
        token, come = _room_credential(bearer, dove["server"])
    except handoff.NoCredential as manca:
        lascia_se_serve()
        raise HTTPException(status_code=503, detail=str(manca)) from None

    candidato = RoomWriter(dove["server"], dove["room"], token,
                           fallback=LOCAL, spool=LARDER,
                           bridge=bridge_for(LOCAL.path, dove["room"]))
    try:
        candidato._seated()
    except Exception as chiusa:        # noqa: BLE001 — porta chiusa o rete
        lascia_se_serve()
        raise HTTPException(
            status_code=502,
            detail=f"La stanza «{dove['room']}» non si è aperta: {chiusa}. "
                   f"Il nodo scrive ancora dove scriveva prima.") from None

    _swap(candidato)
    HOLDING.pointed_at(server=dove["server"], room=dove["room"])
    stato = _room_state(reveal=True)
    stato["ok"] = True
    stato["credential"] = come
    stato["message"] = (
        f"{presa['message']} Scrivo nella stanza «{dove['room']}» "
        f"su {dove['server']}, a nome tuo.")
    return stato


@v1.delete("/room", tags=["room"])
def unpoint(request: Request) -> Dict[str, Any]:
    """Torna al container locale, e lascia il nodo.

    Il ritorno indietro esiste perché esiste l'andata: un nodo che si può
    puntare e non spuntare è un nodo che resta dell'ultima persona che l'ha
    toccato finché qualcuno non lo riavvia.

    Il lavoro **non si perde**: la coda della stanza da cui si esce è la sua e
    resta sul disco, si vede in `queues`, e riparte quando il nodo torna li'.
    """
    who = _who(request)
    tenuta = HOLDING.holder()
    if tenuta is not None and tenuta.who != who:
        raise HTTPException(
            status_code=409,
            detail=f"Questo nodo è in mano a {tenuta.who}: non lo puoi "
                   f"riportare al container locale al posto suo.")
    lasciato = HOLDING.release(who)
    _swap(LOCAL)
    stato = _room_state(reveal=True)
    stato["ok"] = True
    stato["message"] = (
        "Torno a scrivere nel container locale di questo nodo."
        + (" Il nodo è libero." if lasciato else "")
        + (f" Restano {sum(q['pending'] for q in stato['queues'])} operazioni "
           f"in coda per le stanze di prima: ripartono quando il nodo ci torna."
           if stato["queues"] else ""))
    return stato


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


@public.get("/{shell_file:path}", tags=["device"], include_in_schema=False)
def shell_file(shell_file: str) -> Any:
    """Any file of the shell, from `web/`.

    ONE DIRECTORY, ONE SOURCE. `_shell_files` decides what the service worker
    precaches; this decides what the node serves, and they read the same
    directory with the same suffix allowlist. Two lists would be two answers,
    and the failure mode is precisely the one §3 is about: a file that is
    cached and not served, or served and not cached, and neither shows up until
    somebody is in a trench.

    Registered LAST among the public routes so `/`, `/health` and `/v1/...`
    match first — a catch-all declared earlier would swallow them.

    The suffix allowlist is what keeps this from being a directory browser: a
    `.py` or a `.md` next to the page is not the shell, and the resolved path is
    checked to be INSIDE `web/` so `..` cannot walk out of it.
    """
    web = pathlib.Path(__file__).resolve().parent.parent / "web"
    candidate = (web / shell_file).resolve()
    try:
        candidate.relative_to(web.resolve())
    except ValueError:
        raise HTTPException(status_code=404, detail="not part of the shell")
    if (not candidate.is_file()
            or candidate.name in _NOT_SHELL
            or candidate.suffix.lower() not in _SHELL_SUFFIXES):
        raise HTTPException(status_code=404, detail="not part of the shell")
    kind = {".css": "text/css", ".js": "application/javascript",
            ".mjs": "application/javascript", ".html": "text/html",
            ".svg": "image/svg+xml", ".json": "application/json",
            ".webmanifest": "application/manifest+json"}.get(
                candidate.suffix.lower(), "application/octet-stream")
    return FileResponse(candidate, media_type=kind)


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

# L'ORDINE CONTA, ED È QUESTO PER UNA RAGIONE MISURATA.
#
# `public` finisce con la rotta jolly della conchiglia (`/{shell_file:path}`), e
# FastAPI prova le rotte nell'ordine in cui sono registrate. Con `public` per
# primo, **ogni GET sotto `/v1/` che vive su questo router veniva raccolto dal
# jolly** e rispondeva `404 not part of the shell` senza mai arrivare al suo
# gestore: misurato il 2 ottobre su `GET /v1/room`, che esisteva e non si
# raggiungeva. Le altre `/v1/...` in GET non se n'erano accorte perché stanno
# tutte su `public`, dichiarate PRIMA del jolly.
#
# I due router non condividono nessun percorso, quindi invertirli non cambia
# niente per nessuno tranne che il jolly resta l'ultimo — che è l'unica cosa che
# un jolly deve essere. `test_la_conchiglia_non_mangia_le_rotte_di_v1` lo tiene.
app.include_router(v1)
app.include_router(public)
