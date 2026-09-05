"""Il designatore nel modulo, e il bottone che salva sulle soglie grandi.

Due difetti trovati stanotte mentre se ne riparavano altri due, e vale la pena
dire COME, perché è la stessa lezione due volte:

* **il designatore** — `stratigraph-templates` ha smesso ieri di dedurre quale
  campo sia l'unità, e ha aggiunto `identity.human_key.unit_field`. Il servizio
  però non lo spediva al browser, e il modulo continuava a prendere **l'ultimo
  campo della chiave**. Misurato contro il nodo vivo, sulla scheda ungherese —
  che dichiara il PRIMO: il modulo sceglieva `lelohely`, il nome del sito. Una
  scheda archiviata sotto «Aquincum» invece che sotto il numero dello strato.

  Togliere un'assunzione da una libreria non la toglie dai suoi consumatori.

* **il salvataggio** — «Salva» viveva solo nella barra dei pollici, che è
  soltanto del telefono. Su tablet e scrivania una scheda si poteva compilare
  per intero e non si poteva salvare: in `#scheda-host` l'unico bottone che non
  fosse «Ho controllato» era «Svuota la scheda».

Come sopra, quello che si può ESEGUIRE si esegue con node; il resto è misurato
nel browser e sta nel referto.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
TEMPLATES = ROOT.parent / "stratigraph-templates" / "templates"

needs_node = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node non è installato")
have_templates = pytest.mark.skipif(not TEMPLATES.is_dir(),
                                    reason="stratigraph-templates non è accanto")


def _key_field(definition: dict, rule: str = "") -> str | None:
    """`keyField` eseguito davvero, oppure una REGOLA ALTERNATIVA — che è come
    si mostra cosa faceva quella di prima."""
    body = rule or "m.keyField(def_)"
    done = subprocess.run(
        ["node", "--input-type=module", "-e", f"""
const m = await import("./web/scheda.js");
const def_ = {json.dumps(definition)};
console.log(JSON.stringify({{ v: {body} }}));
"""], cwd=ROOT, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)["v"]


# la forma della scheda ungherese, che è il caso che fa scattare tutto
HU = {"human_key": ["retegszam", "lelohely"], "unit_field": "retegszam"}
LAST_ONE = 'def_.human_key[def_.human_key.length - 1]'


# ── il designatore ──────────────────────────────────────────────────────────

@needs_node
def test_il_designatore_dichiarato_vince_sulla_posizione():
    assert _key_field(HU) == "retegszam"


@needs_node
def test_la_vecchia_deduzione_sbagliava_proprio_questa():
    """IL CANCELLO MISURA LA ROTTURA. Con la regola di prima — l'ultimo campo
    della chiave — la stessa scheda sceglieva il nome del sito."""
    assert _key_field(HU, LAST_ONE) == "lelohely"


@needs_node
def test_senza_designatore_una_chiave_composta_si_rifiuta():
    """Non si indovina. Una regolarità osservata su tre casi non è una regola,
    e la quarta forma è arrivata dopo undici giorni."""
    assert _key_field({"human_key": ["sito", "numero"]}) is None
    # …e con la vecchia regola avrebbe risposto con sicurezza la cosa sbagliata
    assert _key_field({"human_key": ["sito", "numero"]}, LAST_ONE) == "numero"


@needs_node
def test_una_chiave_di_un_campo_solo_non_ha_niente_da_dichiarare():
    assert _key_field({"human_key": ["us"]}) == "us"


@needs_node
def test_un_designatore_fuori_dalla_chiave_non_vale():
    assert _key_field({"human_key": ["a", "b"], "unit_field": "c"}) is None


def test_lassunzione_dellultimo_non_e_piu_nel_sorgente():
    """«Sparita dal codice, non messa a riposo»."""
    js = (WEB / "scheda.js").read_text(encoding="utf-8")
    codice = "\n".join(l for l in js.splitlines()
                       if not l.lstrip().startswith(("*", "//", "/*")))
    assert "key[key.length - 1]" not in codice, "la deduzione è ancora viva"
    assert "def.unit_field" in codice


@have_templates
def test_il_servizio_spedisce_il_designatore_di_tutte_e_tre():
    """La definizione che arriva al browser lo porta — altrimenti il modulo può
    solo indovinare, ed è esattamente quello che faceva."""
    import os
    import sys
    sys.path.insert(0, str(ROOT))
    os.environ["STRATIGRAPH_SCHEDE_DIR"] = str(TEMPLATES)
    from app import scheda as S

    attesi = {"iccd-us-2021": "us", "es-ue-demo-2026": "contexto",
              "hu-rl-demo-2026": "retegszam"}
    for sid, atteso in attesi.items():
        found = S.find(sid)
        payload = found.for_browser(found.source_language)
        assert payload["unit_field"] == atteso, (sid, payload["unit_field"])
    # e la posizione del designatore NON è la stessa nelle tre: primo, ultimo,
    # unico. È il motivo per cui dedurla era sbagliato.
    posizioni = set()
    for sid, atteso in attesi.items():
        found = S.find(sid)
        posizioni.add(found.human_key.index(atteso) - len(found.human_key))
    assert len(posizioni) > 1, f"le tre schede lo mettono nello stesso posto: {posizioni}"


# ── salvare al tavolo ───────────────────────────────────────────────────────

def test_la_scheda_si_puo_salvare_anche_senza_barra_dei_pollici():
    """La barra dei pollici è del telefono. Su tablet e scrivania non c'è, e
    fino a stasera non c'era nemmeno un modo di salvare."""
    js = (WEB / "scheda.js").read_text(encoding="utf-8")
    assert 'class: "sheetfoot"' in js
    assert 'if (mode !== "phone") {' in js
    assert "state.onSave()" in js
    shell = (WEB / "shell.js").read_text(encoding="utf-8")
    assert "onSave: () => save(state.def, state)" in shell


def test_salvare_e_svuotare_non_si_toccano_su_nessuna_soglia():
    """L'azione distruttiva è in CIMA apposta. Il bottone nuovo è in FONDO: se
    un giorno finissero nello stesso posto, questo diventa rosso."""
    js = (WEB / "scheda.js").read_text(encoding="utf-8")
    testa = js.index('class: "risky"')
    piede = js.index('class: "sheetfoot"')
    assert testa < piede, "«Svuota» e «Salva» hanno cambiato posto"
    fra = js[testa:piede]
    assert "sheet.append(paras)" in fra, "«Salva» non è più dopo i campi"


def test_il_rifiuto_dice_quale_cosa_manca():
    """«Manca il numero» sarebbe la frase sbagliata: il numero magari c'è, è la
    definizione che non dice quale casella sia."""
    js = (WEB / "scheda.js").read_text(encoding="utf-8")
    assert "identity.human_key.unit_field" in js
    assert "non lo indovino" in js
