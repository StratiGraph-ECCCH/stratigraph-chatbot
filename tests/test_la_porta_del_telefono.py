"""La porta del telefono, e la colonna del dettato.

Due difetti trovati **addosso** il 2026-09-24, non da un test: E.D. ha scelto
«Telefono» su una finestra da 1574px per curiosità e non è più riuscito a
tornare indietro, e ha detto che il pulsantone del dettato, sulla scrivania, è
brutto. Erano vere tutte e due, e questo file è ciò che le tiene chiuse.

── PERCHÉ QUI GIRA node ─────────────────────────────────────────────────────

Il resto di `test_la_scheda_surface.py` legge il SORGENTE del front-end, perché
è Python e il modulo è JavaScript. Una porta murata però non si vede leggendo:
si vede eseguendo. `thumbbarPlan` è pura apposta, e questo file la **esegue**
con node — e nel caso che l'ha fatta scattare, non in uno inventato.

Se node non c'è, i test che lo usano si SALTANO, e questo è un buco dichiarato:
su una macchina senza node la porta non è sorvegliata. Sulla macchina dove è
stato scritto, node è v26.0.0 e i test girano.
"""

from __future__ import annotations

import json
import pathlib
import shutil

import pytest

import sorgenti

ROOT = pathlib.Path(__file__).resolve().parent.parent
WEB = ROOT / "web"

needs_node = pytest.mark.skipif(
    shutil.which("node") is None,
    reason="node non è installato: la porta del telefono resta non sorvegliata")

MODES = ("phone", "tablet", "desktop")
#: «index» è arrivato il 6 ottobre, ed è qui perché un pannello nuovo è
#: un'occasione nuova di murare la porta: è esattamente ciò che questa tabella
#: esiste per impedire.
PANELS = ("voice", "scheda", "index")
DEFS = (None, {"id": "iccd-us-2021"})


#: La prima forza, presa dal modulo condiviso invece che riscritta: `esegui`
#: nasce da queste righe (24 settembre) e adesso la stessa domanda la fanno due
#: file. Vedi `tests/sorgenti.py`.
_run = sorgenti.esegui


def _plans(rule: str = "") -> dict:
    """La tabella completa (modo × pannello × scheda) secondo `thumbbarPlan`,
    oppure secondo una REGOLA ALTERNATIVA passata come sorgente — che è come si
    prova che il cancello misura la rottura e non una sostituzione."""
    body = rule or "m.thumbbarPlan(mode, panel, def_)"
    js = f"""
const m = await import("./web/scheda.js");
const out = {{}};
for (const mode of {json.dumps(list(MODES))})
  for (const panel of {json.dumps(list(PANELS))})
    for (const def_ of {json.dumps(list(DEFS))})
      out[[mode, panel, def_ ? "scheda" : "niente"].join("/")] = {body};
console.log(JSON.stringify(out));
"""
    return _run(js)


# ── la porta ────────────────────────────────────────────────────────────────

@needs_node
def test_il_caso_che_ha_chiuso_dentro_ED():
    """Modo telefono, pannello «detta», nessuna scheda aperta: **la barra c'è**.

    È esattamente la posizione in cui E.D. si è trovato. Nel suo browser, prima
    di stasera, `getBoundingClientRect()` tornava {} sia su `#tb-nav` sia su
    `#modes` — cioè: nessun rettangolo, nessun bersaglio, e i tre chip per
    tornare a «Scrivania» chiusi in una colonna la cui unica maniglia era
    dentro la barra nascosta."""
    plan = _run("""
const m = await import("./web/scheda.js");
console.log(JSON.stringify(m.thumbbarPlan("phone", "voice", null)));
""")
    assert plan["bar"] is True, "la maniglia della colonna non esiste"
    # e i passi no: senza scheda a schermo non hanno su cosa agire.
    assert plan["steps"] is False


@needs_node
def test_finche_i_controlli_stanno_nella_colonna_la_maniglia_esiste():
    """La proprietà, su tutte e dodici le combinazioni e non su un esempio.

    `placeControls` sposta modo, tema e lingua nella colonna quando il modo è
    telefono. Da quel momento la colonna è l'unico posto dove quei tre
    controlli esistono, e la barra è l'unico posto da cui la colonna si apre."""
    plans = _plans()
    assert len(plans) == 18
    murate = [k for k, v in plans.items()
              if k.startswith("phone/") and not v["bar"]]
    assert murate == [], f"porta murata in: {murate}"


@needs_node
def test_la_vecchia_regola_murava_la_porta():
    """IL CANCELLO MISURA LA ROTTURA, non che una sostituzione sia avvenuta.

    Qui si rimette in piedi la regola che c'era davvero — `mode !== "phone" ||
    !state.def`, cioè barra visibile solo con una scheda aperta — e si chiede
    alla stessa proprietà di dire dove cade. Se un giorno qualcuno la
    riscrivesse così, il test sopra diventerebbe rosso: questo lo dimostra
    adesso, invece di sperarlo."""
    vecchia = _plans('({ bar: mode === "phone" && Boolean(def_), steps: false })')
    murate = sorted(k for k, v in vecchia.items()
                    if k.startswith("phone/") and not v["bar"])
    assert murate == ["phone/index/niente", "phone/scheda/niente",
                      "phone/voice/niente"], murate
    #: E la riga che vale la pena leggere due volte: sotto la regola vecchia il
    #: pannello nuovo — quello dove atterra chi arriva da una stanza — sarebbe
    #: stato murato **proprio per chi arriva**, perché arriva senza una scheda
    #: caricata. Con una scheda già aperta no. Un difetto che si vede solo la
    #: prima volta è il peggiore da trovare addosso.
    assert vecchia["phone/index/scheda"]["bar"] is True


@needs_node
def test_i_passi_compaiono_solo_dove_hanno_su_cosa_agire():
    """Indietro, avanti e Salva parlano alla scheda: servono il modo telefono,
    il pannello della scheda, e una definizione caricata. Tutti e tre."""
    plans = _plans()
    con_passi = sorted(k for k, v in plans.items() if v["steps"])
    assert con_passi == ["phone/scheda/scheda"], con_passi


def test_un_solo_posto_decide_se_la_barra_ce():
    """La porta è nata da quattro righe sparse: `setMode`, `draw`, `openScheda`
    e il bottone «Detta» nascondevano la barra ognuno per conto proprio, e
    nessuna delle quattro sapeva che dentro c'era l'unica maniglia.

    Non è una questione di stile: quattro decisioni sullo stesso elemento sono
    quattro occasioni di murare la porta, e tre di esse erano scritte da chi
    stava pensando a un'altra cosa."""
    shell = (WEB / "shell.js").read_text(encoding="utf-8")
    assert shell.count('$("thumbbar").hidden') == 1, (
        "qualcuno decide della barra fuori da `paintThumbbar`")
    assert "function paintThumbbar()" in shell
    # e la decisione non è ricopiata a mano: viene dalla funzione pura.
    assert "thumbbarPlan(state.mode, state.panel, state.def)" in shell


def test_il_pannello_a_schermo_non_si_deduce_dalla_definizione():
    """`state.def` dice che una scheda è CARICATA, non che è a schermo: il
    bottone «Detta» lascia la definizione dov'è e mette davanti la voce. Erano
    la stessa variabile, ed è per questo che «Detta» spegneva la barra."""
    shell = (WEB / "shell.js").read_text(encoding="utf-8")
    assert 'panel: "voice"' in shell
    assert 'state.panel = "scheda"' in shell
    assert 'state.panel = "voice"' in shell


# ── la colonna del dettato ──────────────────────────────────────────────────

def test_la_colonna_del_dettato_ha_un_tetto_sulle_soglie_grandi():
    """MISURATO nel browser di E.D. prima della riparazione, finestra 1574px,
    modo scrivania: `#rec` 1302×67, `#typed` 1302×59, `#send` 1302×63.

    La causa non era il bottone: era `main.work { max-width: none }`, che la
    scheda si è presa per stare su due facciate e che il dettato si è preso
    insieme a lei."""
    css = (WEB / "scheda.css").read_text(encoding="utf-8")
    assert "--sf-thread" in css
    for mode in ("tablet", "desktop"):
        for target in ("#work", "#gate", ".queue"):
            assert f'#surface[data-sg-mode="{mode}"] {target}' in css, (mode, target)
    assert "max-width: var(--sf-thread);" in css


def test_il_tetto_non_e_stato_messo_rimpicciolendo_il_bersaglio():
    """L'EFFETTO CHE UNA RIPARAZIONE SBAGLIATA AVREBBE AVUTO.

    Il modo ovvio di far sembrare meno enorme quel bottone è rimpicciolirlo. Su
    `--sg-accent` il bianco misura 3.53, che passa **solo** come «large text»
    (>= 14pt bold): a 19px/600 il bottone ci sta, sotto no. Restringere la
    colonna non tocca il contrasto; rimpicciolire la scritta lo rompe, e lo
    romperebbe in silenzio."""
    shell = (WEB / "shell.css").read_text(encoding="utf-8")
    assert "font-size: 19px; font-weight: 600;" in shell
    # e i bersagli restano quelli di una mano col guanto, anche sul tablet
    assert "padding: 18px; text-align: left; cursor: pointer;" in shell
    scheda = (WEB / "scheda.css").read_text(encoding="utf-8")
    assert "font-size" not in scheda.split("--sf-thread: 34rem;")[1].split(
        "/* ── il modo: TABLET")[0], (
        "il tetto della colonna sta toccando la dimensione del testo")


# ── una scelta non è una condanna (2026-10-05) ──────────────────────────────
#
# La riparazione del 24 settembre ha rimesso la MANIGLIA. L'altra metà è tornata
# a mordere il 5 ottobre: E.D. è arrivato dal server su una finestra da
# scrivania e ha trovato il telefono. Misurato nel DOM vero, non dedotto:
#
#     finestra 1574×900 · proposeMode → desktop · localStorage → "phone"
#     effectiveMode = phone
#     #modes  y=1372   fuori dallo schermo di 472px, dentro la colonna chiusa
#     #sidenav translateY(+636px), data-open=false
#     l'unico bersaglio visibile che riguardi il modo: ☰ «Apri la navigazione»
#
# La regola vecchia era `chosenMode() || proposeMode(width)`. La nuova è
# `decideMode`, pura e con la sua ragione, e si prova sulla TABELLA COMPLETA —
# ogni categoria di finestra per ogni forma di scelta salvata — come `_plans`.

#: Una larghezza per categoria, prese ai due lati delle soglie che non sono in
#: discussione (680 e 1200).
WIDTHS = {"phone": 375, "tablet": 900, "desktop": 1574}


def _decisions(rule: str = "") -> dict:
    """`decideMode` su ogni (larghezza × scelta salvata), o una REGOLA
    ALTERNATIVA, che è come si prova che il cancello misura la rottura."""
    body = rule or "m.decideMode(width, saved)"
    saved = ([None]
             + [f"{k}" for k in WIDTHS]                       # la forma vecchia
             + [{"mode": k, "at": w} for k in WIDTHS for w in WIDTHS.values()])
    js = f"""
const m = await import("./web/scheda.js");
const out = {{}};
for (const [name, width] of Object.entries({json.dumps(WIDTHS)}))
  for (const saved of {json.dumps(saved)}) {{
    const key = name + "/" + (saved === null ? "niente"
      : (typeof saved === "string" ? "nudo:" + saved
         : saved.mode + "@" + saved.at));
    out[key] = {body};
  }}
console.log(JSON.stringify(out));
"""
    return _run(js)


@needs_node
def test_LA_SERA_DI_ED_una_finestra_da_scrivania_e_una_scelta_nuda():
    """Il caso vero, per primo: la scelta scritta prima del 5 ottobre.

    Una stringa nuda dice il modo e **non la finestra**. Onorarla vorrebbe dire
    inventare l'occasione, quindi si torna a proporre — ed è esattamente ciò che
    ripara la sera di E.D., dove la scelta e l'arrivo erano tutti e due su una
    finestra da scrivania.
    """
    detto = _run("""
const m = await import("./web/scheda.js");
console.log(JSON.stringify(m.decideMode(1574, "phone")));
""")
    assert detto == {"mode": "desktop", "why": "unlabelled"}


@needs_node
def test_E_LA_VECCHIA_REGOLA_su_quello_stesso_caso_dava_il_telefono():
    """IL CANCELLO MISURA LA ROTTURA. `chosenMode() || proposeMode(width)`
    rimessa in piedi, e le si chiede lo stesso caso."""
    vecchia = _run("""
const m = await import("./web/scheda.js");
const chosen = (saved) => (typeof saved === "string" ? saved : (saved||{}).mode);
console.log(JSON.stringify(chosen("phone") || m.proposeMode(1574)));
""")
    assert vecchia == "phone"


@needs_node
def test_UNA_SCELTA_VALE_SULLA_FINESTRA_SU_CUI_E_STATA_FATTA():
    """La tabella intera, e le due proprietà che la reggono.

    · stessa categoria → la scelta vince, sempre, qualunque essa sia;
    · categoria diversa → si ri-propone, sempre.
    """
    tavola = _decisions()
    assert len(tavola) == 3 * (1 + 3 + 9)

    for nome, larghezza in WIDTHS.items():
        for scelto, a in ((s, w) for s in WIDTHS for w in WIDTHS.values()):
            atteso_stessa = _run(f"""
const m = await import("./web/scheda.js");
console.log(JSON.stringify(m.proposeMode({a}) === m.proposeMode({larghezza})));
""") if False else (a == larghezza or
                    (a < 680) == (larghezza < 680) and
                    (a < 1200) == (larghezza < 1200))
            voce = tavola[f"{nome}/{scelto}@{a}"]
            if atteso_stessa:
                assert voce == {"mode": scelto, "why": "chosen"}, (nome, scelto, a)
            else:
                assert voce["why"] == "another-window", (nome, scelto, a)
                assert voce["mode"] == nome, (nome, scelto, a)


@needs_node
def test_I_DUE_CASI_VERI_NON_SI_PERDONO():
    """`scheda.js` difende due casi in cui la larghezza sbaglia: il telefono
    grande in laboratorio e il portatile sul tavolino. Restano, ed è il punto
    della strada scelta: scade la pretesa di parlare di finestre mai viste, non
    la preferenza."""
    tavola = _decisions()
    #  telefono grande: 900px, si sceglie «telefono», e resta a 900
    assert tavola["tablet/phone@900"] == {"mode": "phone", "why": "chosen"}
    #  portatile sul tavolino: 1574px, si sceglie «tablet», e resta a 1574
    assert tavola["desktop/tablet@1574"] == {"mode": "tablet", "why": "chosen"}
    #  e anche il caso di E.D., se lo rifà ADESSO: la scelta porta la finestra
    assert tavola["desktop/phone@1574"] == {"mode": "phone", "why": "chosen"}


@needs_node
def test_SENZA_NIENTE_DI_SCRITTO_decide_la_larghezza():
    tavola = _decisions()
    for nome in WIDTHS:
        assert tavola[f"{nome}/niente"] == {"mode": nome, "why": "proposed"}


@needs_node
def test_QUELLO_CHE_NON_SI_CAPISCE_non_si_onora():
    """Una scelta illeggibile non è una scelta: si propone, non si indovina."""
    detto = _run("""
const m = await import("./web/scheda.js");
console.log(JSON.stringify([
  m.readChoice(null), m.readChoice("frigorifero"), m.readChoice({mode:"x",at:1}),
  m.readChoice({mode:"phone"}), m.readChoice({mode:"phone",at:"molto"}),
  m.readChoice({mode:"phone",at:-3}), m.readChoice({mode:"phone",at:900}),
]));
""")
    assert detto == [None, None, None,
                     {"mode": "phone", "at": None},
                     {"mode": "phone", "at": None},
                     {"mode": "phone", "at": None},
                     {"mode": "phone", "at": 900}]


# ── la via d'uscita, visibile senza sapere già dov'è ────────────────────────

@needs_node
def test_IL_RITORNO_COMPARE_quando_il_modo_non_e_quello_proposto():
    """`wayBack` su tutte e nove le combinazioni modo × finestra.

    Il criterio di §1.3 non è che i chip esistano: il 5 ottobre esistevano, con
    un rettangolo, 472px sotto il bordo della finestra."""
    tavola = _run(f"""
const m = await import("./web/scheda.js");
const out = {{}};
for (const [nome, width] of Object.entries({json.dumps(WIDTHS)}))
  for (const mode of {json.dumps(list(MODES))})
    out[nome + "/" + mode] = m.wayBack(mode, width);
console.log(JSON.stringify(out));
""")
    for nome in WIDTHS:
        for mode in MODES:
            atteso = None if mode == nome else nome
            assert tavola[f"{nome}/{mode}"] == atteso, (nome, mode)


def test_IL_RITORNO_STA_IN_BARRA_e_non_nella_colonna():
    """Dove sta è tutta la riparazione: sul telefono i chip del modo STANNO
    nella colonna, e una via d'uscita che si apre solo da ☰ è la via d'uscita
    che il 5 ottobre non è stata trovata.

    Il DOCUMENTO, contando i tag (`sorgenti.dentro`): la barra contiene già dei
    `<span>`, e domani può contenere un `<div>` — una fetta fino alla prima
    `</div>` direbbe che il ritorno non c'è. È il falso positivo che il 5
    ottobre è scattato davvero, su `<section id="work">`.
    """
    pagina = (WEB / "index.html").read_text(encoding="utf-8")
    barra = sorgenti.dentro(pagina, '<div class="thumbbar"')
    assert 'id="tb-back"' in barra, "il ritorno non è nella barra dei pollici"
    assert 'id="tb-nav"' in barra, "…e ☰ è ancora lì accanto"


def test_UN_SOLO_POSTO_SCRIVE_LA_SCELTA():
    """Prima erano due: `shell.js` faceva `localStorage.setItem` da sé al clic.

    È la stessa forma dei quattro punti che avevano murato la porta — due
    scritture della stessa cosa sono due occasioni di scriverne una a metà, e
    quella di `shell.js` scriveva la stringa nuda che stanotte non si può più
    onorare.

    ── PERCHÉ QUI IL PAGLIAIO È UN SORGENTE, e perché il falso positivo non è
    costruibile (§3 del prompt del 5 ottobre)

    `shell.js` **non si può importare**: tocca `document` al primo livello, e
    quindi la prima forza — eseguirlo — non è disponibile. Resta il testo, con
    la prosa tolta (`sorgenti.senza_prosa`), e l'ago è la chiave letterale.

    Una riga onesta che la faccia scattare dovrebbe essere una stringa che
    contiene `sg.scheda.mode.v1` **fuori da un commento**, in un file che non
    deve nominare quella chiave: cioè esattamente la rottura che questa guardia
    cerca. Il commento — la riga onesta plausibile, e quella che ha morso in
    `check-members` il 5 ottobre — è già tolto.
    """
    shell = sorgenti.senza_prosa((WEB / "shell.js").read_text(encoding="utf-8"))
    assert "sg.scheda.mode.v1" not in shell, (
        "shell.js nomina di nuovo la chiave: la possiede scheda.js")
    assert "rememberMode(value, window.innerWidth)" in shell
    #  …e chi disegna il ritorno lo chiede alla funzione pura invece di
    #  ricopiarne la regola: la stessa disciplina di `paintThumbbar`
    assert "wayBack(state.mode, window.innerWidth)" in shell
    scheda = sorgenti.senza_prosa((WEB / "scheda.js").read_text(encoding="utf-8"))
    assert scheda.count("localStorage.setItem(MODE_KEY") == 1


def test_E_UN_COMMENTO_CHE_NOMINA_LA_CHIAVE_non_fa_scattare_niente():
    """La prova che la guardia qui sopra misura il programma e non il file.

    Il commento è la riga onesta che ha morso davvero, il 5 ottobre, in un altro
    repo: il messaggio di un recinto scritto come commento faceva fallire il
    recinto stesso. Qui non può, e questa è la prova invece della promessa.
    """
    finto = ('// la chiave sg.scheda.mode.v1 la possiede scheda.js\n'
             'const x = 1;\n')
    assert "sg.scheda.mode.v1" not in sorgenti.senza_prosa(finto)
    #  …e una scrittura VERA la fa scattare ancora
    vero = 'localStorage.setItem("sg.scheda.mode.v1", value);\n'
    assert "sg.scheda.mode.v1" in sorgenti.senza_prosa(vero)
