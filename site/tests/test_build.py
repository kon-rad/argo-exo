import json
import shutil
from pathlib import Path

import pytest

import build

SITE = Path(__file__).resolve().parents[1]

# Minimal stand-ins; the real templates arrive in Task 2.
INDEX = """<html><head>{% if not release %}<meta name="robots" content="noindex">{% endif %}</head><body>
<h1>{{ copy.hero.headline }}</h1>
{% for f in features %}<section id="fig-{{ f.fig }}"><h2>{{ f.name }}</h2><p>{{ f.blurb }}</p></section>{% endfor %}
{% for t in tiers %}<div data-tier="{{ t.tier }}">{{ t.name }}{% for i in t.includes %}<li>{{ i }}</li>{% endfor %}</div>{% endfor %}
</body></html>"""
OTHER = "<html><body>{{ copy.title }}</body></html>"


@pytest.fixture
def site(tmp_path):
    root = tmp_path / "site"
    shutil.copytree(SITE / "content", root / "content")
    (root / "templates").mkdir()
    (root / "templates" / "index.html.j2").write_text(INDEX)
    (root / "templates" / "receipt.html.j2").write_text(OTHER)
    (root / "templates" / "terms.html.j2").write_text(OTHER)
    (root / "static").mkdir()
    (root / "static" / "styles.css").write_text("")
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
