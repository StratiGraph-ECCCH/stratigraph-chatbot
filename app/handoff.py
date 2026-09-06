"""The deep-link, consumed — a field node configured by a link instead of by env.

Before this, pointing the assistant at a room meant three environment variables:
`EM_SERVER_URL`, `EM_CHATBOT_ROOM`, `EM_CHATBOT_TOKEN`. The third one is a
credential in a process environment, which is a credential in `ps`, in a crash
dump, and in whatever wrote the unit file.

Now: one link.

    stratigraph://open?server=<addr>&room=<id>

**It carries a place and never a permission.** The node signs in against that
server itself (Authorization Code + PKCE, public client) and holds the token in
memory — so a link pasted into a chat leaks nothing, and the token belongs to the
person who ran the command rather than to whoever wrote the deployment.

The grammar is StratiGraph Server's (`app/handoff.py` there) and is implemented
here rather than fetched, for the same reason EMStudio implements it: a client
that had to reach a server to learn WHICH server to reach could not start. The
three copies are held together by the same strings in all three suites.

**The environment still works**, and is now the fallback rather than the way in.
A field node that boots headless into a known room has no browser to sign in
with, and taking that away to make a point would be worse than the duplication.
"""

from __future__ import annotations

import urllib.parse
from typing import Any, Callable, Dict, Optional

SCHEME = "stratigraph"
ACTION = "open"

#: Refused rather than ignored: accepting one teaches whoever built the link that
#: sending one works, and then the contract has no property left.
FORBIDDEN = ("token", "access_token", "id_token", "password", "secret", "code",
             "authorization", "bearer", "api_key")


class HandoffError(ValueError):
    """A link that is not a handoff, said rather than half-read."""


def parse(link: str) -> Dict[str, str]:
    """`{server, room}` out of either form of the link, or a sentence."""
    raw = str(link or "").strip()
    if not raw:
        raise HandoffError("empty link")
    parsed = urllib.parse.urlsplit(raw)
    if parsed.scheme == SCHEME:
        action = parsed.netloc or parsed.path.lstrip("/")
        if action != ACTION:
            raise HandoffError(
                f"unknown action {action!r}: this scheme understands "
                f"{SCHEME}://{ACTION}")
    elif parsed.scheme in ("http", "https"):
        if not parsed.path.rstrip("/").endswith(f"/{ACTION}"):
            raise HandoffError(
                f"not a handoff link: {raw} (expected a path ending in /{ACTION})")
    else:
        raise HandoffError(
            f"not a handoff link: {raw} (expected {SCHEME}://{ACTION}?… or an "
            f"https link to /{ACTION})")

    query = urllib.parse.parse_qs(parsed.query)
    carried = sorted(k for k in query if k.lower() in FORBIDDEN)
    if carried:
        raise HandoffError(
            f"this link carries {', '.join(carried)} — a handoff names a place "
            f"and never a permission. Refused so that sending one never starts "
            f"working: the assistant signs in by itself.")

    room = (query.get("room") or [""])[0].strip()
    if not room:
        raise HandoffError("the link names no room")
    server = (query.get("server") or [""])[0].strip().rstrip("/")
    if not server:
        if parsed.scheme in ("http", "https"):
            server = f"{parsed.scheme}://{parsed.netloc}"
        else:
            raise HandoffError("the link names no server")
    return {"server": server, "room": room}


# ── signing in, dependency-free ─────────────────────────────────────────────

def auth_config(server: str, *, timeout: float = 10.0
                ) -> Optional[Dict[str, Any]]:
    """How that node wants a client to sign in — `GET /v1/auth-config`.

    `None` when it has no OIDC at all: a dev node runs open, and that is a fact
    about the deployment rather than a failure to report.
    """
    import json
    import urllib.error
    import urllib.request

    url = f"{server.rstrip('/')}/v1/auth-config"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as answer:
            if answer.status != 200:
                return None
            return json.loads(answer.read() or b"{}")
    except (urllib.error.URLError, OSError, ValueError):
        return None


def sign_in(server: str, *, open_browser: Optional[Callable[[str], Any]] = None,
            timeout: float = 300.0) -> Optional[str]:
    """Authorization Code + PKCE against that server; the token stays in memory.

    Stdlib only — no OIDC library — for the same reason the Blender room client
    hand-rolls its WebSocket: this has to be able to run where `pip install` is
    not a step somebody can take. It is short because PKCE is small; what makes
    it correct is that the three things easy to get wrong are all here: `S256`
    (never `plain`), the `state` check, and no client secret.

    The redirect comes back to a LOOPBACK listener, which is what a native app is
    supposed to use (RFC 8252). Nothing touches disk at any point.

    Returns the access token, or `None` when the node has no OIDC — in which case
    the caller writes without one, which is what that node expects.
    """
    import base64
    import hashlib
    import http.server
    import json
    import secrets
    import threading
    import urllib.request
    import webbrowser

    config = auth_config(server)
    if not config or not config.get("authorization_endpoint"):
        return None

    verifier = base64.urlsafe_b64encode(
        secrets.token_bytes(32)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(24)
    got: Dict[str, str] = {}
    done = threading.Event()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):                                  # noqa: N802
            query = urllib.parse.parse_qs(
                urllib.parse.urlsplit(self.path).query)
            got.update({k: v[0] for k, v in query.items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"<h1>Signed in</h1><p>You can close this tab and "
                             b"go back to the assistant.</p>")
            done.set()

        def log_message(self, *args):                      # noqa: A003
            pass                                           # not our stdout

    listener = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    redirect_uri = f"http://127.0.0.1:{listener.server_port}/"
    threading.Thread(target=listener.handle_request, daemon=True).start()

    url = config["authorization_endpoint"] + "?" + urllib.parse.urlencode({
        "response_type": "code",
        "client_id": config.get("client_id") or "em-console",
        "redirect_uri": redirect_uri,
        "scope": config.get("scope") or "openid profile email",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
    })
    (open_browser or webbrowser.open)(url)
    if not done.wait(timeout):
        listener.server_close()
        raise HandoffError(
            f"nobody completed the sign-in within {int(timeout)}s. The link is "
            f"still good — run the command again.")
    listener.server_close()

    if got.get("error"):
        raise HandoffError(f"sign-in refused: "
                           f"{got.get('error_description') or got['error']}")
    if got.get("state") != state:
        # the one check that makes the round trip mean anything: a code
        # delivered with somebody else's state is one this process did not ask for
        raise HandoffError("the sign-in state did not match — refusing a code "
                           "this process did not ask for")
    code = got.get("code")
    if not code:
        raise HandoffError("no authorization code came back")

    body = urllib.parse.urlencode({
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": config.get("client_id") or "em-console",
        "code_verifier": verifier,
        # NO client_secret: a public client that sent one would be publishing it
    }).encode()
    request = urllib.request.Request(
        config["token_endpoint"], data=body, method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(request, timeout=30) as answer:
        payload = json.loads(answer.read() or b"{}")
    token = str(payload.get("access_token") or "")
    if not token:
        raise HandoffError("the sign-in returned no access token")
    return token


# ── il token della stanza, quando la firma è di chi chiama ──────────────────

class NoCredential(RuntimeError):
    """Il nodo non ha come entrare in quella stanza, e la frase dice cosa manca.

    Sua e non un `HandoffError`: il link era buono. È la credenziale che non c'è,
    e le due cose si riparano in posti diversi — una nel link, l'altra nel realm.
    """


#: Le variabili dello scambio, e sono TRE perché tre sono le cose che il realm
#: deve sapere: per chi è il token nuovo, chi lo chiede, e con che prova.
#: Nessuna è una credenziale di una persona: sono configurazione del
#: dispiegamento, come l'issuer.
EXCHANGE_KEYS = ("EM_ROOM_AUDIENCE", "OIDC_CLIENT_ID", "OIDC_CLIENT_SECRET")


def exchange_missing(env: Optional[Dict[str, str]] = None) -> list:
    """Quali delle tre mancano. Vuota vuol dire che lo scambio si può fare."""
    import os
    source = env if env is not None else os.environ
    return [name for name in EXCHANGE_KEYS
            if not (source.get(name) or "").strip()]


def exchange(bearer: str, *, token_endpoint: str,
             env: Optional[Dict[str, str]] = None,
             timeout: float = 15.0) -> str:
    """Scambia il token di chi chiama con uno buono per la stanza (RFC 8693).

    ════════════════════════════════════════════════════════════════════════════
    ## PERCHÉ UNO SCAMBIO E NON UN INOLTRO

    Il token che una persona presenta a questo nodo è stato coniato **per questo
    nodo** (`aud: em-chatbot`). Passarlo pari pari al relay sarebbe chiedere a un
    terzo di accettare una prova che non era per lui: se la accettasse, questo
    servizio potrebbe usare il token dei suoi utenti ovunque — il *confused
    deputy* nella sua forma da manuale.

    Lo scambio è la risposta che OAuth dà a quella domanda: il nodo si presenta
    con **la propria** identità di client e chiede al realm un token *per il
    relay*, che vale **come la persona che ha firmato qui**. È il realm a
    decidere se questo nodo può farlo, ed è la decisione giusta da mettere lì.

    Il `client_secret` è una credenziale **del dispiegamento**, non di una
    persona, e non viene mai né stampato né scritto: si legge dall'ambiente e va
    nel corpo di una `POST`.

    ## E SE IL REALM NON È CONFIGURATO

    Si alza `NoCredential` con dentro **cosa manca**, e il nodo non ripiega su
    niente. Il ripiego che verrebbe in mente — usare il token del nodo preso
    dall'ambiente — attribuirebbe il lavoro di una persona al dispiegamento, che
    è esattamente il difetto che questa notte esiste per togliere.
    """
    import json
    import os
    import urllib.error
    import urllib.parse
    import urllib.request

    source = dict(env if env is not None else os.environ)
    mancano = exchange_missing(source)
    if mancano:
        raise NoCredential(
            "questo nodo non può chiedere al realm un token per la stanza: "
            f"manca {', '.join(mancano)}. Sono configurazione del nodo (non "
            "una password di nessuno) e vanno messe dove stanno OIDC_ISSUER e "
            "OIDC_AUDIENCE.")

    body = urllib.parse.urlencode({
        "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
        "subject_token": bearer,
        "subject_token_type": "urn:ietf:params:oauth:token-type:access_token",
        "requested_token_type": "urn:ietf:params:oauth:token-type:access_token",
        "audience": source["EM_ROOM_AUDIENCE"].strip(),
        "client_id": source["OIDC_CLIENT_ID"].strip(),
        "client_secret": source["OIDC_CLIENT_SECRET"].strip(),
    }).encode()
    request = urllib.request.Request(
        token_endpoint, data=body, method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as answer:
            payload = json.loads(answer.read() or b"{}")
    except urllib.error.HTTPError as problem:
        detail = ""
        try:
            detail = json.loads(problem.read() or b"{}").get(
                "error_description") or ""
        except Exception:                             # noqa: BLE001
            pass
        raise NoCredential(
            f"il realm ha rifiutato lo scambio ({problem.code}"
            f"{': ' + detail if detail else ''}). Di solito vuol dire che a "
            f"questo client non è consentito lo scambio verso "
            f"«{source['EM_ROOM_AUDIENCE']}»: si abilita nel realm, non qui."
        ) from None
    except (urllib.error.URLError, OSError, ValueError) as problem:
        raise NoCredential(
            f"il realm non ha risposto ({problem}): senza di lui questo nodo "
            f"non ha come presentarsi alla stanza.") from None
    token = str(payload.get("access_token") or "")
    if not token:
        raise NoCredential("lo scambio non ha restituito un token")
    return token


def writer_from_link(link: str, *, fallback: Any = None,
                     sign_in_with: Optional[Callable[[str], Optional[str]]] = None):
    """A link in, a configured `RoomWriter` out. The whole point of the contract.

    `sign_in_with` is the seam a test uses; the default is the real PKCE round
    trip, which needs a person and a browser.
    """
    from .writer import RoomWriter

    where = parse(link)
    token = (sign_in_with or sign_in)(where["server"])
    return RoomWriter(where["server"], where["room"], token or "",
                      fallback=fallback)
