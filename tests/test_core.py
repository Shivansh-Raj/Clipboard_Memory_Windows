"""Port of V1's CoreTests.swift: the same inputs must give the same results."""
import time

import pytest

from clipboard_memory.core import classifier, sensitive, transforms
from clipboard_memory.core.models import ContentKind as K, HistoryFilter as F, NewClip
from clipboard_memory.core.store import HistoryStore


@pytest.fixture
def store(tmp_path):
    s = HistoryStore(tmp_path / "data")
    yield s
    s.close()


def test_classifier_kinds():
    c = classifier.classify
    assert c("https://github.com/anthropics") == K.LINK
    assert c("www.example.com/path") == K.LINK
    assert c("someone@example.com") == K.EMAIL
    assert c('{"a": 1, "b": [1,2]}') == K.JSON
    assert c("name,age\nBob,3\nAnn,4") == K.CSV
    assert c("a\tb\n1\t2\n3\t4") == K.CSV
    assert c("def add(a, b):\n    return a + b\n") == K.CODE
    assert c("SELECT id, name FROM users WHERE id = 1") == K.CODE
    assert c("# Title\n\n- one\n- two\n\nSee [link](http://x.y)") == K.MARKDOWN
    assert c("Remember to buy milk and eggs tomorrow morning.") == K.TEXT


def test_domain():
    assert classifier.domain("https://docs.github.com/x") == "docs.github.com"
    assert classifier.domain("www.example.com/a") == "www.example.com"


def test_sensitive_flags_secrets():
    d = sensitive.detect
    assert d("AKIAIOSFODNN7EXAMPLE")
    assert d("ghp_abcdefghijklmnopqrstuvwxyz0123456789")
    assert d("-----BEGIN RSA PRIVATE KEY-----\nabc")
    assert d("password = hunter22hunter")
    assert d("4111 1111 1111 1111")
    assert d("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijk")


def test_sensitive_ignores_ordinary_text():
    d = sensitive.detect
    assert d("Let's meet at 5pm to discuss the password policy") is None
    assert d("https://github.com/anthropics/claude-code") is None
    assert d("def add(a, b):\n    return a + b") is None
    assert d("1234 5678") is None


def test_json_round_trip_keeps_order():
    src = '{"z":1,"a":[1,2,{"k":"v, \\"q\\""}],"e":{},"s":"x:y"}'
    pretty = transforms.format_json(src)
    assert pretty is not None
    assert '\n  "z": 1,\n  "a": [' in pretty
    assert '"e": {}' in pretty
    assert transforms.minify_json(pretty) == src
    assert transforms.format_json("{nope") is None


def test_sql():
    out = transforms.format_sql("select a, b from t left join u on t.id=u.id where x = 1 and y in (select 1, 2) order by a")
    assert out == "SELECT\n  a,\n  b\nFROM t\nLEFT JOIN u\n  ON t.id = u.id\nWHERE x = 1\n  AND y IN (SELECT 1, 2)\nORDER BY a"


def test_whitespace():
    assert transforms.clean_whitespace("  a   b  \n\n\n\n    c    d\t \n") == "a b\n\n    c d"


def test_save_search_dedupe(store):
    code = "def f(x):\n    return x * 2\n"
    a, a_new = store.save(NewClip(kind=K.CODE, text=code, source_app="Xcode"))
    b, b_new = store.save(NewClip(kind=K.CODE, text=code))
    assert a == b and a_new and not b_new
    store.save(NewClip(kind=K.LINK, text="https://github.com/a/b"))
    assert store.count() == 2
    assert len(store.fetch(search="github.com")) == 1
    assert store.fetch(search="return x")[0].id == a
    assert len(store.fetch(search="xcode")) == 1            # source app is searchable
    assert store.fetch(search="100%") == []                  # wildcards are escaped
    assert len(store.fetch(flt=F.LINKS)) == 1 and len(store.fetch(flt=F.CODE)) == 1
    assert store.payload(a).text == code                     # exact indentation preserved


def test_pin_favorite_tags_retention(store):
    old = time.time() - 40 * 86400
    p, _ = store.save(NewClip(kind=K.TEXT, text="keep me"), now=old)
    q, _ = store.save(NewClip(kind=K.TEXT, text="drop me"), now=old)
    store.set_pinned(p, True)
    store.set_favorite(q, True)
    store.set_tags(q, ["sql", " work "])
    assert store.entry(q).tags == ["sql", "work"]
    assert [e.id for e in store.fetch(flt=F.PINNED)] == [p]
    assert len(store.fetch(search="work")) == 1
    d, _ = store.save(NewClip(kind=K.TEXT, text="plain old"), now=old)
    assert store.purge(30, 0) == 1                      # V2: favourites are protected like pinned items
    assert store.entry(p) is not None and store.entry(q) is not None and store.entry(d) is None


def test_images_and_persistence(tmp_path):
    d = tmp_path / "data"
    s = HistoryStore(d)
    png = bytes([0x89, 0x50, 0x4E, 0x47, 1, 2, 3])
    rid, _ = s.save(NewClip(kind=K.IMAGE, image_png=png, image_width=10, image_height=5))
    s.close()
    again = HistoryStore(d)                                   # reopened: history persists
    e = again.entry(rid)
    assert e.kind == K.IMAGE and e.image_width == 10
    with open(again.payload(rid).image_path, "rb") as fh:
        assert fh.read() == png
    again.delete([rid])
    assert list(again.images_dir.iterdir()) == []
    again.close()


def test_clear_keeps_pinned(store):
    a, _ = store.save(NewClip(kind=K.TEXT, text="a"))
    store.save(NewClip(kind=K.TEXT, text="b"))
    store.set_pinned(a, True)
    store.clear(keep_protected=True)
    assert store.count() == 1
    store.clear(keep_protected=False)
    assert store.count() == 0


def test_many_entries_search_is_fast(store):
    for i in range(5000):
        store.save(NewClip(kind=K.TEXT, text=f"entry number {i} lorem ipsum dolor sit amet {i * 7}"))
    t = time.time()
    r = store.fetch(search="number 4999")
    assert len(r) == 1
    assert time.time() - t < 0.5
    assert len(store.fetch(limit=100, offset=100)) == 100


def test_corrupt_database_is_moved_aside(tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    (d / "history.sqlite").write_bytes(b"this is not a database" * 100)
    s = HistoryStore(d)
    assert s.count() == 0
    assert any(p.name.startswith("history.sqlite.corrupt-") for p in d.iterdir())
    s.close()


def test_image_ocr_state(store):
    png = b"\x89PNGfake"
    i, _ = store.save(NewClip(kind=K.IMAGE, image_png=png, image_width=2, image_height=2, ocr_pending=True))
    assert store.pending_ocr_ids() == [i]
    store.set_ocr(i, "Invoice 4821", "done")
    assert store.pending_ocr_ids() == []
    assert store.fetch(search="invoice 4821")[0].id == i       # OCR text is searchable


def test_favourites_survive_clear_and_retention(store):
    old = time.time() - 400 * 86400
    p, _ = store.save(NewClip(kind=K.TEXT, text="pinned"), now=old)
    f, _ = store.save(NewClip(kind=K.TEXT, text="favourite"), now=old)
    x, _ = store.save(NewClip(kind=K.TEXT, text="plain"), now=old)
    store.set_pinned(p, True)
    store.set_favorite(f, True)
    assert store.protected_count() == 2
    assert store.purge(30, 0) == 1 and store.entry(x) is None           # age limit spares both
    for i in range(5):
        store.save(NewClip(kind=K.TEXT, text=f"newer {i}"))
    store.purge(0, 1)                                                   # count limit spares both
    assert store.entry(p) and store.entry(f)
    store.clear(keep_protected=True)
    assert store.count() == 2 and store.entry(p) and store.entry(f)
