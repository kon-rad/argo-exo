import re
from pathlib import Path

T = Path(__file__).resolve().parents[1] / "secondbrain" / "vault-template"


def test_para_buckets_exist_with_readmes():
    for b in ("Projects", "Areas", "Research", "Archives"):
        assert (T / b / "README.md").read_text().strip()


def test_agents_md_has_the_house_rules():
    a = (T / "AGENTS.md").read_text()
    for rule in ("markdown", "Projects/", "kebab-case", "confirmed vs. inferred", ".env"):
        assert rule in a


def test_vault_gitignore_keeps_secrets_out():
    g = (T / ".gitignore").read_text()
    assert ".env" in g and "*.db" in g


def test_template_has_no_personal_markers():
    files = [p for p in T.parent.rglob("*") if p.is_file()]
    assert files
    for p in files:
        text = p.read_text().lower()
        for marker in ("konrad", "/users/", "myargoquest", "kon-rad"):
            assert marker not in text, f"{marker} in {p}"
        assert not re.search(r"\b[\w.+-]+@[\w-]+\.\w+", text), f"email shape in {p}"
