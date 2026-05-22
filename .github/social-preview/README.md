# Social preview

GitHub repo social-preview card (the image shown when the repo is shared on
GitHub / Slack / X). 1280×640 — GitHub's recommended size.

| File | What |
| --- | --- |
| `card.html` | Source layout (themeable: `?theme=dark`). Renders with the Geist webfont from the frontend deps. |
| `mark.svg` | The PaperHub mark: a page with one highlighted **cited line** tracing down to a **provenance node** — "every cited sentence traces back to its source." Same art as `frontend/public/favicon.svg`. |
| `paperhub-card-light.png` | Light variant (rendered). |
| `paperhub-card-dark.png` | Dark variant (rendered). |

## Regenerate the PNGs

Requires `frontend/node_modules` installed (for the Geist font) and Chrome.

```powershell
$chrome = "C:\Program Files\Google\Chrome\Application\chrome.exe"
$dir    = "$PWD\.github\social-preview"
& $chrome --headless --disable-gpu --hide-scrollbars --force-device-scale-factor=1 `
  --window-size=1280,640 --screenshot="$dir\paperhub-card-light.png" "file:///$dir/card.html"
& $chrome --headless --disable-gpu --hide-scrollbars --force-device-scale-factor=1 `
  --window-size=1280,640 --screenshot="$dir\paperhub-card-dark.png" "file:///$dir/card.html?theme=dark"
```

## Set it on GitHub

GitHub does not read this file automatically — upload it manually:
**repo → Settings → General → Social preview → Edit → Upload an image**
(pick `paperhub-card-dark.png` or `-light.png`). PNG/JPG only; SVG is not accepted there.
