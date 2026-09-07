/* Il telaio, e chi guida il modulo.
 *
 * Un modulo ES servito dal nodo: nessuna catena di build, nessun bundler,
 * nessuna dipendenza. Su un Raspberry Pi in cantiere una catena di build è una
 * cosa in più che può non installarsi, e questo file la evita per intero — il
 * browser lo carica come lo trova sul disco.
 *
 * Non parla al grafo. Parla al servizio attraverso `window.SG`, il seam che la
 * pagina dichiara, e la scrittura passa da `SG.send`, che è anche ciò che mette
 * una richiesta fallita nella coda offline.
 */

import {
  definitionFor, effectiveMode, keyField, payloadFor, proposeMode,
  refreshCompleteness, rememberMode, render, save, stepTo, thumbbarPlan,
  trenchFields, otherFields, wayBack,
} from "./scheda.js";
import { mount as mountPhotos } from "./photos.js";
import { mount as mountRoom, roomOf } from "./room.js";
import { mount as mountIndex } from "./indice.js";
import { arrivalPlan, readArrival } from "./arrivo.js";
import { mount as mountChat } from "./chat.js";

const $ = (id) => document.getElementById(id);
const SG = () => window.SG || {};

/* Ridisegna la striscia delle foto. Assegnata al montaggio: prima di allora
 * non c'è niente da ridisegnare, e una funzione che non fa niente sarebbe una
 * bugia comoda. */
let repaintPhotos = () => {};
/* …e la stessa cosa per l'elenco di cosa c'è già nella stanza. */
let repaintIndex = async () => {};

const MODES = [
  ["phone", "Telefono"],
  ["tablet", "Tablet"],
  ["desktop", "Scrivania"],
];
const THEME_KEY = "sg.theme.v1";

/* ── il tema ────────────────────────────────────────────────────────────────
 *
 * Tre stati e non due: chiaro, scuro, e **quello del sistema** — che è il
 * default e non è «chiaro». Il tema del tema (`brand/stratigraph-theme.css`)
 * segue `prefers-color-scheme` quando `data-sg-theme` non c'è, e cancellare
 * l'attributo è come si torna a quello. Un toggle a due stati costringerebbe
 * una scelta che il dispositivo ha già fatto. */
const THEMES = [null, "light", "dark"];

function applyTheme(value) {
  if (value) document.documentElement.dataset.sgTheme = value;
  else delete document.documentElement.dataset.sgTheme;
  try {
    if (value) localStorage.setItem(THEME_KEY, value);
    else localStorage.removeItem(THEME_KEY);
  } catch { /* private window: lasts the session */ }
}

function savedTheme() {
  try {
    const kept = localStorage.getItem(THEME_KEY);
    return THEMES.includes(kept) ? kept : null;
  } catch { return null; }
}

/* ── lo stato della superficie ───────────────────────────────────────────── */

const state = {
  mode: "tablet",
  // QUALE dei due pannelli è a schermo — «detta» o «scheda». Non si deduce da
  // `def`: una scheda può essere caricata e il pannello della voce davanti.
  panel: "voice",
  def: null,
  values: {},
  authored: {},
  validated: new Set(),
  model: "",
  us: "",
  create: true,
  step: 0,
  keyField: null,
  showAll: false,
  onePage: false,
  onChange: () => paintCompleteness(),
  onStep: (i, n) => { $("tb-step").textContent = n ? `${i + 1}/${n}` : ""; },
  onValidate: (field) => validateField(field),
  onClear: () => clearScheda(),
  // Lo stesso atto del bottone della barra dei pollici, chiamato dal piede
  // della scheda quando la barra non c'è. Una via sola verso `save`.
  onSave: () => save(state.def, state),
};

/* ── il modo ─────────────────────────────────────────────────────────────── */

function paintModes() {
  const proposed = proposeMode(window.innerWidth);
  const bar = $("modes");
  bar.replaceChildren();
  for (const [value, label] of MODES) {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "chip";
    chip.textContent = label;
    chip.setAttribute("aria-pressed", String(value === state.mode));
    if (value === proposed) chip.dataset.proposed = "true";
    chip.addEventListener("click", () => {
      // Scegliere SCRIVE la scelta: è ciò che la fa sopravvivere a un
      // ricaricamento, e il puntino sul chip proposto resta a dire che la
      // larghezza ne suggerirebbe un altro.
      // …E LA SCELTA PORTA LA FINESTRA SU CUI È STATA FATTA. `rememberMode` è
      // l'unico posto che scrive: qui prima c'era una `setItem` per conto suo,
      // cioè un secondo scrittore della stessa chiave.
      rememberMode(value, window.innerWidth);
      setMode(value);
    });
    bar.append(chip);
  }
}

/* SUL TELEFONO LA BARRA IN ALTO NON CI STA — e non è una questione di gusto:
 * marchio, tre chip, tema, lingua, ORCID e stato su 375 px si accavallano.
 *
 * I nodi si SPOSTANO nella colonna, che sul telefono si apre col pollice: non
 * si duplicano. Due copie di un selettore di modo sono due stati il giorno che
 * qualcuno ne tocca uno, e sarebbe lo stesso difetto delle due caselle «US».
 *
 * E c'è una ragione oltre allo spazio: quei controlli stanno in cima, cioè dove
 * una mano sola NON arriva. Nella colonna che si apre dal basso ci arriva. */
const MOVABLE = ["modes", "theme", "lang"];

function placeControls(mode) {
  const home = mode === "phone" ? $("nav-controls-host") : null;
  $("nav-controls").hidden = mode !== "phone";
  for (const id of MOVABLE) {
    const node = $(id);
    if (!node) continue;
    const wanted = home || $("topbar-controls");
    if (node.parentElement !== wanted) wanted.append(node);
  }
}

/* L'UNICO posto che decide se la barra dei pollici c'è. Prima erano quattro
 * righe sparse in quattro funzioni, e la porta murata è nata da lì: `setMode`,
 * `draw`, `openScheda` e il bottone «Detta» la nascondevano ognuno per conto
 * suo, e nessuna delle quattro sapeva che dentro c'era l'unica maniglia della
 * colonna. La decisione è in `thumbbarPlan`, che è pura e provata. */
function paintThumbbar() {
  const plan = thumbbarPlan(state.mode, state.panel, state.def);
  $("thumbbar").hidden = !plan.bar;
  for (const id of ["tb-prev", "tb-step", "tb-next", "tb-save"]) {
    $(id).hidden = !plan.steps;
  }
  paintWayBack();
}

/* LA VIA D'USCITA, VISIBILE SENZA SAPERE GIÀ DOV'È.
 *
 * Il 24 settembre la maniglia è tornata a esistere; stanotte è stato misurato
 * che esistere non basta — su una finestra da 1574 i tre chip stavano a y=1372,
 * fuori dallo schermo, e l'unico bersaglio visibile era un ☰ che dice
 * «navigazione» e non «questa finestra ne proporrebbe un altro».
 *
 * Quindi il ritorno sta in barra, che sul telefono c'è sempre, e nomina il modo
 * verso cui torna invece di essere un'altra icona da indovinare. */
function paintWayBack() {
  const bottone = $("tb-back");
  if (!bottone) return;
  const verso = wayBack(state.mode, window.innerWidth);
  bottone.hidden = !verso;
  if (!verso) return;
  const nome = (MODES.find(([value]) => value === verso) || [])[1] || verso;
  bottone.textContent = SG().t
    ? SG().t("mode.back", { mode: nome }) : `↔ ${nome}`;
  bottone.setAttribute("aria-label", bottone.textContent);
  bottone.dataset.mode = verso;
}

function setMode(mode) {
  state.mode = mode;
  $("surface").dataset.sgMode = mode;
  placeControls(mode);
  paintThumbbar();
  $("sidenav").dataset.open = "false";
  $("nav-onepage").hidden = mode !== "desktop";
  paintModes();
  if (state.def) draw();
}

/* ── le schede che il nodo serve ─────────────────────────────────────────── */

async function loadSchede() {
  const host = $("nav-schede");
  host.replaceChildren();
  let listing;
  try {
    const answer = await fetch(`${SG().node}/v1/schede`,
                               { headers: { Accept: "application/json" } });
    listing = await answer.json();
    try { localStorage.setItem("sg.schede.v1", JSON.stringify(listing)); }
    catch { /* full */ }
  } catch {
    // Offline: quello che il telefono ha già visto. E se non ha visto niente,
    // lo dice — non finge una lista.
    try { listing = JSON.parse(localStorage.getItem("sg.schede.v1") || "null"); }
    catch { listing = null; }
  }
  if (!listing || !listing.schede || !listing.schede.length) {
    host.append(Object.assign(document.createElement("p"), {
      className: "help",
      textContent: listing
        ? "Questo nodo non serve schede."
        : "Nessuna scheda in cache: serve una connessione la prima volta.",
    }));
    return;
  }
  // UNA VOCE PER DEFINIZIONE, dalla lista. È il vincolo del §3: una
  // definizione nuova compare perché il NODO la dichiara, senza che questo
  // file la conosca. Non c'è un elenco di schede in questo codice.
  for (const item of listing.schede) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "navitem";
    button.dataset.scheda = item.id;
    button.textContent = item.id;
    const count = document.createElement("span");
    count.className = "count";
    count.textContent = `${item.recorded_in.trench}/${item.fields}`;
    count.title = "campi da trincea su campi totali";
    button.append(count);
    button.addEventListener("click", () => openScheda(item.id));
    host.append(button);
  }
}

/* ── aprire una scheda ───────────────────────────────────────────────────── */

async function openScheda(id, su = null) {
  const says = $("scheda-says");
  state.panel = "scheda";
  $("scheda").hidden = false;
  $("work").hidden = true;
  $("index").hidden = true;
  says.hidden = true;
  markNav(id);
  try {
    const { def, from } = await definitionFor(id, SG().locale || "it");
    // CAMBIARE SCHEDA SVUOTA CIÒ CHE C'ERA, e non è pulizia: i campi di uno
    // standard non sono i campi di un altro. Trovato nel giro offline —
    // aprendo prima la scheda ungherese e poi la US ICCD, il payload in coda
    // portava `ertelmezes`, che l'ICCD non ha. `scheda.slots_for` lo avrebbe
    // rifiutato al momento della consegna (ed è giusto che lo faccia), ma la
    // scheda sarebbe rimasta in coda a fallire per sempre.
    //
    // Ricaricare la STESSA scheda invece non perde niente: è la stessa scheda.
    if (!state.def || state.def.id !== def.id) {
      state.values = {};
      state.authored = {};
      state.validated = new Set();
      state.us = "";
      state.model = "";
    }
    state.def = def;
    state.keyField = keyField(def);
    state.step = 0;
    // APERTA SU UN'UNITÀ CHE ESISTE GIÀ: il numero non si riscrive a memoria, e
    // `create` è falso perché l'atto è correggere e non inventare. È la
    // differenza che `update_su` esiste per tenere: un `add_node` su un id che
    // non c'è lo CREA, quindi un numero sbagliato diventerebbe una US nuova.
    if (su && su.us) {
      state.us = String(su.us);
      state.create = false;
    }
    if (from === "cache") {
      says.hidden = false;
      says.textContent = "Definizione dalla cache: il nodo non risponde, " +
        "ma questa scheda l'avevi già aperta.";
    }
    draw();
  } catch (err) {
    // LA DEFINIZIONE CHE IL TELEFONO NON HA MAI VISTO E NON PUÒ SCARICARE **SI
    // DICE**. Improvvisare un modulo per una scheda di cui non si conoscono le
    // etichette sarebbe inventare uno standard.
    state.def = null;
    $("scheda-host").replaceChildren();
    paintThumbbar();
    says.hidden = false;
    says.textContent =
      `Non ho la definizione di «${id}» e non riesco a chiederla al nodo. ` +
      `Non posso disegnare una scheda che non conosco.`;
  }
}

function markNav(id) {
  for (const item of document.querySelectorAll(".sidenav .navitem")) {
    item.setAttribute("aria-current",
                      String(item.dataset.scheda === id));
  }
}

function draw() {
  render($("scheda-host"), state.def, state);
  paintThumbbar();
  const shown = state.mode === "phone" && !state.showAll
    ? trenchFields(state.def) : state.def.fields;
  $("nav-all-count").textContent =
    `${shown.length}/${state.def.fields.length}`;
  $("nav-all").setAttribute("aria-pressed", String(state.showAll));
}

function paintCompleteness() {
  if (state.def) refreshCompleteness($("scheda-host"), state.def, state);
}

/* ── validare ────────────────────────────────────────────────────────────── */

async function validateField(field) {
  if (!state.us) {
    SG().show(false, "Serve il numero dell'unità per validare un campo.", "");
    return;
  }
  const ok = await SG().send("/v1/validate",
                             { us: state.us, fields: [field] }, "validate");
  if (ok !== false) {
    state.validated.add(field);
    draw();
  }
}

function clearScheda() {
  state.values = {};
  state.authored = {};
  state.validated = new Set();
  state.step = 0;
  if (state.def) draw();
}

/* ── il telefono: un campo per volta, coi pollici ────────────────────────── */

function wireThumbbar() {
  $("tb-nav").addEventListener("click", () => {
    const nav = $("sidenav");
    nav.dataset.open = nav.dataset.open === "true" ? "false" : "true";
  });
  $("tb-prev").addEventListener("click",
    () => stepTo($("scheda-host"), state, "prev"));
  $("tb-next").addEventListener("click",
    () => stepTo($("scheda-host"), state, "next"));
  $("tb-save").addEventListener("click", () => save(state.def, state));
  //  IL RITORNO: un tocco, e la scelta che si scrive porta questa finestra —
  //  così tornare non è un'eccezione temporanea ma una scelta come le altre.
  $("tb-back").addEventListener("click", () => {
    const verso = $("tb-back").dataset.mode;
    if (!verso) return;
    rememberMode(verso, window.innerWidth);
    setMode(verso);
  });
}

/* ── avvio ───────────────────────────────────────────────────────────────── */

function wireShell() {
  applyTheme(savedTheme());
  $("theme").addEventListener("click", () => {
    const now = savedTheme();
    applyTheme(THEMES[(THEMES.indexOf(now) + 1) % THEMES.length]);
  });

  $("nav-voice").addEventListener("click", () => {
    state.panel = "voice";
    $("scheda").hidden = true;
    $("index").hidden = true;
    $("work").hidden = false;
    paintThumbbar();
    markNav("");
    $("nav-voice").setAttribute("aria-current", "true");
  });

  $("nav-all").addEventListener("click", () => {
    state.showAll = !state.showAll;
    state.step = 0;
    if (state.def) draw();
  });
  $("nav-onepage").addEventListener("click", () => {
    state.onePage = !state.onePage;
    $("nav-onepage").setAttribute("aria-pressed", String(state.onePage));
    if (state.def) draw();
  });

  wireThumbbar();

  // LE FOTO. Montate da qui e non dalla pagina perché è qui che si sa quale
  // scheda è aperta — e la scheda aperta è il CONTESTO da cui esce la
  // proposta. `proposeUs` è una funzione e non un valore: la proposta è quella
  // del momento in cui si tocca la miniatura, non quella dello scatto.
  repaintPhotos = mountPhotos({
    shoot: $("shoot"), camera: $("camera"), host: $("photos"),
    seam: SG(), proposeUs: () => state.us || "",
  });
  repaintPhotos();

  // DOVE SCRIVE IL NODO. Esposto su `window` perché a chiamarlo è `ping()`,
  // che vive nella pagina e non in un modulo: la conchiglia dichiara un seam
  // sola (`window.SG`) e questa è la sua controparte nell'altro verso.
  window.SGRoom = mountRoom($("nav-room"));
  // COSA C'È GIÀ. `schede` è una funzione e non un valore: la lista delle
  // definizioni arriva dal nodo dopo l'avvio, e passarla adesso vorrebbe dire
  // passarne una vuota per sempre.
  repaintIndex = mountIndex({
    t: (k, v) => SG().t(k, v),
    openScheda,
    schede: () => {
      try { return (JSON.parse(localStorage.getItem("sg.schede.v1") || "null")
                    || {}).schede || []; } catch { return []; }
    },
  });
  //  IL SEAM E BASTA: la conchiglia dichiara `window.SG` e questo modulo non
  //  conosce altro. `t` e `show` vengono da lì, come per ogni altra superficie.
  window.SGChat = mountChat({ t: (k, v) => SG().t(k, v),
                              toast: (m) => SG().show(true, m) });

  // Il modo si RIPROPONE quando la finestra cambia, ma non sovrascrive una
  // scelta: `effectiveMode` guarda prima cosa è stato scelto.
  window.addEventListener("resize", () => {
    const wanted = effectiveMode(window.innerWidth);
    if (wanted !== state.mode) setMode(wanted);
    else paintModes();
  });

  setMode(effectiveMode(window.innerWidth));
}

/* ── SI ARRIVA DA UNA STANZA ───────────────────────────────────────────────
 *
 * Il link del server porta un posto — `?server=…&room=…` — e mai un modo. Fino
 * al 6 ottobre quei due parametri non li leggeva nessuno: chi arrivava da
 * `/em/rooms/` atterrava sul microfono e riscriveva a mano il nome della stanza
 * che era già nell'indirizzo.
 *
 * I tre esiti li decide `arrivalPlan`, che è pura e provata. Qui c'è solo cosa
 * si mostra, e la riga che conta è la terza: **ripuntare il nodo lo decide una
 * persona**. Farlo da soli vorrebbe dire togliere un nodo condiviso a chi ce
 * l'ha, in silenzio, per aver aperto un link.
 */
async function land() {
  const arrivo = readArrival(window.location.search);
  if (arrivo.refused.length) {
    // RIFIUTATO A VOCE, come fa `FORBIDDEN` in `app/handoff.py`: accettarne uno
    // insegna a chi ha costruito il link che mandarlo funziona.
    SG().show(false, SG().t("index.refused", { what: arrivo.refused.join(", ") }));
  }
  let salute = null;
  try {
    salute = await (await fetch(SG().node + "/health",
                                { cache: "no-store" })).json();
  } catch { /* il nodo non risponde: si resta dove si è, ed è onesto */ }
  const piano = arrivalPlan(arrivo, roomOf(salute));
  if (piano.do === "nothing") return;

  if (piano.do === "offer") {
    // La colonna, aperta sul pannello che punta il nodo, con la stanza già
    // scritta: il gesto resta uno, e resta di chi lo fa.
    $("sidenav").dataset.open = "true";
    const campo = $("room-target");
    if (campo) campo.value = piano.server
      ? `${piano.server.replace(/\/+$/, "")}/open?room=${encodeURIComponent(piano.room)}`
      : piano.room;
    SG().show(true, SG().t("index.offer", { room: piano.room }));
    return;
  }

  state.panel = "index";
  $("work").hidden = true;
  $("scheda").hidden = true;
  $("index").hidden = false;
  paintThumbbar();
  await repaintIndex();
}

wireShell();
loadSchede();
void land();

// Esposto per la verifica dal browser: è quello che una cattura non può
// dimostrare (dove stanno i bersagli, quale modo è attivo, quanti campi).
window.SGShell = { state, openScheda, setMode, draw, trenchFields, otherFields,
                   payloadFor, paintThumbbar, thumbbarPlan, land,
                   repaintPhotos: () => repaintPhotos(),
                   repaintIndex: () => repaintIndex() };
