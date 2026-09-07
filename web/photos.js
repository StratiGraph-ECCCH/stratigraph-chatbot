/* Le foto sul telefono: dove stanno i byte prima di partire, e come si legano.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * ## QUALE DELLE DUE CODE TIENE LE FOTO
 *
 * Ce ne sono due, e due notti fa averle confuse è costato una notte: quella del
 * browser (`sg.queue.v1` in `localStorage`) e quella del servizio
 * (`app/bridge.py`, e da stanotte `app/spool.py`). **Tutte e due**, e non è una
 * risposta comoda: sono due tratte diverse dello stesso viaggio.
 *
 *     telefono ──1──▶ nodo ──2──▶ stanza + store condiviso
 *
 * La tratta 1 cade quando chi scava è fuori dalla portata del nodo; la 2 quando
 * il nodo è in galleria. Un solo magazzino non copre entrambe, e chiamarle «la
 * coda» al singolare è precisamente l'errore del 28.
 *
 * Il piano perché non divergano è che **non condividono codice, condividono una
 * regola**, ed è scritta nelle due lingue in due posti soli:
 *
 *     prima i byte, poi le parole.
 *
 * Di qua: una foto non parte finché non è sul disco del telefono. Di là:
 * `RoomWriter._bytes_before_words`.
 *
 * ── E PERCHÉ NON `localStorage`, che pure c'è già ───────────────────────────
 *
 * Misurato in un browser vero, su questa origine, non supposto:
 *
 *     una foto di 4 MB in base64        →  5,33 MB di stringa (×1,33)
 *     il tetto di localStorage          →  49 MB, poi QuotaExceededError
 *     quindi                            →  NOVE foto, e la decima butta tutto
 *
 * Nove è un pomeriggio di scavo. E il numero misurato è quello di un Chromium
 * di scrivania: **il tetto di un telefono è più basso**, e non lo si misura da
 * qui — dichiarato, non spacciato.
 *
 * IndexedDB, sulla stessa origine e nello stesso momento, dichiara **12,5 GB**
 * e tiene i `Blob` come sono: niente ×1,33, e soprattutto niente
 * `JSON.parse` dell'intera coda per leggere un elemento — che è quello che
 * `readQueue()` fa a ogni disegno.
 *
 * ── COSA RESTA IN `localStorage` ────────────────────────────────────────────
 *
 * La coda delle FRASI, che è quella di prima e non si tocca. Una frase pesa
 * quanto una riga; il posto giusto per una riga è quello dove è sempre stata.
 */

//: LE DUE CONFERME, da un posto solo e senza dizionario dentro. Vendorizzato
//: da `stratigraph-server/app/node_admin/confirm.js` — vedi `sync-brand.sh`.
import { makeConfirm } from "./confirm.js";

const DB = "sg-photos";
const STORE = "photos";
const VERSION = 1;

/* Gli stati di una foto, e sono tre perché tre sono i fatti diversi.
 *
 * «in attesa» NON è un errore: E.D. dice «faccio delle foto, LE collego» — al
 * plurale e in un secondo momento. Una foto che aspetta di essere collegata è
 * uno stato legittimo del lavoro, e in questo progetto i buchi si mostrano e si
 * nominano invece di essere nascosti. */
export const ATTESA = "attesa";     // scattata, nessuna unità
export const PRONTA = "pronta";     // legata a un'unità, non ancora partita

/* E il terzo stato non esiste, di proposito: una foto che il nodo ha preso
 * SPARISCE da qui. Tenerla come «partita» vorrebbe dire due copie della stessa
 * foto — una sul telefono e una nello store — e la seconda è quella che vale.
 * Lo spazio di un telefono è la risorsa che finisce per prima. */

let _db = null;

function open() {
  if (_db) return Promise.resolve(_db);
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB, VERSION);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(STORE)) {
        db.createObjectStore(STORE, { keyPath: "id" });
      }
    };
    req.onsuccess = () => { _db = req.result; resolve(_db); };
    req.onerror = () => reject(req.error);
  });
}

function tx(mode, work) {
  return open().then((db) => new Promise((resolve, reject) => {
    const t = db.transaction(STORE, mode);
    const out = work(t.objectStore(STORE));
    t.oncomplete = () => resolve(out && out.result !== undefined
      ? out.result : out);
    t.onerror = () => reject(t.error);
  }));
}

/* ── il digest, calcolato QUI ───────────────────────────────────────────────
 *
 * Il nodo lo verifica prima di scrivere (`app/spool.py::verify`), e senza
 * qualcuno che lo dichiari quel controllo non ha chi lo eserciti. Il caso
 * misurato è stretto ma reale: un base64 troncato a un multiplo di 4 si
 * decodifica pulito in mezza foto, con un riferimento valido che dopo nessuno
 * può più smascherare. Una possibilità su quattro.
 *
 * `crypto.subtle` esiste solo su origini sicure. Su `http://` senza TLS torna
 * stringa vuota, che il nodo tratta come «non dichiarato» — cioè come prima. */
export async function digest(blob) {
  try {
    const buf = await blob.arrayBuffer();
    const hash = await crypto.subtle.digest("SHA-256", buf);
    return "sha256:" + [...new Uint8Array(hash)]
      .map((b) => b.toString(16).padStart(2, "0")).join("");
  } catch { return ""; }
}

/* ── tenere ─────────────────────────────────────────────────────────────── */

/** Metti una foto al sicuro PRIMA di provare a mandarla.
 *
 *  L'ordine è la regola della notte vista da questa parte: i byte prima delle
 *  parole. Se la scrittura fallisce — spazio finito, finestra privata — la
 *  cosa si dice e non si scatta nel vuoto. */
export async function keep(file, { us = "" } = {}) {
  const id = (crypto.randomUUID ? crypto.randomUUID()
    : String(Date.now()) + Math.random().toString(16).slice(2));
  const voce = {
    id,
    blob: file,
    name: file.name || "foto.jpg",
    type: file.type || "image/jpeg",
    size: file.size,
    // L'ISTANTE DEL DISPOSITIVO, non quello del nodo. `lastModified` è quando
    // il file è stato creato dalla fotocamera; l'EXIF lo dirà meglio, e lo
    // legge il nodo (`app/exif.py`) perché è lui che deve poterlo trasportare.
    at: file.lastModified || Date.now(),
    us: String(us || ""),
    state: us ? PRONTA : ATTESA,
    sha256: await digest(file),
  };
  await tx("readwrite", (s) => s.put(voce));
  return voce;
}

export function list() {
  return tx("readonly", (s) => s.getAll());
}

/** Togli una foto dalla coda. Chiamata da `deliver` quando il nodo l'ha presa,
 *  e — dall'8 ottobre 2026 — da una persona che la scarta.
 *
 *  Misurato prima di aggiungere il gesto: questa funzione esisteva e **nessuna
 *  persona la chiamava**. L'unico chiamante era `deliver`, dopo una consegna
 *  riuscita, cioè la rimozione automatica sul successo. Il che vuol dire che la
 *  coda era un posto dove si metteva e non si toglieva, e il 6 settembre è
 *  costato una serata: il solo modo di togliere una foto era cancellare i dati
 *  del sito, cioè buttare anche tutto il resto. */
export function forget(id) {
  return tx("readwrite", (s) => s.delete(id));
}

/** Lega una foto a un'unità. Il gesto che E.D. fa «dopo». */
export async function link(id, us) {
  const voce = await tx("readonly", (s) => s.get(id));
  if (!voce) return null;
  voce.us = String(us || "");
  voce.state = voce.us ? PRONTA : ATTESA;
  await tx("readwrite", (s) => s.put(voce));
  return voce;
}

/* ── mandare ────────────────────────────────────────────────────────────── */

async function base64(blob) {
  const reader = new FileReader();
  return new Promise((resolve) => {
    reader.onload = () => resolve(String(reader.result).split(",")[1]);
    reader.readAsDataURL(blob);
  });
}

/** Manda al nodo le foto che hanno un'unità, in ordine, **fermandoti alla
 *  prima che non passa**.
 *
 *  La stessa regola di `Bridge.deliver` di là, e per lo stesso motivo: saltare
 *  quella che non passa riordinerebbe operazioni che il CRDT ordina per
 *  orologio. Una fila ferma si vede; una fila riordinata no.
 *
 *  Una foto senza unità NON viene mandata: il nodo la rifiuterebbe con «Non
 *  trovo la US» e la risposta giusta a «non so ancora di che unità è» non è
 *  chiederlo a un servizio. */
export async function deliver(seam) {
  const tutte = await list();
  const pronte = tutte.filter((v) => v.state === PRONTA)
    .sort((a, b) => a.at - b.at);
  let mandate = 0;
  for (const voce of pronte) {
    const corpo = {
      transcript: "questa foto è per la US " + voce.us,
      us: voce.us,
      photo_base64: await base64(voce.blob),
      filename: voce.name,
      media_type: voce.type,
      sha256: voce.sha256,
    };
    try {
      const esito = await seam.post("/v1/photo", corpo);
      if (!esito.ok) break;               // il nodo ha detto no: si ferma
      await forget(voce.id);
      mandate++;
    } catch {
      break;                              // il nodo non c'è: si riprova dopo
    }
  }
  return mandate;
}

/* ── farsi vedere ───────────────────────────────────────────────────────── */

/** La frase sotto la striscia. Pura, perché è la regola detta a voce e una
 *  regola che vale la pena dire vale la pena provarla. */
export function sentence(voci, t) {
  const attesa = voci.filter((v) => v.state === ATTESA).length;
  const pronte = voci.filter((v) => v.state === PRONTA).length;
  const righe = [];
  if (attesa) {
    righe.push(t("photos.waiting" + (attesa === 1 ? ".one" : ".many"),
      { n: attesa }));
  }
  if (pronte) {
    righe.push(t("photos.ready" + (pronte === 1 ? ".one" : ".many"),
      { n: pronte }));
  }
  return righe.join(" ");
}

/** Disegna la striscia. Miniature e niente moduli: chi ha i guanti tocca una
 *  foto, non compila un campo. */
export async function paint(host, seam, { proposeUs = () => "" } = {}) {
  //: il dizionario è del seam, quindi la conferma si lega QUI e non al modulo:
  //: `makeConfirm` prende `t` come argomento precisamente per questo
  const { confirmTyped } = makeConfirm(seam.t);
  const voci = (await list()).sort((a, b) => b.at - a.at);
  host.textContent = "";
  host.hidden = voci.length === 0;
  if (!voci.length) return voci;

  const ridisegna = () => paint(host, seam, { proposeUs });

  async function altro(voce) {
    const scelto = window.prompt(seam.t("photos.ask"), voce.us || proposeUs());
    if (scelto === null) return;
    await link(voce.id, scelto.trim());
    await ridisegna();
    if (scelto.trim()) { await deliver(seam); await ridisegna(); }
  }

  const striscia = document.createElement("div");
  striscia.className = "shots";
  for (const voce of voci) {
    // UN DIV CHE CONTIENE DUE BOTTONI, e non un bottone dentro un bottone:
    // quello non è HTML valido, e il modo in cui ogni browser lo «aggiusta»
    // non è lo stesso — l'annidamento viene sciolto, e il bottone interno
    // finisce fuori dal contenitore su cui è disegnato il bordo di stato.
    const cella = document.createElement("div");
    cella.className = "shot-cell";
    cella.dataset.state = voce.state;
    cella.dataset.id = voce.id;
    const tocca = document.createElement("button");
    tocca.type = "button";
    tocca.className = "shot-face";
    const img = document.createElement("img");
    img.src = URL.createObjectURL(voce.blob);
    img.alt = "";
    // Rilasciato appena il browser l'ha disegnata: un `createObjectURL` per
    // foto per ridisegno tiene in vita i blob finché la pagina non si chiude,
    // e su un telefono con dieci foto da tredici megabyte si sente.
    img.onload = () => URL.revokeObjectURL(img.src);
    // UN FORMATO CHE QUESTO BROWSER NON DISEGNA NON È UNA FOTO PERSA. Un
    // iPhone consegna HEIC, e non tutti i motori lo mostrano: la foto c'è, ha
    // il suo digest, parte lo stesso, e qui al suo posto compare il nome. Una
    // miniatura rotta direbbe una cosa falsa su un file che sta benissimo.
    img.onerror = () => {
      URL.revokeObjectURL(img.src);
      img.replaceWith(Object.assign(document.createElement("span"), {
        className: "shot-blind", textContent: voce.name,
      }));
    };
    // LA SCHEDA APERTA È UN CONTESTO, non una decisione. Il numero proposto
    // sta SCRITTO SUL BOTTONE: si vede prima di toccare, e toccare è
    // acconsentire. Nessun collegamento avviene da solo — è la differenza fra
    // proporre e decidere al posto di chi scava.
    const proposta = voce.us || proposeUs() || "";
    const etichetta = document.createElement("span");
    etichetta.textContent = voce.us
      ? seam.t("photos.on", { us: voce.us })
      : (proposta ? seam.t("photos.propose", { us: proposta })
                  : seam.t("photos.unlinked"));
    tocca.append(img, etichetta);
    cella.append(tocca);
    tocca.addEventListener("click", async () => {
      if (!proposta) return altro(voce);      // niente da proporre: si chiede
      await link(voce.id, proposta);
      await ridisegna();
      await deliver(seam);
      await ridisegna();
    });
    striscia.append(cella);

    // L'altra strada, piccola e dietro: un numero diverso da quello proposto.
    const cambia = document.createElement("button");
    cambia.type = "button";
    cambia.className = "shot-other";
    cambia.textContent = seam.t("photos.other");
    cambia.addEventListener("click", () => altro(voce));
    cella.append(cambia);

    // ── SCARTARE · il verbo che mancava ───────────────────────────────────
    //
    // `forget` esisteva e nessuna persona la chiamava: l'unico chiamante era
    // `deliver`, dopo una consegna riuscita. Quindi la coda era un posto dove
    // si metteva e non si toglieva, e il 6 settembre è costato una serata —
    // l'unico modo di togliere una foto era cancellare i dati del sito, cioè
    // buttare anche tutto il resto.
    //
    // NON SI TORNA INDIETRO, e questo decide la conferma: i byte di questa
    // foto stanno **solo su questo telefono**, non sono mai arrivati al nodo.
    // Quindi `confirmTyped` e non `confirmNamed` (decisione del 7 ottobre; non
    // ce n'è una terza), e quello che si scrive è il NOME DEL FILE, che è
    // l'unica cosa di questa foto che una persona vede scritta — è quello che
    // compare al posto della miniatura quando il browser non sa disegnarla.
    //
    // IL COSTO, dichiarato: con i guanti, scrivere `IMG_4821.HEIC` è un gesto
    // difficile. È il prezzo dell'irreversibilità, e sta scritto nel referto
    // dell'8 ottobre invece di essere risolto con una conferma più comoda.
    const scarta = document.createElement("button");
    scarta.type = "button";
    scarta.className = "shot-drop";
    scarta.textContent = seam.t("photos.drop");
    scarta.title = seam.t("photos.drop.title", { name: voce.name });
    scarta.addEventListener("click", async () => {
      if (!confirmTyped(seam.t("photos.drop.ask"), voce.name)) return;
      await forget(voce.id);
      await ridisegna();
    });
    cella.append(scarta);
  }
  const detto = document.createElement("p");
  detto.className = "hint";
  detto.textContent = sentence(voci, seam.t);
  host.append(striscia, detto);
  return voci;
}

/** Aggancia l'otturatore, e disegna. Una via sola verso `keep`.
 *
 *  Il difetto che questo sostituisce: la pagina indovinava l'unità con
 *  `saidBox.textContent.match(/\b(\d{1,5})\b/)` — il primo numero comparso
 *  nell'ultima frase — e mandava subito. Su «la US 12 taglia la 7» avrebbe
 *  legato la foto alla 12 senza chiedere, e offline avrebbe messo 5,33 MB di
 *  base64 in `localStorage`. */
export function mount({ shoot, camera, host, seam, proposeUs }) {
  const ridisegna = () => paint(host, seam, { proposeUs });
  shoot.addEventListener("click", () => camera.click());
  camera.addEventListener("change", async () => {
    const file = camera.files && camera.files[0];
    camera.value = "";                 // due scatti di fila della stessa foto
    if (!file) return;
    try {
      // SENZA UNITÀ, sempre. Il numero si propone nella striscia, dove si
      // vede prima di toccare. Scattare è un gesto; collegare è un atto, e
      // farli diventare uno solo vuol dire deciderlo al posto della persona.
      await keep(file);
    } catch (err) {
      seam.show(false, seam.t("photos.nokeep"), String(err && err.name || ""));
      return;
    }
    await ridisegna();
  });
  return ridisegna;
}
