/* Il modulo da compilare — disegnato QUI, nel browser, e solo qui.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * PERCHÉ IN JAVASCRIPT E NON IN PYTHON
 *
 * `stratigraph-templates` ha un renderer Python che da una definizione produce
 * il foglio A4. Quel renderer resta, e resta dov'è: la stampa è un atto da
 * laboratorio, si fa a lotti, deve impaginare identica ovunque ed è un
 * deliverable ministeriale. Nessuna di quelle ragioni vale per il modulo, e una
 * ne vale contro: **il modulo deve funzionare offline, nel browser, su un
 * telefono in trincea.** Un renderer Python gira su un server.
 *
 * La terza via, quella scelta: la definizione arriva come DATO e il modulo lo
 * disegna solo il JS. Le altre due sono sbagliate — HTML generato dal server e
 * messo in cache è HTML morto (niente vocabolari, e un template nuovo non è
 * usabile finché non si è fatto un giro col server); e disegnarlo in JS
 * *mentre* il Python continua a disegnarlo è la malattia diagnosticata in
 * pyarchinit-mini, la stessa scheda scritta due volte con sette etichette
 * sbagliate da una parte sola.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * LEGGE, NON REINTERPRETA
 *
 * Etichette, `required`, `repeatable`, paragrafi, `options`, `help`,
 * `vocabulary` e `recorded_in` **si leggono dal dato**. Se qui comparisse una
 * regola che il formato già esprime — un elenco di campi obbligatori, una mappa
 * di etichette, una lista di «campi da campo» — sarebbe la seconda
 * implementazione dello standard in un altro posto.
 *
 * L'unica cosa che questo file DECIDE è come si presenta un tipo: quale
 * elemento di modulo usare per un `longtext` invece che per un `choice`. È
 * presentazione, non semantica, e la tabella è dichiarata sotto in un posto.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * NON PARLA AL GRAFO
 *
 * Parla al SERVIZIO (`/v1/scheda/{id}`), che parla ai tool, che parlano al
 * writer. `tests/test_one_write_path.py` tiene quella via unica, e questo file
 * non la aggira: usa `SG.send`, che è anche ciò che mette una richiesta fallita
 * nella coda offline. Un `fetch` diretto qui funzionerebbe online e perderebbe
 * una scheda in aereo.
 */

const SG = () => window.SG || {};

/* ── il modo, e come si decide ─────────────────────────────────────────────
 *
 * Tre soglie: telefono una colonna un campo per volta, tablet una facciata
 * intera, desktop due pagine affiancate.
 *
 * Il modo si PROPONE dalla larghezza e si può FORZARE, quindi non è una media
 * query: una media query non si lascia contraddire, e i due casi in cui la
 * larghezza sbaglia sono reali — un telefono grande in laboratorio, un
 * portatile su un tavolino in cantiere. La scelta si ricorda.
 */
const MODES = ["phone", "tablet", "desktop"];
const MODE_KEY = "sg.scheda.mode.v1";
const LAST_KEY = "sg.scheda.last.v1";

export function proposeMode(width) {
  if (width < 680) return "phone";
  if (width < 1200) return "tablet";
  return "desktop";
}

function chosenMode() {
  try {
    const saved = localStorage.getItem(MODE_KEY);
    return MODES.includes(saved) ? saved : null;
  } catch { return null; }
}

function rememberMode(mode) {
  try {
    if (mode) localStorage.setItem(MODE_KEY, mode);
    else localStorage.removeItem(MODE_KEY);
  } catch { /* private window: the choice lasts the session, and that is all */ }
}

export function effectiveMode(width) {
  return chosenMode() || proposeMode(width);
}

/* ── LA BARRA DEI POLLICI È IL PAVIMENTO DEL TELEFONO, NON QUELLO DELLA SCHEDA
 *
 * Trovata addosso il 2026-09-24: E.D. sceglie «Telefono» su una finestra da
 * 1574px per curiosità, e resta chiuso dentro. La catena, misurata nel suo
 * browser e non dedotta:
 *
 *   modo = telefono   → `placeControls` SPOSTA i tre chip nella colonna
 *                     → la colonna si apre solo da ☰, che sta nella barra
 *                     → la barra era nascosta perché nessuna scheda era aperta
 *                     → ☰ non esiste: getBoundingClientRect() su #tb-nav e su
 *                       #modes tornava {} — nessun rettangolo, nessun bersaglio
 *                     → e la scelta sta in localStorage, quindi ricaricare la
 *                       rimette com'era.
 *
 * Nessuno di quei passaggi è sbagliato da solo. Insieme sono una porta che si
 * chiude da fuori. La regola che li tiene onesti è una sola e sta qui:
 * **finché i controlli vivono nella colonna, la maniglia della colonna deve
 * esistere**. Quindi la barra c'è per tutto il tempo che il modo è telefono, e
 * ciò che compare e sparisce sono i bottoni della SCHEDA — che senza una scheda
 * a schermo non hanno su cosa agire.
 *
 * Pura ed esportata perché è l'unica riga di questa interfaccia che si può
 * sbagliare **in silenzio**: un bottone brutto si vede, una porta murata no.
 * `tests/test_la_porta_del_telefono.py` la esegue davvero, con node.
 */
export function thumbbarPlan(mode, panel, hasDef) {
  const phone = mode === "phone";
  return {
    bar: phone,                                      // la maniglia: sempre
    steps: phone && panel === "scheda" && Boolean(hasDef),
  };
}

/* ── quale elemento per quale tipo ──────────────────────────────────────────
 *
 * LA SOLA DECISIONE DI PRESENTAZIONE, in un posto. I tipi sono quelli che
 * `SPEC.md` §1.5 dichiara; un tipo che non è qui prende un campo di testo, che
 * è il ripiego onesto — una casella che si può compilare batte una casella che
 * non compare. Non una validazione: la forma di un `date` la fa il browser, e
 * `required` viene dal dato. */
const ELEMENT = {
  longtext: "textarea",
  choice: "select",
  checkbox: "checkbox",
  integer: "number",
  decimal: "number",
  date: "date",
};

const el = (tag, attrs = {}, ...kids) => {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else node.setAttribute(k, v === true ? "" : String(v));
  }
  for (const kid of kids) if (kid) node.append(kid);
  return node;
};

/* ── la definizione, presa dal nodo ────────────────────────────────────────
 *
 * E MESSA IN CACHE, perché il vincolo è che valga anche offline: il service
 * worker serve il guscio, e una definizione che il nodo dichiara deve essere
 * disponibile senza rete. Una definizione che il telefono non ha mai visto e
 * non può scaricare **si dice**, non si finge — vedi `openScheda`. */
const DEF_KEY = (id, lang) => `sg.scheda.def.${id}.${lang}`;

async function fetchDefinition(id, lang) {
  const url = `${SG().node}/v1/schede/${encodeURIComponent(id)}?lang=${encodeURIComponent(lang)}`;
  const answer = await fetch(url, { headers: { Accept: "application/json" } });
  if (!answer.ok) {
    const body = await answer.json().catch(() => ({}));
    throw new Error(body.detail || `HTTP ${answer.status}`);
  }
  return answer.json();
}

export async function definitionFor(id, lang, { cache = localStorage } = {}) {
  const key = DEF_KEY(id, lang);
  try {
    const fresh = await fetchDefinition(id, lang);
    try { cache.setItem(key, JSON.stringify(fresh)); } catch { /* full: fine */ }
    return { def: fresh, from: "node" };
  } catch (err) {
    let kept = null;
    try { kept = cache.getItem(key); } catch { /* nothing to read */ }
    if (kept) return { def: JSON.parse(kept), from: "cache" };
    // Neither. This is the honest case, and it gets SAID: a definition the
    // device has never seen and cannot download is not something to improvise
    // a form for.
    throw new Error(`no-definition:${err.message}`);
  }
}

/* ── quali campi, e in quale ordine ────────────────────────────────────────
 *
 * Sul telefono si mostrano i campi `recorded_in: "trench"`. **Un campo senza
 * marcatore NON è da trincea**: il default non promette nulla, ed è così per
 * progetto — leggere il silenzio come «allora è da campo» significherebbe
 * montare sul telefono una decisione che nessuno ha preso.
 *
 * Il resto non sparisce: `otherFields` è ciò che si raggiunge, e il modulo lo
 * dice. */
export const trenchFields = (def) =>
  (def.fields || []).filter((f) => f.recorded_in === "trench");

export const otherFields = (def) =>
  (def.fields || []).filter((f) => f.recorded_in !== "trench");

/* ── la completezza, come STATO ────────────────────────────────────────────
 *
 * Una scheda compilata in trincea è incompleta PER COSTRUZIONE, e non è un
 * errore: è il mestiere. Sulla US ICCD `definizione` è obbligatoria e non ha
 * marcatore, quindi da trincea non si compila — e il modulo lo dice come uno
 * stato.
 *
 * I tre casi vengono dalla tabella di `SPEC.md` §1.6, e sono tre frasi diverse
 * perché portano a tre conclusioni diverse:
 *
 *   required + trench   mancante → manca qualcosa: era compilabile qui
 *   required + lab      mancante → si compila dopo
 *   required + unknown  mancante → non si può decidere, e lo si dice
 */
export function completeness(def, values) {
  const filled = (f) => {
    const v = values[f.id];
    return v !== undefined && v !== null && String(v).trim() !== "";
  };
  const required = (def.fields || []).filter((f) => f.required && !filled(f));
  return {
    missingHere: required.filter((f) => f.recorded_in === "trench"),
    laterInLab: required.filter((f) => f.recorded_in === "lab"),
    undecided: required.filter((f) => f.recorded_in === "unknown"),
  };
}

export function completenessLine(def, values, mode) {
  const state = completeness(def, values);
  const bits = [];
  if (state.missingHere.length) {
    bits.push(`${state.missingHere.length} da compilare qui: ` +
      state.missingHere.map((f) => f.label).join(", "));
  }
  if (state.laterInLab.length) {
    bits.push(`${state.laterInLab.length} in laboratorio`);
  }
  if (state.undecided.length) {
    bits.push(`${state.undecided.length} obbligatori senza marcatore ` +
      `(${state.undecided.map((f) => f.label).join(", ")}): la definizione non ` +
      `dice se si compilano in trincea`);
  }
  const box = el("p", { class: "completeness" });
  if (!bits.length) {
    box.append(el("span", { text: "Tutti i campi obbligatori sono compilati." }));
    return box;
  }
  // Il caso della trincea NON è dipinto come un problema: dipingerlo così
  // insegnerebbe a chi scava che sta sbagliando quando sta lavorando.
  if (state.missingHere.length) box.classList.add("blocking");
  box.append(el("b", { text: mode === "phone" ? "Scheda da trincea" : "Stato" }));
  box.append(el("span", { text: bits.join(" · ") }));
  return box;
}

/* ── una casella ───────────────────────────────────────────────────────────── */

function boxFor(field, state) {
  const kind = ELEMENT[field.type] || "text";
  const id = `f-${field.id}`;
  const box = el("div", {
    class: "box" + (field.required ? " required" : ""),
    "data-field": field.id,
    "data-recorded-in": field.recorded_in,
  });
  box.append(el("label", { for: id, text: field.label }));

  let input;
  if (kind === "textarea") {
    input = el("textarea", { id, rows: 3 });
  } else if (kind === "select") {
    input = el("select", { id });
    input.append(el("option", { value: "", text: "—" }));
    for (const option of field.options || []) {
      input.append(el("option", { value: option.value, text: option.label }));
    }
  } else if (kind === "checkbox") {
    input = el("input", { id, type: "checkbox" });
  } else {
    input = el("input", { id, type: kind });
  }
  // `required` e `max_len` VENGONO DAL DATO. Scritti qui a mano sarebbero la
  // seconda copia di ciò che lo standard dichiara.
  if (field.required) input.setAttribute("aria-required", "true");
  if (field.max_len && kind !== "select" && kind !== "checkbox") {
    const digits = String(field.max_len).match(/\d+/g);
    if (digits) input.setAttribute("maxlength", digits[digits.length - 1]);
  }
  input.value = state.values[field.id] ?? "";
  input.addEventListener("input", () => {
    state.values[field.id] = kind === "checkbox" ? input.checked : input.value;
    // La casella dell'identità È il numero dell'unità: una sola casella, un
    // solo valore. Quale sia lo dice la definizione, non questo file.
    if (field.id === state.keyField) state.us = String(input.value || "").trim();
    // Un valore che una persona ha appena digitato è SUO: se il campo era di
    // un modello, l'autorialità torna alla persona nel momento in cui lo
    // riscrive, senza bisogno di validarlo.
    if (state.authored[field.id] === "ai") delete state.authored[field.id];
    state.onChange();
  });
  box.append(input);

  if (field.help) box.append(el("p", { class: "help", text: field.help }));
  if (field.vocabulary) {
    box.append(el("p", { class: "help" },
      el("span", { class: "voc", text: field.vocabulary })));
  }

  // ── L'AUTORIALITÀ SI VEDE, e validare è UN GESTO ────────────────────────
  const strip = el("div", { class: "authored" });
  const said = el("span", {});
  const button = el("button", { class: "validate", type: "button",
                                text: "Ho controllato" });
  button.addEventListener("click", () => state.onValidate(field.id));
  strip.append(said, button);
  box.append(strip);
  paintAuthorship(box, said, field, state);
  return box;
}

function paintAuthorship(box, said, field, state) {
  const author = state.authored[field.id];
  const validated = state.validated.has(field.id);
  box.classList.toggle("ai", author === "ai" && !validated);
  box.classList.toggle("validated", validated);
  if (validated) {
    said.textContent = "Validato da te — l'aveva proposto un modello";
  } else if (author === "ai") {
    said.textContent = state.model
      ? `Proposto da ${state.model}, non ancora validato`
      : "Proposto da un modello, non ancora validato";
  } else {
    said.textContent = "";
  }
}

/* ── la scheda intera ──────────────────────────────────────────────────────── */

function paragraphsOf(def, shown) {
  const wanted = new Set(shown.map((f) => f.id));
  const byId = new Map((def.fields || []).map((f) => [f.id, f]));
  return (def.paragraphs || [])
    .map((p) => ({
      ...p,
      shown: (p.fields || []).filter((id) => wanted.has(id))
        .map((id) => byId.get(id)).filter(Boolean),
    }))
    .filter((p) => p.shown.length);
}

/* Ridisegnare l'intera scheda a ogni tasto perderebbe il fuoco e la posizione
 * del cursore: mentre si scrive cambia SOLO la riga di stato, e si sostituisce
 * quella. */
export function refreshCompleteness(container, def, state) {
  const old = container.querySelector(".completeness");
  if (!old) return;
  old.replaceWith(completenessLine(def, state.values, state.mode));
}

/* QUALE CASELLA DICE DI CHE UNITÀ È LA SCHEDA.
 *
 * Dalla definizione (`identity.human_key`, SPEC §1.2), non da un nome scritto
 * qui: la US ICCD la chiama `us`, la ficha spagnola `contexto`, e una costante
 * in questo file sarebbe la conoscenza di uno standard dentro il renderer.
 *
 * La prima versione aveva una casella «US» SUA, sopra la scheda, e il numero
 * compariva DUE VOLTE — la mia e quella del modello. Due caselle per un dato
 * sono due valori il giorno che qualcuno ne cambia una.
 *
 * ── E QUI C'È UN'ASSUNZIONE, dichiarata perché il formato non la dice ──────
 *
 * La chiave umana è COMPOSTA, e misurata sulle due definizioni vere:
 *
 *     iccd-us-2021    fields: [localita, area, us]
 *                     pattern: "US {us} — {area} ({localita})"
 *     es-ue-demo-2026 fields: [yacimiento, contexto]
 *                     pattern: "Contexto {contexto} · {yacimiento}"
 *
 * Il numero DELL'UNITÀ è l'ULTIMO dell'elenco in entrambe, e gli altri sono il
 * contesto che lo disambigua — che è anche come `create_su` li tratta (`sito` e
 * `area` sono dati a parte, `us` è il numero). Ma **il formato non dichiara
 * quale dei campi della chiave sia il designatore dell'unità**: lo si deduce.
 *
 * Riportato nell'end-of come un buco del formato da chiudere (un
 * `identity.unit_field`), non colmato a intuito qui. Finché non c'è, questa
 * riga era l'assunzione, ed è scritta dove si vede. */
export const keyField = (def) => {
  const key = def.human_key || [];
  // DICHIARATO. `identity.human_key.unit_field` (SPEC §1.2) dice QUALE campo è
  // l'unità, e il servizio lo spedisce con la definizione.
  if (def.unit_field) return key.includes(def.unit_field) ? def.unit_field : null;
  // Una chiave di un campo solo non ha niente da dichiarare: è quello.
  if (key.length === 1) return key[0];
  // E QUI SI RIFIUTA, invece di indovinare.
  //
  // Fino al 2026-09-24 questa riga tornava `key[key.length - 1]` — l'ultimo
  // campo della chiave — e la sua giustificazione era che valeva per tutte e
  // tre le definizioni esistenti. Poi ne è arrivata una quarta forma: la scheda
  // ungherese ha `human_key = [retegszam, lelohely]` e dichiara `retegszam`,
  // cioè il PRIMO. Misurato contro il nodo vivo prima della riparazione: il
  // modulo sceglieva `lelohely`, il nome del sito. Una scheda archiviata sotto
  // «Aquincum» invece che sotto il numero dello strato, senza che niente lo
  // dicesse.
  //
  // Una regolarità osservata su tre casi non è una regola. Senza designatore
  // dichiarato non si sa, e `save` lo dice con quelle parole.
  return null;
};

export function render(container, def, state) {
  const mode = state.mode;
  const shown = mode === "phone" && !state.showAll
    ? trenchFields(def) : (def.fields || []);

  container.replaceChildren();
  const sheet = el("div", {
    class: "sheet" + (mode === "desktop" && !state.onePage ? " two" : ""),
  });

  const head = el("header", {},
    el("h2", { text: def.title }),
    el("span", { class: "std",
                 text: `${def.standard.authority} ${def.standard.code} ` +
                       `${def.standard.version}` +
                       (def.standard.invented ? " · demo" : "") }));
  // L'AZIONE DISTRUTTIVA STA QUI, IN CIMA — non nella barra dei pollici, cioè
  // non dove cade il pollice. Svuotare una scheda che qualcuno ha compilato
  // con i guanti addosso non deve essere a un centimetro dal bottone che salva.
  const clear = el("button", { class: "risky", type: "button",
                               text: "Svuota la scheda" });
  clear.addEventListener("click", () => state.onClear());
  head.append(clear);
  sheet.append(head);

  sheet.append(completenessLine(def, state.values, mode));

  const paras = el("div", { class: "paras" });
  for (const para of paragraphsOf(def, shown)) {
    const group = el("fieldset", { class: "para", "data-para": para.id },
      el("legend", { text: para.label }));
    for (const field of para.shown) group.append(boxFor(field, state));
    paras.append(group);
  }
  sheet.append(paras);

  // SALVARE, SULLE SOGLIE GRANDI. Sul telefono il bottone sta nella barra dei
  // pollici; su tablet e scrivania quella barra non c'è — e fino a stasera non
  // c'era nemmeno il bottone, quindi una scheda compilata al tavolo non si
  // poteva salvare. Misurato: `#scheda-host` conteneva un solo bottone che non
  // fosse «Ho controllato», ed era «Svuota la scheda».
  //
  // In FONDO, e non accanto a «Svuota»: quella è in cima apposta, per non stare
  // a un centimetro da questo. Le due azioni non si toccano su nessuna soglia.
  if (mode !== "phone") {
    const foot = el("footer", { class: "sheetfoot" });
    const saveIt = el("button", { class: "primary save", type: "button",
                                  text: "Salva la scheda" });
    saveIt.addEventListener("click", () => state.onSave());
    foot.append(saveIt);
    sheet.append(foot);
  }

  container.append(sheet);

  if (mode === "phone") focusStep(container, state);
  return { shown };
}

/* ── un campo per volta, sul telefono ─────────────────────────────────────── */

function stepBoxes(container) {
  return Array.from(container.querySelectorAll(".box"));
}

function focusStep(container, state) {
  const boxes = stepBoxes(container);
  if (!boxes.length) return;
  state.step = Math.max(0, Math.min(state.step, boxes.length - 1));
  boxes.forEach((box, i) => {
    const current = i === state.step;
    box.toggleAttribute("data-current", current);
    if (current) box.setAttribute("data-current", "true");
    const para = box.closest(".para");
    if (para && current) para.setAttribute("data-has-current", "true");
    else if (para && !para.querySelector('.box[data-current="true"]')) {
      para.removeAttribute("data-has-current");
    }
  });
  state.onStep(state.step, boxes.length);
}

export function stepTo(container, state, where) {
  const boxes = stepBoxes(container);
  state.step = where === "next" ? state.step + 1
    : where === "prev" ? state.step - 1 : where;
  state.step = Math.max(0, Math.min(state.step, boxes.length - 1));
  focusStep(container, state);
  const current = boxes[state.step];
  if (current) {
    const input = current.querySelector("input, textarea, select");
    if (input) input.focus({ preventScroll: false });
  }
}

/* ── salvare ───────────────────────────────────────────────────────────────── */

export function payloadFor(def, state) {
  const values = {};
  for (const [key, value] of Object.entries(state.values)) {
    if (value === undefined || value === null) continue;
    if (typeof value === "string" && value.trim() === "") continue;
    values[key] = value;
  }
  // Il campo-identità NON viaggia fra i valori: è `us`, e mandarlo anche come
  // `data.us` scriverebbe due volte la stessa cosa in due posti del nodo.
  if (state.keyField) delete values[state.keyField];
  const authored = {};
  for (const [key, who] of Object.entries(state.authored)) {
    if (values[key] !== undefined) authored[key] = who;
  }
  return {
    us: String(state.us || "").trim(),
    values,
    authored_by: authored,
    model: state.model || "",
    create: Boolean(state.create),
  };
}

export async function save(def, state) {
  // IL DESIGNATORE PRIMA DEL NUMERO. Senza di lui non si sa quale casella è
  // l'unità, e «manca il numero» direbbe la cosa sbagliata: il numero magari
  // c'è, è la definizione che non dice dove.
  if (!state.keyField) {
    SG().show(false,
      `«${def.id}» ha una chiave di ${(def.human_key || []).length} campi e non ` +
      `dichiara quale sia l'unità (identity.human_key.unit_field): non so ` +
      `sotto quale identità salvare, e non lo indovino.`, def.id);
    return false;
  }
  const body = payloadFor(def, state);
  if (!body.us) {
    SG().show(false, "Una scheda è di un'unità: manca il numero.", def.id);
    return false;
  }
  // `SG.send` E NON `fetch`: è ciò che mette una richiesta fallita nella coda
  // offline. Un fetch diretto funzionerebbe online e perderebbe una scheda in
  // aereo, che è il caso per cui questo servizio esiste.
  return SG().send(`/v1/scheda/${encodeURIComponent(def.id)}`, body, def.id);
}
