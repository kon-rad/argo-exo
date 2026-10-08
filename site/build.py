"""Render the Exo landing page from content/*.json + templates/ into dist/,
and the blog from content/blog/*.md into dist/blog/.
    python site/build.py            draft (TODO(konrad) allowed, noindex)
    python site/build.py --release  refuses while any slot is unfilled"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from datetime import date
from pathlib import Path

import markdown
from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup

TODO = "TODO(konrad)"
_TODO_RE = re.compile(re.escape(TODO), re.IGNORECASE)
PAGES = ("index.html",)
# The pre-order pages (receipt.html, terms.html) and checkout scripts stay in the repo but aren't published
# while the site runs a waitlist instead of a sale. Add them back here when sales open.
DORMANT_PAGES = ("receipt.html", "terms.html")
POST_FIELDS = ("title", "slug", "date", "summary")
_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def load_posts(root: Path) -> list[dict]:
    """content/blog/*.md, newest first. Each file opens with a front-matter block of `key: value` lines between
    `---` fences (title, slug, date as YYYY-MM-DD, summary, optional order); the rest is Markdown."""
    posts = []
    for p in sorted((root / "content" / "blog").glob("*.md")):
        text = p.read_text(encoding="utf-8")
        m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.S)
        if not m:
            raise SystemExit(f"{p.name}: missing the --- front-matter block")
        meta = dict(line.split(":", 1) for line in m[1].splitlines() if line.strip())
        meta = {k.strip(): v.strip() for k, v in meta.items()}
        missing = [f for f in POST_FIELDS if not meta.get(f)]
        if missing:
            raise SystemExit(f"{p.name}: front matter lacks {', '.join(missing)}")
        if not _SLUG_RE.match(meta["slug"]):
            raise SystemExit(f"{p.name}: slug must be lowercase kebab-case")
        meta["date"] = date.fromisoformat(meta["date"])
        meta["order"] = int(meta.get("order", 0))
        meta["html"] = Markup(markdown.markdown(m[2], extensions=["tables", "fenced_code", "sane_lists"]))
        posts.append(meta)
    slugs = [p["slug"] for p in posts]
    if len(set(slugs)) != len(slugs):
        raise SystemExit("two blog posts share a slug")
    return sorted(posts, key=lambda p: (p["date"], -p["order"]), reverse=True)


def load_content(root: Path) -> dict:
    c = root / "content"
    return {k: json.loads((c / f"{k}.json").read_text(encoding="utf-8")) for k in ("copy", "features", "tiers", "renders", "token2049")}


def todo_slots(node, path="") -> list[str]:
    """Paths of every string slot still holding TODO(konrad) (any case, anywhere in the
    string) or left empty/whitespace, through nested dicts and lists."""
    if isinstance(node, str):
        return [path] if _TODO_RE.search(node) or not node.strip() else []
    if isinstance(node, dict):
        return [s for k, v in node.items() for s in todo_slots(v, f"{path}.{k}" if path else k)]
    if isinstance(node, list):
        return [s for i, v in enumerate(node) for s in todo_slots(v, f"{path}[{i}]")]
    return []


def load_preorder(root: Path) -> dict | None:
    """static/preorder.json (Task 3's export) if present; None, or a zero contract, means not deployed."""
    p = root / "static" / "preorder.json"
    if not p.exists():
        return None
    data = json.loads(p.read_text(encoding="utf-8"))
    return data if int(data.get("contract", "0x0") or "0x0", 16) else None


def _tier_problems(tiers: list) -> list[str]:
    ids = [t.get("tier") for t in tiers]
    if any(not isinstance(i, int) or isinstance(i, bool) or not 1 <= i <= 255 for i in ids) or len(set(ids)) != len(ids):
        return ["tiers[].tier must be unique integers 1-255"]
    return []


def build(root: Path, out: Path, release: bool = False, pages: tuple = PAGES) -> list[Path]:
    content = load_content(root)
    if release:
        problems = [s.replace("copy.", "", 1) for s in todo_slots(content)] + _tier_problems(content["tiers"])
        if problems:
            raise SystemExit("release blocked, fill these first: " + ", ".join(problems))
    env = Environment(loader=FileSystemLoader(root / "templates"), autoescape=select_autoescape(["html", "j2"]))
    # ?v=<content hash> on the stylesheet and scripts, so a browser never pairs new pages with a cached old file.
    env.globals["asset_v"] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()[:10]
                              for p in (root / "static").iterdir() if p.suffix in (".css", ".mjs", ".js")}
    preorder = load_preorder(root)
    posts = load_posts(root)
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for page in pages:
        (out / page).write_text(env.get_template(page + ".j2").render(**content, posts=posts, preorder=preorder, release=release), encoding="utf-8")
        written.append(out / page)
    t2049 = out / "token2049"
    t2049.mkdir(exist_ok=True)
    (t2049 / "index.html").write_text(env.get_template("token2049.html.j2").render(**content, posts=posts, release=release), encoding="utf-8")
    written.append(t2049 / "index.html")
    blog = out / "blog"
    if blog.exists():
        shutil.rmtree(blog)
    blog.mkdir()
    pages = [(blog / "index.html", "blog_index.html.j2", {})]
    pages += [(blog / f"{p['slug']}.html", "blog_post.html.j2", {"post": p}) for p in posts]
    for path, tpl, extra in pages:
        path.write_text(env.get_template(tpl).render(**content, posts=posts, release=release, **extra), encoding="utf-8")
        written.append(path)
    if (out / "static").exists():
        shutil.rmtree(out / "static")
    shutil.copytree(root / "static", out / "static")
    return written


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--release", action="store_true")
    ap.add_argument("--out", default=str(Path(__file__).parent / "dist"))
    a = ap.parse_args(argv)
    print("\n".join(str(p) for p in build(Path(__file__).parent, Path(a.out), release=a.release)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
