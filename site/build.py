"""Render the Exo landing page from content/*.json + templates/ into dist/.
    python site/build.py            draft (TODO(konrad) allowed, noindex)
    python site/build.py --release  refuses while any slot is unfilled"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

TODO = "TODO(konrad)"
PAGES = ("index.html", "receipt.html", "terms.html")


def load_content(root: Path) -> dict:
    c = root / "content"
    return {k: json.loads((c / f"{k}.json").read_text()) for k in ("copy", "features", "tiers")}


def todo_slots(node, path="") -> list[str]:
    if isinstance(node, str):
        return [path] if TODO in node else []
    if isinstance(node, dict):
        return [s for k, v in node.items() for s in todo_slots(v, f"{path}.{k}" if path else k)]
    if isinstance(node, list):
        return [s for i, v in enumerate(node) for s in todo_slots(v, f"{path}[{i}]")]
    return []


def _tier_problems(tiers: list) -> list[str]:
    ids = [t.get("tier") for t in tiers]
    if any(not isinstance(i, int) or isinstance(i, bool) or not 1 <= i <= 255 for i in ids) or len(set(ids)) != len(ids):
        return ["tiers[].tier must be unique integers 1-255"]
    return []


def build(root: Path, out: Path, release: bool = False) -> list[Path]:
    content = load_content(root)
    if release:
        problems = [s.replace("copy.", "", 1) for s in todo_slots(content)] + _tier_problems(content["tiers"])
        if problems:
            raise SystemExit("release blocked, fill these first: " + ", ".join(problems))
    env = Environment(loader=FileSystemLoader(root / "templates"), autoescape=select_autoescape(["html", "j2"]))
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for page in PAGES:
        (out / page).write_text(env.get_template(page + ".j2").render(**content, release=release))
        written.append(out / page)
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
