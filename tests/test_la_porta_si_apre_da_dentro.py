"""La porta si apre da dentro: una persona firma e il nodo va nella sua stanza.

════════════════════════════════════════════════════════════════════════════════
## IL CASO CHE LO FA SCATTARE

`handoff.writer_from_link` esiste dal 14 agosto e non la chiamava nessuno: il
nodo si puntava a una stanza **solo all'avvio, dall'ambiente**. Una persona che
apriva l'assistente e firmava diceva CHI parla e non DOVE arriva.

E la ragione per cui non bastava aggiungere una rotta e basta è misurata nel
primo blocco di questo file: **lo scrivano è un singleton**, e la coda del ponte
apparteneva al nodo e non alla stanza. Una rotta che ripunta, sopra quelle due
cose, avrebbe consegnato il lavoro di una stanza dentro un'altra.

*Cambiare destinazione a un lavoro già accodato non è ripuntare, è perderlo con
un'altra faccia.*
"""

from __future__ import annotations

import datetime
import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

jwt = pytest.importorskip("jwt")
pytest.importorskip("cryptography")
pytest.importorskip("websockets", reason="serve il client websockets")

from fastapi.testclient import TestClient                     # noqa: E402

from app import handoff                                       # noqa: E402
from app import main as main_module                           # noqa: E402
from app.assets import InMemoryAssetStore                     # noqa: E402
from app.auth import OidcSettings, authenticator              # noqa: E402
from app.bridge import bridge_for, queues_beside, room_key    # noqa: E402
from app.contract import GraphDelta                           # noqa: E402
from app.holding import HeldByAnother, Holding                # noqa: E402
from app.tools import build_registry                          # noqa: E402
from app.writer import LocalWriter, RoomWriter                # noqa: E402
from tests.test_room_writer_wire import FakeRelay             # noqa: E402

ISSUER = "https://keycloak.example/realms/stratigraph"
AUDIENCE = "stratigraph-chatbot"
KID = "field-key-1"
DEV = "0000-0002-1825-0097"
VIEWER = "0000-0001-5109-3700"


# ═══ l'impalcatura ═══════════════════════════════════════════════════════════

@pytest.fixture()
def realm():
    """Un realm di cui questo test possiede la chiave, con due identità."""
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    class _Keys:
        def key_for(self, kid):
            assert kid == KID
            return key.public_key()

    prima = (authenticator.settings, authenticator._jwks)
    authenticator.settings = OidcSettings(
        issuer=ISSUER, audience=AUDIENCE,
        jwks_uri=f"{ISSUER}/protocol/openid-connect/certs")
    authenticator._jwks = _Keys()

    def firma(orcid=DEV):
        adesso = datetime.datetime.now(datetime.timezone.utc)
        return jwt.encode({"sub": orcid, "iss": ISSUER, "aud": AUDIENCE,
                           "orcid": orcid, "iat": adesso,
                           "exp": adesso + datetime.timedelta(minutes=30)},
                          key, algorithm="RS256", headers={"kid": KID})

    try:
        yield firma
    finally:
        authenticator.settings, authenticator._jwks = prima


@pytest.fixture()
def nodo(tmp_path, monkeypatch):
    """Un nodo con il suo container locale, la sua presa e nessuna stanza."""
    local = LocalWriter(str(tmp_path / "scavo.em.json"), study="Saggio B")
    store = InMemoryAssetStore()
    presa = Holding(idle_after=1200)
    monkeypatch.setattr(main_module, "LOCAL", local)
    monkeypatch.setattr(main_module, "WRITER", local)
    monkeypatch.setattr(main_module, "LARDER", None)
    monkeypatch.setattr(main_module, "STORE", store)
    monkeypatch.setattr(main_module, "HOLDING", presa)
    monkeypatch.setattr(main_module, "REGISTRY", build_registry(local, store))
    # niente scambio configurato e nessun token d'ambiente, se non lo dice il test
    for nome in handoff.EXCHANGE_KEYS + ("EM_CHATBOT_TOKEN",):
        monkeypatch.delenv(nome, raising=False)
    return local, presa


@pytest.fixture()
def client(nodo, realm):
    with TestClient(main_module.app) as c:
        yield c


def _con(firma, orcid=DEV):
    return {"Authorization": "Bearer " + firma(orcid)}


def _ambiente(monkeypatch, token="tok-del-dispiegamento"):
    monkeypatch.setenv("EM_CHATBOT_TOKEN", token)


# ═══ 1 · cosa succede OGGI con due firme ═════════════════════════════════════

def test_due_firme_scrivono_nello_stesso_posto_ma_con_nomi_diversi(
        client, realm, nodo):
    """La misura di §1, dentro la suite perché una misura che vale la pena fare
    vale la pena tenerla.

    Sul container locale **l'autore è già giusto per ciascuno**: `_author` lo
    prende dal token a ogni richiesta. Quello che i due condividono è **dove**
    finisce il lavoro, non a nome di chi risulta.
    """
    local, _ = nodo
    scrivano_prima = main_module.WRITER
    for chi, numero in ((DEV, "10"), (VIEWER, "20")):
        risposta = client.post(
            "/v1/say", json={"transcript": f"crea una nuova scheda, US {numero}"},
            headers=_con(realm, chi))
        assert risposta.status_code == 200, risposta.text

    assert main_module.WRITER is scrivano_prima, "un solo scrivano, per tutti"
    doc = json.loads(pathlib.Path(local.path).read_text(encoding="utf-8"))
    sezione = next(iter(doc["graphs"].values()))
    autori = {n["id"]: (n.get("data") or {}).get("created_by")
              for n in sezione["nodes"] if n.get("node_type") == "US"}
    assert autori == {"US10": DEV, "US20": VIEWER}


def test_nella_stanza_invece_il_nome_e_quello_del_NODO(tmp_path):
    """E questa è la metà che rende «una persona alla volta» obbligatoria.

    Il relay prende l'autore dal token e non dal payload — lo dice la sua
    docstring, letta e non dedotta. Qui si misura il lato client: le operazioni
    partono **senza autore**, quindi nella stanza il nome è quello del token del
    nodo, chiunque abbia parlato.
    """
    with FakeRelay() as relay:
        local = LocalWriter(str(tmp_path / "scavo.em.json"), study="Scavo")
        writer = RoomWriter(f"http://127.0.0.1:{relay.port}", "stanza", "tok",
                            timeout=2.0, fallback=local,
                            bridge=bridge_for(local.path, "stanza"))
        writer.apply(GraphDelta(
            nodes=[{"id": "US1", "node_type": "US", "name": "US 1",
                    "data": {"created_by": VIEWER}}],
            edges=[], process=None, author=VIEWER))
        [operazione] = [op for op in relay.ops if op.get("id") == "US1"]
        assert "author" not in operazione, (
            "il client non dichiara l'autore: lo mette il token, e il token è "
            "del nodo")
        assert operazione["node"]["data"]["created_by"] == VIEWER, (
            "nel payload c'è chi ha parlato — e le due cose possono "
            "contraddirsi, che è il fatto")


# ═══ 2 · la presa: chi arriva secondo vede una frase ═════════════════════════

def test_chi_arriva_secondo_legge_chi_lo_tiene(client, realm, nodo,
                                               monkeypatch):
    _, presa = nodo
    _ambiente(monkeypatch)
    with FakeRelay() as relay:
        indirizzo = f"http://127.0.0.1:{relay.port}"
        primo = client.post("/v1/room", json={"server": indirizzo, "room": "A"},
                            headers=_con(realm, DEV))
        assert primo.status_code == 200, primo.text

        secondo = client.post("/v1/room",
                              json={"server": indirizzo, "room": "B"},
                              headers=_con(realm, VIEWER))
        assert secondo.status_code == 409
        detto = secondo.json()["detail"]
        assert DEV in detto, "la frase dice CHI"
        assert "meno di un minuto" in detto, "e da quanto"
        assert "aspetta" in detto, "e cosa può fare"
        # e il nodo non si è mosso
        assert main_module.WRITER.room_id == "A"
        assert presa.holder().who == DEV


def test_un_nodo_fermo_si_prende_e_LO_DICE(client, realm, nodo, monkeypatch):
    """L'altra metà: una presa che nessuno può prendere è un blocco, e in una
    tenda di cantiere è la fine della giornata di qualcun altro.

    Subentrare in silenzio sarebbe la stessa sorpresa girata dall'altra parte.
    """
    _, presa = nodo
    _ambiente(monkeypatch)
    with FakeRelay() as relay:
        indirizzo = f"http://127.0.0.1:{relay.port}"
        client.post("/v1/room", json={"server": indirizzo, "room": "A"},
                    headers=_con(realm, DEV))
        # il telefono è stato messo giù: mezz'ora di silenzio, contata
        # sull'orologio monotono che è quello che la presa guarda
        presa._seen_at -= 1_800
        secondo = client.post("/v1/room",
                              json={"server": indirizzo, "room": "B"},
                              headers=_con(realm, VIEWER))
        assert secondo.status_code == 200, secondo.text
        detto = secondo.json()["message"]
        assert "era tenuto da" in detto and DEV in detto
        assert presa.holder().who == VIEWER
        assert main_module.WRITER.room_id == "B"


def test_lavorare_tiene_il_nodo(client, realm, nodo, monkeypatch):
    """La presa dura finché chi la tiene **lavora**: ogni atto autenticato la
    rinfresca. Un nodo tenuto per aver detto una volta «è mio» sarebbe di nuovo
    un blocco, solo con un'altra causa."""
    _, presa = nodo
    _ambiente(monkeypatch)
    with FakeRelay() as relay:
        client.post("/v1/room",
                    json={"server": f"http://127.0.0.1:{relay.port}", "room": "A"},
                    headers=_con(realm, DEV))
        presa._seen_at -= 1_000                 # sedici minuti di silenzio finti
        assert presa.describe()["idle_seconds"] >= 1_000
        client.post("/v1/say", json={"transcript": "crea una nuova scheda, US 5"},
                    headers=_con(realm, DEV))
        assert presa.describe()["idle_seconds"] < 5, "il lavoro l'ha rinfrescata"


def test_senza_firma_non_si_prende_niente(client, realm, nodo):
    risposta = client.post("/v1/room", json={"server": "http://x", "room": "A"})
    assert risposta.status_code == 401


# ═══ 3 · IL CANCELLO: la coda non cambia destinazione ════════════════════════

def test_IL_CANCELLO_la_coda_di_A_non_finisce_in_B(client, realm, nodo,
                                                   monkeypatch):
    """Operazioni accodate per A, si ripunta a B, e **in B non arrivano**.

    Costruito apposta: si scrive verso A con A irraggiungibile (le operazioni
    finiscono in coda), poi si ripunta a B che invece risponde, e si guarda cosa
    ha ricevuto B.
    """
    local, _ = nodo
    _ambiente(monkeypatch)
    with FakeRelay() as relay:
        vivo = f"http://127.0.0.1:{relay.port}"
        morto = "http://127.0.0.1:9"           # nessuno ascolta

        # ── A, irraggiungibile: il lavoro si accoda ──
        scrivano_a = RoomWriter(morto, "A", "tok", timeout=1.0, fallback=local,
                                bridge=bridge_for(local.path, "A"))
        main_module.WRITER = scrivano_a
        main_module.REGISTRY = build_registry(scrivano_a, main_module.STORE)
        client.post("/v1/say", json={"transcript": "crea una nuova scheda, US 77"},
                    headers=_con(realm, DEV))
        assert len(scrivano_a.bridge) >= 1, "in coda per A"
        in_coda_per_a = [op.get("id") for op in scrivano_a.bridge.pending()]
        assert "US77" in in_coda_per_a

        # ── si ripunta a B, che risponde ──
        prima_in_b = len(relay.ops)
        risposta = client.post("/v1/room", json={"server": vivo, "room": "B"},
                               headers=_con(realm, DEV))
        assert risposta.status_code == 200, risposta.text

        # ── e in B non è arrivato niente di A ──
        arrivate = [op.get("id") for op in relay.ops[prima_in_b:]]
        assert "US77" not in arrivate, (
            "il lavoro dettato per la stanza A è comparso nella stanza B")
        assert len(main_module.WRITER.bridge) == 0, "B parte con la sua coda vuota"
        # e quello di A è ancora lì, suo, e si vede
        code = {q["queue"]: q["pending"] for q in queues_beside(local.path)}
        mia = f"{pathlib.Path(local.path).name}.{room_key('A')}.pending.jsonl"
        assert code.get(mia, 0) >= 1, code
        assert risposta.json()["queues"], "e /v1/room lo dice a chi ha ripuntato"


def test_E_CON_UNA_CODA_SOLA_il_lavoro_di_A_FINISCE_IN_B(tmp_path):
    """La gemella della rottura, e misura **l'effetto**: con la coda del nodo —
    quella di ieri, non per stanza — le stesse operazioni entrano nell'altra
    stanza e nessuno può accorgersene, perché sono valide."""
    with FakeRelay() as relay:
        local = LocalWriter(str(tmp_path / "scavo.em.json"), study="Scavo")
        # ── la rottura: `bridge_for` senza stanza, come prima del 2 ottobre ──
        coda_del_nodo = bridge_for(local.path)
        a = RoomWriter("http://127.0.0.1:9", "A", "tok", timeout=1.0,
                       fallback=local, bridge=coda_del_nodo)
        a.apply(GraphDelta(nodes=[{"id": "US77", "node_type": "US",
                                   "name": "US 77"}],
                           edges=[], process=None, author=DEV))
        assert len(coda_del_nodo) == 1

        b = RoomWriter(f"http://127.0.0.1:{relay.port}", "B", "tok",
                       timeout=2.0, fallback=local, bridge=coda_del_nodo)
        b._seated()                                   # il rientro attraversa
        arrivate = [op.get("id") for op in relay.ops]
        assert "US77" in arrivate, (
            "ed ecco il danno: una scheda dettata per A è nella stanza B")


def test_la_coda_che_cera_gia_viene_adottata(tmp_path):
    """Un nodo che gira da ieri ha una coda senza nome di stanza, e magari del
    lavoro dentro. Lasciarla in un file che nessuno guarda più sarebbe la
    perdita del 27 settembre ripetuta da capo."""
    local = LocalWriter(str(tmp_path / "scavo.em.json"), study="Scavo")
    vecchia = bridge_for(local.path)              # la forma di prima
    vecchia.keep([{"op": "add_node", "id": "US1"}], why="prima del 2 ottobre")
    assert vecchia.path.is_file()

    mia = bridge_for(local.path, "A")
    assert not vecchia.path.exists(), "adottata, non copiata"
    assert [op["id"] for op in mia.pending()] == ["US1"]


def test_due_stanze_che_si_somigliano_non_condividono_la_coda():
    """`saggio/B` e `saggio-B` sono due stanze diverse. Due code che si
    confondono sarebbero lo stesso difetto del cancello, in miniatura."""
    assert room_key("saggio/B") != room_key("saggio-B")
    assert room_key("saggio/B").startswith("saggio-B.")


# ═══ 4 · la dispensa al momento del cambio ═══════════════════════════════════

def test_la_dispensa_NON_e_per_stanza_e_cè_una_ragione(client, realm, nodo,
                                                       monkeypatch, tmp_path):
    """I byte in attesa restano dove sono, e non è una dimenticanza.

    Un'operazione appartiene a una stanza; **dei byte no**. La dispensa consegna
    allo store condiviso, che è configurato sul NODO (`MINIO_*`) e non sulla
    stanza: gli stessi byte, con lo stesso digest, servono a chiunque li citi.
    Dividerla per stanza vorrebbe dire caricare due volte la stessa foto.

    Il limite che ne segue, dichiarato: ripuntare il nodo a una stanza **su un
    altro server** non cambia il bucket. Oggi non succede — un nodo di campo ha
    un solo store — e il giorno che succedesse la dispensa andrebbe divisa per
    store, non per stanza.
    """
    from app.spool import Spool

    local, _ = nodo
    dispensa = Spool(tmp_path / "dispensa", remote=InMemoryAssetStore())
    monkeypatch.setattr(main_module, "LARDER", dispensa)
    dispensa.put(b"\xff\xd8\xff\xe0 foto \xff\xd9", "image/jpeg")
    prima = list(dispensa.waiting())
    assert len(prima) == 1

    _ambiente(monkeypatch)
    with FakeRelay() as relay:
        risposta = client.post(
            "/v1/room",
            json={"server": f"http://127.0.0.1:{relay.port}", "room": "B"},
            headers=_con(realm, DEV))
        assert risposta.status_code == 200, risposta.text
    # …e al rientro nella stanza nuova sono SALITI, non spariti: `_seated`
    # attraversa la dispensa prima del ponte, che è la regola del 30 settembre.
    # Il punto di questo test è che la dispensa è **una sola** — non una per
    # stanza — e che quei byte non sono stati né persi né caricati due volte.
    assert dispensa.waiting() == [], "consegnati allo store condiviso"
    assert dispensa._remote.get(prima[0]) is not None
    assert dispensa._remote.count() == 1, "una volta sola"
    assert main_module.WRITER.spool is dispensa, "e la stessa dispensa, una sola"


# ═══ 5 · il ritorno indietro ═════════════════════════════════════════════════

def test_si_torna_al_container_locale_e_si_lascia_il_nodo(client, realm, nodo,
                                                          monkeypatch):
    local, presa = nodo
    _ambiente(monkeypatch)
    with FakeRelay() as relay:
        client.post("/v1/room",
                    json={"server": f"http://127.0.0.1:{relay.port}", "room": "A"},
                    headers=_con(realm, DEV))
        assert main_module.WRITER.room_id == "A"

        indietro = client.delete("/v1/room", headers=_con(realm, DEV))
        assert indietro.status_code == 200, indietro.text
        assert main_module.WRITER is local
        assert presa.holder() is None, "il nodo è libero"
        assert "container locale" in indietro.json()["message"]


def test_non_si_spunta_il_nodo_di_un_altro(client, realm, nodo, monkeypatch):
    _ambiente(monkeypatch)
    with FakeRelay() as relay:
        client.post("/v1/room",
                    json={"server": f"http://127.0.0.1:{relay.port}", "room": "A"},
                    headers=_con(realm, DEV))
        rifiuto = client.delete("/v1/room", headers=_con(realm, VIEWER))
        assert rifiuto.status_code == 409
        assert DEV in rifiuto.json()["detail"]
        assert main_module.WRITER.room_id == "A"


# ═══ 6 · il nodo lo dice ═════════════════════════════════════════════════════

def test_il_nodo_dice_dove_scrive_e_chi_lo_tiene(client, realm, nodo,
                                                 monkeypatch):
    _ambiente(monkeypatch)
    libero = client.get("/health").json()
    assert libero["held"] == "libero"

    with FakeRelay() as relay:
        indirizzo = f"http://127.0.0.1:{relay.port}"
        client.post("/v1/room", json={"server": indirizzo, "room": "A"},
                    headers=_con(realm, DEV))

        salute = client.get("/health").json()
        assert "A" in salute["writes_to"], salute["writes_to"]
        assert salute["held"].startswith("tenuto"), salute["held"]
        assert DEV not in json.dumps(salute), (
            "/health è pubblica: il fatto sì, il nome no")

        risposta = client.get("/v1/room", headers=_con(realm, DEV))
        assert risposta.status_code == 200, risposta.text
        mio = risposta.json()
        assert mio["room"] == "A" and mio["holding"]["who"] == DEV


def test_dopo_un_ripuntamento_dice_la_cosa_nuova(client, realm, nodo,
                                                 monkeypatch):
    _ambiente(monkeypatch)
    with FakeRelay() as relay:
        indirizzo = f"http://127.0.0.1:{relay.port}"
        client.post("/v1/room", json={"server": indirizzo, "room": "A"},
                    headers=_con(realm, DEV))
        client.post("/v1/room", json={"server": indirizzo, "room": "B"},
                    headers=_con(realm, DEV))
        assert "B" in client.get("/health").json()["writes_to"]
        assert client.get("/v1/room", headers=_con(realm, DEV)).json()["room"] == "B"


# ═══ 7 · la porta si prova PRIMA di scambiare lo scrivano ════════════════════

def test_una_stanza_che_non_risponde_non_sposta_il_nodo(client, realm, nodo,
                                                        monkeypatch):
    """Ripuntare a una stanza che non c'è lascerebbe il nodo fermo dove non può
    scrivere. Si prova la porta, e se non si apre non si tocca niente."""
    _ambiente(monkeypatch)
    with FakeRelay() as relay:
        client.post("/v1/room",
                    json={"server": f"http://127.0.0.1:{relay.port}", "room": "A"},
                    headers=_con(realm, DEV))
        rifiuto = client.post("/v1/room",
                              json={"server": "http://127.0.0.1:9", "room": "Z"},
                              headers=_con(realm, DEV))
        assert rifiuto.status_code == 502
        assert "non si è aperta" in rifiuto.json()["detail"]
        assert main_module.WRITER.room_id == "A", "il nodo è dove era"


def test_una_stanza_in_sola_lettura_dice_la_frase_del_relay(client, realm, nodo,
                                                            monkeypatch):
    _ambiente(monkeypatch)
    with FakeRelay(can_write=False) as relay:
        rifiuto = client.post(
            "/v1/room",
            json={"server": f"http://127.0.0.1:{relay.port}", "room": "A"},
            headers=_con(realm, DEV))
        assert rifiuto.status_code == 502
        assert "read-only" in rifiuto.json()["detail"]


def test_se_la_porta_non_si_apre_la_presa_si_lascia(client, realm, nodo,
                                                    monkeypatch):
    """Aver preso un nodo e non averne ottenuto niente non è tenerlo: se
    restasse preso, il nodo sarebbe bloccato da un tentativo fallito."""
    _, presa = nodo
    _ambiente(monkeypatch)
    rifiuto = client.post("/v1/room",
                          json={"server": "http://127.0.0.1:9", "room": "Z"},
                          headers=_con(realm, DEV))
    assert rifiuto.status_code == 502
    assert presa.holder() is None, "il nodo è ancora libero"


# ═══ 8 · la credenziale ══════════════════════════════════════════════════════

def test_il_token_di_chi_chiama_NON_viene_inoltrato(client, realm, nodo,
                                                    monkeypatch):
    """L'attacco che questa regola esiste per chiudere: il server lo sceglie il
    LINK, e un link lo scrive chiunque. «Incolla questo» e il nodo consegnerebbe
    la firma di chi ha firmato a chi ha scritto il link."""
    _ambiente(monkeypatch, "tok-del-dispiegamento")
    mia = realm(DEV)
    with FakeRelay() as relay:
        risposta = client.post(
            "/v1/room",
            json={"server": f"http://127.0.0.1:{relay.port}", "room": "A"},
            headers={"Authorization": "Bearer " + mia})
        assert risposta.status_code == 200, risposta.text
        assert mia not in relay.tokens, "la firma di chi chiama è uscita dal nodo"
        assert relay.tokens == ["tok-del-dispiegamento"]


def test_col_token_dellambiente_il_nodo_DICE_di_chi_sara_il_lavoro(
        client, realm, nodo, monkeypatch):
    """Il comportamento che questo nodo ha sempre avuto, che stanotte smette di
    essere taciuto."""
    _ambiente(monkeypatch)
    with FakeRelay() as relay:
        risposta = client.post(
            "/v1/room",
            json={"server": f"http://127.0.0.1:{relay.port}", "room": "A"},
            headers=_con(realm, DEV)).json()
    assert "non tuo" in risposta["credential"]
    assert "EM_ROOM_AUDIENCE" in risposta["credential"]


def test_senza_niente_il_nodo_dice_cosa_manca(client, realm, nodo):
    rifiuto = client.post("/v1/room",
                          json={"server": "http://127.0.0.1:9", "room": "A"},
                          headers=_con(realm, DEV))
    assert rifiuto.status_code == 503
    detto = rifiuto.json()["detail"]
    assert "EM_ROOM_AUDIENCE" in detto and "EM_CHATBOT_TOKEN" in detto


def test_lo_scambio_chiede_al_realm_a_nome_di_chi_ha_firmato(monkeypatch):
    """Lo scambio, misurato su un endpoint finto: cosa parte davvero."""
    visto = {}

    class _Risposta:
        status = 200

        def read(self):
            return b'{"access_token": "tok-per-la-stanza"}'

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def finto_urlopen(request, timeout=None):
        import urllib.parse
        visto["url"] = request.full_url
        visto["body"] = dict(urllib.parse.parse_qsl(request.data.decode()))
        return _Risposta()

    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", finto_urlopen)
    ambiente = {"EM_ROOM_AUDIENCE": "em-server", "OIDC_CLIENT_ID": "em-chatbot",
                "OIDC_CLIENT_SECRET": "segreto-del-nodo"}
    fuori = handoff.exchange("la-firma-di-chi-chiama",
                             token_endpoint=ISSUER + "/protocol/openid-connect/token",
                             env=ambiente)
    assert fuori == "tok-per-la-stanza"
    assert visto["body"]["grant_type"] == \
        "urn:ietf:params:oauth:grant-type:token-exchange"
    assert visto["body"]["subject_token"] == "la-firma-di-chi-chiama"
    assert visto["body"]["audience"] == "em-server"
    assert visto["body"]["client_secret"] == "segreto-del-nodo"


def test_lo_scambio_non_configurato_dice_le_tre_cose(monkeypatch):
    with pytest.raises(handoff.NoCredential) as manca:
        handoff.exchange("x", token_endpoint="http://x", env={})
    for nome in handoff.EXCHANGE_KEYS:
        assert nome in str(manca.value)


# ═══ 9 · il ripiego dall'ambiente resta ══════════════════════════════════════

def test_un_nodo_headless_parte_dalla_sua_variabile(tmp_path, monkeypatch):
    """*«A field node that boots headless into a known room has no browser to
    sign in with»* — la rotta si aggiunge, non sostituisce."""
    from app.writer import writer_from_env

    with FakeRelay() as relay:
        monkeypatch.setenv("EM_SERVER_URL", f"http://127.0.0.1:{relay.port}")
        monkeypatch.setenv("EM_CHATBOT_ROOM", "stanza-dallambiente")
        monkeypatch.setenv("EM_CHATBOT_TOKEN", "tok")
        monkeypatch.setenv("EM_CHATBOT_CONTAINER", str(tmp_path / "scavo.em.json"))
        monkeypatch.delenv("EM_CHATBOT_HANDOFF", raising=False)
        monkeypatch.delenv("EM_ASSET_SPOOL", raising=False)
        scrivano = writer_from_env()
        assert scrivano.room_id == "stanza-dallambiente"
        # e la sua coda è quella DELLA SUA STANZA, come tutte le altre
        assert room_key("stanza-dallambiente") in scrivano.bridge.path.name


def test_un_nodo_headless_non_apre_nessun_browser(monkeypatch):
    """La regola del docstring, tenuta da un test: `writer_from_env` viene
    chiamata all'import, e aprire un browser come effetto del caricamento di un
    modulo è il modo di appendere un servizio all'avvio."""
    fonte = (pathlib.Path(__file__).resolve().parent.parent / "app"
             / "writer.py").read_text(encoding="utf-8")
    codice = "\n".join(riga for riga in fonte.split("\n")
                       if not riga.strip().startswith("#"))
    assert "webbrowser" not in codice
    assert "sign_in" not in codice


# ═══ 10 · nessun token in un ambiente di processo ════════════════════════════

def test_ripuntare_non_scrive_nessun_token_da_nessuna_parte(
        client, realm, nodo, monkeypatch, tmp_path):
    """Dimostrato, non affermato: si guarda `os.environ` prima e dopo, e si
    cerca il token sul disco del nodo.

    *«un token che finisce in un processo è un token in `ps`, in un crash dump e
    nel file dell'unità»* — `handoff.py`, ed è la ragione per cui la rotta
    esiste invece delle tre variabili."""
    import os

    local, _ = nodo
    _ambiente(monkeypatch, "tok-del-dispiegamento")
    mia = realm(DEV)
    prima = dict(os.environ)
    with FakeRelay() as relay:
        risposta = client.post(
            "/v1/room",
            json={"server": f"http://127.0.0.1:{relay.port}", "room": "A"},
            headers={"Authorization": "Bearer " + mia})
        assert risposta.status_code == 200, risposta.text

    dopo = dict(os.environ)
    assert dopo == prima, "l'ambiente del processo non è cambiato"
    assert not [k for k, v in dopo.items() if mia in str(v)]
    # e niente sul disco del nodo
    for path in pathlib.Path(local.path).parent.rglob("*"):
        if path.is_file():
            assert mia not in path.read_text(encoding="utf-8", errors="ignore"), \
                f"la firma di chi ha chiamato è finita in {path.name}"
    # né nella risposta che il nodo dà
    assert mia not in json.dumps(risposta.json())


def test_il_token_non_finisce_nemmeno_nella_presa(client, realm, nodo,
                                                  monkeypatch):
    """La presa porta un ORCID — che è un identificatore pubblico e finisce nel
    grafo comunque — e mai una credenziale."""
    _, presa = nodo
    _ambiente(monkeypatch)
    mia = realm(DEV)
    with FakeRelay() as relay:
        client.post("/v1/room",
                    json={"server": f"http://127.0.0.1:{relay.port}", "room": "A"},
                    headers={"Authorization": "Bearer " + mia})
    tenuta = presa.holder()
    assert tenuta.who == DEV
    assert mia not in json.dumps(presa.describe(reveal=True))


# ═══ 11 · la presa, da sola ══════════════════════════════════════════════════

def test_la_presa_e_dello_stesso_e_non_si_rinnova_il_da_quando():
    presa = Holding(idle_after=1200)
    prima = presa.take(DEV)["grip"]
    dopo = presa.take(DEV)["grip"]
    assert dopo.since == prima.since, "è sempre lo stesso, dalle stesse ore"


def test_una_presa_senza_nome_si_rifiuta():
    with pytest.raises(ValueError) as vuoto:
        Holding().take("")
    assert "senza identità" in str(vuoto.value)


def test_lasciare_una_presa_che_non_e_tua_non_fa_niente():
    presa = Holding()
    presa.take(DEV)
    assert presa.release(VIEWER) is False
    assert presa.holder().who == DEV


def test_un_riavvio_libera_il_nodo():
    """Dichiarato invece che scoperto: la presa vive nel processo, perché il
    token della stanza non tocca mai il disco e una presa durevole senza il suo
    token sarebbe un nodo che dichiara una stanza in cui non può scrivere."""
    presa = Holding()
    presa.take(DEV)
    rinata = Holding()                            # il processo è ripartito
    assert rinata.holder() is None
    assert rinata.take(VIEWER)["took_over_from"] is None


def test_la_presa_scaduta_non_e_una_presa():
    presa = Holding(idle_after=0)
    presa.take(DEV)
    assert presa.holder() is None
    assert presa.stale().who == DEV, "ma si sa ancora di chi era, per dirlo"
    with pytest.raises(HeldByAnother):
        Holding(idle_after=10_000).take(DEV) and \
            _presa_occupata().take(VIEWER)


def _presa_occupata():
    presa = Holding(idle_after=10_000)
    presa.take(DEV)
    return presa


# ═══ 12 · la superficie ══════════════════════════════════════════════════════

WEB = pathlib.Path(__file__).resolve().parent.parent / "web"


def test_la_pagina_dice_dove_scrive_senza_che_glielo_si_chieda():
    """`#room-name` esisteva nel DOM dal 23 settembre e **nessuno lo
    riempiva**. Un nodo che ha cambiato stanza e non lo mostra è la stessa
    famiglia di difetti di tutta questa settimana."""
    pagina = (WEB / "index.html").read_text(encoding="utf-8")
    assert 'id="room-name"' in pagina
    assert 'id="nav-room"' in pagina
    assert "window.SGRoom?.(health)" in pagina, (
        "la riga si aggiorna a ogni /health, non una volta all'avvio: il nodo "
        "si può ripuntare da un altro dispositivo")
    modulo = (WEB / "room.js").read_text(encoding="utf-8")
    assert "export function headline" in modulo


def test_il_nome_di_chi_tiene_il_nodo_NON_passa_da_health():
    """`/health` è pubblica: il fatto sì, il nome no. Il nome si legge da
    `/v1/room`, che una firma la chiede."""
    modulo = (WEB / "room.js").read_text(encoding="utf-8")
    assert '"/v1/room"' in modulo and "Authorization" in modulo
    codice = "\n".join(riga for riga in modulo.split("\n")
                       if not riga.strip().startswith(("*", "/*", "//")))
    intestazione = codice.split("export function headline")[1].split(
        "export function leftBehind")[0]
    assert "who" not in intestazione, (
        "la riga pubblica dell'intestazione si costruisce da /health, che il "
        "nome non ce l'ha")


def test_la_pagina_ripete_la_frase_del_NODO_e_non_una_sua():
    """Un rifiuto dice chi tiene il nodo e da quanto, oppure cosa manca perché
    possa presentarsi alla stanza. Riscriverlo in «non riuscito» butterebbe via
    l'unica cosa utile."""
    modulo = (WEB / "room.js").read_text(encoding="utf-8")
    assert "detto.detail || t(\"room.refused\")" in modulo
    assert "detto.message || t(\"room.pointed\")" in modulo


def test_la_stanza_non_porta_colori_letterali():
    import re
    for nome in ("room.js",):
        testo = (WEB / nome).read_text(encoding="utf-8")
        assert not re.search(r"#[0-9a-fA-F]{3,8}\b", testo), nome


def test_la_conchiglia_non_mangia_le_rotte_di_v1():
    """Il jolly della conchiglia (`/{shell_file:path}`) raccoglieva ogni GET
    sotto `/v1/` che vivesse sul router `v1`: misurato il 2 ottobre su
    `GET /v1/room`, che esisteva e rispondeva `404 not part of the shell`.

    Un test sull'ORDINE e non sul sintomo: il jolly deve restare l'ultimo, che
    è l'unica cosa che un jolly deve essere.
    """
    fonte = (pathlib.Path(__file__).resolve().parent.parent / "app"
             / "main.py").read_text(encoding="utf-8")
    assert fonte.index("app.include_router(v1)") < \
        fonte.index("app.include_router(public)")


def test_GET_v1_room_arriva_al_suo_gestore(client, realm, nodo):
    """E lo stesso fatto, misurato invece che dedotto dall'ordine."""
    risposta = client.get("/v1/room", headers=_con(realm, DEV))
    assert risposta.status_code == 200, risposta.text
    assert "holding" in risposta.json()
