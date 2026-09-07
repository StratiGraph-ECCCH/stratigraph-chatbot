#!/usr/bin/env bash
# Vendor the StratiGraph brand from `stratigraph-brand/` — the single source of
# truth. Same shape and same reasoning as EMStudio's `sync-datamodels.sh`: the
# app cannot import the brand at runtime, so it is COPIED in and committed.
#
#   ./sync-brand.sh                       # from the sibling checkout
#   ./sync-brand.sh ../stratigraph-brand  # from an explicit path
#
# Why a copy and not a `<link>` to the node: this is a PWA that runs in a trench
# on a phone. It has no CDN, and it has to work when the node itself is the thing
# that is down — a theme fetched over the network would leave the assistant
# unstyled exactly where it matters most. Review the diff, commit it.
#
# Never edit `web/brand/` by hand: the next sync overwrites it.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DST="$HERE/web/brand"
SIBLING="$(cd "$HERE/.." && pwd)/stratigraph-brand"

has_theme() { [ -f "$1/stratigraph-theme.css" ]; }

SRC=""
if [ -n "${1:-}" ]; then
  for cand in "$1" "$1/stratigraph-brand"; do
    if has_theme "$cand"; then SRC="$cand"; break; fi
  done
  [ -n "$SRC" ] || { echo "no stratigraph-theme.css under '$1'" >&2; exit 1; }
fi
if [ -z "$SRC" ] && has_theme "$SIBLING"; then SRC="$SIBLING"; fi
[ -n "$SRC" ] || {
  echo "stratigraph-brand not found beside this repo — pass a path." >&2
  exit 1
}

mkdir -p "$DST/fonts" "$DST/logo"
cp "$SRC/stratigraph-theme.css" "$DST/"
cp "$SRC"/fonts/*.woff2 "$DST/fonts/"

# Only the logos this app REACHES. Every vendored byte is served to a phone over
# a trench's wifi, and an unused colourway is weight nobody asked for. The two
# here are what `index.html` names: the hourglass on the light ground, and the
# off-white one for the dark scheme.
for f in favicon-deep-charcoal.svg favicon-off-white.svg; do
  cp "$SRC/logo/$f" "$DST/logo/"
done

# ── E LA CONFERMA CONDIVISA ─────────────────────────────────────────────────
#
# `confirm.js` viene da `stratigraph-server/app/node_admin/`, dove è la
# sorgente. Copiato per la stessa ragione del marchio — un browser non può
# importare un modulo da un'altra origine senza CORS, e mettere CORS su un file
# di codice per risparmiare una copia è un cattivo scambio.
#
# È senza dipendenze, ed è la condizione che lo rende copiabile: zero `import`,
# e il dizionario arriva come argomento (`makeConfirm(t)`) precisamente perché
# ogni superficie ha il suo. Stessa relazione dichiarata che ha col catalogo,
# vendorizzato là il 7 ottobre.
#
# Perché serve qui: dall'8 ottobre una foto in coda si può SCARTARE, e i suoi
# byte stanno solo su questo telefono — non si torna indietro. `confirmTyped`
# chiede di scrivere il nome, e non ce n'è una terza (decisione del 7 ottobre).
SHELL_SRC="$(cd "$HERE/.." && pwd)/stratigraph-server/app/node_admin"
if [ -f "$SHELL_SRC/confirm.js" ]; then
  cp "$SHELL_SRC/confirm.js" "$HERE/web/"
  SHELL_SYNCED="confirm.js  (da stratigraph-server/app/node_admin)"
else
  SHELL_SYNCED="NON sincronizzato: stratigraph-server non è accanto a questo repo"
fi

fonts=$(ls -1 "$DST/fonts" | wc -l | tr -d ' ')
bytes=$(du -sh "$DST" | cut -f1)
version=$(grep -oE '^- `[0-9]+\.[0-9]+\.[0-9]+`' "$SRC/README.md" | head -1 \
          | tr -d '`-' | tr -d ' ')
cat <<EOF
synced the brand from $SRC:
  theme            stratigraph-theme.css${version:+  (v$version)}
  fonts            $fonts woff2 (Erode · IBM Plex Sans · IBM Plex Mono)
  logo             $(ls -1 "$DST/logo" | wc -l | tr -d ' ') svg
  vendored size    $bytes
  shared module    $SHELL_SYNCED
EOF
