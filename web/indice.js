/* Cosa c'è già in questa stanza — l'elenco delle schede, e come riaprirne una.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * ## PERCHÉ QUESTA SUPERFICIE È IL PRIMO POSTO E NON UNO IN PIÙ
 *
 * Chi arriva dal link di una stanza ha già scelto la stanza: **sta continuando
 * un lavoro, non ne comincia uno**. Fino al 6 ottobre atterrava sul microfono,
 * cioè sull'inizio di qualcosa di nuovo, e per riprendere in mano una US doveva
 * aprire una scheda vuota e riscriverne il numero a memoria.
 *
 * ## DUE TOCCHI, E IL SECONDO NON SI PUÒ SALTARE
 *
 * Un'unità **non registra con quale scheda è stata compilata** (misurato:
 * `create_su` scrive `node_type`, `name`, `description` e i campi, e nient'altro
 * che dica lo standard). Quindi il secondo tocco — quale standard — non è un
 * gesto in più che si potrebbe togliere con un default: è una domanda a cui
 * solo chi riapre sa rispondere. Una US registrata con l'ICCD non è una US
 * registrata col foglio ungherese, e indovinarlo sarebbe la stessa famiglia di
 * errori del numero mistypato che `update_su` esiste per rifiutare.
 *
 * Zero colori letterali: tutto dalle variabili del tema.
 */

const $ = (id) => document.getElementById(id);
const SG = () => window.SG || {};

/** Quante schede si mostrano prima di dire che ce n'è altre. Uno scavo vero ne
 *  ha migliaia, e una lista lunga su un telefono è una lista che non si legge. */
export const SHOWN = 40;

/** La riga di un'unità: cosa è, quanto è piena, e chi l'ha toccata per ultimo. */
function line(u, t, onPick) {
  const riga = document.createElement("button");
  riga.type = "button";
  riga.className = "navitem indice-riga";
  riga.dataset.unit = u.id;

  const nome = document.createElement("span");
  nome.className = "indice-nome";
  nome.textContent = u.name || u.id;

  const quanti = document.createElement("span");
  quanti.className = "count";
  //  I CAMPI SCRITTI DA QUALCUNO, non le chiavi di `data`: i timbri e gli
  //  orologi sono del sistema, e contarli direbbe che una scheda vuota è piena.
  //  Uno e molti sono due frasi, come `room.left.one` / `room.left.many`: «1
  //  campi» è una traduzione che nessuna lingua accetta.
  quanti.textContent = t(u.fields === 1 ? "index.fields.one"
                                        : "index.fields.many", { n: u.fields });
  quanti.title = (u.field_names || []).join(", ");

  riga.append(nome, quanti);
  if (u.description) {
    const cosa = document.createElement("span");
    cosa.className = "indice-cosa";
    cosa.textContent = u.description;
    riga.append(cosa);
  }
  if (!u.number) {
    //  UN'UNITÀ DI CUI NON SI SA IL NUMERO NON SI RIAPRE. Succede con i grafi
    //  importati, e proporre una scheda su un numero indovinato sarebbe la
    //  stessa famiglia di errori del numero mistypato.
    riga.disabled = true;
    riga.title = t("index.noNumber");
    const perche = document.createElement("span");
    perche.className = "indice-cosa";
    perche.textContent = t("index.noNumber");
    riga.append(perche);
    return riga;
  }
  riga.addEventListener("click", () => onPick(u));
  return riga;
}

export function mount({ t, openScheda, schede }) {
  const host = $("index-list");
  const nota = $("index-note");
  const titolo = $("index-title");
  if (!host) return async () => {};

  /** Il secondo tocco: con quale standard si riapre questa unità. */
  function askStandard(u) {
    const lista = schede();
    host.replaceChildren();
    const detto = document.createElement("p");
    detto.className = "hint";
    detto.textContent = t("index.which", { unit: u.name || u.id });
    host.append(detto);
    for (const item of lista) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "navitem";
      b.textContent = item.id;
      //  IL NUMERO, non il nome: gli attrezzi vogliono «12» e non «US 12», e
      //  `update_su` rifiuta un numero che non corrisponde a nessun nodo — che
      //  è la sua virtù. Il numero lo dice il nodo (`number_from_unit_id`),
      //  non questa pagina.
      b.addEventListener("click", () => openScheda(item.id, { us: u.number }));
      host.append(b);
    }
    if (!lista.length) {
      const vuoto = document.createElement("p");
      vuoto.className = "hint";
      vuoto.textContent = t("index.noSchede");
      host.append(vuoto);
    }
    const indietro = document.createElement("button");
    indietro.type = "button";
    indietro.className = "navitem";
    indietro.textContent = t("index.back");
    indietro.addEventListener("click", () => void repaint());
    host.append(indietro);
  }

  async function repaint() {
    const seam = SG();
    host.replaceChildren();
    if (!seam.signed) { nota.textContent = t("index.signin"); return; }
    let letto;
    try {
      const risposta = await fetch(seam.node + "/v1/room/units", {
        headers: { Authorization: "Bearer " + seam.token },
      });
      if (!risposta.ok) { nota.textContent = t("index.unreachable"); return; }
      letto = await risposta.json();
    } catch { nota.textContent = t("index.unreachable"); return; }

    if (titolo) {
      titolo.textContent = letto.room
        ? t("index.head.room", { room: letto.room }) : t("index.head.local");
    }
    const unita = letto.units || [];
    host.replaceChildren(...unita.slice(0, SHOWN)
      .map((u) => line(u, t, askStandard)));
    if (!unita.length) { nota.textContent = t("index.empty"); return; }
    const oltre = unita.length - Math.min(unita.length, SHOWN);
    nota.textContent = oltre > 0
      ? t("index.more", { n: oltre, total: unita.length })
      : t(unita.length === 1 ? "index.total.one" : "index.total.many",
          { n: unita.length });
  }

  return repaint;
}
