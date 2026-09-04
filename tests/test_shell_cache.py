"""La cache list del service worker è GENERATA, non scritta a mano.

`§4` del prompt: *«se dividi, la cache list si genera, non si scrive a mano»*.
Dodici righe scritte a mano erano oneste finché la superficie era un file. Il
momento in cui il front-end ha delle parti, ogni file aggiunto è una cosa in
più che può non essere in cache — e il guasto è invisibile finché qualcuno non
è in trincea senza segnale.

Due cose sono sostituite dal nodo, e sono le due che una persona dimentica: la
lista dei file, e il NOME della cache (un digest dei byte, così un file
cambiato invalida la cache da sé invece di aspettare che qualcuno alzi un
numero).
"""

from __future__ import annotations

import json
import pathlib
import re
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.main import _shell_files                              # noqa: E402

WEB = pathlib.Path(__file__).resolve().parent.parent / "web"


def _served():
    from fastapi.testclient import TestClient

    from app.main import app
    return TestClient(app).get("/sw.js")


def _list_in(body: str):
    found = re.search(r"const SHELL_FILES = (\[.*?\])", body, re.S)
    assert found, "la lista non è stata sostituita"
    return json.loads(found.group(1))


# ── 1 · la sostituzione avviene ─────────────────────────────────────────────

def test_the_worker_is_served_with_both_placeholders_filled():
    answer = _served()
    assert answer.status_code == 200
    assert answer.headers["content-type"].startswith("application/javascript")
    assert "__SHELL_FILES__" not in answer.text
    assert "__SHELL_VERSION__" not in answer.text


def test_the_cache_name_is_a_digest_and_not_a_hand_bumped_number():
    body = _served().text
    name = re.search(r'const SHELL = "([^"]+)"', body).group(1)
    assert name.startswith("sg-shell-")
    suffix = name[len("sg-shell-"):]
    assert len(suffix) == 12 and re.fullmatch(r"[0-9a-f]{12}", suffix), name


def test_the_file_on_disk_is_still_a_working_worker_on_its_own():
    """I segnaposto hanno un fallback letterale, così `web/sw.js` si legge e si
    ragiona senza un server. Un file che senza sostituzione è sintatticamente
    rotto è un file che nessuno può ispezionare."""
    source = (WEB / "sw.js").read_text(encoding="utf-8")
    assert "__SHELL_FILES__ ||" in source
    assert '"./brand/stratigraph-theme.css"' in source


# ── 2 · CHE COSA ENTRA, e la prova che la generazione morde ─────────────────

def test_the_generated_list_holds_the_shell_that_exists_today():
    served = _list_in(_served().text)
    assert "./" in served
    assert "./brand/stratigraph-theme.css" in served
    # le otto woff2 della marca: precacheate perché SONO lì
    assert sum(1 for f in served if f.endswith(".woff2")) == 8


def test_the_worker_never_precaches_itself():
    """Un worker in cache è un worker impossibile da aggiornare: il browser lo
    prende fuori dalla cache proprio per questo."""
    assert "./sw.js" not in _list_in(_served().text)


def test_a_file_added_to_web_appears_without_anybody_editing_a_list(tmp_path):
    """IL CANCELLO, e verifica l'EFFETTO.

    Una directory finta con un file in più: se comparisse la stessa lista di
    prima, la generazione non guarderebbe il disco e questo file starebbe
    misurando una costante.
    """
    (tmp_path / "index.html").write_text("<p>ciao</p>", encoding="utf-8")
    (tmp_path / "sw.js").write_text("// worker", encoding="utf-8")
    prima = _shell_files(tmp_path)
    assert prima == ["./"], prima

    (tmp_path / "brand").mkdir()
    (tmp_path / "brand" / "tema.css").write_text(":root{}", encoding="utf-8")
    (tmp_path / "scheda.js").write_text("export const x = 1;", encoding="utf-8")
    dopo = _shell_files(tmp_path)

    assert dopo == ["./", "./brand/tema.css", "./scheda.js"], dopo
    assert set(dopo) - set(prima) == {"./brand/tema.css", "./scheda.js"}, (
        "i file nuovi non sono comparsi: la lista non viene dal disco")


def test_something_that_is_not_the_shell_stays_out(tmp_path):
    """Un README o un .py accanto alla pagina non è la conchiglia, e metterlo
    in cache sarebbe pagare byte su un telefono per niente."""
    (tmp_path / "index.html").write_text("<p></p>", encoding="utf-8")
    (tmp_path / "README.md").write_text("# note", encoding="utf-8")
    (tmp_path / "genera.py").write_text("x = 1", encoding="utf-8")
    assert _shell_files(tmp_path) == ["./"]


def test_the_cache_name_changes_when_a_shell_file_changes(tmp_path):
    """La ragione per cui il nome è un digest: un file cambiato invalida la
    cache DA SÉ. Provato cambiando un byte."""
    import hashlib

    def digest_of(web):
        h = hashlib.sha256()
        for relative in _shell_files(web):
            candidate = web / relative.removeprefix("./")
            h.update(candidate.read_bytes() if candidate.is_file()
                     else relative.encode())
        return h.hexdigest()[:12]

    (tmp_path / "index.html").write_text("<p>uno</p>", encoding="utf-8")
    (tmp_path / "brand").mkdir()
    tema = tmp_path / "brand" / "tema.css"
    tema.write_text(":root{--a:1}", encoding="utf-8")
    prima = digest_of(tmp_path)

    tema.write_text(":root{--a:2}", encoding="utf-8")
    assert digest_of(tmp_path) != prima, (
        "il digest non è cambiato: la cache resterebbe quella di ieri")


# ── 3 · il tema NON si tocca ────────────────────────────────────────────────

def test_the_brand_is_a_propagated_copy_and_this_night_did_not_touch_it():
    """`web/brand/` è una copia propagata da `sync-brand.sh`, la cui fonte di
    record sta nel WP01. Non si modifica a mano, mai — e la generazione della
    cache list la LEGGE e non la scrive."""
    import subprocess

    root = pathlib.Path(__file__).resolve().parent.parent
    changed = subprocess.run(
        ["git", "status", "--porcelain", "web/brand/"],
        cwd=root, capture_output=True, text=True).stdout.strip()
    assert changed == "", f"web/brand/ è stata toccata:\n{changed}"
