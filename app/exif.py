"""Cosa una foto porta con sé — letto dai byte, e TRASPORTATO, non capito.

════════════════════════════════════════════════════════════════════════════════
## LA MISURA, PRIMA DELLA DECISIONE

Il prompt del 30 settembre chiede di **misurare cosa c'è davvero in una foto**,
non di dedurlo da come funzionano di solito le fotocamere. Misurato su
ventuno file veri sul disco di E.D. — reflex di scavo, telefoni, immagini
arrivate per messaggio:

    IMG_5540.JPG  13 623 291 B  Canon EOS 6D · EF35mm f/2 IS USM
                                DateTimeOriginal 2021:05:17 18:05:52
                                GPSVersionID presente, COORDINATE NO
    IMG_2634.JPG     778 248 B  Adobe Photoshop 23.5
                                DateTimeOriginal 2023:01:12 19:57:14
                                OffsetTimeOriginal +03:00
                                posizione: nessuna
    …

Il censimento intero, con questo stesso modulo:

    su 21 foto vere        presente in
    ─────────────────────────────────────
    istante                 9 / 21
    posizione               0 / 21
    macchina                7 / 21
    software                9 / 21
    autore                  1 / 21

**Zero posizioni.** Non perché le fotocamere non sappiano dove sono: la reflex
di cantiere non ha un GPS, un editor la toglie riesportando, e ogni sistema di
messaggistica la butta prima di consegnare. L'unico caso in cui c'è davvero è
**uno scatto appena fatto da un telefono** — che è appunto il caso di
StratiField, ma è l'eccezione, non la regola, e il codice non va scritto come
se fosse il contrario.

E l'istante c'è **in meno della metà**, che è meno di quanto sembrasse: la
prima versione di questa docstring diceva «c'è quasi sempre», e il censimento
completo l'ha smentita. Le nove sono tutte e sole quelle uscite da una
macchina; le dodici senza sono passate per WhatsApp, per una schermata o per un
editor. Su una foto di cantiere appena scattata l'istante c'è; su una foto che
qualcuno «ti manda» non c'è quasi mai — e le due cose finirebbero nello stesso
`ResourceNode`.

════════════════════════════════════════════════════════════════════════════════
## LA REGOLA DI CASA, APPLICATA QUI

*Quello che il servizio ha CAPITO va tenuto distinto da quello che ha soltanto
TRASPORTATO.* Il precedente è `tools.py:153-162`, dove i `rapporti` di
pyArchInit restano sotto `source_fields` — «*a reader can always tell what this
service UNDERSTOOD from what it merely carried*».

Quindi **questo modulo non promuove niente**. Legge, normalizza le stringhe, e
consegna un dizionario che `attach_photo_to_su` mette sotto
`data.source_fields.exif`. Nessun campo di questo dizionario diventa un campo
del grafo stanotte.

**La proposta è nel referto**, e in una riga è questa: l'unico candidato serio è
`DateTimeOriginal` → un istante di provenienza sul `ResourceNode`, distinto dal
`created_at` editoriale (che è *quando la riga è stata scritta*, non *quando la
foto è stata scattata*: sulla IMG_5540 sono cinque anni di distanza). La
posizione **non** è un candidato finché non è misurata su scatti veri di campo,
e comunque non lo è da sola: una coordinata senza incertezza dichiarata, messa
in un grafo archeologico, è un'asserzione più forte del dato che la sostiene.

E il collegamento non dipende da niente di tutto questo: **un telefono col GPS
spento funziona uguale**, perché nessun ramo di `attach_photo_to_su` guarda
questi campi.

════════════════════════════════════════════════════════════════════════════════
## PERCHÉ SCRITTO A MANO

Pillow non è fra le dipendenze di questo servizio e non ci si aggiunge per
leggere tre numeri: su un nodo di campo ogni ruota in più è una cosa che può
non installarsi. Qui si legge il TIFF dentro `APP1/Exif` e si prendono i tag
dichiarati sotto — se un tag non c'è, non c'è, e nessun valore viene inventato.

**Non decodifica l'immagine.** Non è un lettore di immagini e non deve
diventarlo: se i byte non cominciano con `FFD8` il risultato è vuoto, non
un'eccezione. Una foto che questo modulo non capisce si allega lo stesso.
"""

from __future__ import annotations

import struct
from typing import Any, Dict, List, Optional, Tuple

#: I tipi TIFF, con la lettera di `struct` e i byte che occupano. Presi dalla
#: specifica e non dedotti: un tipo letto male sposta l'offset e legge un tag
#: diverso, e l'errore assomiglia a un valore.
_TYPES = {1: ("B", 1), 2: ("s", 1), 3: ("H", 2), 4: ("I", 4), 5: ("II", 8),
          6: ("b", 1), 7: ("s", 1), 8: ("h", 2), 9: ("i", 4), 10: ("ii", 8),
          11: ("f", 4), 12: ("d", 8)}

#: I tag che si leggono, per IFD. UNA LISTA E NON «tutto»: un dizionario che
#: porta ogni tag di ogni marca finisce nel grafo, e finirebbe con dentro i dati
#: di calibrazione del sensore. Questi sono quelli che un archeologo potrebbe
#: un giorno voler citare.
_IFD0 = {271: "Make", 272: "Model", 274: "Orientation", 305: "Software",
         306: "DateTime", 315: "Artist", 33432: "Copyright",
         34665: "@Exif", 34853: "@GPS"}
_EXIF = {36867: "DateTimeOriginal", 36868: "DateTimeDigitized",
         36880: "OffsetTime", 36881: "OffsetTimeOriginal",
         33437: "FNumber", 34855: "ISOSpeedRatings", 37386: "FocalLength",
         40962: "PixelXDimension", 40963: "PixelYDimension",
         42016: "ImageUniqueID", 42035: "LensMake", 42036: "LensModel"}
_GPS = {1: "GPSLatitudeRef", 2: "GPSLatitude", 3: "GPSLongitudeRef",
        4: "GPSLongitude", 5: "GPSAltitudeRef", 6: "GPSAltitude",
        7: "GPSTimeStamp", 29: "GPSDateStamp", 11: "GPSDOP",
        31: "GPSHPositioningError"}

#: Quanto in profondità si segue un puntatore. Un file storto può far puntare
#: un IFD a sé stesso, e senza questo il lettore gira per sempre su un byte
#: sbagliato invece di dire che non ha capito.
_MAX_DEPTH = 3


def _read_ifd(buf: bytes, off: int, order: str, names: Dict[int, str],
              out: Dict[str, Any], depth: int = 0) -> None:
    if depth > _MAX_DEPTH or off <= 0 or off + 2 > len(buf):
        return
    (count,) = struct.unpack_from(order + "H", buf, off)
    if count > 512:                      # nessun IFD onesto ha 512 voci
        return
    for index in range(count):
        entry = off + 2 + index * 12
        if entry + 12 > len(buf):
            return
        tag, kind, n = struct.unpack_from(order + "HHI", buf, entry)
        name = names.get(tag)
        if name is None or kind not in _TYPES:
            continue
        fmt, size = _TYPES[kind]
        total = size * n
        if total > len(buf):
            continue
        if name.startswith("@"):
            (ptr,) = struct.unpack_from(order + "I", buf, entry + 8)
            _read_ifd(buf, ptr, order, _EXIF if name == "@Exif" else _GPS,
                      out, depth + 1)
            continue
        if total <= 4:
            data = buf[entry + 8:entry + 8 + total]
        else:
            (ptr,) = struct.unpack_from(order + "I", buf, entry + 8)
            data = buf[ptr:ptr + total]
        if len(data) < total:
            continue
        out[name] = _value(data, fmt, n, order)


def _value(data: bytes, fmt: str, n: int, order: str) -> Any:
    if fmt == "s":
        return data.split(b"\x00")[0].decode("utf-8", "replace").strip()
    if fmt in ("II", "ii"):
        pieces: List[float] = []
        for k in range(n):
            num, den = struct.unpack_from(order + fmt, data, k * 8)
            pieces.append(num / den if den else 0.0)
        return pieces if n > 1 else (pieces[0] if pieces else None)
    values = list(struct.unpack_from(order + fmt * n, data))
    return values if n > 1 else (values[0] if values else None)


def segments(data: bytes) -> List[Tuple[str, int]]:
    """I segmenti JPEG e quanto pesano — la misura, non il dato.

    Serve a poter dire «l'EXIF è lo 0,11% del file» con un numero invece che
    con un'impressione.
    """
    out: List[Tuple[str, int]] = []
    if data[:2] != b"\xff\xd8":
        return out
    i = 2
    while i + 4 <= len(data):
        if data[i] != 0xFF:
            break
        marker = data[i + 1]
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        (length,) = struct.unpack_from(">H", data, i + 2)
        body = data[i + 4:i + 2 + length]
        name = {0xE0: "APP0/JFIF", 0xE1: "APP1", 0xE2: "APP2/ICC",
                0xED: "APP13/IPTC", 0xDB: "DQT", 0xC0: "SOF0", 0xC2: "SOF2",
                0xC4: "DHT", 0xDA: "SOS"}.get(marker, f"0x{marker:02X}")
        if marker == 0xE1:
            name += "/XMP" if body[:4] == b"http" else "/Exif"
        out.append((name, length + 2))
        if marker == 0xDA:               # da qui in poi è l'immagine
            break
        i += 2 + length
    return out


def read(data: bytes) -> Dict[str, Any]:
    """I tag dichiarati sopra, o un dizionario vuoto.

    Mai un'eccezione: una foto che questo modulo non capisce va allegata lo
    stesso, e un allegato che fallisce per un byte storto nei metadati sarebbe
    il difetto peggiore di non avere metadati affatto.
    """
    out: Dict[str, Any] = {}
    if not data or data[:2] != b"\xff\xd8":
        return out
    try:
        i = 2
        while i + 4 <= len(data):
            if data[i] != 0xFF:
                break
            marker = data[i + 1]
            if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
                i += 2
                continue
            (length,) = struct.unpack_from(">H", data, i + 2)
            body = data[i + 4:i + 2 + length]
            if marker == 0xE1 and body[:6] == b"Exif\x00\x00":
                tiff = body[6:]
                order = "<" if tiff[:2] == b"II" else ">"
                (first,) = struct.unpack_from(order + "I", tiff, 4)
                _read_ifd(tiff, first, order, _IFD0, out)
                break
            if marker == 0xDA:
                break
            i += 2 + length
    except Exception:                    # noqa: BLE001 — vedi la docstring
        return out
    return {k: v for k, v in out.items() if v not in ("", None, [])}


def position(tags: Dict[str, Any]) -> Optional[Dict[str, float]]:
    """La posizione in gradi decimali, se c'è.

    **Non viene messa nel grafo** (vedi la docstring del modulo): esiste perché
    un referto onesto deve poter dire «misurato: zero file su ventuno», e
    per dirlo bisogna saperla leggere.
    """
    lat, lon = tags.get("GPSLatitude"), tags.get("GPSLongitude")
    if not (isinstance(lat, list) and isinstance(lon, list)
            and len(lat) == 3 and len(lon) == 3):
        return None

    def gradi(parts: List[float], ref: Any) -> float:
        value = parts[0] + parts[1] / 60 + parts[2] / 3600
        return -value if str(ref or "").upper() in ("S", "W") else value

    return {"lat": gradi(lat, tags.get("GPSLatitudeRef")),
            "lon": gradi(lon, tags.get("GPSLongitudeRef"))}
