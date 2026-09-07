/* Si arriva da una stanza — cosa dice il link, e cosa si trova.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * ## IL COLLEGAMENTO C'ERA GIÀ. MANCAVA COSA SUCCEDE DOPO IL CLIC
 *
 * Il server sa mandare qui dal 5 settembre: `handoff.browser_url` costruisce
 * `<web>/?server=…&room=…` e `EM_FIELD_ASSISTANT_URL` è valorizzata nel
 * dev-stack. Misurato il 6 ottobre: **i due parametri non li leggeva nessuno.**
 * L'unico che la pagina guardava era `?token=`, la porta di servizio del banco.
 *
 * Quindi chi arriva da `/em/rooms/` — che una stanza l'ha già scelta —
 * atterrava sulla superficie di dettatura, e per continuare il lavoro doveva
 * aprire la colonna, trovare «Dove scrivo», e **riscrivere a mano il nome della
 * stanza che era già nell'indirizzo**.
 *
 * ## IL LINK PORTA UN POSTO, MAI UN MODO
 *
 * Come ha sempre fatto (`app/handoff.py` del server, primo paragrafo). Chi
 * decide come si vede è la finestra che arriva, non chi ha scritto il link: un
 * `&mode=` sarebbe una permessa travestita da comodità. Qui non si aggiunge, e
 * se arriva si **rifiuta a voce** — come fa `FORBIDDEN` in `app/handoff.py`,
 * perché accettarne uno insegna a chi ha costruito il link che mandarlo
 * funziona, e da quel momento il contratto non ha più la proprietà.
 *
 * E si legge una LISTA CHIUSA: `server`, `room`, e `token` — che è la porta di
 * servizio dichiarata del banco e non una cosa che nasce stanotte. Tutto il
 * resto non si legge, senza inventare una politica per parametri che nessuno
 * ha ancora scritto.
 */

/** Le grafie del modo, rifiutate a voce. Non un elenco di tutto ciò che è
 *  vietato: il resto semplicemente non si legge (vedi `LETTI`). */
export const FORBIDDEN = ["mode", "sgmode", "sg-mode", "sg_mode"];

/** Quello che un link di arrivo può dire. `token` è la porta di servizio del
 *  banco, dichiarata in `index.html` e più vecchia di questo file. */
export const LETTI = ["server", "room", "token"];

/** Cosa dice l'indirizzo con cui questa pagina è stata aperta.
 *
 *  Pura, perché è la riga che decide dove atterra chi arriva — e perché così la
 *  si esegue davvero invece di leggerla (`tests/sorgenti.py`, la prima forza).
 *
 *  `{server, room, refused, ignored}`. `refused` non è vuoto quando il link ha
 *  provato a dire come si vede: chi chiama lo dice a voce e non lo onora.
 */
export function readArrival(search) {
  const query = new URLSearchParams(String(search || ""));
  const refused = [];
  const ignored = [];
  for (const key of new Set([...query.keys()])) {
    const low = key.toLowerCase();
    if (FORBIDDEN.includes(low)) refused.push(key);
    else if (!LETTI.includes(low)) ignored.push(key);
  }
  return {
    server: (query.get("server") || "").trim().replace(/\/+$/, ""),
    room: (query.get("room") || "").trim(),
    refused: refused.sort(),
    ignored: ignored.sort(),
  };
}

/** Cosa deve succedere all'avvio, dato il link e dove il nodo scrive adesso.
 *
 *  Tre esiti, e la differenza fra i primi due è **chi ha in mano il nodo**:
 *
 *  `nothing`  il link non nomina una stanza — un nodo headless su una stanza
 *             nota non ha una finestra e non deve cambiare comportamento
 *  `index`    il nodo scrive GIÀ lì: si atterra su cosa c'è, senza gesti
 *  `offer`    il nodo scrive altrove: si offre di ripuntarlo, e lo decide una
 *             persona. Ripuntare da solo vorrebbe dire togliere un nodo
 *             condiviso a chi ce l'ha, in silenzio, per aver aperto un link
 */
export function arrivalPlan(link, writingTo) {
  if (!link || !link.room) return { do: "nothing", room: "" };
  const same = String(writingTo || "").trim() === link.room;
  return { do: same ? "index" : "offer", room: link.room, server: link.server };
}
