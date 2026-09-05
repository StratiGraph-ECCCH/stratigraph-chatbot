"""La metà che si vede: le rotte che la reggono, e i vincoli sul front-end.

Il modulo si disegna in JavaScript e questo è Python, quindi ciò che si può
misurare qui è di due specie e va detto:

* **le rotte** — `/v1/scheda/{id}` e `/v1/validate` sono la sola via che il
  browser ha verso il grafo, e passano dai tool. Queste si provano davvero;
* **i vincoli sul front-end** — zero colori letterali, nessuna chiamata alla
  stanza, la cache list che copre i file nuovi. Sono proprietà del SORGENTE, e
  un test che le legge è ciò che le tiene vere domani.

Quello che NON si prova qui è come si vede: le tre soglie, la mano sola, il
tema. Quello è nel referto, con le misure prese nel browser
(`getBoundingClientRect`, non «è responsive»).
"""

from __future__ import annotations

import json
import pathlib
import re
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

WEB = pathlib.Path(__file__).resolve().parent.parent / "web"
TEMPLATES = (pathlib.Path(__file__).resolve().parents[2]
             / "stratigraph-templates" / "templates")
have_templates = pytest.mark.skipif(
    not TEMPLATES.is_dir(), reason="stratigraph-templates non è accanto")


@pytest.fixture
def client(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("STRATIGRAPH_SCHEDE_DIR", str(tmp_path))
    import yaml
    (tmp_path / "prova").mkdir()
    (tmp_path / "prova" / "template.yaml").write_text(yaml.safe_dump({
        "template": {
            "id": "prova", "source_language": "it", "languages": ["it"],
            "standard": {"authority": "TEST", "code": "P", "version": "1",
                         "title": {"it": "Prova"}},
            "identity": {"human_key": {"fields": ["sito", "numero"],
                                       "pattern": "US {numero} · {sito}"},
                         "uid": {"policy": "minted_by_creator"}},
            "paragraphs": [{"id": "tutto", "labels": {"it": "Tutto"},
                            "fields": ["sito", "numero", "nota"]}],
            "fields": [
                {"id": "sito", "labels": {"it": "SITO"}, "type": "text",
                 "recorded_in": "trench"},
                {"id": "numero", "labels": {"it": "NUMERO"},
                 "type": "identifier", "required": True,
                 "recorded_in": "trench"},
                {"id": "nota", "labels": {"it": "NOTA"}, "type": "longtext"},
            ]}}, allow_unicode=True), encoding="utf-8")

    from app.main import app
    return TestClient(app)


# ── 1 · la definizione arriva al browser come DATO ──────────────────────────

def test_the_definition_carries_the_human_key(client):
    """Il modulo deve sapere QUALE casella dice di che unità è la scheda, e non
    può saperlo da un nome scritto nel suo codice: la US la chiama `us`, la
    ficha spagnola `contexto`."""
    doc = client.get("/v1/schede/prova?lang=it").json()
    assert doc["human_key"] == ["sito", "numero"]


def test_the_graph_binding_never_crosses_the_wire(client):
    """Il modulo non decide che cosa un campo significa per il grafo."""
    doc = client.get("/v1/schede/prova?lang=it").json()
    for field in doc["fields"]:
        assert "graph" not in field and "verdict" not in field


def test_a_language_the_definition_does_not_declare_is_a_400(client):
    answer = client.get("/v1/schede/prova?lang=pl")
    assert answer.status_code == 400
    assert "pl" in answer.json()["detail"]


# ── 2 · la scheda compilata passa dai TOOL ─────────────────────────────────

def test_a_filled_scheda_becomes_a_tool_call(client, monkeypatch):
    """LA TESI DI TUTTO L'ARCO: una scheda è lo stesso atto con un'altra
    superficie d'ingresso. Non una seconda via di scrittura."""
    import app.main as main

    seen = {}

    def spy(descriptor, slots, author, registry=None):
        seen[descriptor.name] = dict(slots)
        from app.contract import ToolResult
        return ToolResult(ok=True, message="fatto", data={"updated": []})

    monkeypatch.setattr(main, "invoke", spy)
    answer = client.post("/v1/scheda/prova", json={
        "us": "12", "create": True,
        "values": {"sito": "Cencelle", "nota": "terra bruna"},
        "authored_by": {"nota": "ai"}, "model": "un-modello"})
    assert answer.status_code == 200

    assert set(seen) == {"create_su", "update_su"}
    assert seen["create_su"] == {"us": "12"}
    assert seen["update_su"]["us"] == "12"
    assert seen["update_su"]["fields"] == {"sito": "Cencelle",
                                           "nota": "terra bruna"}
    assert seen["update_su"]["authored_by"] == {"nota": "ai"}
    assert seen["update_su"]["model"] == "un-modello"


def test_a_field_the_definition_does_not_declare_is_refused(client):
    """Un modulo non è un modo per mettere qualunque cosa in `data`."""
    answer = client.post("/v1/scheda/prova",
                         json={"us": "12", "values": {"inventato": "x"}})
    assert answer.status_code == 400
    assert "inventato" in answer.json()["detail"]


def test_a_scheda_without_a_unit_is_refused(client):
    answer = client.post("/v1/scheda/prova", json={"values": {"nota": "x"}})
    assert answer.status_code == 400
    assert "unità" in answer.json()["detail"]


def test_a_scheda_this_node_does_not_serve_is_a_404(client):
    assert client.post("/v1/scheda/mai-vista",
                       json={"us": "1", "values": {}}).status_code == 404


def test_create_is_declared_and_not_guessed(client, monkeypatch):
    """«Crealo se manca» è come un numero digitato male diventa un'unità nuova:
    è il motivo per cui `update_su` esiste. Quindi lo dice il chiamante."""
    import app.main as main

    called = []

    def spy(descriptor, slots, author, registry=None):
        called.append(descriptor.name)
        from app.contract import ToolResult
        return ToolResult(ok=True, message="", data={})

    monkeypatch.setattr(main, "invoke", spy)
    client.post("/v1/scheda/prova",
                json={"us": "12", "values": {"nota": "x"}})   # create assente
    assert called == ["update_su"]


def test_a_failed_creation_stops_before_filling(client, monkeypatch):
    """Riempire le caselle di un'unità che non è stata creata è come
    `update_field` risponde «node is not here» — meglio non arrivarci."""
    import app.main as main

    called = []

    def spy(descriptor, slots, author, registry=None):
        called.append(descriptor.name)
        from app.contract import ToolResult
        return ToolResult(ok=False, message="niente da fare", data={})

    monkeypatch.setattr(main, "invoke", spy)
    answer = client.post("/v1/scheda/prova",
                         json={"us": "12", "create": True,
                               "values": {"nota": "x"}})
    assert called == ["create_su"]
    assert answer.json()["ok"] is False


# ── 3 · la conchiglia è servita dalla STESSA directory che si mette in cache ─

@pytest.mark.parametrize("name", ["shell.css", "scheda.css", "shell.js",
                                  "scheda.js"])
def test_every_new_shell_file_is_both_served_and_precached(client, name):
    """UNA DIRECTORY, UNA FONTE. Due elenchi sarebbero due risposte, e il guasto
    è quello del §3: un file in cache e non servito, o servito e non in cache, e
    nessuno dei due si vede finché qualcuno non è in trincea."""
    assert client.get(f"/{name}").status_code == 200
    body = client.get("/sw.js").text
    listed = json.loads(re.search(r"const SHELL_FILES = (\[.*?\])", body,
                                  re.S).group(1))
    assert f"./{name}" in listed


def test_something_that_is_not_the_shell_is_not_served(client):
    for path in ("README.md", "pyproject.toml", "app/main.py"):
        assert client.get(f"/{path}").status_code == 404, path


def test_the_worker_still_refuses_to_serve_itself(client):
    assert client.get("/sw.js").status_code == 200
    body = client.get("/sw.js").text
    listed = json.loads(re.search(r"const SHELL_FILES = (\[.*?\])", body,
                                  re.S).group(1))
    assert "./sw.js" not in listed


# ── 4 · i vincoli sul front-end, letti sul sorgente ────────────────────────

def _code_of(css: pathlib.Path) -> str:
    """Il CSS senza commenti: la prosa che NOMINA un colore non lo usa."""
    return re.sub(r"/\*.*?\*/", "", css.read_text(encoding="utf-8"), flags=re.S)


KEYWORDS = ("red|blue|green|black|white|gray|grey|orange|yellow|purple|pink|"
            "brown|cyan|magenta|silver|gold|beige|olive|navy|teal")


def _literals(css: pathlib.Path):
    """Un letterale è un `#hex`, una `rgb()`, o una parola-colore come VALORE.

    NON lo sono il nome di una proprietà (`white-space`) né il nome di un token
    (`--sg-off-white`) — la prima versione di questo controllo li contava
    entrambi e riportava due colori dove non ce n'era nessuno.
    """
    found = []
    for number, line in enumerate(_code_of(css).split("\n"), 1):
        if re.search(r"#[0-9a-fA-F]{3,8}\b", line):
            found.append((number, "hex", line.strip()))
        if re.search(r"\b(rgba?|hsla?)\s*\(", line):
            found.append((number, "funzione", line.strip()))
        for match in re.finditer(r":\s*([^;{]*)", line):
            value = re.sub(r"var\(--[a-z0-9-]+\)", "", match.group(1))
            if re.search(rf"\b({KEYWORDS})\b", value):
                found.append((number, "parola", line.strip()))
    return found


def test_the_css_written_for_the_scheda_has_no_literal_colour():
    assert _literals(WEB / "scheda.css") == []


def test_that_the_literal_detector_actually_detects(tmp_path):
    """Una guardia che non morde dà lo stesso verde di una che funziona."""
    guilty = tmp_path / "guilty.css"
    guilty.write_text(".a { color: #ff0000; }\n"
                      ".b { background: rgb(1,2,3); }\n"
                      ".c { border-color: red; }\n"
                      "/* qui si parla di #ffffff e di rgb() ma non si usa */\n"
                      ".d { white-space: nowrap; color: var(--sg-off-white); }\n",
                      encoding="utf-8")
    kinds = sorted(k for _n, k, _l in _literals(guilty))
    assert kinds == ["funzione", "hex", "parola"], kinds


def test_the_browser_talks_to_the_service_and_never_to_the_room():
    """§0 vale anche stanotte: il JavaScript non parla al grafo."""
    for name in ("scheda.js", "shell.js"):
        code = (WEB / name).read_text(encoding="utf-8")
        for call in re.findall(r"fetch\(\s*`?([^`\"',)]+)", code):
            assert "/v1/rooms" not in call, f"{name}: {call}"
            assert "ws://" not in call and "wss://" not in call, f"{name}: {call}"
        assert "WebSocket" not in code, name
        # …e la scrittura passa da `SG.send`, che è ciò che alimenta la coda
        if name == "scheda.js":
            assert "SG().send(" in code


def test_the_module_does_not_import_python_or_a_bundler():
    """§3bis: nessuna catena di build. Su un Raspberry Pi in cantiere un
    bundler è una cosa in più che può non installarsi."""
    page = (WEB / "index.html").read_text(encoding="utf-8")
    assert '<script type="module" src="./shell.js"></script>' in page
    for name in ("scheda.js", "shell.js"):
        code = (WEB / name).read_text(encoding="utf-8")
        for suspicious in ("require(", "node_modules", "from \"react",
                           "stratigraph_templates"):
            assert suspicious not in code, f"{name}: {suspicious}"


def test_the_page_carries_no_style_block_of_its_own():
    """Il CSS è in due file, e la lista della cache li prende perché SONO lì."""
    assert "<style>" not in (WEB / "index.html").read_text(encoding="utf-8")


def _without_comments(name: str) -> str:
    """Il sorgente senza commenti — perché la prosa che NOMINA una cosa non la
    usa, e questo controllo l'aveva già scambiata una volta: `index.html` e
    `scheda.css` spiegano *nel commento* che le classi `pam-*` non si copiano,
    e il test le contava come copiate."""
    text = (WEB / name).read_text(encoding="utf-8")
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)      # HTML
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)       # CSS e JS a blocco
    text = re.sub(r"(?m)^\s*//.*$", "", text)                # JS a riga
    return text


def test_no_class_of_the_gpl_project_was_copied():
    """pyarchinit-mini è GPL-2.0: la struttura è una convenzione e si prende, il
    markup no. Le sue classi hanno tutte il prefisso `pam-`."""
    for name in ("index.html", "scheda.css", "shell.css", "scheda.js",
                 "shell.js"):
        assert "pam-" not in _without_comments(name), name


def test_that_the_comment_stripper_actually_strips():
    """Se non togliesse i commenti, il test qui sopra sarebbe rosso per il
    motivo sbagliato: la regola è SCRITTA in due file, con la parola dentro."""
    raw = (WEB / "index.html").read_text(encoding="utf-8")
    assert "pam-" in raw, "la spiegazione non c'è più: la prova non misura nulla"
    assert "pam-" not in _without_comments("index.html")


# ── 5 · il sottoinsieme da trincea, sul dato vero ──────────────────────────

@have_templates
def test_the_phone_subset_of_the_real_sheet_is_twentyfive_not_fiftynine(
        monkeypatch):
    """E un campo SENZA marcatore non ci finisce: il default non promette nulla.
    Provato sull'effetto — si conta cosa torna, non si legge una lista.

    Era 8 su 59 il 2026-09-23, ed era una scheda quasi vuota. Sono 25 da quando
    il field assistant sa sentirsi dire `definizione`, le quote, le misure, il
    colore e la consistenza, e da quando le dodici caselle dei rapporti sono
    state marcate nella definizione — `relate_su` le copriva dal 21 settembre e
    nessuno era tornato a chiudere il cerchio.
    """
    from fastapi.testclient import TestClient

    monkeypatch.setenv("STRATIGRAPH_SCHEDE_DIR", str(TEMPLATES))
    from app.main import app

    doc = TestClient(app).get("/v1/schede/iccd-us-2021?lang=it").json()
    trench = [f for f in doc["fields"] if f["recorded_in"] == "trench"]
    assert len(doc["fields"]) == 59
    assert len(trench) == 25

    senza = [f for f in doc["fields"] if f["recorded_in"] == "unknown"]
    assert len(senza) == 31
    assert not [f for f in senza if f in trench], (
        "un campo senza marcatore è finito fra quelli da trincea")

    # …e il caso che il 2026-09-23 rendeva vivo — un obbligatorio senza
    # marcatore — NON C'È PIÙ: `definizione` è da trincea, quindi una scheda
    # compilata sullo scavo adesso chiude.
    definizione = next(f for f in doc["fields"] if f["id"] == "definizione")
    assert definizione["required"] and definizione["recorded_in"] == "trench"
    assert not [f for f in doc["fields"]
                if f["required"] and f["recorded_in"] == "unknown"]
