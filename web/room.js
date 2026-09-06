/* Dove scrive questo nodo, e chi lo tiene — visto dalla pagina.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * ## PERCHE' UNA SUPERFICIE E NON SOLO UNA ROTTA
 *
 * Un nodo che ha cambiato stanza e non lo mostra e' la stessa famiglia di
 * difetti di tutta questa settimana: il lavoro finisce in un posto e chi lo
 * detta crede sia finito in un altro. Le due domande che decidono se quello che
 * scrivi conta sono **dove sei** e **chi sei**, e l'intestazione le tiene
 * accanto da sempre — `#room-name` esisteva nel DOM dal 23 settembre e nessuno
 * lo riempiva.
 *
 * ## E PERCHE' NELLA COLONNA E NON SOTTO IL BOTTONE GRANDE
 *
 * La regola del telefono di questo progetto: *UI minimale, niente che inviti a
 * uscire dall'app*. Puntare il nodo a una stanza e' un gesto raro — si fa una
 * volta la mattina — mentre dettare e fotografare si fanno tutto il giorno.
 * Quello che si fa una volta sta dietro; quello che si fa sempre sta davanti.
 *
 * Il NOME della stanza pero' sta davanti, nell'intestazione, perche' quello si
 * legge in continuazione: e' la riga che dice se stai scrivendo dove credi.
 */

const $ = (id) => document.getElementById(id);
const SG = () => window.SG || {};

/* Chi tiene il nodo si legge da `/v1/room`, che vuole una firma. `/health` dice
 * solo il FATTO («tenuto», «libero») perche' e' pubblica: «chi sta lavorando in
 * questa tenda adesso» non si da' a chi non si e' presentato. */
export async function look() {
  const seam = SG();
  if (!seam.signed) return null;
  try {
    const risposta = await fetch(seam.node + "/v1/room", {
      headers: { Authorization: "Bearer " + seam.token },
    });
    return risposta.ok ? await risposta.json() : null;
  } catch { return null; }
}

/** La riga dell'intestazione: dove finisce quello che dici.
 *
 *  Costruita dalla salute (pubblica, sempre disponibile) e non da `/v1/room`:
 *  deve dire qualcosa anche prima che qualcuno firmi, perche' e' proprio prima
 *  di firmare che serve sapere dove si andra' a scrivere. */
export function headline(health, t) {
  const dove = String((health && health.writes_to) || "");
  const stanza = dove.startsWith("room ") ? dove.slice(5).split(" at ")[0] : "";
  if (!stanza) return t("room.local");
  const tenuto = String((health && health.held) || "").startsWith("tenuto");
  return t(tenuto ? "room.in.held" : "room.in", { room: stanza });
}

/** Le code rimaste indietro, dette in una frase. Una coda per stanza introduce
 *  un modo nuovo di perdere del lavoro — ripuntare via e dimenticarsene — e
 *  l'unica difesa e' che si veda. */
export function leftBehind(health, t) {
  const code = (health && health.queues) || [];
  if (!code.length) return "";
  const quante = code.reduce((a, q) => a + q.pending, 0);
  return t(quante === 1 ? "room.left.one" : "room.left.many",
           { n: quante, rooms: code.length });
}

export function paintHeader(health) {
  const seam = SG();
  const riga = $("room-name");
  if (riga) riga.textContent = headline(health, seam.t);
}

/* ── la colonna ───────────────────────────────────────────────────────────── */

export function paintPanel(host, health, stato) {
  const t = SG().t;
  host.textContent = "";

  const dove = document.createElement("p");
  dove.className = "hint";
  dove.textContent = headline(health, t);
  host.append(dove);

  if (stato && stato.holding && stato.holding.who) {
    const chi = document.createElement("p");
    chi.className = "hint";
    chi.textContent = t("room.heldby", { who: stato.holding.who });
    host.append(chi);
  }
  const rimaste = leftBehind(health, t);
  if (rimaste) {
    const coda = document.createElement("p");
    coda.className = "hint";
    coda.textContent = rimaste;
    host.append(coda);
  }

  const campo = document.createElement("input");
  campo.type = "text";
  campo.id = "room-target";
  campo.placeholder = t("room.placeholder");
  campo.enterKeyHint = "go";
  const vai = document.createElement("button");
  vai.type = "button";
  vai.id = "room-go";
  vai.textContent = t("room.point");
  const torna = document.createElement("button");
  torna.type = "button";
  torna.id = "room-back";
  torna.textContent = t("room.back");

  const esito = document.createElement("p");
  esito.className = "hint";
  esito.id = "room-says";

  const punta = async () => {
    const detto = campo.value.trim();
    if (!detto) return;
    esito.textContent = t("room.pointing");
    // UN LINK O UNA STANZA: il campo accetta tutti e due, perche' un tablet in
    // tenda non sempre ha da dove incollare un link e una stanza si sa a
    // memoria. Quale dei due sia lo decide la stringa, non un selettore.
    const corpo = detto.includes("://")
      ? { link: detto }
      : { room: detto, server: (health && health.server_hint) || "" };
    esito.textContent = await point(corpo, t);
    await refresh(host, health);
  };
  vai.addEventListener("click", punta);
  campo.addEventListener("keydown", (e) => { if (e.key === "Enter") punta(); });
  torna.addEventListener("click", async () => {
    esito.textContent = await unpoint(t);
    await refresh(host, health);
  });

  const riga = document.createElement("div");
  riga.className = "row";
  riga.append(vai, torna);
  host.append(campo, riga, esito);
}

async function point(corpo, t) {
  const seam = SG();
  try {
    const risposta = await fetch(seam.node + "/v1/room", {
      method: "POST",
      headers: { "Content-Type": "application/json",
                 Authorization: "Bearer " + seam.token },
      body: JSON.stringify(corpo),
    });
    const detto = await risposta.json();
    // LA FRASE DEL NODO, non una nostra. Un rifiuto qui dice chi tiene il nodo
    // e da quanto, oppure cosa manca perche' possa presentarsi alla stanza:
    // riscriverla in «non riuscito» butterebbe via l'unica cosa utile.
    return risposta.ok ? (detto.message || t("room.pointed"))
                       : (detto.detail || t("room.refused"));
  } catch { return t("room.unreachable"); }
}

async function unpoint(t) {
  const seam = SG();
  try {
    const risposta = await fetch(seam.node + "/v1/room", {
      method: "DELETE",
      headers: { Authorization: "Bearer " + seam.token },
    });
    const detto = await risposta.json();
    return risposta.ok ? (detto.message || t("room.went.back"))
                       : (detto.detail || t("room.refused"));
  } catch { return t("room.unreachable"); }
}

async function refresh(host, _health) {
  const seam = SG();
  try {
    const salute = await (await fetch(seam.node + "/health",
                                      { cache: "no-store" })).json();
    paintHeader(salute);
    paintPanel(host, salute, await look());
  } catch { /* il nodo non c'e': la riga di prima resta, ed e' onesta */ }
}

/** Aggancia il pannello. Ridisegnato a ogni `ping`, come il resto. */
export function mount(host) {
  return async (health) => {
    paintHeader(health);
    if (host && !host.hidden) paintPanel(host, health, await look());
  };
}
