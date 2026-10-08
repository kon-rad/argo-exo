"""QR codes for the TOKEN2049 slides and site: SVG + PNG into docs/qr/, SVG into site/static/img/qr/.
    pip install segno && python scripts/make_qr.py docs/qr site/static/img/qr
The list lives here; site/content/token2049.json carries the same entries for the /token2049 page."""
import json, sys, segno
out_docs, out_site = sys.argv[1], sys.argv[2]
G = "https://github.com/kon-rad/argo-exo"
CODES = [
  ("website", "Website", "https://exo.myargoquest.com"),
  ("token2049", "TOKEN2049 page", "https://exo.myargoquest.com/token2049/"),
  ("github", "GitHub repo", G),
  ("video", "Demo video", "https://www.youtube.com/watch?v=yyxsdwOJtts"),
  ("slides", "Slide deck", "https://docs.google.com/presentation/d/1jMS6c9_Qkjl9TIvXBBCmAikPJLPxmV9X_ygEas1I3mg/edit?usp=sharing"),
  ("cre-evidence", "Chainlink CRE evidence", G + "/blob/main/docs/hackathon-evidence.md#chainlink-cre-the-transaction-guardian-workflow"),
  ("cre-runs", "CRE simulator runs (raw output)", G + "/tree/main/docs/evidence/cre"),
  ("nownodes-evidence", "NOWNodes evidence", G + "/blob/main/docs/hackathon-evidence.md#nownodes-which-endpoint-powers-what"),
  ("nownodes-live-check", "NOWNodes live check (raw output)", G + "/blob/main/docs/evidence/nownodes/live-check.txt"),
]
for slug, name, url in CODES:
    q = segno.make(url, error="m")
    for d in (out_docs, out_site):
        q.save(f"{d}/{slug}.svg", scale=8, border=2, dark="#000", light="#fff")
    q.save(f"{out_docs}/{slug}.png", scale=16, border=2)
json.dump([{"slug": s, "name": n, "url": u} for s, n, u in CODES], open(f"{out_docs}/codes.json", "w"), indent=2)
print(len(CODES))
