"""La dispensa: dove stanno i byte di una foto mentre si è fuori.

════════════════════════════════════════════════════════════════════════════════
## IL BUCO CHE QUESTO FILE CHIUDE

Da ieri il nodo ha un ponte per le **operazioni**. Una foto non è
un'operazione: un'operazione ICCD misurata sul relay pesa **173 byte**, e una
foto di cantiere — misurata stanotte su una vera, del Ninfeo di Segni, Canon
6D — pesa **13 623 291 byte**. Quattro ordini di grandezza.

E lo store del nodo **non è sul nodo**: è MinIO, dietro la rete. Misurato
stanotte staccando `em-dev-minio` dalla rete di Docker mentre il servizio era
in piedi:

    create_su            ok=True        54 ms   Ho creato la US 701.
    attach_photo_to_su   ok=False   306 153 ms  «attach_photo_to_su» non è
                                                riuscito: HTTPConnectionPool(
                                                host='minio', port=9000)

**Cinque minuti e sei secondi** con il telefono in mano, e poi la foto non
esiste da nessuna parte: né nello store, né in coda, né sul disco. L'unità
`US701`, scritta un istante prima, esiste. La fotografia che la documentava no.

Peggio, e già scritto nel referto del 27: staccato dalla rete il servizio
**non parte proprio** — `MinioAssetStore._ensure_bucket` alza un `RuntimeError`
al momento della costruzione. Su un nodo di cantiere che si accende in una
galleria, quella riga dice che l'assistente non si apre.

════════════════════════════════════════════════════════════════════════════════
## LE TRE DECISIONI, E L'ARGOMENTO DI CIASCUNA

### 1 · DOVE stanno i byte: accanto alla coda, non dentro

Accanto. E la ragione non è estetica: `Bridge._rewrite` riscrive la coda per
intero a ogni consegna, e `Bridge._read` la legge tutta in memoria. Sono le
scelte giuste per righe da 173 byte e sono quelle sbagliate per tredici
megabyte — una coda di dieci foto verrebbe riletta e riscritta, tutta, a ogni
singola operazione consegnata.

Quindi: **le operazioni dove sono, i byte in una dispensa content-addressed
sul disco del nodo**, sotto lo stesso volume dei dati, che è il volume di cui
si fa il backup e che sopravvive al riavvio del container.

**E chi tiene insieme le due?** Nessuno, ed è il punto: le tiene **il digest**.
Un'operazione cita `sha256:…`; la dispensa è indicizzata dallo stesso
`sha256:…`. Non c'è un identificatore da mantenere allineato fra due file,
perché il nome del contenuto È il contenuto. È la prima delle tre proprietà che
`assets.py` promette nella sua docstring, usata per la prima volta per qualcosa.

### 2 · L'ORDINE del rientro: prima i byte, poi le parole

Misurato, non creduto (vedi `tests/test_le_foto_e_la_scheda.py`, il cancello
della notte): se l'operazione che dice *«questa foto è della US 12»* arriva
**prima** dei byte, il relay la accetta — non sa niente di store, e non deve
saperlo — la propaga a tutti i presenti col fan-out, e chiunque nella stanza si
trova un `ResourceNode` con `url` e `checksum` che puntano a un oggetto che non
esiste. Non un errore: **un'asserzione falsa**, visibile subito e da tutti.

Al contrario, byte che arrivano prima della loro operazione sono byte che per un
istante nessuno cita. Un oggetto orfano in un bucket. Non fa male a nessuno, e
lo si raccoglie.

    LA REGOLA, in una riga:
    finché la dispensa non è vuota, il ponte non si attraversa.

Globale e non per-riferimento, di proposito. Per-riferimento vorrebbe dire
lasciar passare l'operazione successiva mentre si trattiene quella della foto —
cioè **riordinare**, che è esattamente ciò che `Bridge.deliver` si rifiuta di
fare e per lo stesso motivo. Il prezzo è dichiarato: una foto che non riesce a
salire ferma anche il resto della coda. Si vede in `describe()` e in `/health`,
come una coda ferma si è sempre vista.

### 3 · A METÀ: cosa può restare rotto, e cosa no

Misurato su un uvicorn vero e un socket vero, dichiarando `Content-Length` per
il corpo intero e mandandone la metà:

    intero      (400 136 byte) → 200, il gestore ha letto 400 014 byte
    metà        (200 068 byte) → nessuna risposta, IL GESTORE NON È MAI STATO
                                  CHIAMATO
    un decimo    (40 013 byte) → idem

Cioè: un corpo `multipart` troncato **non arriva** al codice che scrive. Nello
store resta un oggetto solo, quello intero. La strada HTTP è sicura da sé, e la
scrittura locale della dispensa è atomica (`tmp` + `replace`, già in
`DirectoryAssetStore`), quindi **nessun lettore vede mai mezzo file**.

Ne resta uno, stretto, e questo file non lo chiude — lo chiude `tools.py` con il
digest dichiarato: se il **client** manda un base64 troncato a un multiplo di 4,
si decodifica pulito e diventa mezza foto con un `ref` valido:

    266 676 caratteri (mod 4 = 0): DECODIFICATO, 200 007 byte, finisce con FFD9: False
    266 677 caratteri (mod 4 = 1): rifiutato

Una probabilità su quattro, e nessuno può accorgersene dopo: il
content-addressing rende quel mezzo file **coerente con sé stesso**. La cura è
che chi manda dica quale digest si aspetta, e il nodo lo verifichi prima di
scrivere — che è la terza proprietà promessa da `assets.py`, anch'essa mai usata
fino a stanotte.

════════════════════════════════════════════════════════════════════════════════
## COSA QUESTO FILE NON FA

**Non parla con nessuno nel momento del gesto.** `put` scrive sul disco locale e
basta: nessuna chiamata di rete dentro l'atto di chi ha il telefono in mano.
I cinque minuti misurati sopra non possono ripresentarsi, perché non c'è più
niente da aspettare. Verso lo store condiviso si sale in `deliver()`, che viene
chiamata al rientro — quando la rete, per definizione, c'è.

**Non ha una seconda coda.** La fila d'attesa È `Bridge`, la stessa classe del
ponte delle operazioni. Due implementazioni di «una fila durevole che si
consegna in ordine e si ferma al primo che non passa» divergerebbero al primo
rifiuto che qualcuno aggiunge a una sola delle due — ed è letteralmente la
trappola che il prompt di stanotte chiede di guardare prima.
"""

from __future__ import annotations

import logging
import os
import pathlib
from typing import Any, Callable, Dict, List, Optional

from .assets import AssetStore, DirectoryAssetStore, asset_ref_valid, content_id
from .bridge import Bridge

log = logging.getLogger("stratigraph.spool")

#: Il verbo con cui una voce della dispensa si presenta nella coda. Sta lì
#: perché `Bridge` compone la frase di «coda ferma» da `op` e `id`: senza, un
#: rifiuto direbbe «None None: …», che è una palude con un nome.
PUT_ASSET = "put_asset"


class Larder(DirectoryAssetStore):
    """La directory della dispensa, con in più il gesto di **raccogliere**.

    Una sottoclasse qui e non un metodo in più là: `assets.py` è un DOPPIONE
    DICHIARATO del modulo di StratiGraph Server — *«a change here is a change
    there»* — e togliere la copia locale dopo che è salita è una politica del
    nodo di campo, non un'operazione dello store. Lo store non dimentica: è
    tutto il suo mestiere.
    """

    def forget(self, ref: str) -> None:
        path = self._path(ref)
        for candidate in (path, path.with_suffix(".type")):
            try:
                candidate.unlink(missing_ok=True)
            except OSError:               # disco in sola lettura, permessi
                pass


class Spool:
    """I byte sul disco del nodo, e quelli che devono ancora salire.

    Implementa `AssetStore` — `put` / `get` / `head` — quindi si infila dove
    stava lo store e nessun chiamante cambia. Quello che cambia è **quando** si
    parla con la rete: mai dentro il gesto, sempre al rientro.
    """

    def __init__(self, root: str | os.PathLike[str], *,
                 remote: Optional[AssetStore] = None,
                 remote_factory: Optional[Callable[[], AssetStore]] = None,
                 keep_local: bool = False) -> None:
        self.root = pathlib.Path(root)
        self.local = Larder(self.root / "objects")
        #: LA FILA, che è quella del ponte. Vedi la docstring del modulo.
        self.queue = Bridge(str(self.root / "waiting.jsonl"))
        #: LO STORE CONDIVISO, PIGRO. Costruito alla prima consegna e non
        #: all'avvio: `MinioAssetStore.__init__` fa un giro di rete
        #: (`_ensure_bucket`) e alzarlo all'import è la riga per cui un nodo in
        #: galleria non si accende. Un nodo deve poter aprire, registrare e
        #: fotografare senza rete; consegnare è un'altra cosa e vuole la rete
        #: per definizione.
        self._remote = remote
        self._remote_factory = remote_factory
        #: Perché l'ultima salita non è riuscita. Come `bridge.last_refusal`.
        self.last_refusal: Optional[str] = None
        #: Tenere la copia locale anche dopo che è salita. Falso di default: la
        #: dispensa è una dispensa, non un secondo archivio — e su un nodo di
        #: campo il disco è la risorsa che finisce per prima. Vero è utile a chi
        #: vuole un nodo che risponda alle immagini anche da solo, ed è una
        #: scelta di configurazione, non un'opinione di questo file.
        self.keep_local = keep_local

    # ── lo store ────────────────────────────────────────────────────────────

    def put(self, data: bytes, media_type: str) -> Dict[str, Any]:
        """Scrivi i byte **sul disco di qui**, e mettili in fila per lo store.

        Nessuna rete. È l'intero valore di questo metodo: il gesto di chi
        fotografa finisce in millisecondi anche in una galleria.
        """
        info = self.local.put(data, media_type)
        ref = info["ref"]
        if ref not in self.waiting():
            self.queue.keep([{"op": PUT_ASSET, "id": ref, "ref": ref,
                              "media_type": media_type, "size": len(data)}],
                            why="lo store condiviso non è ancora stato raggiunto")
        info["spooled"] = True
        return info

    def get(self, ref: str) -> Optional[bytes]:
        """I byte: prima da qui, poi da lassù.

        Prima da qui perché qui c'è sempre risposta, e perché una foto appena
        scattata è ancora solo qui.
        """
        found = self.local.get(ref)
        if found is not None:
            return found
        remote = self._reach()
        return remote.get(ref) if remote is not None else None

    def head(self, ref: str) -> Optional[Dict[str, Any]]:
        found = self.local.head(ref)
        if found is not None:
            return found
        remote = self._reach()
        return remote.head(ref) if remote is not None else None

    # ── la fila ─────────────────────────────────────────────────────────────

    def waiting(self) -> List[str]:
        """I riferimenti che non hanno ancora raggiunto lo store condiviso."""
        return [str(voce.get("ref") or "") for voce in self.queue.pending()]

    def __len__(self) -> int:
        return len(self.queue)

    def describe(self) -> Dict[str, Any]:
        quanti = len(self.queue)
        byte = sum(int(v.get("size") or 0) for v in self.queue.pending())
        return {"waiting": quanti, "bytes": byte, "root": str(self.root),
                "stuck_because": self.last_refusal if quanti else None,
                "keeps_local_copy": self.keep_local}

    def deliver(self) -> Dict[str, Any]:
        """Fai salire quello che aspetta, in ordine, fermandoti al primo che no.

        Chiamata al rientro, prima del ponte delle operazioni. Il valore di
        ritorno è quello di `Bridge.deliver`, perché è `Bridge.deliver`.
        """
        remote = self._reach()
        if remote is None:
            self.last_refusal = "nessuno store condiviso configurato"
            return {"delivered": 0, "already": 0, "left": len(self.queue),
                    "stopped": self.last_refusal}
        salite: List[str] = []

        def sali(voce: Dict[str, Any]):
            ref = str(voce.get("ref") or "")
            if not asset_ref_valid(ref):
                # Non è un guasto di rete e riprovare non lo aggiusta: si dice e
                # si toglie, altrimenti una riga storta ferma la fila per sempre.
                log.warning("dispensa: %r non è un riferimento, lo tolgo", ref)
                return True, "already"
            data = self.local.get(ref)
            if data is None:
                # I byte non sono più qui. Non è un errore da ritentare: o sono
                # già saliti e la copia è stata raccolta, o qualcuno ha pulito
                # sotto. In entrambi i casi la fila non deve restarci appesa.
                if remote.head(ref) is not None:
                    return True, "already"
                return False, ("i byte non sono più nella dispensa e non sono "
                               "nello store: la foto è persa")
            esito = remote.put(data, str(voce.get("media_type")
                                         or "application/octet-stream"))
            salite.append(ref)
            return True, ("" if esito.get("created") else "already")

        esito = self.queue.deliver(sali)
        self.last_refusal = esito.get("stopped")
        if not self.keep_local:
            self._collect(salite)
        return esito

    def _collect(self, refs: List[str]) -> None:
        """Togli le copie locali di quello che è salito — e **solo dopo** aver
        controllato che sia davvero lassù.

        Il controllo è un `head`, non un'assunzione sull'esito del `put`: fra i
        due c'è una rete, ed è la rete il motivo per cui questo file esiste.
        """
        remote = self._reach()
        if remote is None:
            return
        for ref in refs:
            try:
                if remote.head(ref) is None:
                    continue
                self.local.forget(ref)
            except Exception as exc:      # noqa: BLE001 — pulire non è urgente
                log.warning("dispensa: %s non si è raccolto: %s", ref, exc)

    # ── lassù ───────────────────────────────────────────────────────────────

    def _reach(self) -> Optional[AssetStore]:
        """Lo store condiviso, costruito alla prima occasione utile.

        Se costruirlo fallisce — è un giro di rete — non si alza niente: si
        registra e si riproverà al prossimo rientro. Un nodo che si rifiuta di
        funzionare perché il bucket non risponde è il difetto che questo file
        toglie, e riproporlo qui sarebbe averlo solo spostato.
        """
        if self._remote is not None:
            return self._remote
        if self._remote_factory is None:
            return None
        try:
            self._remote = self._remote_factory()
        except Exception as exc:          # noqa: BLE001
            self.last_refusal = f"{type(exc).__name__}: {exc}"
            log.info("dispensa: lo store condiviso non risponde: %s", exc)
            return None
        return self._remote


def spool_from_env(environ: Optional[Dict[str, str]] = None
                   ) -> Optional[Spool]:
    """La dispensa che questo nodo ha, o nessuna.

    `EM_ASSET_SPOOL` è la directory. Senza, non c'è dispensa e il servizio si
    comporta come prima — che è la scelta giusta per un nodo di scrivania con
    la rete sotto il tavolo, e la scelta sbagliata per un telefono in trincea.
    Il `docker-compose` di campo la mette sotto lo stesso volume del container
    locale e del ponte: le tre cose vivono la stessa vita.
    """
    env = environ if environ is not None else os.environ
    root = (env.get("EM_ASSET_SPOOL") or "").strip()
    if not root:
        return None
    from .assets import _minio_settings

    def costruisci() -> AssetStore:
        from .assets import DirectoryAssetStore as _Dir
        from .assets import MinioAssetStore
        minio = _minio_settings(dict(env))
        if minio:
            return MinioAssetStore(endpoint=minio["endpoint"],
                                   access_key=minio["access_key"],
                                   secret_key=minio["secret_key"],
                                   bucket=minio["bucket"],
                                   secure=minio["secure"])
        shared = (env.get("EM_ASSET_DIR") or "").strip()
        if shared:
            return _Dir(shared)
        raise RuntimeError(
            "c'è una dispensa (EM_ASSET_SPOOL) ma nessuno store condiviso dove "
            "consegnarla: le foto resterebbero su questo nodo per sempre. "
            "Configura MinIO (MINIO_ENDPOINT/…) oppure EM_ASSET_DIR.")

    keep = (env.get("EM_ASSET_SPOOL_KEEPS") or "").strip().lower() in (
        "1", "true", "yes", "on")
    return Spool(root, remote_factory=costruisci, keep_local=keep)


def shared_name(environ: Optional[Dict[str, str]] = None) -> str:
    """Come si chiama lo store condiviso, LETTO DALLA CONFIGURAZIONE.

    Senza costruirlo, e la differenza è tutto il punto: costruire un
    `MinioAssetStore` è un giro di rete (`_ensure_bucket`), e `/health` deve
    poter rispondere anche a un nodo in galleria. Un `/health` che si blocca
    perché il bucket non risponde è la spia che si spegne quando serve.
    """
    env = environ if environ is not None else os.environ
    from .assets import _MINIO_KEYS
    trovati = {campo: next((env[nome].strip() for nome in nomi
                            if (env.get(nome) or "").strip()), "")
               for campo, nomi in _MINIO_KEYS.items()}
    if trovati["endpoint"]:
        return (f"minio ({trovati['endpoint']}, bucket "
                f"{trovati['bucket'] or '?'}) — non contattato all'avvio")
    directory = (env.get("EM_ASSET_DIR") or "").strip()
    if directory:
        return f"directory ({directory}) — local only, not for replicas"
    return "nessuno: la dispensa non ha dove consegnare"


def describe(spool: Optional[Spool]) -> str:
    if spool is None:
        return "nessuna (i byte vanno diritti allo store: senza rete si perdono)"
    stato = spool.describe()
    if not stato["waiting"]:
        return f"vuota ({stato['root']})"
    kb = stato["bytes"] / 1024
    fermo = f", ferma su {stato['stuck_because']}" if stato["stuck_because"] else ""
    return f"{stato['waiting']} in attesa, {kb:.0f} KB{fermo}"


def verify(data: bytes, declared: str) -> None:
    """Il digest che il client dichiara deve essere quello dei byte arrivati.

    Alza `ValueError` quando non lo è. Esiste per il caso misurato nella
    docstring del modulo — un base64 troncato a un multiplo di 4 — che senza
    questo controllo diventa mezza foto con un riferimento valido, e nessuno
    può più accorgersene: il contenuto è coerente con il proprio nome.

    Chi non dichiara niente passa come prima. Non è un cancello che si può
    chiudere da qui: i client che mandano foto oggi sono la pagina di questo
    repo e chiunque altro parli con `/v1/photo`, e rifiutare chi tace vorrebbe
    dire romperli tutti in una notte. `/health` dice quanti hanno dichiarato.
    """
    atteso = str(declared or "").strip()
    if not atteso:
        return
    if ":" not in atteso:
        atteso = f"sha256:{atteso}"
    vero = content_id(data)
    if atteso.lower() != vero:
        raise ValueError(
            f"i byte arrivati non sono quelli annunciati: dichiarato "
            f"{atteso[:23]}…, ricevuti {vero[:23]}… ({len(data)} byte). "
            f"La foto non è stata scritta.")
