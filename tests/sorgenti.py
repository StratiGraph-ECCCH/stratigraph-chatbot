"""Come si guarda un sorgente, quando una guardia deve guardarlo.

════════════════════════════════════════════════════════════════════════════════
## PERCHÉ QUESTO FILE ESISTE, E PERCHÉ NASCE OGGI

Il prompt del 5 ottobre lo cita come se ci fosse: *«Le prove nuove usano
`tests/sorgenti.py` — il modulo delle tre forze»*. **In questo repo non
c'era.** Ci sono i gemelli:

    stratigraph-server/tests/sorgenti.py            4 ottobre   (Python)
    EMStudio/frontend/scripts/sorgenti.mjs          5 ottobre   (JS/TS)

e il referto del 5 ottobre lo diceva già: *«il chatbot è adesso il terzo repo
con la stessa classe di difetti e senza lettore condiviso»*. Questo è quel
lettore, e nasce **spostando** cose che c'erano — `dentro` viveva dentro
`test_field_signature.py` — invece di aggiungerne di nuove.

La classe di difetti, in una riga: **una guardia che cerca una parola dentro un
sorgente letto come testo misura il file, non il programma.**

════════════════════════════════════════════════════════════════════════════════
## LE TRE FORZE, NELLO STESSO ORDINE DEI GEMELLI

**1 · IL PROGRAMMA.** Qui non c'è un parser JavaScript — niente
`node_modules`, niente `typescript`, niente `acorn`: `node` è installato e
basta. Ma la forma più forte di «chiedere al programma» questo repo ce l'ha già
dal 24 settembre, ed è **eseguirlo**: `esegui` importa il modulo vero con node
e ne legge la risposta. Una funzione pura interrogata così non si può
fraintendere, perché non la si sta leggendo.

Il limite è dichiarato e non aggirato: **vale solo per i moduli puri.**
`shell.js` tocca `document` al primo livello e non si può importare, quindi su
`shell.js` resta la terza forza, con la sua ragione scritta accanto.

**2 · IL DOCUMENTO.** `dentro` legge il sottoalbero di un elemento **contando i
tag**, non tagliando alla prima chiusura. Nato il 5 ottobre da una guardia
scattata a torto: annidata una `<section id="chat">` dentro
`<section id="work">`, il recinto ha detto che la casella del dettato «è
raggiungibile senza firma». Non lo era: era dopo la sezione annidata.

**3 · IL CONFINE DI PAROLA**, dove niente di meglio è possibile, **con la
ragione per cui ci si è fermati lì**. Un minimo dichiarato vale più di un
massimo taciuto.

E `senza_prosa` c'è, ma **retrocessa a igiene**: non risponde niente su un
programma. Nei gemelli otto morsi su nove erano su codice vero, non su commenti.

════════════════════════════════════════════════════════════════════════════════
## IL CORPUS DEI FALSI POSITIVI, CONDIVISO FRA I TRE

Il patto dichiarato il 5 ottobre: stesso nome di file, stesse tre forze nello
stesso ordine, **stesso corpus di falsi positivi** — e uno nuovo va in tutte e
tre le liste. Quelli misurati finora, costruiti e fatti girare:

    parola        `\\bd3\\.` dentro `1.d3.0`, un numero di versione
    parola        `\\bUS\\b` dentro `"en-US"`, una locale
    sottostringa  `subscribe` dentro `unsubscribe`
    sottostringa  `A8` dentro `#00A8FF`, un colore
    sottostringa  `#shelf-bar` dentro `#shelf-barcode`
    sottostringa  `display` dentro `--display-muted`, il NOME di una proprietà
    sottostringa  `/chat` in `/rooms/{id}/chat`, che è una rotta di casa
    prosa         un commento «deliberately NOT https://em.example.org»
    taglio        una `<section>` annidata, tagliata alla prima `</section>`
    finestra      320 caratteri di CSS a partire da un selettore

## LA TABELLA DI CORRISPONDENZA

    qui                        stratigraph-server       EMStudio (.mjs)
    ───────────────────────────────────────────────────────────────────────
    parola(termine)            parola(termine)          parola(termine)
    dentro(markup, apertura)   —                        elementi(html, sel)
    esegui(js)                 chiama_python / ast      nomina / chiama (ts)
    senza_prosa(source)        senza_prosa(source)      senzaProsa(src)

`dentro` non ha un gemello in Python perché quel repo non ha markup da leggere;
`esegui` non ne ha uno altrove perché è la risposta a un vincolo di questo repo
— nessun parser JS installato, e un modulo puro che si può eseguire.
"""

from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parent.parent

#: `node` c'è o non c'è, e quando non c'è i test che lo usano si SALTANO. È un
#: buco dichiarato: su una macchina senza node la prima forza non c'è.
HA_NODE = shutil.which("node") is not None


# ── 1 · il programma, eseguito ──────────────────────────────────────────────


def esegui(js: str, *, cwd: pathlib.Path | None = None) -> Any:
    """Esegui `js` come modulo ES e leggi il JSON che stampa.

    **Il modulo si importa davvero**: se `scheda.js` toccasse `document` al
    primo livello questo esploderebbe — ed è giusto, perché allora non sarebbe
    più puro e non si potrebbe più interrogare così.

    Nato come `_run` in `test_la_porta_del_telefono.py` il 24 settembre. Sta qui
    perché la stessa domanda la fanno adesso due file.
    """
    done = subprocess.run(["node", "--input-type=module", "-e", js],
                          cwd=str(cwd or ROOT), capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


# ── 2 · il documento ────────────────────────────────────────────────────────


def dentro(markup: str, apertura: str) -> str:
    """Il sottoalbero di un elemento, CONTANDO i tag di quel nome.

    E non `markup[markup.index(apertura):][:markup.index("</section>")]`, che è
    come si faceva: quella fetta finisce al PRIMO tag di chiusura, quindi una
    sezione annidata la tronca — e tutto ciò che viene dopo l'annidata diventa
    invisibile alla guardia, che continua a sembrare verde finché il controllo
    che cerca non è proprio uno di quelli persi.

    Misurato il 5 ottobre: aggiunto un `<section id="chat">` dentro
    `<section id="work">`, la guardia della firma ha detto che la casella del
    dettato «è raggiungibile senza firma».
    """
    nome = apertura.lstrip("<").split()[0].split(">")[0]
    inizio = markup.index(apertura)
    livello, i = 0, inizio
    apre, chiude = f"<{nome}", f"</{nome}>"
    while i < len(markup):
        if markup.startswith(apre, i):
            livello += 1
            i += len(apre)
        elif markup.startswith(chiude, i):
            livello -= 1
            i += len(chiude)
            if livello == 0:
                return markup[inizio:i]
        else:
            i += 1
    raise AssertionError(f"{apertura!r} non è mai chiuso")


# ── 3 · il confine di parola, e l'igiene ────────────────────────────────────


def parola(termine: str) -> "re.Pattern[str]":
    """Un confine di parola per `termine`. **La più debole delle tre forze**, e
    sta qui perché una guardia che la usa lo dica.

    Quello che NON risolve, misurato: `\\b` sta anche fra un segno di
    punteggiatura e una lettera, quindi `\\bd3\\b` combacia dentro il numero di
    versione `1.d3.0` e `\\bUS\\b` dentro la locale `"en-US"`.
    """
    return re.compile(rf"\b{re.escape(termine)}\b")


def senza_prosa(source: str) -> str:
    """I commenti, tolti. **Igiene, non una forza**: non risponde niente su un
    programma, e nei gemelli otto morsi su nove erano su codice vero.

    È qui perché tre file di questo repo ne avevano scritta una copia per uno.
    """
    return re.sub(r"/\*[\s\S]*?\*/|//[^\n]*", "", source)
