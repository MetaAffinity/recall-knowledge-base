"""
One-time setup: downloads the UI libraries and fonts into static/vendor/
so Recall's interface loads instantly and works fully offline.

Run once (needs internet):    python get_vendor.py
Re-run any time to refresh the files.
"""

import re
import urllib.request
from pathlib import Path

VENDOR = Path(__file__).resolve().parent / "static" / "vendor"
FONTS_DIR = VENDOR / "fonts"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0 Safari/537.36")

FILES = {
    "tailwind.js": "https://cdn.tailwindcss.com",
    "gsap.min.js": "https://cdnjs.cloudflare.com/ajax/libs/gsap/3.12.5/gsap.min.js",
    "lucide.min.js": "https://unpkg.com/lucide@0.469.0/dist/umd/lucide.min.js",
}

FONTS_CSS_URL = ("https://fonts.googleapis.com/css2"
                 "?family=Bricolage+Grotesque:wght@500;600;700;800"
                 "&family=Inter:wght@400;500;600"
                 "&family=JetBrains+Mono:wght@400;500;700&display=swap")


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def main():
    VENDOR.mkdir(parents=True, exist_ok=True)
    FONTS_DIR.mkdir(parents=True, exist_ok=True)

    for name, url in FILES.items():
        print(f"downloading {name} ...")
        (VENDOR / name).write_bytes(fetch(url))

    print("downloading fonts ...")
    css = fetch(FONTS_CSS_URL).decode("utf-8")
    urls = sorted(set(re.findall(r"url\((https://[^)]+)\)", css)))
    for i, url in enumerate(urls):
        fname = f"font-{i}.woff2"
        (FONTS_DIR / fname).write_bytes(fetch(url))
        css = css.replace(url, f"fonts/{fname}")
    (VENDOR / "fonts.css").write_text(css, encoding="utf-8")

    print(f"\nDone — {len(FILES)} libraries + {len(urls)} font files saved to "
          f"{VENDOR}\nStart the app with: python app.py")


if __name__ == "__main__":
    main()
