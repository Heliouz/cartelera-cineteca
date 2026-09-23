"""Per-film preview pages - offline, no network."""
import json
import os
import re
from datetime import date
from html.parser import HTMLParser

import pytest

import parsing
import previews
import scrape
from test_parse import load_fixture

SITE = "https://cartelera.example/"
POSTER = (
    "https://rbvfcn.cinetecanacional.net/CDN/media/entity/get/FilmPosterGraphic/"
    "HO00009426?referenceScheme=Cinema&allowPlaceHolder"
)
THUMB = POSTER.replace("&allowPlaceHolder", "&allowPlaceHolder=") + "&width=200"
REDIRECT_JS = os.path.join(previews.DOCS_DIR, "p", "redirect.js")


def film(**overrides):
    base = {
        "id": "HO00009426",
        "title": "Memorias de un cuerpo que arde",
        "director": "Antonella Sudasassi Furniss",
        "country": "Costa Rica-España",
        "year": 2024,
        "poster": POSTER,
    }
    base.update(overrides)
    return base


class _Page(HTMLParser):
    """What a link unfurler sees: the tags, and no script execution."""

    def __init__(self):
        super().__init__()
        self.tags = {}
        self.title = None
        self.scripts = []
        self.links = []
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "meta":
            key = a.get("property") or a.get("name")
            if key:
                self.tags[key] = a.get("content")
        elif tag == "script":
            self.scripts.append(a)
        elif tag == "a":
            self.links.append(a.get("href"))
        self._in_title = tag == "title"

    def handle_data(self, data):
        if self._in_title:
            self.title = data

    def handle_endtag(self, tag):
        self._in_title = False


def parse(page):
    p = _Page()
    p.feed(page)
    return p


# ---------- the page a crawler reads ----------

def test_preview_carries_the_film_and_its_poster():
    m = parse(previews.render_preview(film(), SITE))
    assert m.tags["og:title"] == "Memorias de un cuerpo que arde"
    assert m.tags["og:image"] == THUMB
    assert m.tags["twitter:image"] == THUMB
    # No declared size: WhatsApp measures the image itself, and a declared
    # size would be a guess for whatever Vista returns for a film with no art.
    assert "og:image:width" not in m.tags and "og:image:height" not in m.tags
    assert m.tags["og:image:alt"] == "póster de Memorias de un cuerpo que arde"
    assert m.tags["og:url"] == SITE + "p/HO00009426/"
    assert m.tags["og:description"] == (
        "Antonella Sudasassi Furniss · Costa Rica-España · 2024 — en la Cineteca Nacional"
    )
    # A portrait poster: the small-image card, not the wide banner.
    assert m.tags["twitter:card"] == "summary"


def test_preview_escapes_what_it_did_not_write():
    """Titles come from a third-party page. A quote must not end the attribute
    and a tag must not become markup."""
    page = previews.render_preview(film(title='Él dijo "<script>x</script>" & más'), SITE)
    assert "<script>x" not in page
    m = parse(page)
    assert m.tags["og:title"] == 'Él dijo "<script>x</script>" & más'
    assert m.title == 'Él dijo "<script>x</script>" & más · cartelera'
    assert m.tags["og:image:alt"] == 'póster de Él dijo "<script>x</script>" & más'


def test_preview_hands_people_to_the_shared_redirect_and_keeps_a_link():
    m = parse(previews.render_preview(film(), SITE))
    # Forwarding lives in one shared file, so a fix to it reaches every page
    # ever written, including films no scrape will rewrite again.
    assert m.scripts == [{"src": "../redirect.js", "data-film": "HO00009426"}]
    # Always there - not in <noscript> - so anyone the script doesn't forward
    # still has something to tap.
    assert m.links == ["../../?film=HO00009426"]
    assert "<noscript>" not in previews.render_preview(film(), SITE)


def test_preview_has_no_colors_of_its_own():
    """The palette lives in style.css alone."""
    page = previews.render_preview(film(), SITE)
    assert '<meta name="color-scheme" content="light dark" />' in page
    assert not re.search(r"#[0-9a-fA-F]{3,6}\b", page)


@pytest.mark.parametrize("poster", [None, "", "http://insecure.example/p.jpg", "javascript:x"])
def test_preview_without_a_usable_poster_falls_back_to_the_site_card(poster):
    m = parse(previews.render_preview(film(poster=poster), SITE))
    assert m.tags["og:image"] == SITE + "og.png"
    # og.png is a wide banner and isn't a poster, as on index.html.
    assert m.tags["twitter:card"] == "summary_large_image"
    assert not m.tags["og:image:alt"].startswith("póster")


def test_preview_description_without_details():
    m = parse(previews.render_preview(film(director=None, country="", year=None), SITE))
    assert m.tags["og:description"] == "en la Cineteca Nacional"


def test_preview_is_deterministic():
    """Same film in, same bytes out - or every scrape would rewrite every page."""
    assert previews.render_preview(film(), SITE) == previews.render_preview(film(), SITE)


@pytest.mark.parametrize("bad", ["../escape", "a/b", "a\\b", "HO1\n", "", "x" * 65, None, 123])
def test_preview_refuses_an_id_that_is_not_a_plain_token(bad):
    with pytest.raises((ValueError, TypeError)):
        previews.render_preview(film(id=bad), SITE)


# ---------- the thumbnail ----------

def test_thumbnail_url_asks_vista_for_the_thumbnail_width():
    """Under ~300px wide, WhatsApp draws the small square thumbnail beside
    the text rather than a banner above it."""
    assert previews.thumbnail_url(POSTER) == THUMB


def test_thumbnail_url_keeps_other_parameters_and_replaces_a_width():
    assert previews.thumbnail_url("https://x.example/p.jpg") == "https://x.example/p.jpg?width=200"
    assert (previews.thumbnail_url("https://x.example/p.jpg?width=300&a=1")
            == "https://x.example/p.jpg?a=1&width=200")


# ---------- writing them, and saying so ----------

def test_write_previews_writes_a_page_and_marks_the_film(tmp_path):
    films = [film(), film(id="HO2", title="Otra")]
    previews.write_previews(films, str(tmp_path), SITE)
    assert [f["preview"] for f in films] == ["p/HO00009426/", "p/HO2/"]
    page = (tmp_path / "HO2" / "index.html").read_text(encoding="utf-8")
    assert parse(page).tags["og:title"] == "Otra"


def test_write_previews_leaves_unchanged_pages_alone(tmp_path):
    page = tmp_path / "HO00009426" / "index.html"
    previews.write_previews([film()], str(tmp_path), SITE)
    os.utime(page, ns=(0, 0))
    again = [film()]
    previews.write_previews(again, str(tmp_path), SITE)
    # Unchanged: not rewritten, but still marked as having a page.
    assert page.stat().st_mtime_ns == 0
    assert again[0]["preview"] == "p/HO00009426/"
    previews.write_previews([film(title="Nuevo título")], str(tmp_path), SITE)
    assert page.stat().st_mtime_ns != 0


def test_write_previews_uses_lf_line_endings(tmp_path):
    """Written on Windows too, when regenerated by hand; CRLF would show up as
    a whole-file diff against what the Linux scraper commits."""
    previews.write_previews([film()], str(tmp_path), SITE)
    assert b"\r" not in (tmp_path / "HO00009426" / "index.html").read_bytes()


def test_write_previews_rewrites_a_corrupted_page(tmp_path):
    """A page that isn't valid UTF-8 (a bad merge, a write cut off mid-way)
    must be rewritten, not skipped - skipping would lock the film out of
    previews for good while the broken page kept being served."""
    page = tmp_path / "HO00009426" / "index.html"
    page.parent.mkdir()
    page.write_bytes(b"<html>\xe2\x80")  # truncated mid-character
    films = [film()]
    previews.write_previews(films, str(tmp_path), SITE)
    assert films[0]["preview"] == "p/HO00009426/"
    assert parse(page.read_text(encoding="utf-8")).tags["og:title"] == film()["title"]


def test_write_previews_skips_a_bad_film_and_unmarks_it(tmp_path):
    films = [film(id="../evil", preview="p/../evil/"), film()]
    previews.write_previews(films, str(tmp_path), SITE)
    assert "preview" not in films[0]
    assert films[1]["preview"] == "p/HO00009426/"
    assert sorted(os.listdir(tmp_path)) == ["HO00009426"]
    assert not (tmp_path.parent / "evil").exists()


@pytest.mark.parametrize("junk", [None, "HO1", 42, ["HO1"]])
def test_write_previews_survives_an_entry_that_is_not_a_film(tmp_path, junk):
    """The "never raises" promise has to hold even when the logging of a
    failure is itself handed something odd."""
    films = [junk, film()]
    previews.write_previews(films, str(tmp_path), SITE)
    assert films[1]["preview"] == "p/HO00009426/"


def test_write_previews_writes_and_marks_nothing_when_the_domain_is_unknown(tmp_path, monkeypatch):
    def no_domain():
        raise OSError("no CNAME")

    monkeypatch.setattr(previews, "site_url", no_domain)
    films = [film(preview="p/HO00009426/")]
    previews.write_previews(films, str(tmp_path / "p"))
    assert not (tmp_path / "p").exists()
    # A stale claim from an earlier run is withdrawn, not left pointing at a
    # page this run can't vouch for.
    assert "preview" not in films[0]


# ---------- the domain ----------

def test_site_url_reads_the_cname(tmp_path):
    cname = tmp_path / "CNAME"
    cname.write_text("cartelera.example\n", encoding="utf-8")
    assert previews.site_url(str(cname)) == "https://cartelera.example/"


@pytest.mark.parametrize("contents", ["", "   \n", "https://x.example/", "a b"])
def test_site_url_rejects_anything_but_a_bare_domain(tmp_path, contents):
    cname = tmp_path / "CNAME"
    cname.write_text(contents, encoding="utf-8")
    with pytest.raises(ValueError):
        previews.site_url(str(cname))


def test_the_repo_cname_is_readable():
    assert previews.site_url().startswith("https://")


# ---------- who redirect.js sends on ----------

def _crawler_re():
    """The pattern redirect.js actually runs, read out of the file."""
    with open(REDIRECT_JS, encoding="utf-8") as f:
        m = re.search(r"var CRAWLER_UA_RE = /(.+?)/i;", f.read())
    assert m, "CRAWLER_UA_RE not found in docs/p/redirect.js"
    return re.compile(m.group(1), re.I)


UAS_THAT_STAY = {
    # iMessage runs the page's JavaScript while it builds a preview.
    "imessage": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_11_1) AppleWebKit/601.2.4 "
                "(KHTML, like Gecko) Version/9.0.1 Safari/601.2.4 facebookexternalhit/1.1 "
                "Facebot Twitterbot/1.0",
    "whatsapp": "WhatsApp/2.24.6.77 A",
    "facebook": "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)",
    "telegram": "TelegramBot (like TwitterBot)",
    "discord": "Mozilla/5.0 (compatible; Discordbot/2.0; +https://discordapp.com)",
    "slack": "Slackbot-LinkExpanding 1.0 (+https://api.slack.com/robots)",
    "pinterest-crawler": "Pinterestbot/1.0 (+http://www.pinterest.com/bot.html)",
}
UAS_THAT_GO = {
    "iphone": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 "
              "(KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
    "android": "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36",
    # Real phones and in-app browsers that a broader pattern would catch.
    "cubot": "Mozilla/5.0 (Linux; Android 10; CUBOT X30) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36",
    "pinterest-app": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) "
                     "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 [Pinterest/iOS]",
    "telegram-app": "Mozilla/5.0 (Linux; Android 13; SM-A536E) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36 Telegram-Android/10.14.5",
    "facebook-app": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) "
                    "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 "
                    "[FBAN/FBIOS;FBAV/470.0.0.0;FBBV/1;FBDV/iPhone15,2]",
}


@pytest.mark.parametrize("name", sorted(UAS_THAT_STAY))
def test_link_previewers_are_not_sent_on(name):
    assert _crawler_re().search(UAS_THAT_STAY[name])


@pytest.mark.parametrize("name", sorted(UAS_THAT_GO))
def test_people_are_sent_on(name):
    assert not _crawler_re().search(UAS_THAT_GO[name])


# ---------- refresh(): the by-hand path ----------

def _schedule(films):
    return {"days": ["2026-08-26"], "sedes": {"001": {}}, "films": films}


def test_refresh_rewrites_pages_and_never_the_schedule(tmp_path):
    """Pages only: rewriting generated data by hand, on a checkout that may be
    behind, is how a stale cartelera gets committed. The next scrape marks
    `preview` on its own."""
    schedule = tmp_path / "schedule.json"
    original = json.dumps(_schedule([
        {"id": "HO1", "title": "A", "poster": POSTER, "official_url": "x", "showtimes": []},
        {"id": "HO 2", "title": "B", "poster": POSTER, "official_url": "x", "showtimes": []},
    ]))
    schedule.write_text(original, encoding="utf-8")
    previews.refresh(str(schedule), str(tmp_path / "p"))
    assert schedule.read_text(encoding="utf-8") == original
    assert sorted(os.listdir(tmp_path / "p")) == ["HO1"]


def test_refresh_touches_nothing_when_the_schedule_is_invalid(tmp_path):
    schedule = tmp_path / "schedule.json"
    original = json.dumps(_schedule([{"id": "HO1", "title": "A"}]))  # missing keys
    schedule.write_text(original, encoding="utf-8")
    with pytest.raises(scrape.ScheduleInvalid):
        previews.refresh(str(schedule), str(tmp_path / "p"))
    assert schedule.read_text(encoding="utf-8") == original
    assert not (tmp_path / "p").exists()


# ---------- inside run() ----------

@pytest.fixture
def days():
    return parsing.build_day_window(date(2026, 8, 21))


def _fake_run(monkeypatch, tmp_path, days):
    detail_html = load_fixture("detail_two_sede.html")
    film_refs = {f"HO0000{i}": "001,002" for i in range(3)}
    out_path = tmp_path / "schedule.json"
    monkeypatch.setattr(scrape, "get_day_window", lambda: days)
    monkeypatch.setattr(scrape, "get_film_refs", lambda d: film_refs)
    monkeypatch.setattr(scrape, "fetch_film_detail_html", lambda fid, c: detail_html)
    monkeypatch.setattr(scrape, "load_previous", lambda: None)
    monkeypatch.setattr(scrape, "DATA_PATH", str(out_path))
    monkeypatch.setattr(scrape, "attach_letterboxd", lambda films, previous: None)
    # Every fake film shares one fixture's session ids.
    monkeypatch.setattr(scrape, "validate_schedule", lambda data: None)
    return out_path


def test_run_writes_a_preview_for_every_film_and_says_so(monkeypatch, tmp_path, days):
    out_path = _fake_run(monkeypatch, tmp_path, days)
    scrape.run()
    with open(out_path, encoding="utf-8") as f:
        films = json.load(f)["films"]
    assert sorted(os.listdir(previews.PREVIEW_DIR)) == sorted(f["id"] for f in films)
    assert all(f["preview"] == f"p/{f['id']}/" for f in films)


def test_run_validates_the_preview_it_publishes(monkeypatch, tmp_path, days):
    """`preview` is set after the first validation, and shareUrl() uses it
    verbatim, so the gate has to run again on what actually gets written."""
    _fake_run(monkeypatch, tmp_path, days)
    seen = []
    monkeypatch.setattr(
        scrape, "validate_schedule",
        lambda data: seen.append([f.get("preview") for f in data["films"]]),
    )
    scrape.run()
    assert len(seen) == 2
    assert all(p is None for p in seen[0])
    assert all(p is not None for p in seen[1])


def test_a_bad_preview_stops_the_run_before_the_data_is_written(monkeypatch, tmp_path, days):
    out_path = _fake_run(monkeypatch, tmp_path, days)
    monkeypatch.setattr(previews, "preview_path", lambda film_id: "https://evil.example/")
    # The real gate, minus the duplicate-session check the fake films trip.
    monkeypatch.setattr(
        scrape, "validate_schedule",
        lambda data: [
            scrape._check(f.get("preview") in (None, f"p/{f['id']}/"), "bad preview")
            for f in data["films"]
        ],
    )
    with pytest.raises(SystemExit):
        scrape.run()
    assert not out_path.exists()


def test_a_broken_preview_never_costs_the_schedule(monkeypatch, tmp_path, days):
    out_path = _fake_run(monkeypatch, tmp_path, days)

    def boom(film, site):
        raise RuntimeError("renderer broke")

    monkeypatch.setattr(previews, "render_preview", boom)
    scrape.run()
    with open(out_path, encoding="utf-8") as f:
        films = json.load(f)["films"]
    # The schedule is published, and no film claims a page it doesn't have -
    # so the share button falls back to ?film= instead of a 404.
    assert films and all("preview" not in f for f in films)


def test_an_aborted_run_writes_no_previews(monkeypatch, tmp_path, days):
    _fake_run(monkeypatch, tmp_path, days)
    monkeypatch.setattr(scrape, "fetch_film_detail_html", lambda fid, c: "<html>corrupted</html>")
    with pytest.raises(SystemExit):
        scrape.run()
    assert not os.path.exists(previews.PREVIEW_DIR)


def test_a_run_that_fails_validation_writes_no_previews(monkeypatch, tmp_path, days):
    _fake_run(monkeypatch, tmp_path, days)

    def invalid(data):
        raise scrape.ScheduleInvalid("nope")

    monkeypatch.setattr(scrape, "validate_schedule", invalid)
    with pytest.raises(SystemExit):
        scrape.run()
    assert not os.path.exists(previews.PREVIEW_DIR)


# ---------- the last gate ----------

def _one_film_schedule(preview):
    return _schedule([{"id": "HO1", "title": "A", "poster": "x", "official_url": "x",
                       "showtimes": [], "preview": preview}])


def test_validate_schedule_accepts_a_film_pointing_at_its_own_page():
    scrape.validate_schedule(_one_film_schedule("p/HO1/"))
    scrape.validate_schedule(_one_film_schedule(None))


@pytest.mark.parametrize("wrong", ["p/HO2/", "https://evil.example/", "../p/HO1/", ""])
def test_validate_schedule_rejects_a_preview_that_is_not_the_films_own_page(wrong):
    """The frontend hands `preview` to the share sheet verbatim."""
    with pytest.raises(scrape.ScheduleInvalid):
        scrape.validate_schedule(_one_film_schedule(wrong))


def test_write_schedule_is_lf_and_leaves_no_temp_file(tmp_path):
    path = tmp_path / "data" / "schedule.json"
    scrape.write_schedule({"a": "ñ"}, str(path))
    assert path.read_bytes() == '{\n  "a": "ñ"\n}\n'.encode("utf-8")
    assert not (tmp_path / "data" / "schedule.json.tmp").exists()
