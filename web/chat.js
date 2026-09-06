/* Cosa ci siamo detti — la conversazione della stanza, sul telefono.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * ## PERCHE' STA DIETRO UN TOCCO E NON DAVANTI
 *
 * La regola del telefono di questo progetto: *UI minimale, niente che inviti a
 * uscire dall'app*. Dettare e fotografare si fanno tutto il giorno e stanno
 * davanti; **leggere cosa si sono detti gli altri** e' una cosa che si fa
 * quando ci si ferma, e sta dietro un tocco. Non e' una gerarchia di
 * importanza: e' quante volte si tocca.
 *
 * ## E COSA QUESTA SUPERFICIE NON PROMETTE
 *
 * **Senza rete si puo' dire e non si puo' rileggere.** Una frase detta senza
 * campo finisce nella coda e parte al ritorno, come una fotografia; la
 * conversazione invece vive nella stanza e la stanza e' l'unica che la sa. Una
 * copia locale sarebbe un secondo database, e questo nodo ne ha gia' uno.
 * Quindi il pannello lo dice, invece di sembrare vuoto.
 *
 * Zero colori letterali: tutto viene dalle variabili del tema.
 */

const $ = (id) => document.getElementById(id);
const SG = () => window.SG || {};

/** Gli ultimi N, perche' una conversazione si legge dal fondo. */
const SHOWN = 20;

/** Una riga: chi, cosa, quando. Un messaggio ritrattato resta e lo dice —
 *  sparire in silenzio lascerebbe un buco che sembra un errore di lettura. */
function line(m, t) {
  const riga = document.createElement("div");
  riga.className = "chat-line" + (m.retracted ? " chat-gone" : "");
  const chi = document.createElement("span");
  chi.className = "chat-who";
  chi.textContent = m.by || t("chat.nobody");
  const cosa = document.createElement("span");
  cosa.className = "chat-said";
  cosa.textContent = m.retracted ? t("chat.retracted") : m.said;
  const quando = document.createElement("span");
  quando.className = "chat-at";
  quando.textContent = (m.at || "").slice(11, 16);
  riga.append(chi, cosa, quando);
  return riga;
}

export function mount({ t, toast }) {
  const zona = $("chat");
  const elenco = $("chat-lines");
  const nota = $("chat-note");
  const casella = $("chat-say");
  const bottone = $("chat-send");
  const apri = $("chat-open");
  if (!zona || !elenco || !casella || !bottone || !apri) return () => {};

  async function reread() {
    const seam = SG();
    if (!seam.signed) { nota.textContent = t("chat.signin"); return; }
    let letto;
    try {
      const risposta = await fetch(seam.node + "/v1/room/chat", {
        headers: { Authorization: "Bearer " + seam.token },
      });
      if (!risposta.ok) {
        //  409 = questo nodo scrive nel contenitore locale; 502 = niente rete
        nota.textContent = risposta.status === 409
          ? t("chat.nolocal") : t("chat.unreachable");
        elenco.replaceChildren();
        return;
      }
      letto = await risposta.json();
    } catch {
      nota.textContent = t("chat.unreachable");
      return;
    }
    const detti = letto.messages || [];
    elenco.replaceChildren(...detti.slice(-SHOWN).map((m) => line(m, t)));
    if (!detti.length) { nota.textContent = t("chat.none"); return; }
    const parti = [t("chat.boundary")];
    const oltre = detti.length - Math.min(detti.length, SHOWN);
    if (oltre > 0) parti.unshift(t("chat.more", { n: oltre }));
    nota.textContent = parti.join(" ");
  }

  async function say() {
    const testo = casella.value.trim();
    if (!testo) return;
    const seam = SG();
    casella.value = "";
    try {
      const risposta = await fetch(seam.node + "/v1/room/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json",
                   Authorization: "Bearer " + seam.token },
        body: JSON.stringify({ said: testo }),
      });
      if (!risposta.ok) throw new Error(String(risposta.status));
    } catch {
      //  DETTO LO STESSO: lo scrivano mette in coda quando la stanza non
      //  risponde, e la coda si vede gia' nella riga delle code rimaste
      //  indietro. Qui si dice solo che non si rilegge adesso.
      toast(t("chat.queued"));
      return;
    }
    await reread();
  }

  apri.addEventListener("click", () => {
    zona.hidden = !zona.hidden;
    apri.setAttribute("aria-expanded", String(!zona.hidden));
    if (!zona.hidden) void reread();
  });
  bottone.addEventListener("click", () => void say());
  casella.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); void say(); }
  });
  return reread;
}
