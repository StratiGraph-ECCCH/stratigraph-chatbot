"""THE ONE PLACE that reads a `stratigraph-templates` definition.

A scheda is not a new subsystem: it is the same act as `create_su` with another
INPUT SURFACE. Instead of filling one slot at a time by voice, many are filled
at once by looking at them. So this module does exactly one job — take a
definition written in the format `stratigraph-templates/SPEC.md` describes, and
hand it to this service in the shape it needs.

**If the format changes, it changes in here and nowhere else.** That is the
whole reason it is one module and not a helper next to whatever needs it.

════════════════════════════════════════════════════════════════════════════════
## WHAT THIS DELIBERATELY DOES NOT DO

**It does not import `stratigraph_templates`.** Not for tidiness — the Python
renderer over there runs on a SERVER, and the form has to work offline in a
browser on a telephone in a trench. Those two do not meet. The decision (E.D.,
5 September) is that the module is rendered in JavaScript from the definition
travelling as DATA, and the Python stays the authoring engine and the A4 print.
So this reads YAML and nothing else, and `pyproject.toml` gains no dependency.

**It does not interpret.** Labels, `required`, `repeatable`, vocabularies and
`recorded_in` are READ. If a rule appears here that the format already
expresses, that is a second implementation of the standard in another place —
which is the defect measured in pyarchinit-mini, where the same sheet was
written twice (786 lines of form, 4,864 of PDF) and seven labels were wrong on
one side only.

**It does not translate.** The labels come from the definition, in the
languages the definition declares. The interface's own chrome is a different
dictionary and stays a different dictionary: what the STANDARD says is not
translatable by us. Asking for a language a definition does not declare is an
**error**, not a degraded mode — `SPEC.md` §1.5 states it, and
`labels_for` refuses rather than falling back.

## THE ONE THING IT ADDS: `recorded_in` DEFAULTS TO NOTHING

`SPEC.md` §1.6: three values, `trench` · `lab` · `unknown`, and the absence of
the marker means `unknown`. **A consumer must not read silence as «trench».**
So `trench_fields()` returns only what the definition SAYS is trench, and a
definition with no markers yields an empty list — which is the honest answer,
and the reason a phone form built on it shows nothing rather than everything.
"""

from __future__ import annotations

import pathlib
from typing import Any, Dict, List, Optional

#: Where a node keeps the definitions it serves. A DIRECTORY, so that adding a
#: standard is dropping a file in — which is the format's own claim, and the
#: thing tonight's end-of has to demonstrate: a definition nobody has seen must
#: reach the telephone without rebuilding the front-end.
SCHEDE_DIR_VARIABLE = "STRATIGRAPH_SCHEDE_DIR"

#: The three values of `recorded_in`, and the default. Repeated from the format
#: rather than imported, because importing would mean a Python dependency on
#: `stratigraph-templates` — and `test_scheda.py` asserts that these agree with
#: that repository's `SPEC.md` when it is present, so the repetition cannot
#: drift silently.
TRENCH = "trench"
LAB = "lab"
UNKNOWN = "unknown"
RECORDED_IN_VALUES = (UNKNOWN, TRENCH, LAB)


class SchedaError(ValueError):
    """A definition this node cannot serve, in a sentence that says why."""


class Scheda:
    """One definition, read. Immutable as far as this service is concerned."""

    def __init__(self, doc: Dict[str, Any], *, path: Optional[str] = None):
        template = doc.get("template") if isinstance(doc, dict) else None
        if not isinstance(template, dict):
            raise SchedaError(
                "questo file non è una definizione di scheda: manca la chiave "
                "`template` di primo livello (SPEC.md §1)")
        self.raw = template
        self.path = path
        self.id = str(template.get("id") or "")
        if not self.id:
            raise SchedaError("una definizione senza `id` non è servibile")
        self.languages: List[str] = list(template.get("languages") or [])
        self.source_language = str(template.get("source_language") or "")
        self.standard: Dict[str, Any] = dict(template.get("standard") or {})
        self.fields: List[Dict[str, Any]] = list(template.get("fields") or [])
        #: WHICH FIELDS SPELL THE UNIT'S NAME, from `identity.human_key`
        #: (SPEC §1.2). The module needs it so the box that says which unit the
        #: record is about is the definition's own, not a second one beside it
        #: — and it cannot be `us` hard-coded here, because the Spanish sheet
        #: calls it `contexto`.
        identity = template.get("identity") or {}
        self.human_key: List[str] = list(
            ((identity.get("human_key") or {}).get("fields")) or [])
        #: WHICH of those fields IS the unit — `identity.human_key.unit_field`,
        #: added to SPEC §1.2 on 2026-09-24. It is READ and passed on, not
        #: resolved here: this module does not interpret, and the rule for what
        #: to do when it is absent belongs to the consumer that has to draw a
        #: box (`keyField` in `web/scheda.js`).
        #:
        #: WHY IT HAD TO TRAVEL. Until tonight it did not, and the module took
        #: the LAST field of the key. Measured against this node, on the
        #: Hungarian sheet: `human_key = [retegszam, lelohely]`, declared
        #: designator `retegszam` — the FIRST — and the browser chose
        #: `lelohely`, the place name. A scheda filed under «Aquincum» instead
        #: of under the layer number, and nothing anywhere would have said so.
        self.unit_field: str = str(
            ((identity.get("human_key") or {}).get("unit_field")) or "")
        self.paragraphs: List[Dict[str, Any]] = list(
            template.get("paragraphs") or [])
        if not self.fields:
            raise SchedaError(f"«{self.id}» non dichiara nessun campo")
        self._by_id = {str(f.get("id")): f for f in self.fields}

    # ── what the definition says ────────────────────────────────────────────

    def title(self, lang: str) -> str:
        return labels_for(self.standard.get("title") or {}, lang,
                          f"il titolo di «{self.id}»")

    def field(self, fid: str) -> Dict[str, Any]:
        try:
            return self._by_id[fid]
        except KeyError:
            raise SchedaError(
                f"«{self.id}» non ha un campo «{fid}»") from None

    def recorded_in(self, fid: str) -> str:
        """Where a field is filled in — ABSENT MEANS `unknown`.

        Read through this rather than off the dict, so the default lives in one
        place and no caller can accidentally spell it `!= "lab"`.
        """
        value = self.field(fid).get("recorded_in")
        if value is None:
            return UNKNOWN
        if value not in RECORDED_IN_VALUES:
            raise SchedaError(
                f"«{self.id}», campo «{fid}»: recorded_in={value!r} non è uno "
                f"di {list(RECORDED_IN_VALUES)}. Una definizione con un valore "
                f"che non esiste non si serve: il modulo non saprebbe se "
                f"mostrare quella casella o no.")
        return str(value)

    def trench_fields(self) -> List[str]:
        """What a telephone form shows. Nothing more, and nothing by default."""
        return [str(f.get("id")) for f in self.fields
                if self.recorded_in(str(f.get("id"))) == TRENCH]

    def counts(self) -> Dict[str, int]:
        tally = {value: 0 for value in RECORDED_IN_VALUES}
        for f in self.fields:
            tally[self.recorded_in(str(f.get("id")))] += 1
        return tally

    # ── what the browser gets ───────────────────────────────────────────────

    def for_browser(self, lang: str) -> Dict[str, Any]:
        """The definition, as DATA, in one language.

        Flattened to one language on purpose: a phone in a trench does not need
        five, and `labels_for` has already refused a language the definition
        does not declare — so what crosses the wire cannot contain a label
        nobody wrote.

        Everything the JS renderer needs is here and nothing it does not:
        `graph` bindings, `provenance` and the print `sheet` stay behind,
        because the module does not draw an A4 and does not decide what a field
        means to the graph.
        """
        if lang not in self.languages:
            raise SchedaError(
                f"«{self.id}» dichiara {self.languages} e non «{lang}». "
                f"Chiedere una lingua che la definizione non ha è un errore, "
                f"non una modalità degradata: servirebbe una parola che "
                f"nessuno ha scritto per quello standard.")
        return {
            "id": self.id,
            "lang": lang,
            "title": self.title(lang),
            "standard": {k: self.standard.get(k)
                         for k in ("authority", "code", "version", "invented")},
            "languages": self.languages,
            "paragraphs": [
                {"id": str(p.get("id")),
                 "label": labels_for(p.get("labels") or {}, lang,
                                     f"il paragrafo «{p.get('id')}»"),
                 "fields": list(p.get("fields") or [])}
                for p in self.paragraphs],
            "fields": [self._field_for_browser(f, lang) for f in self.fields],
            "human_key": list(self.human_key),
            "unit_field": self.unit_field,
            "counts": self.counts(),
        }

    def _field_for_browser(self, f: Dict[str, Any], lang: str) -> Dict[str, Any]:
        fid = str(f.get("id"))
        out: Dict[str, Any] = {
            "id": fid,
            # THE LABEL COMES FROM THE DEFINITION, and its absence is an error.
            # This is the line that makes end-of §8 demonstrable: remove a
            # label from a definition and the scheda refuses instead of
            # fishing a word out of the interface's dictionary.
            "label": labels_for(f.get("labels") or {}, lang,
                                f"il campo «{fid}» di «{self.id}»"),
            "type": str(f.get("type") or ""),
            "required": bool(f.get("required", False)),
            "repeatable": bool(f.get("repeatable", False)),
            "recorded_in": self.recorded_in(fid),
        }
        if f.get("max_len"):
            out["max_len"] = f["max_len"]
        help_text = f.get("help") or {}
        if help_text:
            # Help is OPTIONAL, so a definition that has it in one language and
            # not another is not broken — unlike a label. `.get`, deliberately.
            said = help_text.get(lang)
            if said:
                out["help"] = said
        options = f.get("options") or []
        if options:
            out["options"] = [
                {"value": str(o.get("value")),
                 "label": labels_for(o.get("labels") or {}, lang,
                                     f"l'opzione «{o.get('value')}» di «{fid}»")}
                for o in options]
        vocabulary = f.get("vocabulary") or {}
        if vocabulary.get("scheme"):
            # The SCHEME's name only: resolving a vocabulary is the authoring
            # engine's job and needs a server. The form says which controlled
            # list a box belongs to, and a node with the vocabulary can offer
            # it; a node without it still shows the box.
            out["vocabulary"] = str(vocabulary["scheme"])
        return out


def labels_for(labels: Dict[str, Any], lang: str, what: str) -> str:
    """A label in one language, or a refusal naming what is missing.

    NO FALLBACK, and this is the measured reason: `pdf_export` in
    pyarchinit-mini resolved sheet labels against a generic i18n dictionary and
    printed «Notifica» where the sheet says FLOTTAZIONE, «Struttura Valida»
    where it says AREA — seven labels wrong, and wrong only in print. A missing
    label is a hole in the definition and has to read as one.
    """
    said = (labels or {}).get(lang)
    if said in (None, ""):
        raise SchedaError(
            f"{what}: manca l'etichetta in «{lang}». Le etichette di una "
            f"scheda vengono dalla definizione dello standard, non dal "
            f"dizionario dell'interfaccia — quindi questa non si sostituisce "
            f"con una parola generica, si segnala.")
    return str(said)


# ── loading ──────────────────────────────────────────────────────────────────

def load(path: Any) -> Scheda:
    """One definition from a YAML file."""
    import yaml

    where = pathlib.Path(path)
    try:
        doc = yaml.safe_load(where.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SchedaError(f"nessuna definizione in {where}") from None
    except yaml.YAMLError as exc:
        raise SchedaError(f"{where} non è YAML leggibile: {exc}") from exc
    return Scheda(doc, path=str(where))


def schede_dir(environ: Optional[Dict[str, str]] = None) -> Optional[pathlib.Path]:
    """Where this node keeps its definitions, or None.

    ABSENT MEANS NO SCHEDE, and the assistant is exactly what it was: the same
    rule the OIDC door, the session key and the room client all follow in this
    ecosystem. A node that serves no definitions is not broken; it is a node
    that takes dictation.
    """
    import os

    source = environ if environ is not None else os.environ
    raw = (source.get(SCHEDE_DIR_VARIABLE) or "").strip()
    if not raw:
        return None
    where = pathlib.Path(raw).expanduser()
    return where if where.is_dir() else None


def available(environ: Optional[Dict[str, str]] = None) -> List[Scheda]:
    """Every definition this node can serve, in id order.

    A DIRECTORY LISTING, and that is the point: dropping the Spanish sheet in
    makes it appear, with no code change and no release. A file that does not
    parse is SKIPPED and named in the log rather than taking the others down —
    one bad definition must not cost a person their whole scheda list.
    """
    import logging

    where = schede_dir(environ)
    if where is None:
        return []
    found: List[Scheda] = []
    for candidate in sorted(where.rglob("*.yaml")) + sorted(where.rglob("*.yml")):
        try:
            found.append(load(candidate))
        except SchedaError as problem:
            logging.getLogger("stratigraph-chatbot.scheda").warning(
                "[scheda] %s non servibile: %s", candidate, problem)
    return sorted(found, key=lambda s: s.id)


def find(scheda_id: str,
         environ: Optional[Dict[str, str]] = None) -> Optional[Scheda]:
    return next((s for s in available(environ) if s.id == scheda_id), None)


# ── from a filled scheda to the slots a tool takes ──────────────────────────

def slots_for(scheda: Scheda, values: Dict[str, Any], *,
              us: str) -> Dict[str, Any]:
    """A filled scheda becomes the slots of `create_su` / `update_su`.

    THE POINT OF THE WHOLE MODULE, in one function: a scheda is the same act
    with another input surface, so what comes out of here is what a voice would
    have produced. No new write path — `tools.py` and `writer.py` are the only
    road, and this just fills their slots.

    Fields the definition does not declare are REFUSED, not carried: a form
    that posted a name nobody wrote would be a way to put anything into
    `data`, and the definition is what says what a box is.
    """
    unknown = sorted(k for k in values if k not in scheda._by_id)
    if unknown:
        raise SchedaError(
            f"«{scheda.id}» non ha i campi {unknown}: una scheda compila le "
            f"caselle che lo standard dichiara, non altre.")
    fields = {k: v for k, v in values.items() if k != "us"}
    return {"us": str(us).strip(), "fields": fields}
