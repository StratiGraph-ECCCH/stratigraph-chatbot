"""Scartare una foto dalla coda — il verbo che c'era e nessuno chiamava.

════════════════════════════════════════════════════════════════════════════════
## LA MISURA, PRIMA DEL GESTO

`web/photos.js:144` `forget(id)` esisteva. Chi la chiamava, misurato:

    deliver()  →  await forget(voce.id)     dopo una consegna RIUSCITA

Un chiamante solo, e automatico. **Nessuna persona.** Quindi la coda era un
posto dove si metteva e non si toglieva, e il 6 settembre è costato una serata:
l'unico modo di togliere una foto era cancellare i dati del sito — cioè buttare
via anche tutto il resto.

*(Il prompt dell'8 ottobre dice «esiste, e nessuno la chiama». Chiamata è, una
volta: dalla consegna. Quello che non esisteva è la strada di una persona.)*

## PERCHÉ LA CONFERMA È QUELLA CHE CHIEDE DI SCRIVERE

I byte di una foto in coda stanno **solo su questo telefono**: non sono mai
arrivati al nodo, non hanno un digest nello store, non li ha nessun altro.
Scartarla è irreversibile in senso pieno, e quindi è `confirmTyped` — la
decisione del 7 ottobre, e non ce n'è una terza.

Il costo è dichiarato e non risolto: con i guanti, scrivere `IMG_4821.HEIC` è
un gesto difficile. È il prezzo dell'irreversibilità.

## IL PAGLIAIO (§5)

`photos.js` **si importa** in node: non tocca `document` al primo livello, e
`sorgenti.esegui` lo esegue davvero. Le prove sul comportamento sono eseguite;
le due che hanno per pagliaio un sorgente dicono perché il falso positivo non è
costruibile.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import sorgenti                                        # noqa: E402

WEB = pathlib.Path(__file__).resolve().parent.parent / "web"
needs_node = pytest.mark.skipif(
    not sorgenti.HA_NODE,
    reason="niente node: il verbo resta non sorvegliato")


# ═══ 1 · LA CONFERMA, ESEGUITA ═══════════════════════════════════════════════

@needs_node
def test_LA_CONFERMA_CHIEDE_DI_SCRIVERE_IL_NOME():
    """`confirm.js` è vendorizzato dal server (`sync-brand.sh`) e si esegue.

    Quattro casi, e il quarto è quello che conta: **nessuna strada in cui una
    risposta vuota vale un sì**.
    """
    detto = sorgenti.esegui("""
const m = await import("./web/confirm.js");
const t = (k, v) => `${k} ${JSON.stringify(v)}`;
const out = [];
for (const [etichetta, risposta] of [["giusto", "  IMG_1.HEIC  "],
                                     ["maiuscole", "img_1.heic"],
                                     ["annullato", null],
                                     ["vuoto", ""]]) {
  globalThis.window = { prompt: () => risposta, confirm: () => true };
  out.push([etichetta, m.makeConfirm(t).confirmTyped("Scarto?", "IMG_1.HEIC")]);
}
console.log(JSON.stringify(out));
""")
    assert detto == [["giusto", True], ["maiuscole", False],
                     ["annullato", False], ["vuoto", False]]


@needs_node
def test_ED_E_LA_STESSA_del_server_non_una_seconda():
    """Vendorizzata, non riscritta: una sorgente, una copia dichiarata.

    Il pagliaio sono DUE FILE, e il confronto è byte a byte — non c'è nessuna
    parola da cercare, quindi nessun falso positivo da costruire.
    """
    qui = (WEB / "confirm.js").read_text(encoding="utf-8")
    la = (pathlib.Path(__file__).resolve().parent.parent.parent
          / "stratigraph-server" / "app" / "node_admin" / "confirm.js")
    if not la.is_file():
        pytest.skip("stratigraph-server non è accanto a questo repo")
    assert qui == la.read_text(encoding="utf-8"), (
        "la copia ha divergiuto dalla sorgente: ./sync-brand.sh")


# ═══ 2 · IL VERBO, E CHI LO CHIAMA ═══════════════════════════════════════════

@needs_node
def test_forget_TOGLIE_UNA_FOTO_SOLA():
    """Eseguito su un IndexedDB finto: `forget` cancella per id, e le altre
    restano. È la differenza fra scartare una foto e cancellare i dati del
    sito, che era l'unica strada prima."""
    detto = sorgenti.esegui("""
//: UN IndexedDB MINIMO, quanto basta a `tx` di `photos.js`: `open` risolve su
//: `onsuccess`, e `tx` risolve su `t.oncomplete` leggendo `out.result`. Le due
//: richiamate si programmano DOPO che chi chiama le ha assegnate — che è come
//: si comporta quello vero, e la ragione per cui una versione sincrona di
//: questo finto lasciava l'await appeso per sempre.
const dati = new Map([["a", {id:"a"}], ["b", {id:"b"}], ["c", {id:"c"}]]);
const richiesta = (valore) => ({ result: valore });
const store = {
  getAll: () => richiesta([...dati.values()]),
  get: (id) => richiesta(dati.get(id)),
  put: (v) => { dati.set(v.id, v); return richiesta(undefined); },
  delete: (id) => { dati.delete(id); return richiesta(undefined); },
};
const db = {
  objectStoreNames: { contains: () => true },
  createObjectStore: () => store,
  transaction: () => {
    const t = { objectStore: () => store, oncomplete: null, onerror: null,
                error: null };
    setTimeout(() => t.oncomplete && t.oncomplete(), 0);
    return t;
  },
};
globalThis.indexedDB = {
  open: () => {
    const req = { result: db, onsuccess: null, onerror: null,
                  onupgradeneeded: null, error: null };
    setTimeout(() => req.onsuccess && req.onsuccess(), 0);
    return req;
  },
};
const m = await import("./web/photos.js");
const prima = (await m.list()).map((v) => v.id).sort();
await m.forget("b");
const dopo = (await m.list()).map((v) => v.id).sort();
//: e due volte non è un errore
await m.forget("b");
console.log(JSON.stringify({ prima, dopo, di_nuovo: (await m.list()).length }));
""")
    assert detto["prima"] == ["a", "b", "c"]
    assert detto["dopo"] == ["a", "c"]
    assert detto["di_nuovo"] == 2


def test_E_LA_STRISCIA_HA_TRE_BOTTONI_per_foto():
    """Toccare (collega), «altro numero», e «scarta».

    ── PERCHÉ IL PAGLIAIO È UN SORGENTE, e perché il falso positivo non è
    costruibile

    La striscia si disegna nel DOM e questa prova non ne ha uno; quello che si
    afferma è che i tre bottoni sono COSTRUITI, cioè tre `className` assegnate
    nel programma. La prosa si toglie prima (`sorgenti.senza_prosa`), e la riga
    onesta plausibile — un commento che nomina una classe, come quello che ha
    morso in `check-members` il 5 ottobre — non c'è più. Quello che resta è una
    stringa fra apici assegnata a `className`: per farla comparire senza
    costruire il bottone servirebbe scriverla in un'altra stringa del
    programma, che è la rottura che questa guardia cerca.
    """
    js = sorgenti.senza_prosa((WEB / "photos.js").read_text(encoding="utf-8"))
    for classe in ("shot-face", "shot-other", "shot-drop"):
        assert f'className = "{classe}"' in js, classe
    #: …e chi scarta passa dalla conferma che chiede di scrivere
    assert "confirmTyped(seam.t(\"photos.drop.ask\"), voce.name)" in js
    #: …e NON da quella che mostra e basta
    assert "confirmNamed" not in js


def test_E_UN_COMMENTO_CHE_NOMINA_UNA_CLASSE_non_basta():
    """La prova che la guardia qui sopra misura il programma e non il file."""
    finto = '// la classe shot-drop sta qui\nconst x = 1;\n'
    spogliato = sorgenti.senza_prosa(finto)
    assert 'className = "shot-drop"' not in spogliato
    vero = 'b.className = "shot-drop";\n'
    assert 'className = "shot-drop"' in sorgenti.senza_prosa(vero)


# ═══ 3 · LE PAROLE, IN DUE LINGUE ════════════════════════════════════════════

def test_LA_SUPERFICIE_DICE_CHE_NON_SI_TORNA_INDIETRO():
    pagina = (WEB / "index.html").read_text(encoding="utf-8")
    for chiave in ("photos.drop", "photos.drop.title", "photos.drop.ask",
                   "console.confirm.typed"):
        assert pagina.count(f'"{chiave}"') == 2, chiave
    #: …e la frase dice DOVE stanno i byte, che è la ragione per cui non tornano
    assert "solo su questo dispositivo" in pagina
    assert "only on this device" in pagina
