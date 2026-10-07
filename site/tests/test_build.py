import json
import shutil
from pathlib import Path

import pytest

import build

SITE = Path(__file__).resolve().parents[1]

@pytest.fixture
def site(tmp_path):
    root = tmp_path / "site"
    for d in ("content", "templates", "static"):
        shutil.copytree(SITE / d, root / d)
    return root


def fill(root):
    for name in ("copy.json", "features.json", "tiers.json"):
        p = root / "content" / name
        p.write_text(p.read_text().replace("TODO(konrad)", "Filled <b>by</b> Konrad"))


def test_draft_build_keeps_todos(site, tmp_path):
    build.build(site, tmp_path / "dist")
    assert "TODO(konrad)" in (tmp_path / "dist" / "index.html").read_text()


def test_release_refuses_todos_and_names_them(site, tmp_path):
    with pytest.raises(SystemExit) as e:
        build.build(site, tmp_path / "dist", release=True)
    assert "hero.headline" in str(e.value) and "tiers[0].name" in str(e.value)


def test_release_lists_every_feature_and_tier_and_escapes(site, tmp_path):
    fill(site)
    build.build(site, tmp_path / "dist", release=True)
    html = (tmp_path / "dist" / "index.html").read_text()
    for f in json.loads((site / "content" / "features.json").read_text()):
        assert f'id="fig-{f["fig"]}"' in html
    assert 'data-tier="1"' in html and 'data-tier="2"' in html
    assert "Filled &lt;b&gt;by&lt;/b&gt; Konrad" in html
    for page in ("terms.html", "receipt.html"):
        assert (tmp_path / "dist" / page).exists()
    assert 'name="robots"' not in html


def test_tier_ids_must_be_unique_positive_ints(site, tmp_path):
    fill(site)
    t = json.loads((site / "content" / "tiers.json").read_text())
    t[1]["tier"] = t[0]["tier"]
    (site / "content" / "tiers.json").write_text(json.dumps(t))
    with pytest.raises(SystemExit, match="tier"):
        build.build(site, tmp_path / "dist", release=True)


def test_todo_slots_walks_nested_lists_and_dicts():
    node = {"a": {"b": [{"c": "ok"}, {"c": "TODO(konrad)"}], "d": ["x", "TODO(konrad)"]}, "e": 3, "f": None}
    assert build.todo_slots(node) == ["a.b[1].c", "a.d[1]"]


def test_todo_slots_matches_any_case_and_embedded():
    node = {"a": "Ships in todo(Konrad) weeks", "b": "TODO(KONRAD)", "c": "Filled", "d": "TODO later"}
    assert build.todo_slots(node) == ["a", "b"]


def test_todo_slots_flags_empty_and_blank_strings():
    assert build.todo_slots({"a": "", "b": "   ", "c": [""], "d": "x"}) == ["a", "b", "c[0]"]


def test_release_refuses_an_emptied_slot(site, tmp_path):
    fill(site)
    c = json.loads((site / "content" / "copy.json").read_text(encoding="utf-8"))
    c["colophon"] = ""
    (site / "content" / "copy.json").write_text(json.dumps(c), encoding="utf-8")
    with pytest.raises(SystemExit, match="colophon"):
        build.build(site, tmp_path / "dist", release=True)


def test_draft_renders_the_volume(site, tmp_path):
    build.build(site, tmp_path / "dist")
    html = (tmp_path / "dist" / "index.html").read_text(encoding="utf-8")
    for key in ("features", "kit", "preorder", "faq"):
        assert f'id="{key}"' in html and f'href="#{key}"' in html
    assert html.count("<h1") == 1
    assert "Fig. 3. On-command camera and memos." in html
    assert "Plate II." in html and "Plate III." in html
    # no live price yet: every buy button is disabled, prices show the closed state
    assert html.count("data-buy=") == html.count("disabled>") == 2
    assert html.count("Subscriptions open shortly") == 2
    for f in json.loads((site / "content" / "features.json").read_text(encoding="utf-8")):
        assert (tmp_path / "dist" / "static" / "img" / "plates" / f"{f['id']}.svg").exists()
    assert (tmp_path / "dist" / "static" / "styles.css").exists()


def test_every_img_has_an_alt(site, tmp_path):
    import re
    build.build(site, tmp_path / "dist")
    for page in ("index.html", "receipt.html", "terms.html"):
        for tag in re.findall(r"<img\b[^>]*>", (tmp_path / "dist" / page).read_text(encoding="utf-8")):
            assert "alt=" in tag, tag


def test_terms_show_contract_only_once_deployed(site, tmp_path):
    build.build(site, tmp_path / "dist")
    assert "basescan" not in (tmp_path / "dist" / "terms.html").read_text(encoding="utf-8")
    addr = "0x" + "ab" * 20
    (site / "static" / "preorder.json").write_text(json.dumps({"contract": addr, "explorer": "https://basescan.org"}))
    build.build(site, tmp_path / "dist")
    assert f"https://basescan.org/address/{addr}" in (tmp_path / "dist" / "terms.html").read_text(encoding="utf-8")
