#!/usr/bin/env python3
"""One static page per film, so a shared link previews as that film.

Link unfurlers (WhatsApp, Telegram, iMessage, X...) read `og:` tags out of the
HTML they fetch. The site is a single client-rendered index.html, so
`?film=<id>` can only ever preview as the generic og.png. The fix is a real
file per film: docs/p/<id>/index.html carries that film's title, details and
poster, and loads docs/p/redirect.js, which sends a person on to
`/?film=<id>` and leaves link previewers where they are.

The poster is Cineteca's own URL, not a copy, asked for at 215x307 (see
thumbnail_url()). Their CDN serves it publicly and resizes on request, and
copying it here would commit images into the repo twice a day. A film without
a usable poster falls back to the site's og.png.

Pages are never pruned. A link shared last month keeps its preview and still
lands somewhere: the app ignores a `film=` it no longer knows and shows the
agenda. Each page is ~2KB.

Previews are cosmetic. Nothing here may stop a scrape from publishing
schedule.json - see write_previews().

Run directly to rewrite every page from the committed schedule.json, to look
at a template change locally - see refresh(). Don't commit its output: the
next scrape rewrites every page whose bytes changed, and marks `preview`.
"""
import html
import json
import logging
import os
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

log = logging.getLogger("previews")

DOCS_DIR = os.path.join(os.path.dirname(__file__), "..", "docs")
PREVIEW_DIR = os.path.join(DOCS_DIR, "p")
CNAME_PATH = os.path.join(DOCS_DIR, "CNAME")
SCHEDULE_PATH = os.path.join(DOCS_DIR, "data", "schedule.json")

# Ids become directory names, and they come from a third-party JSON feed, so
# anything but a plain token is refused rather than trusted near a path.
FILM_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")

# WhatsApp decides between a small square thumbnail beside the text and a wide
# banner above it by the image's own size - undocumented, but images under
# about 300px wide get the thumbnail, which is the one wanted here. Vista's
# posters are exactly 300x429, right on that line. Its CDN resizes on request
# and snaps to fixed steps: width=200 returns 215x307 (150 -> 170x243, 240 ->
# 265x379, 280 and up -> the 300x429 original), measured 2026-09-22.
#
# No og:image:width/height are declared. WhatsApp builds its preview on the
# sender's phone and measures the image itself; the declared size would only
# help Facebook's first share, and would be wrong for whatever Vista returns
# for a listed film with no art (unverified: the URL asks for its placeholder).
THUMBNAIL_WIDTH = 200


def thumbnail_url(poster):
    """The poster URL with `width` set, keeping every other parameter.

    Vista's poster URLs already carry a query (`referenceScheme=Cinema&
    allowPlaceHolder`, the second with no value), so this rebuilds the query
    rather than appending to a string that might already have a width.
    """
    parts = urlsplit(poster)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != "width"]
    query.append(("width", str(THUMBNAIL_WIDTH)))
    # Re-encoding turns the valueless `allowPlaceHolder` into `allowPlaceHolder=`,
    # which Vista reads the same: byte-identical image back, checked 2026-09-22.
    return urlunsplit(parts._replace(query=urlencode(query)))


def site_url(cname_path=None):
    """The site's origin with a trailing slash, from the Pages CNAME.

    og:url and a fallback og:image have to be absolute, and the CNAME is the
    one place the domain is already recorded.
    """
    with open(cname_path or CNAME_PATH, "r", encoding="utf-8") as f:
        domain = f.read().strip()
    if not domain or "/" in domain or " " in domain:
        raise ValueError(f"unexpected CNAME contents: {domain!r}")
    return f"https://{domain}/"


def preview_path(film_id):
    """Where a film's page is served, relative to the site root."""
    return f"p/{film_id}/"


def _description(film):
    parts = [film.get("director"), film.get("country"), film.get("year")]
    details = " · ".join(str(p) for p in parts if p)
    return f"{details} — en la Cineteca Nacional" if details else "en la Cineteca Nacional"


def _image(film, site):
    """(url, alt, twitter card) - the poster, or the site card.

    One decision, so the image, its description and the card shape can't
    disagree: a portrait poster wants the small `summary` card, and og.png is
    the wide banner index.html already uses.
    """
    poster = film.get("poster")
    title = film.get("title") or "cartelera"
    if isinstance(poster, str) and poster.startswith("https://"):
        return thumbnail_url(poster), f"póster de {title}", "summary"
    # index.html's own alt text for og.png.
    alt = "cartelera — la cartelera de la Cineteca Nacional, todas las sedes"
    return site + "og.png", alt, "summary_large_image"


def render_preview(film, site):
    """The page for one film. Deterministic: same film in, same bytes out, so
    an unchanged film never shows up in the scraper's commit."""
    film_id = film["id"]
    if not isinstance(film_id, str) or not FILM_ID_RE.fullmatch(film_id):
        raise ValueError(f"film id not usable as a path: {film_id!r}")

    e = lambda s: html.escape(str(s), quote=True)  # noqa: E731
    title = e(film.get("title") or "cartelera")
    description = e(_description(film))
    image_url, alt, card = _image(film, site)
    image = e(image_url)
    url = e(site + preview_path(film_id))
    app_link = f"../../?film={film_id}"
    # Sending people on is redirect.js's job, not this template's, so a fix
    # there reaches pages this module will never rewrite (films that have left
    # the cartelera). The page has no colors of its own - color-scheme lets the
    # browser paint its canvas, redirect.js picks the visitor's saved theme -
    # and the link is always there for anyone the script doesn't forward.
    return f"""<!doctype html>
<html lang="es-MX">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<meta name="color-scheme" content="light dark" />
<title>{title} · cartelera</title>
<meta name="robots" content="noindex" />
<meta name="description" content="{description}" />
<meta property="og:type" content="website" />
<meta property="og:site_name" content="cartelera" />
<meta property="og:locale" content="es_MX" />
<meta property="og:title" content="{title}" />
<meta property="og:description" content="{description}" />
<meta property="og:url" content="{url}" />
<meta property="og:image" content="{image}" />
<meta property="og:image:alt" content="{e(alt)}" />
<meta name="twitter:card" content="{card}" />
<meta name="twitter:title" content="{title}" />
<meta name="twitter:description" content="{description}" />
<meta name="twitter:image" content="{image}" />
<link rel="icon" type="image/svg+xml" href="../../favicon.svg" />
<script src="../redirect.js" data-film="{film_id}"></script>
</head>
<body>
<p><a href="{app_link}">{title} en cartelera</a></p>
</body>
</html>
"""


def write_previews(films, out_dir=None, site=None):
    """Write docs/p/<id>/index.html for every film, and set each film's
    `preview` to match. Never raises.

    `preview` is set on the films that have a current page on disk - written
    now, or already identical - and removed from the rest. The share button
    hands out /p/<id>/ only when it's set; for any other film it falls back to
    ?film=, which always resolves. A link to a page that was never written
    would 404 for the friend, silently. Marking happens in here, inside the
    same per-film guard as the writing, so it can't break the promise either.

    A film whose page can't be built is logged and skipped, and a failure to
    even find the site's domain skips the lot: the data is what the site exists
    for, and a preview is not worth a single missed schedule.json.
    """
    out_dir = out_dir or PREVIEW_DIR
    try:
        site = site or site_url()
    except (OSError, ValueError) as exc:
        log.warning("previews skipped: %s", exc)
        site = None

    ready = 0
    written = 0
    for film in films:
        try:
            if isinstance(film, dict):
                film.pop("preview", None)
            if site is None:
                continue
            page = render_preview(film, site)
            path = os.path.join(out_dir, film["id"], "index.html")
            try:
                with open(path, "r", encoding="utf-8") as f:
                    unchanged = f.read() == page
            except (OSError, UnicodeDecodeError):
                # Missing, or corrupted (a bad merge, a run cut off mid-write).
                # Either way it gets rewritten; letting a decode error reach
                # the per-film guard would lock this film out of previews for
                # good, with the broken page still being served.
                unchanged = False
            if not unchanged:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", encoding="utf-8", newline="\n") as f:
                    f.write(page)
                written += 1
            film["preview"] = preview_path(film["id"])
            ready += 1
        except Exception as exc:  # noqa: BLE001 — per-film isolation is the point
            # Not film.get(): the entry itself may be what's malformed, and an
            # exception raised in here would escape the "never raises" promise.
            film_id = film.get("id") if isinstance(film, dict) else film
            log.warning("preview for film %r skipped: %s", film_id, exc)
    log.info("previews: %d written, %d current, %d films", written, ready, len(films))


def refresh(schedule_path=None, out_dir=None):
    """Rewrite every page from schedule.json, to see a template change locally.

    Pages only. schedule.json is read and never written: rewriting generated
    data by hand, on a checkout that may be behind, is how a stale cartelera
    gets committed or a bot run hits a rebase conflict. Nothing needs it
    anyway - the next scrape rewrites every page whose bytes changed and marks
    `preview` itself. A bad schedule.json leaves docs/p untouched: it is
    validated first, as in run().
    """
    import scrape  # here, not at the top: scrape imports this module

    schedule_path = schedule_path or SCHEDULE_PATH
    with open(schedule_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    scrape.validate_schedule(data)
    write_previews(data["films"], out_dir)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    refresh()
