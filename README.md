# stratigraph-chatbot — the StratiGraph field assistant

> **An orchestrator, not a re-implementation.** Voice or text comes in, an intent
> comes out, a tool acts, and what changes is a **DTC-attributed write on the
> shared graph**. It does not rebuild voice capture (ARC's **ATRIUM** does that),
> nor speech-to-text (**Whisper** runs ON the field node — see below), nor the
> structured US database (**PyArchInit**, **iDAI.field**), nor photos and 3D
> (**Tropy**, the object store, **Heriverse**). What was missing is the
> **convergence and orchestration layer** — and that is this.
>
> Design note: `StratiGraph-grp / WP 07 / stratigraph-chatbot_design-note.md`.
> Working name; a nicer one to be chosen together (candidate: **Groma**).

## The centre of gravity: the tool contract

Read [`app/contract.py`](app/contract.py) first. Everything else here —
the speech engine, the intent parser, the web app, even the tools — is
replaceable. The contract is not: it is the shape that lets a partner add a
capability by **writing a descriptor and a thin adapter**, instead of
re-architecting anything.

```python
ToolDescriptor(
    name="create_su",
    intents=["crea una nuova scheda", "nuova US"],   # what the router may map
    input_schema=[Slot("us", required=True)],        # what it needs
    output="graph-delta",                            # what it changes
    handler=…,                                       # the service behind it
)
```

`ToolRegistry` registers, lists and routes. `invoke` runs, and returns two
things that are not the same and are both required: a **graph delta**
(`crmdig:D7`, author = the ORCID of the token) and a **spoken message**, because
the person hearing it has their hands in the soil.

**Four refusals, and each is a decision:** an unknown intent is a clean *I cannot
do that* (never a nearest match — acting on a sentence nobody meant puts a wrong
record in a graph that outlives the excavation); a declared tool with no adapter
says so; a missing slot is **asked for, never invented**; and a writing tool
without an author is **refused**.

## The five MVP tools

Grown from Elisa Dalla Longa's field card — a thick forex card of colour-coded
voice commands, an accessibility artefact that doubles as the command spec.

| said in the field | tool | what changes |
|---|---|---|
| "crea una nuova scheda, US 12" | `create_su` | a StratigraphicUnit in the graph |
| "in che progetto sto lavorando" | `which_project` | nothing — it answers |
| "questa foto è per la US 12" | `attach_photo_to_su` | bytes in the store, a resource on the unit |
| "ti passo delle foto" | `ingest_photos` | several at once |
| "quante unità abbiamo registrato" | `query_kg` | nothing — it answers from the graph |

## The three rules it inherits

1. **the domain lives in s3Dgraphy** — what a stratigraphic unit *is* is the
   library's answer, and the tools ask it rather than restating it;
2. **the author is the token's** — ORCID through the Keycloak realm the stack
   already runs. Never a field the client filled in: a record without a
   verifiable hand behind it is one nobody can defend three years later;
3. **offline-first** — the Field Computing Node is the host. Nothing calls out
   to a cloud; the shared room adds reach, it is not a precondition.

## Where a write lands

Two implementations of one seam, chosen by what the excavation has:

* **room** (`EM_SERVER_URL` + `EM_CHATBOT_ROOM`) — the StratiGraph Server room on the
  node. EMStudio joins the same room and sees the unit appear. *Convergence on
  the graph, not coupling*: the assistant is another client, exactly like
  EMStudio and EMtools;
* **local container** — no room, or the room is unreachable. The delta goes into
  the node's own em.json and syncs later. Not a degraded mode: the field case is
  the base case.

`/health` says which one is answering, because *"is what I just said reaching the
others?"* is the first question anybody asks.

## Running it

```bash
python3 -m venv .venv
.venv/bin/pip install -e . -e ../s3Dgraphy
.venv/bin/uvicorn app.main:app --reload --port 8020
curl -s localhost:8020/health | python3 -m json.tool
```

Open <http://localhost:8020/> on a phone on the same network: that is the field
client. A PWA rather than a native app (ATRIUM is a web app, the ecosystem is
web-first, one codebase reaches both platforms with camera, microphone and GPS)
— the heavy AI stays on the node and the device stays thin.

Tests:

```bash
.venv/bin/python -m pytest -q      # 46 passed
```

## Configuration

| variable | what it does |
|---|---|
| `OIDC_ISSUER` (or `TOKEN_ENDPOINT`), `OIDC_AUDIENCE` | Keycloak/ORCID. Half-configured → the process refuses to start |
| `EM_SERVER_URL`, `EM_CHATBOT_ROOM`, `EM_CHATBOT_TOKEN` | the shared room. A room without a token is a startup refusal |
| `EM_CHATBOT_CONTAINER`, `EM_CHATBOT_STUDY` | the node's own container, for the offline case |
| `MINIO_ENDPOINT` / `_ACCESS_KEY` / `_SECRET_KEY` / `_BUCKET` | the photos. **The same variables StratiGraph Server reads** |
| `EM_CHATBOT_WHISPER_MODEL` | a Whisper model ON THIS NODE. Set → `/v1/listen` transcribes here and the field page sends audio. Unset → the page transcribes in the browser (or ATRIUM does) and sends text to `/say`. Half-configured (a path with no model) refuses, it does not fall back |

## Hearing: three roads, and the node says which

`Whisper runs downstream on the field node` used to be the first line of this
file, and **that word `downstream` was the whole misunderstanding**: it reads as
*elsewhere, later, in the institutional node*, and it is the only reason we
believed local transcription was still to come. It is not. The seam and both
implementations are in `app/speech.py`, `POST /v1/listen` exists, and `/health`
already declares which engine this node has. What was missing was a page that
used it.

**The node may carry the models; the device stays thin.** An FCN is a mini-PC
switched on at the dig: it is the right place for a frugal model. A borrowed
tablet is not.

| road | what chooses it | what it needs | with no network |
|---|---|---|---|
| **the node transcribes** | `/health` says `speech: "whisper (…)"` | `EM_CHATBOT_WHISPER_MODEL` on the node; a microphone on the device | **works** — the model is a LAN away, and the LAN is the node's own wifi |
| **the browser transcribes** | `/health` says `speech: "passthrough"` and the browser has `SpeechRecognition` | nothing on the node | **does not work.** On Chrome the recording goes to a REMOTE recogniser. The page says so instead of «I did not hear anything» |
| **you type** | neither of the above | nothing | works |

The page asks `/health` on every poll, so a node that gains a model is followed
rather than remembered. And the language it transcribes in is **the node's**, not
the reader's: `/v1/tools` publishes `command_language`, because the commands are
the node's phrasebook (today Italian) and transcribing Italian speech with a
Polish model produces words — the wrong ones — and presents itself as «the
assistant misunderstands me».

### The rule this is one case of

> **If the node has an AI you have functions; if it does not, you do not — and
> the surface says so.** A function that depends on a model is never faked, never
> silently disabled, and never offered as a button that does nothing: the node
> declares the capability, the surface shows what is there and names what is
> missing.

`app/speech.py` already worked this way for transcription — *it exists when it is
configured, it is never chosen in silence, and a half-configuration refuses with
a sentence* — and `/health` already declared it. This slice only opened the road
to the one client that would use it.

## What is deliberately not here

The partners' adapters (ATRIUM voice-sheets, PyArchInit REST, ARC Document
Analysis, FBK Shape Recognition) — those are later slices, and the contract is
ready for them. The real LLM and Whisper models — those are a deployment on the
node. The US sheet-window in EMStudio — a different repository. Discovery.

`app/auth.py` and `app/assets.py` are **deliberate copies** of StratiGraph Server's, with
the service name changed; at the third service they become a shared package.
Stated here rather than discovered later.
