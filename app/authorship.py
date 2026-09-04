"""CHI HA SCRITTO QUEL CAMPO — la persona, o il modello.

E.D., 4 settembre: *«riempire dettando quanto possibile, e nella stanza qualcuno
compila i campi ripetitivi, corregge errori e VALIDA quanto scritto, se
eventualmente passa per un LLM che corregge e magari distorce.»*

Quel «magari distorce» è tutto il problema. Un modello che riscrive una frase
detta in trincea produce un testo che **somiglia** a quello che una persona ha
detto, e tre anni dopo nessuno sa più quale delle due cose sta leggendo. Quindi
il campo se lo porta scritto.

════════════════════════════════════════════════════════════════════════════════
## IL MODELLO ESISTE GIÀ, E NON SI INVENTA NIENTE

* **`AuthorAINode`** — misurato: `s3dgraphy.mappings.authoring.target_groups()`
  dichiara **32 target**, e uno è `{"cidoc": "E39 Actor", "em_type":
  "AuthorAINode", "label": "AI Author"}`. Un autore AI è già un attore del
  datamodel: non serve un tipo di nodo nuovo, che è ciò che il recinto vieta.
* **L'orologio del CRDT è per CAMPO** — `Clock(ts, by)`, e
  `field_clocks` tiene un orologio per ogni campo. L'autorialità sta allo
  stesso grado di finezza, perché una scheda con undici campi di una persona e
  uno del modello è il caso normale, non l'eccezione.

## IL CRITERIO — misurato, non deciso qui

`intent.py` dichiara già la cosa che serve: `Intent.via` vale `"rules"` o
`"llm"`, ed è documentato come *«reported because an operator debugging a field
node needs to know whether the model was involved»*. Quindi:

* `via == "llm"` → **il modello ha composto quegli slot**: autore AI;
* `via == "rules"` → deterministico, verbatim: **della persona**.

**E LA TRASCRIZIONE NON CONTA.** Whisper sul nodo trascrive, non compone: una
frase detta e trascritta è ancora ciò che la persona ha detto, parola per
parola. Il criterio è il modello di INTENTO, non il motore vocale — e la
differenza è precisamente quella fra «ripetere» e «riformulare». Sarebbe stato
facile marcare AI tutto ciò che passa da un microfono, e avrebbe attribuito a
una macchina ogni parola detta sullo scavo.

## LA VALIDAZIONE TRASFERISCE L'AUTORIALITÀ

Una persona che legge un campo AI e lo conferma **se ne assume la
responsabilità**: da quel momento il campo è suo, e il grafo lo dice. Prima di
allora il campo **non ha l'aria di un campo qualunque** — perché una spunta
accanto a un'affermazione che nessuno ha verificato sarebbe un'affermazione che
nessuno ha fatto.

## E NON È `aux_volatile`

Quel meccanismo del contratto risponde alla domanda della **residenza** — un
dato che vive altrove e di cui il grafo mostra una vista. Questa è
l'**autorialità**. Sono due assi diversi, ed è un errore già fatto e già
corretto altrove nel progetto.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

#: I due autori possibili di un VALORE. Non un elenco aperto: o l'ha composto
#: una persona, o l'ha composto un modello, e un terzo caso vorrebbe una
#: discussione e non una stringa in più.
HUMAN = "human"
AI = "ai"
AUTHORS = (HUMAN, AI)

#: Il prefisso sotto cui l'autorialità di un campo vive, dentro `data`.
#:
#: MISURATO: il CRDT indirizza `data.<qualunque>.<cosa>` e lo appiattisce in una
#: chiave `"<qualunque>.<cosa>"` dentro `data`, **con un suo orologio di campo**
#: (`field_clocks["data.authorship.interpretazione"]`). È esattamente ciò che
#: serve: due persone che validano due campi diversi non si sovrascrivono a
#: vicenda, come farebbero se l'autorialità fosse una sola mappa.
#:
#: Il nome non collide con niente di riservato — `crdt.META_KEYS` è
#: `{removed, modified_by, modified_at, em_volatile_aux, created_at, created_by,
#: field_clocks}` — e questo è stato guardato, non sperato.
PREFIX = "authorship"

#: Il nodo attore che rappresenta il modello. Il TIPO viene dal datamodel
#: (`AuthorAINode`, E39 Actor); l'id è del nodo che ha girato il modello,
#: perché «l'AI» in astratto non è un attore: un modello su un Field Computing
#: Node in un cantiere lo è.
AI_AUTHOR_NODE_TYPE = "AuthorAINode"


def field_key(field: str) -> str:
    """Dove sta l'autorialità di un campo, come un'operazione la indirizza."""
    clean = str(field or "").strip()
    if not clean:
        raise ValueError("un campo senza nome non ha un'autorialità")
    # `data.` è aggiunto da `writer.addressable`, quindi qui NON si mette: due
    # posti che aggiungono lo stesso prefisso producono `data.data.…`.
    return f"{PREFIX}.{clean}"


def stamp(author: str, *, model: Optional[str] = None) -> Dict[str, Any]:
    """Il valore che dice chi ha composto quel campo.

    `validated_by` NASCE ASSENTE, e non `False`: «non ancora validato» e
    «validato da nessuno» sono la stessa cosa solo se si smette di distinguere
    fra un campo nuovo e un campo che qualcuno ha guardato e respinto — e la
    seconda cosa, quando la costruiremo, avrà bisogno di un posto suo.
    """
    if author not in AUTHORS:
        raise ValueError(
            f"«{author}» non è un autore: o {HUMAN!r} o {AI!r}. Un terzo caso "
            f"vuole una discussione, non una stringa in più.")
    out: Dict[str, Any] = {"by": author}
    if author == AI and model:
        out["model"] = str(model)
    return out


def marks_for(fields: Iterable[str], authored_by: Optional[Dict[str, Any]],
              *, default: str = HUMAN,
              model: Optional[str] = None) -> Dict[str, Any]:
    """L'autorialità da scrivere accanto a un lotto di campi.

    IL DEFAULT È `human`, e la ragione è la simmetrica di quella del marcatore
    `recorded_in`: là il default non doveva promettere nulla, qui non deve
    **accusare** nulla. Un campo su cui nessuno ha detto niente è stato scritto
    da chi ha salvato — che è ciò che accade quando una persona digita in una
    casella. Marcare AI per difetto attribuirebbe a una macchina il lavoro di
    chi scava.
    """
    said = authored_by or {}
    out: Dict[str, Any] = {}
    for field in fields:
        author = str(said.get(field) or default)
        out[field_key(field)] = stamp(author, model=model)
    return out


def read(node: Dict[str, Any], field: str) -> Dict[str, Any]:
    """Che cosa il grafo dice dell'autorialità di un campo.

    Un campo senza marcatore torna come `human` **non validato**: è il caso di
    ogni campo scritto prima che questo meccanismo esistesse, e leggerlo come
    «AI» sarebbe riscrivere la storia di ogni scheda già compilata.
    """
    data = node.get("data") or {}
    found = data.get(field_key(field))
    if not isinstance(found, dict):
        return {"by": HUMAN, "validated": False, "declared": False}
    return {"by": str(found.get("by") or HUMAN),
            "model": found.get("model"),
            "validated": bool(found.get("validated_by")),
            "validated_by": found.get("validated_by"),
            "validated_at": found.get("validated_at"),
            "declared": True}


def needs_validation(node: Dict[str, Any],
                     fields: Iterable[str]) -> List[str]:
    """I campi che il modello ha composto e nessuno ha ancora confermato.

    È quello che la scheda deve far VEDERE: un campo che nessuno ha validato
    non ha l'aria di un campo qualunque.
    """
    out = []
    for field in fields:
        said = read(node, field)
        if said["by"] == AI and not said["validated"]:
            out.append(field)
    return out


def validated(previous: Dict[str, Any], by: str, at: str) -> Dict[str, Any]:
    """L'autorialità dopo che una persona ha confermato il campo.

    **LA VALIDAZIONE TRASFERISCE**: `by` diventa `human`, ed è la persona che
    ha guardato. Ciò che resta è la memoria di come il valore era arrivato —
    `composed_by: "ai"` e il modello — perché cancellarla trasformerebbe la
    validazione in una riscrittura della storia, e fra tre anni nessuno saprebbe
    più che quel testo l'aveva proposto una macchina.
    """
    out: Dict[str, Any] = {"by": HUMAN,
                           "validated_by": str(by),
                           "validated_at": str(at)}
    was = str((previous or {}).get("by") or HUMAN)
    if was == AI:
        out["composed_by"] = AI
        if (previous or {}).get("model"):
            out["model"] = previous["model"]
    return out
