"""Le foto: dove stanno i byte mentre si è fuori, e in che ordine rientrano.

════════════════════════════════════════════════════════════════════════════════
## IL CASO CHE LO FA SCATTARE

Il ponte del 27 settembre accoda **operazioni**: 173 byte l'una, misurate sul
relay. Una foto di cantiere ne pesa tredici milioni, e lo store del nodo non è
sul nodo — è MinIO, dietro la rete. Misurato staccando `em-dev-minio` mentre il
servizio era in piedi:

    create_su            ok=True        54 ms
    attach_photo_to_su   ok=False   306 153 ms  «attach_photo_to_su» non è
                                                riuscito: HTTPConnectionPool

Cinque minuti in mano a chi scava, e poi la foto non è da nessuna parte.

Il primo test spegne la dispensa e rimisura la perdita; il secondo è lo stesso
giro con la dispensa accesa. Poi il cancello della notte, che è l'ordine.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

pytest.importorskip("websockets", reason="serve il client websockets")

from app.assets import InMemoryAssetStore, content_id          # noqa: E402
from app.bridge import bridge_for                              # noqa: E402
from app.contract import GraphDelta, ToolRegistry, invoke      # noqa: E402
from app.spool import Spool, describe as larder_describe       # noqa: E402
from app.spool import spool_from_env, verify                   # noqa: E402
from app.tools import make_attach_photo, make_create_su        # noqa: E402
from app.writer import BytesFirst, LocalWriter, RoomWriter     # noqa: E402
from tests.test_room_writer_wire import FakeRelay              # noqa: E402

ORCID = "0000-0002-1825-0097"
CHIUSO = "http://127.0.0.1:9"          # nessuno ascolta

#: Un JPEG piccolo ma vero nella testa e nella coda: `FFD8` davanti, `FFD9` in
#: fondo. Serve a poter dire «questa metà non è un JPEG» e averne una prova.
FOTO = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01" + bytes(4096) + b"\xff\xd9"


class StoreMuto(InMemoryAssetStore):
    """Lo store condiviso che non risponde — MinIO dietro una rete che non c'è.

    Non un mock del client: un `AssetStore` che alza, che è quello che il
    chiamante vede quando la rete manca. Si accende e si spegne, perché il caso
    da misurare è «era giù e poi è tornata».
    """

    def __init__(self):
        super().__init__()
        self.giu = True
        self.messi = []

    def put(self, data, media_type):
        if self.giu:
            raise ConnectionError("minio non risponde")
        self.messi.append(content_id(data))
        return super().put(data, media_type)

    def head(self, ref):
        if self.giu:
            raise ConnectionError("minio non risponde")
        return super().head(ref)

    def get(self, ref):
        if self.giu:
            raise ConnectionError("minio non risponde")
        return super().get(ref)


def _dispensa(tmp_path, remoto):
    return Spool(tmp_path / "dispensa", remote=remoto)


def _scrivano(tmp_path, relay=None, *, dispensa=None):
    local = LocalWriter(str(tmp_path / "scavo.em.json"), study="Scavo")
    base = f"http://127.0.0.1:{relay.port}" if relay else CHIUSO
    w = RoomWriter(base, "stanza", "tok", timeout=2.0, fallback=local,
                   bridge=bridge_for(local.path), spool=dispensa)
    return w, local


def _rientra(writer, relay):
    """La stanza torna, e il backoff si azzera (vedi `test_il_ponte`)."""
    writer.base_url = f"http://127.0.0.1:{relay.port}"
    writer.session._not_before = 0.0
    writer.session._backoff = writer.session._backoff_base


def _cera_gia(local, numero="12"):
    """L'unità esiste già nel grafo che il nodo vede.

    Scritta nel container locale e non con `create_su`: `FakeRelay` risponde
    con uno snapshot vuoto, quindi un'unità creata NELLA STANZA non si rilegge
    da lì. `RoomWriter.node` chiede alla stanza e poi al container, ed è il
    container a rispondere — che è anche quello che succede a un telefono che
    ha appena scritto offline.
    """
    local.apply(GraphDelta(nodes=[{"id": f"US{numero}", "node_type": "US",
                                   "name": f"US {numero}"}],
                           edges=[], process=None, author=ORCID))


def _registro(writer, store):
    reg = ToolRegistry()
    for d in (make_create_su(writer), make_attach_photo(writer, store)):
        reg.register(d)
    return reg


# ═══ 1 · l'effetto della rottura: senza dispensa la foto si perde ════════════

def test_senza_dispensa_lo_store_giu_perde_la_foto(tmp_path):
    """Il difetto, rimisurato nel piccolo: nessuna dispensa, lo store non
    risponde, e alla fine i byte non sono da nessuna parte.

    Nemmeno il ponte li tiene: il ponte accoda operazioni, e l'operazione non
    è mai stata composta — `asset_store.put` alza prima."""
    with FakeRelay() as relay:
        store = StoreMuto()
        writer, local = _scrivano(tmp_path, relay)
        reg = _registro(writer, store)                # lo store CRUDO
        _cera_gia(local)

        esito = invoke(reg.get("attach_photo_to_su"),
                       {"us": "12", "photo": FOTO}, ORCID, registry=reg)

        assert esito.ok is False
        assert "non è riuscito" in esito.message
        assert store.messi == [], "niente nello store"
        assert len(writer.bridge) == 0, "e niente in coda: l'operazione non esiste"
        # e sul disco del nodo nemmeno: non c'è un posto dove guardare
        rimasti = [p for p in tmp_path.rglob("*") if p.suffix in (".jpg", "")
                   and p.is_file() and p.read_bytes()[:2] == b"\xff\xd8"]
        assert rimasti == []


def test_con_la_dispensa_lo_stesso_giro_tiene_la_foto(tmp_path):
    """Identico riga per riga, tranne la dispensa."""
    with FakeRelay() as relay:
        store = StoreMuto()
        dispensa = _dispensa(tmp_path, store)
        writer, local = _scrivano(tmp_path, relay, dispensa=dispensa)
        reg = _registro(writer, dispensa)             # la DISPENSA
        _cera_gia(local)

        esito = invoke(reg.get("attach_photo_to_su"),
                       {"us": "12", "photo": FOTO}, ORCID, registry=reg)

        assert esito.ok is True, esito.message
        assert dispensa.local.get(content_id(FOTO)) == FOTO, "i byte sono qui"
        assert dispensa.waiting() == [content_id(FOTO)], "e sono in fila"
        assert store.messi == [], "lassù ancora no, e va bene così"


# ═══ 2 · il cancello della notte: l'operazione non precede i byte ════════════

def test_IL_CANCELLO_loperazione_non_parte_prima_dei_byte(tmp_path):
    """Il pericolo del riferimento vuoto, costruito apposta.

    La stanza c'è ed è raggiungibile; lo store condiviso no. Se l'operazione
    partisse, ogni presente vedrebbe un `ResourceNode` con `checksum` che punta
    a un oggetto che non esiste — subito, perché il fan-out non aspetta.
    """
    with FakeRelay() as relay:
        store = StoreMuto()
        dispensa = _dispensa(tmp_path, store)
        writer, local = _scrivano(tmp_path, relay, dispensa=dispensa)
        reg = _registro(writer, dispensa)
        _cera_gia(local)
        prima = len(relay.ops)

        invoke(reg.get("attach_photo_to_su"), {"us": "12", "photo": FOTO},
               ORCID, registry=reg)

        arrivate = relay.ops[prima:]
        assert arrivate == [], "nessuna parola sul filo finché i byte aspettano"
        citazioni = [op for op in relay.ops
                     if "sha256:" in json.dumps(op)]
        assert citazioni == [], "e nessuno nella stanza vede un riferimento vuoto"
        # il lavoro non è perso: è sul ponte, con la ragione scritta accanto
        assert len(writer.bridge) >= 3
        assert "devono ancora raggiungere" in (writer.last_refusal or "")


def test_E_SENZA_LA_REGOLA_il_riferimento_vuoto_ARRIVA(tmp_path):
    """La gemella della rottura: tolta la regola, misura cosa vede un altro.

    Non «la sostituzione è avvenuta»: **l'effetto**. Il relay accetta
    l'operazione — non sa niente di store, e non deve saperlo — e nella stanza
    compare un checksum che non corrisponde a nessun oggetto.
    """
    with FakeRelay() as relay:
        store = StoreMuto()
        dispensa = _dispensa(tmp_path, store)
        writer, local = _scrivano(tmp_path, relay, dispensa=dispensa)
        # ── la rottura: il writer torna a non guardare la dispensa ──
        writer._bytes_before_words = lambda: None
        reg = _registro(writer, dispensa)
        _cera_gia(local)

        invoke(reg.get("attach_photo_to_su"), {"us": "12", "photo": FOTO},
               ORCID, registry=reg)

        risorse = [op for op in relay.ops
                   if (op.get("node") or {}).get("node_type") == "resource"]
        assert risorse, "l'operazione è arrivata nella stanza"
        citato = risorse[0]["node"]["data"]["checksum"]
        assert citato == content_id(FOTO)
        # ed ecco il danno, misurato dal punto di vista di chi è nella stanza:
        store.giu = False
        assert store.head(citato) is None, (
            "la stanza cita byte che nello store condiviso non esistono")


def test_quando_i_byte_salgono_le_parole_seguono(tmp_path):
    """E l'ordine, quando la rete torna: prima la dispensa, poi il ponte."""
    with FakeRelay() as relay:
        store = StoreMuto()
        dispensa = _dispensa(tmp_path, store)
        writer, local = _scrivano(tmp_path, relay, dispensa=dispensa)
        reg = _registro(writer, dispensa)
        _cera_gia(local)
        invoke(reg.get("attach_photo_to_su"), {"us": "12", "photo": FOTO},
               ORCID, registry=reg)
        assert len(writer.bridge) >= 3

        store.giu = False                     # la rete torna
        writer.session.close()
        _rientra(writer, relay)
        writer._seated()

        assert dispensa.waiting() == [], "la dispensa è salita"
        assert store.messi == [content_id(FOTO)]
        assert len(writer.bridge) == 0, "e poi il ponte è passato"
        risorse = [op for op in relay.ops
                   if (op.get("node") or {}).get("node_type") == "resource"]
        assert risorse, "la risorsa è nella stanza"
        assert store.head(risorse[0]["node"]["data"]["checksum"]) is not None, (
            "e stavolta il riferimento è pieno")


# ═══ 3 · il gesto non aspetta la rete ════════════════════════════════════════

def test_il_gesto_non_tocca_la_rete(tmp_path):
    """`put` scrive e basta. È la cura dei 306 secondi misurati sul vero.

    Lo store condiviso qui alza a ogni chiamata: se `put` lo toccasse, questo
    test sarebbe rosso.
    """
    store = StoreMuto()
    dispensa = _dispensa(tmp_path, store)
    esito = dispensa.put(FOTO, "image/jpeg")
    assert esito["ref"] == content_id(FOTO)
    assert esito["spooled"] is True
    assert store.messi == []


def test_due_volte_la_stessa_foto_e_una_riga_sola(tmp_path):
    """Il dito che preme due volte: stesso contenuto, stesso nome, una fila."""
    dispensa = _dispensa(tmp_path, StoreMuto())
    dispensa.put(FOTO, "image/jpeg")
    dispensa.put(FOTO, "image/jpeg")
    assert dispensa.waiting() == [content_id(FOTO)]


# ═══ 4 · la dispensa dura ════════════════════════════════════════════════════

def test_la_dispensa_sopravvive_al_riavvio(tmp_path):
    """Il container riparte e i byte sono ancora lì, e ancora in fila.

    È la stessa proprietà del ponte e per la stessa ragione: il servizio viene
    riavviato di continuo, e una dispensa in memoria metterebbe i byte in RAM.
    """
    store = StoreMuto()
    _dispensa(tmp_path, store).put(FOTO, "image/jpeg")

    rinata = _dispensa(tmp_path, store)          # un processo nuovo
    assert rinata.waiting() == [content_id(FOTO)]
    assert rinata.local.get(content_id(FOTO)) == FOTO

    store.giu = False
    esito = rinata.deliver()
    assert esito["delivered"] == 1
    assert store.messi == [content_id(FOTO)]


def test_la_copia_locale_si_raccoglie_solo_dopo_la_conferma(tmp_path):
    """Il `head` fra il `put` e la cancellazione non è pignoleria: in mezzo
    c'è una rete, ed è la rete il motivo per cui la dispensa esiste."""
    store = StoreMuto()
    dispensa = _dispensa(tmp_path, store)
    dispensa.put(FOTO, "image/jpeg")
    store.giu = False
    dispensa.deliver()
    assert dispensa.local.get(content_id(FOTO)) is None, "raccolta"
    assert dispensa.get(content_id(FOTO)) == FOTO, "ma si legge ancora, da lassù"


def test_chi_tiene_le_due_code_insieme_e_il_digest(tmp_path):
    """Nessun identificatore da allineare: la fila e l'operazione si nominano
    con la stessa stringa, che è il contenuto stesso."""
    with FakeRelay() as relay:
        store = StoreMuto()
        dispensa = _dispensa(tmp_path, store)
        writer, local = _scrivano(tmp_path, relay, dispensa=dispensa)
        reg = _registro(writer, dispensa)
        _cera_gia(local)
        invoke(reg.get("attach_photo_to_su"), {"us": "12", "photo": FOTO},
               ORCID, registry=reg)

        in_fila = dispensa.waiting()
        in_coda = [op["node"]["data"]["checksum"]
                   for op in writer.bridge.pending()
                   if (op.get("node") or {}).get("node_type") == "resource"]
        assert in_fila == in_coda == [content_id(FOTO)]


# ═══ 5 · mezza foto ══════════════════════════════════════════════════════════

def test_mezza_foto_dichiarata_viene_rifiutata(tmp_path):
    """Il caso misurato: un base64 troncato a un multiplo di 4 decodifica
    pulito, e senza il digest dichiarato diventa mezza foto con un `ref`
    valido che nessuno può più smascherare."""
    meta = FOTO[:len(FOTO) // 2]
    assert not meta.endswith(b"\xff\xd9"), "non è un JPEG, e nessuno lo sa"
    with pytest.raises(ValueError) as storto:
        verify(meta, content_id(FOTO))
    assert "non sono quelli annunciati" in str(storto.value)
    # e chi non dichiara niente passa, dichiaratamente
    verify(meta, "")


def test_il_tool_rifiuta_mezza_foto_PRIMA_di_scrivere(tmp_path):
    """E il rifiuto arriva prima dello store: mezza foto non entra nemmeno
    nella dispensa, altrimenti resterebbe lì a occupare posto per sempre."""
    with FakeRelay() as relay:
        store = StoreMuto()
        dispensa = _dispensa(tmp_path, store)
        writer, local = _scrivano(tmp_path, relay, dispensa=dispensa)
        reg = _registro(writer, dispensa)
        _cera_gia(local)

        esito = invoke(reg.get("attach_photo_to_su"),
                       {"us": "12", "photo": FOTO[:len(FOTO) // 2],
                        "sha256": content_id(FOTO)}, ORCID, registry=reg)

        assert esito.ok is False
        assert esito.data["reason"] == "digest-mismatch"
        assert dispensa.waiting() == [], "e non è rimasto niente nella dispensa"


def test_E_SENZA_IL_CONTROLLO_mezza_foto_entra_con_un_ref_valido(tmp_path):
    """La gemella della rottura. Il danno non è che entra: è che **è coerente
    con sé stessa**, quindi dopo non se ne accorge più nessuno."""
    meta = FOTO[:len(FOTO) // 2]
    dispensa = _dispensa(tmp_path, StoreMuto())
    esito = dispensa.put(meta, "image/jpeg")        # nessuna verifica
    from app.assets import asset_ref_valid
    assert asset_ref_valid(esito["ref"]), "il riferimento è ineccepibile"
    assert hashlib.sha256(dispensa.local.get(esito["ref"])).hexdigest() == \
        esito["sha256"], "e il contenuto corrisponde al proprio nome"
    assert not dispensa.local.get(esito["ref"]).endswith(b"\xff\xd9"), \
        "ma è mezza foto"


# ═══ 6 · il nodo parte anche senza store condiviso ═══════════════════════════

def test_il_nodo_si_accende_in_galleria(tmp_path, monkeypatch):
    """Il difetto scritto nel referto del 27: staccato dalla rete il servizio
    **non parte proprio**, perché `MinioAssetStore.__init__` fa un giro di
    rete. Con la dispensa lo store condiviso si costruisce alla prima consegna,
    e un nodo che si accende in galleria registra e fotografa."""
    monkeypatch.setenv("EM_ASSET_SPOOL", str(tmp_path / "dispensa"))
    monkeypatch.setenv("MINIO_ENDPOINT", "http://127.0.0.1:9")
    monkeypatch.setenv("MINIO_ACCESS_KEY", "x")
    monkeypatch.setenv("MINIO_SECRET_KEY", "y")
    monkeypatch.setenv("MINIO_BUCKET", "em-assets")

    dispensa = spool_from_env()                     # non alza
    assert dispensa is not None
    esito = dispensa.put(FOTO, "image/jpeg")        # e si può lavorare
    assert esito["ref"] == content_id(FOTO)
    assert dispensa.waiting() == [content_id(FOTO)]


def test_il_servizio_INTERO_si_accende_in_galleria(tmp_path):
    """E la prova vera è un PROCESSO NUOVO, non un `monkeypatch`.

    Il test qui sopra era verde per la ragione sbagliata, e l'ha scoperto la
    sonda dal vivo del 30 settembre: `app/assets.py` costruiva `ASSET_STORE`
    **all'import**, quindi importare il modulo con MinIO staccato faceva
    esplodere tutto — dispensa o no.

        RuntimeError: the object store at http://minio:9000 did not answer …
        the field node will not start without the store it is configured to
        write to

    In un test già importato non si vede: il modulo era stato caricato prima,
    senza MinIO in configurazione. Serve un interprete che parta da zero, ed è
    quello che questo test fa.
    """
    import subprocess
    ambiente = dict(os.environ)
    ambiente.update({
        "EM_ASSET_SPOOL": str(tmp_path / "dispensa"),
        "EM_CHATBOT_CONTAINER": str(tmp_path / "scavo.em.json"),
        "MINIO_ENDPOINT": "http://127.0.0.1:9", "MINIO_ACCESS_KEY": "x",
        "MINIO_SECRET_KEY": "y", "MINIO_BUCKET": "em-assets",
        "MINIO_SECURE": "false",
    })
    for nome in ("EM_SERVER_URL", "EM_CHATBOT_ROOM", "EM_CHATBOT_TOKEN",
                 "EM_CHATBOT_HANDOFF", "OIDC_ISSUER", "OIDC_AUDIENCE"):
        ambiente.pop(nome, None)
    radice = str(pathlib.Path(__file__).resolve().parent.parent)
    ambiente["PYTHONPATH"] = radice
    esito = subprocess.run(
        [sys.executable, "-c",
         "import app.main as m; print('LARDER', m.LARDER is not None); "
         "print('HEALTH', m._health().larder)"],
        capture_output=True, text=True, env=ambiente, cwd=radice, timeout=120)
    assert esito.returncode == 0, esito.stderr[-1500:]
    assert "LARDER True" in esito.stdout, esito.stdout
    assert "HEALTH vuota" in esito.stdout, esito.stdout


def test_E_SENZA_LA_PIGRIZIA_il_servizio_NON_si_accende(tmp_path):
    """La gemella della rottura: si legge `ASSET_STORE`, che è il gesto che lo
    costruisce, e il processo muore con la frase misurata dal vivo."""
    import subprocess
    ambiente = dict(os.environ)
    ambiente.update({"MINIO_ENDPOINT": "http://127.0.0.1:9",
                     "MINIO_ACCESS_KEY": "x", "MINIO_SECRET_KEY": "y",
                     "MINIO_BUCKET": "em-assets", "MINIO_SECURE": "false"})
    radice = str(pathlib.Path(__file__).resolve().parent.parent)
    ambiente["PYTHONPATH"] = radice
    esito = subprocess.run(
        [sys.executable, "-c", "from app import assets; assets.ASSET_STORE"],
        capture_output=True, text=True, env=ambiente, cwd=radice, timeout=120)
    assert esito.returncode != 0
    assert "did not answer" in esito.stderr


def test_senza_dispensa_configurata_niente_cambia(tmp_path, monkeypatch):
    """Un nodo di scrivania con la rete sotto il tavolo continua a scrivere
    diritto nel bucket, e `/health` lo dice con una frase e non col silenzio."""
    monkeypatch.delenv("EM_ASSET_SPOOL", raising=False)
    assert spool_from_env() is None
    assert "senza rete si perdono" in larder_describe(None)


def test_una_dispensa_senza_dove_consegnare_si_rifiuta(tmp_path, monkeypatch):
    """Byte che restano su un nodo per sempre sono la stessa perdita di ieri
    con un nome più gentile: si dice al primo tentativo, non al primo backup."""
    monkeypatch.setenv("EM_ASSET_SPOOL", str(tmp_path / "dispensa"))
    for nome in ("MINIO_ENDPOINT", "MINIO_ACCESS_KEY", "MINIO_SECRET_KEY",
                 "MINIO_BUCKET", "EM_ASSET_DIR"):
        monkeypatch.delenv(nome, raising=False)
    dispensa = spool_from_env()
    dispensa.put(FOTO, "image/jpeg")
    esito = dispensa.deliver()
    assert esito["delivered"] == 0
    assert "nessuno store condiviso" in str(dispensa.last_refusal)


# ═══ 7 · quello che si vede da fuori ═════════════════════════════════════════

def test_la_dispensa_si_vede_in_health(tmp_path):
    """Una foto che aspetta è uno stato legittimo, e in questo progetto i buchi
    si mostrano e si nominano."""
    store = StoreMuto()
    dispensa = _dispensa(tmp_path, store)
    assert "vuota" in larder_describe(dispensa)
    dispensa.put(FOTO, "image/jpeg")
    detto = larder_describe(dispensa)
    assert "1 in attesa" in detto and "KB" in detto


def test_la_coda_ferma_dice_perche(tmp_path):
    """`Bridge` compone la frase da `op` e `id`: la voce della dispensa porta
    entrambi apposta, perché «None None: …» è una palude con un nome."""
    store = StoreMuto()
    dispensa = _dispensa(tmp_path, store)
    dispensa.put(FOTO, "image/jpeg")
    # i byte spariscono da sotto (qualcuno ha pulito il volume) mentre la rete
    # invece c'è: cioè un RIFIUTO e non un guasto, che è il ramo in cui `Bridge`
    # compone la frase dai campi della voce.
    store.giu = False
    dispensa.local.forget(content_id(FOTO))
    esito = dispensa.deliver()
    assert esito["delivered"] == 0
    assert "put_asset" in str(esito["stopped"])
    assert "sha256:" in str(esito["stopped"])


def test_bytes_first_e_una_eccezione_sua(tmp_path):
    """Non un `RoomRefused`: la stanza non ha rifiutato niente, e confonderli
    farebbe finire nel container locale un lavoro che deve solo aspettare."""
    with FakeRelay() as relay:
        dispensa = _dispensa(tmp_path, StoreMuto())
        writer, _ = _scrivano(tmp_path, relay, dispensa=dispensa)
        dispensa.put(FOTO, "image/jpeg")
        with pytest.raises(BytesFirst):
            writer._bytes_before_words()


def test_apply_senza_dispensa_si_comporta_come_prima(tmp_path):
    """Il nodo che non ha una dispensa non cambia di una riga: la regola nuova
    non deve costare niente a chi non ha il problema."""
    with FakeRelay() as relay:
        writer, _ = _scrivano(tmp_path, relay)          # spool=None
        writer.apply(GraphDelta(nodes=[{"id": "US1", "node_type": "US",
                                        "name": "US 1"}],
                                edges=[], process=None, author=ORCID))
        assert [op.get("id") for op in relay.ops] == ["US1"]
        assert writer.degraded is False


# ═══ 8 · la superficie: quello che il telefono fa e quello che non fa più ════

WEB = pathlib.Path(__file__).resolve().parent.parent / "web"


def senza_prosa(testo: str) -> str:
    """Il codice senza i commenti — SESTA VOLTA che serve, in questo ecosistema.

    La prima versione di questo cancello è diventata rossa **sul commento che
    spiega perché il difetto è stato tolto**, che cita il difetto per esteso.
    È la stessa forma di `anno` dentro «cannot», di `white` dentro
    `--sg-off-white`, di `area` in una frase italiana, di `gc_watermark` in una
    docstring e di `compact_section` in una spiegazione: **un cancello che
    guarda il testo invece del codice morde chi lo documenta.**

    Grezzo di proposito — niente parser: toglie i blocchi e le righe di
    commento, e non prova a distinguere uno `//` dentro una stringa. Un
    cancello deve essere leggibile quanto la regola che tiene, e il caso che
    sbaglierebbe (un URL dentro un letterale) non contiene mai il difetto che
    sta cercando.
    """
    import re
    senza = re.sub(r"/\*.*?\*/", "", testo, flags=re.S)
    return "\n".join(riga.split("//")[0] for riga in senza.split("\n"))


def test_la_pagina_non_indovina_piu_lunita():
    """Il difetto tolto, e la prova che è tolto.

    C'era il primo numero comparso nell'ultima frase, preso con
    un'espressione regolare. Su «la US 12 taglia la 7» la foto sarebbe andata
    sulla 12 senza che nessuno l'avesse chiesto.
    """
    pagina = (WEB / "index.html").read_text(encoding="utf-8")
    codice = senza_prosa(pagina.split("<script>")[-1])
    assert "d{1,5}" not in codice, (
        "la pagina sta di nuovo indovinando un numero di unità da una frase")


def test_il_rilevatore_rileva_ancora():
    """E il cancello addolcito morde ancora: la regola di casa vuole che una
    guardia sia dimostrata su un caso che la fa scattare."""
    finto = 'const us = frase.match(/\\b(\\d{1,5})\\b/)[1];  // indovina'
    assert "d{1,5}" in senza_prosa(finto)
    assert "indovina" not in senza_prosa(finto), "e la prosa la toglie"


def test_i_byte_non_passano_piu_da_localstorage():
    """Misurato: 4 MB di foto sono 5,33 MB di base64, il tetto è 49 MB, quindi
    nove foto e la decima butta tutto. IndexedDB, sulla stessa origine, ne
    dichiara 12,5 GB."""
    modulo = (WEB / "photos.js").read_text(encoding="utf-8")
    codice = "\n".join(riga for riga in modulo.split("\n")
                       if not riga.strip().startswith(("*", "/*", "//")))
    assert "indexedDB" in codice
    assert "localStorage" not in codice, (
        "le foto sono tornate nella coda delle frasi: nove e poi si rompe")


def test_il_telefono_dichiara_il_digest():
    """Il controllo del nodo senza nessuno che lo eserciti sarebbe una guardia
    per un caso che non capita mai."""
    modulo = (WEB / "photos.js").read_text(encoding="utf-8")
    assert 'crypto.subtle.digest("SHA-256"' in modulo
    assert "sha256: voce.sha256" in modulo


def test_la_striscia_usa_solo_ruoli_del_tema():
    """Nessun esadecimale nuovo: gli stati parlano con `--sg-warn` e
    `--sg-info`, che sono i ruoli che il tema dichiara."""
    css = (WEB / "shell.css").read_text(encoding="utf-8")
    striscia = css[css.index(".waiting {"):css.index(".shot-other {")]
    import re
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", striscia), striscia
    assert "var(--sg-warn)" in striscia and "var(--sg-info)" in striscia


def test_una_foto_senza_unita_non_parte():
    """«Non so ancora di che unità è» non è una domanda da fare a un servizio:
    il nodo risponderebbe «Non trovo la US» e la fila si fermerebbe lì."""
    modulo = (WEB / "photos.js").read_text(encoding="utf-8")
    assert "v.state === PRONTA" in modulo


def test_niente_bottoni_annidati():
    """Un `button` dentro un `button` non è HTML valido, e ogni motore lo
    scioglie a modo suo: il bottone interno finisce fuori dal contenitore su
    cui è disegnato il bordo di stato."""
    modulo = (WEB / "photos.js").read_text(encoding="utf-8")
    assert 'const cella = document.createElement("div")' in modulo


# ═══ 9 · cosa la foto porta con sé ═══════════════════════════════════════════

def test_quello_che_non_e_una_foto_non_alza(tmp_path):
    """Una foto che il lettore non capisce si allega lo stesso: un allegato che
    fallisce per un byte storto nei metadati è peggio di nessun metadato."""
    from app import exif
    assert exif.read(b"") == {}
    assert exif.read(b"non sono un jpeg") == {}
    assert exif.read(FOTO) == {}                    # niente APP1: niente tag
    storta = b"\xff\xd8\xff\xe1\x00\x20Exif\x00\x00MM\x00\x2a\xff\xff\xff\xff"
    assert exif.read(storta) == {}                  # e nemmeno qui alza


def test_i_campi_portati_stanno_sotto_source_fields(tmp_path):
    """La regola di casa: capito e trasportato non si mescolano. Il precedente
    sono i `rapporti` di pyArchInit in `tools.py`."""
    with FakeRelay() as relay:
        dispensa = _dispensa(tmp_path, StoreMuto())
        writer, local = _scrivano(tmp_path, relay, dispensa=dispensa)
        reg = _registro(writer, dispensa)
        _cera_gia(local)
        # un JPEG con un APP1/Exif vero dentro: `Make` = "Sonda"
        import struct
        tiff = (b"MM\x00\x2a\x00\x00\x00\x08\x00\x01"
                + struct.pack(">HHI", 271, 2, 6) + b"\x00\x00\x00\x1a"
                + b"\x00\x00\x00\x00" + b"Sonda\x00")
        app1 = b"Exif\x00\x00" + tiff
        con_exif = (b"\xff\xd8\xff\xe1" + struct.pack(">H", len(app1) + 2)
                    + app1 + b"\xff\xd9")
        from app import exif
        assert exif.read(con_exif).get("Make") == "Sonda", "la sonda è valida"

        esito = invoke(reg.get("attach_photo_to_su"),
                       {"us": "12", "photo": con_exif}, ORCID, registry=reg)
        assert esito.ok is True
        risorsa = next(n for n in esito.delta.nodes
                       if n["node_type"] == "resource")
        assert risorsa["data"]["source_fields"]["exif"]["Make"] == "Sonda"
        # e NIENTE è stato promosso a campo del grafo
        assert "Make" not in risorsa["data"]
        assert "DateTimeOriginal" not in risorsa["data"]


def test_il_collegamento_non_dipende_dai_metadati(tmp_path):
    """Un telefono col GPS spento funziona uguale: nessun ramo di
    `attach_photo_to_su` guarda quei campi."""
    with FakeRelay() as relay:
        dispensa = _dispensa(tmp_path, StoreMuto())
        writer, local = _scrivano(tmp_path, relay, dispensa=dispensa)
        reg = _registro(writer, dispensa)
        _cera_gia(local)
        from app import exif
        assert exif.read(FOTO) == {}, "questa foto non porta niente"
        esito = invoke(reg.get("attach_photo_to_su"),
                       {"us": "12", "photo": FOTO}, ORCID, registry=reg)
        assert esito.ok is True
        risorsa = next(n for n in esito.delta.nodes
                       if n["node_type"] == "resource")
        assert "source_fields" not in risorsa["data"]
