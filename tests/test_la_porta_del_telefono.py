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
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
WEB = ROOT / "web"

needs_node = pytest.mark.skipif(
    shutil.which("node") is None,
    reason="node non è installato: la porta del telefono resta non sorvegliata")

MODES = ("phone", "tablet", "desktop")
PANELS = ("voice", "scheda")
DEFS = (None, {"id": "iccd-us-2021"})


def _run(js: str):
    """Esegue `js` come modulo ES nella cartella del front-end e ne legge il
    JSON stampato. Il modulo si importa davvero: se `scheda.js` toccasse
    `document` al primo livello, questo esploderebbe — ed è giusto, perché
    allora non sarebbe più puro."""
    done = subprocess.run(["node", "--input-type=module", "-e", js],
                          cwd=ROOT, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


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
    assert len(plans) == 12
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
    assert murate == ["phone/scheda/niente", "phone/voice/niente"], murate


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
