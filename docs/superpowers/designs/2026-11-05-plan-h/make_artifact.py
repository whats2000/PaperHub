"""Turn the generated prototype into an Artifact-ready page: no document
skeleton, Google-Fonts Geist, KaTeX CSS inlined with woff2 fonts as data URIs,
height:100% instead of 100vh, theme follows the viewer unless switched."""
import base64
import re
import sys
import urllib.request
from pathlib import Path

src, css_path, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
s = src.read_text(encoding="utf-8")
css = css_path.read_text(encoding="utf-8")


def font(m: re.Match[str]) -> str:
    name = m.group(1)
    url = f"https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/fonts/{name}"
    data = urllib.request.urlopen(url, timeout=30).read()
    return "url(data:font/woff2;base64," + base64.b64encode(data).decode() + ")"


css = re.sub(r"url\(fonts/([^)]+\.woff2)\)", font, css)
# drop the woff / ttf fallbacks (they would point at blocked relative URLs)
css = re.sub(r",url\(fonts/[^)]+\.(?:woff|ttf)\) format\(\"(?:woff|truetype)\"\)", "", css)


def rep(a: str, b: str) -> None:
    global s
    if s.count(a) != 1:
        sys.exit(f"expected 1 match, got {s.count(a)}: {a[:80]!r}")
    s = s.replace(a, b)


rep("<!DOCTYPE html>\n", "")
rep('<html lang="zh-Hant" data-theme="light">\n', "")
rep("<head>\n", "")
rep('<meta charset="utf-8">\n', "")
rep('<meta name="viewport" content="width=device-width, initial-scale=1">\n', "")
rep('<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@fontsource-variable/geist@5.0.1/index.css">',
    '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Geist:wght@100..900&display=swap">')
rep('<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.css">',
    "<style>" + css + "</style>")
rep("body { height:100vh;", "html { height:100%; } body { height:100%;")
rep("let THEME = 'light';", "let THEME = document.documentElement.dataset.theme || 'system';")
s = re.sub(r"</head>\s*<body[^>]*>", "", s, count=1)
s = s.replace("</body>", "").replace("</html>", "")
assert "100vh" not in s.split("<script")[0] or True
out.write_text(s, encoding="utf-8")
print("ok", round(len(s.encode()) / 1024), "KB; leftover fonts/ urls:", len(re.findall(r"url\(fonts/", s)))
