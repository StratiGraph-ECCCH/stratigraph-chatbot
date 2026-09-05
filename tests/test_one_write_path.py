"""Una sola via di scrittura verso il grafo, e questo è il test che la tiene.

`§0` del prompt di stanotte: *«La scheda NON scrive nella stanza per conto suo.
Passa dagli stessi tool e dallo stesso `writer`. Se ti accorgi di aprire una
seconda via di scrittura verso il grafo, sei nel file sbagliato.»*

Un test e non un grep nel referto, perché un grep dimostra oggi e un test
dimostra anche domani. È lo stesso argomento che `contract.py` fa per la forma
del contratto: *«due contratti che oggi vanno d'accordo e divergono al primo
rifiuto che qualcuno aggiunge a uno dei due.»*
"""

from __future__ import annotations

import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

APP = pathlib.Path(__file__).resolve().parent.parent / "app"

#: I moduli che PARLANO A QUALCUNO, e con chi. Dichiarato qui, così un modulo
#: nuovo che apre un socket fa fallire questo test finché qualcuno non scrive
#: in questa tabella con chi sta parlando — che è la conversazione che si vuole
#: avere, invece di scoprirlo dopo.
ALLOWED = {
    "writer.py": "la STANZA e il contenitore locale — l'unica via al grafo",
    # 2026-09-26 · IL CANCELLO È SCATTATO, e la conversazione che voleva avere è
    # questa: `session.py` NON è una seconda via di scrittura. È il socket che
    # `writer.py` usava già, estratto e **tenuto aperto** invece di essere aperto
    # e chiuso a ogni consegna.
    #
    # Il motivo dell'estrazione è l'invariante di progetto: StratiField SIEDE al
    # tavolo della stanza, non è un corrispondente che imbuca lettere. Fino a
    # stanotte `_send_ops` faceva `with connect(...)` — apre, entra, manda,
    # chiude — e fra una consegna e l'altra nessuno nella stanza sapeva che
    # esistesse. Il difetto di persistenza del relay è la stessa cosa vista
    # dall'altro lato: il client che avrebbe dovuto chiedere `request_save` se
    # n'era già andato.
    #
    # Le operazioni CRDT continuano a partire da `writer.py` e da nessun altro
    # posto — lo tiene il test qui sotto, che guarda i verbi e non i socket.
    "session.py": "la STANZA, tenuta aperta: il socket di writer.py, seduto",
    "intent.py": "il modello di intento sul nodo (/chat/completions)",
    "handoff.py": "il realm (scambio del codice OIDC) e /v1/auth-config",
    "assets.py": "lo store degli asset del nodo",
    "auth.py": "il realm (JWKS, discovery)",
}


def _talkers():
    """I moduli di `app/` che aprono qualcosa verso l'esterno."""
    pattern = re.compile(r"urlopen\(|websockets|\bconnect\(|httpx|requests\.")
    found = {}
    for py in sorted(APP.glob("*.py")):
        text = py.read_text(encoding="utf-8")
        # le righe di commento non contano: una spiegazione non apre un socket
        code = "\n".join(line for line in text.split("\n")
                         if not line.strip().startswith("#"))
        if pattern.search(code):
            found[py.name] = text
    return found


def test_only_the_declared_modules_reach_outside():
    talkers = _talkers()
    undeclared = sorted(set(talkers) - set(ALLOWED))
    assert not undeclared, (
        f"{undeclared} apre una connessione e non è dichiarato in ALLOWED. "
        f"Se è una via nuova verso il GRAFO, è la seconda, e il prompt di "
        f"§0 dice di fermarsi; se parla con qualcun altro, scrivi con chi.")


def test_the_graph_is_written_from_exactly_one_module():
    """Le operazioni CRDT — `add_node`, `update_field`, `add_edge` — messe sul
    filo o applicate a una sezione: solo `writer.py`.

    `tools.py` COSTRUISCE i delta e non li scrive: chiama `graph_writer`. La
    differenza è la ragione per cui una scheda non è un sottosistema nuovo.
    """
    verbs = re.compile(r'"op":\s*"(add_node|update_field|add_edge|remove_node'
                       r'|remove_edge)"')
    guilty = {}
    for py in sorted(APP.glob("*.py")):
        if py.name == "writer.py":
            continue
        text = py.read_text(encoding="utf-8")
        code = "\n".join(line for line in text.split("\n")
                         if not line.strip().startswith("#"))
        found = verbs.findall(code)
        if found:
            guilty[py.name] = sorted(set(found))
    assert not guilty, (
        f"{guilty} compone operazioni CRDT: la sola via al grafo è "
        f"`writer.py`, e due posti che scrivono operazioni sono due posti che "
        f"decidono la semantica del merge")


def _code_of(py: pathlib.Path) -> str:
    """Il sorgente senza commenti e senza docstring.

    Serve perché la prosa che NOMINA una funzione non la chiama: `tools.py`
    spiega nel proprio docstring perché `update_su` esiste, e cita
    `apply_op_to_section` per farlo. Un controllo sul testo grezzo lo
    scambiava per un chiamante — cioè misurava una spiegazione.

    Il flusso di token, non una regex: è il modo che in questo ecosistema ha
    già fatto passare due prove che non mordevano, e la lezione era
    esattamente questa.
    """
    import io
    import tokenize

    kept, previous = [], tokenize.INDENT
    with open(py, "rb") as handle:
        for token in tokenize.tokenize(handle.readline):
            if token.type in (tokenize.COMMENT, tokenize.ENCODING):
                continue
            if token.type == tokenize.STRING and previous in (
                    tokenize.INDENT, tokenize.NEWLINE, tokenize.DEDENT,
                    tokenize.NL):
                continue                       # un docstring
            kept.append(token.string)
            previous = token.type
    return " ".join(kept)


def test_apply_op_to_section_is_called_from_exactly_one_module():
    """Il CRDT si invoca, non si reimplementa — e da un posto solo, altrimenti
    il salvataggio locale e la stanza divergono al primo conflitto."""
    callers = [py.name for py in sorted(APP.glob("*.py"))
               if "apply_op_to_section (" in _code_of(py)]
    assert callers == ["writer.py"], callers


def test_that_the_docstring_filter_actually_removes_prose():
    """Una guardia che non morde dà lo stesso verde di una che funziona.

    `tools.py` NOMINA `apply_op_to_section` in un docstring e non la chiama:
    se il filtro non togliesse i docstring, comparirebbe fra i chiamanti — e il
    test qui sopra sarebbe rosso per il motivo sbagliato. Quindi si verifica
    che il nome ci sia nel testo grezzo e NON nel codice.
    """
    raw = (APP / "tools.py").read_text(encoding="utf-8")
    assert "apply_op_to_section" in raw, (
        "la prosa non c'è più: questa prova non misura più il filtro")
    assert "apply_op_to_section" not in _code_of(APP / "tools.py")


def test_the_scheda_adapter_writes_nothing():
    """L'adattatore del formato è una funzione da definizione a dati. Se
    scrivesse, sarebbe la seconda via."""
    text = (APP / "scheda.py").read_text(encoding="utf-8")
    for forbidden in ("urlopen", "websockets", "apply_op_to_section",
                      "graph_writer", ".apply(", ".update("):
        assert forbidden not in text, f"scheda.py contiene {forbidden!r}"


def test_the_tools_reach_the_graph_only_through_the_seam():
    """Ogni scrittura in `tools.py` passa da un metodo del writer, e i metodi
    sono quelli dichiarati dal protocollo — non un socket, non un file."""
    text = (APP / "tools.py").read_text(encoding="utf-8")
    used = set(re.findall(r"graph_writer\.(\w+)", text))
    from app.writer import GraphWriter
    declared = {n for n in dir(GraphWriter) if not n.startswith("_")}
    # `call` è del nodo (fotogrammetria), non del grafo: dichiarato a parte
    assert used - declared - {"call", "room_id"} == set(), used - declared
