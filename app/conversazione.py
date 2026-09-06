"""Dire una frase alla stanza, e rileggere cosa ci si è detti.

In trincea la conversazione è il posto dove si decide: che cosa sia quella US,
se il taglio è lo stesso, se la fotografia mostra quello che sembra. Questo
modulo dà al nodo di campo la sua metà — dire e rileggere — e non inventa
niente:

* **una frase è un nodo**, e un nodo lo scrivano lo sa già mandare. Nessuna
  operazione nuova, nessun protocollo nuovo, e in regalo la coda di quando non
  c'è rete: una frase detta senza campo finisce nel contenitore locale e parte
  al ritorno, come una fotografia;
* **l'autore lo mette il relay dal token**, e questo modulo non offre il posto
  dove scriverlo;
* **l'istante lo mette chi parla**, perché una frase detta alle dieci e
  sincronizzata alle diciotto porta le dieci. È la stessa asimmetria che
  `writer.py` ha già scritto: `pop("author")` e `setdefault("ts")`.

════════════════════════════════════════════════════════════════════════════════
## LA CUCITURA CHE PUÒ DIVERGERE, DETTA

La forma di un messaggio è definita in `stratigraph-server/app/conversation.py`.
Questo repo non dipende da quello, quindi tre stringhe sono ricopiate qui —
`INJECTOR`, `SAID`, `NODE_TYPE` — mentre la quarta, il marcatore di volatilità,
si **importa** da `s3dgraphy.contract`, che è la libreria che tutti e due hanno.

Tre stringhe con una prova per parte che le fissa. Non è una soluzione: è una
cucitura dichiarata, e il giorno che serve un posto solo quel posto è
s3Dgraphy, non uno dei due servizi.
"""

from __future__ import annotations

from typing import Any, Dict

from s3dgraphy.contract import VOLATILE_KEY        # «aux_volatile»

#: Ricopiate da `stratigraph-server/app/conversation.py`. Vedi la docstring.
INJECTOR = "chat"
SAID = "said"
NODE_TYPE = "UnknownNode"


def message_node(text: str, *, node_id: str) -> Dict[str, Any]:
    """Il nodo che porta una frase. Senza autore: quello lo mette il token."""
    return {
        "id": node_id,
        "node_type": NODE_TYPE,
        "name": "",
        "data": {SAID: str(text), VOLATILE_KEY: INJECTOR},
    }


def is_message(node: Dict[str, Any]) -> bool:
    data = node.get("data")
    return isinstance(data, dict) and data.get(VOLATILE_KEY) == INJECTOR
