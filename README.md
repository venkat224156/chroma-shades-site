# Chroma Shades & Blinds (GitHub Pages mirror)

This repo is intended to host a **static** copy of `chromashadesandblinds.com` on GitHub Pages.

## How to build the site (download + rewrite links)

From this folder:

```bash
rm -rf docs
python3 mirror_site.py --start "https://chromashadesandblinds.com/"
```

This will create a `docs/` folder containing the mirrored site content (HTML + assets). GitHub Pages can serve directly from `docs/`.

## Preview locally

```bash
python3 -m http.server 5173 -d docs
```

Then open `http://localhost:5173`.

## Publish on GitHub Pages

1. Create a GitHub repo (example: `chroma-shades-site`)
2. Push this code to GitHub
3. In GitHub repo settings:
   - Pages → **Build and deployment**
   - Source: **Deploy from a branch**
   - Branch: `main`
   - Folder: `/docs`

## Notes / troubleshooting

- If you get blocked downloading (403 / bot protection), try re-running the script on your local machine (not through a restricted corporate proxy), or try a different network.
- If the live site is heavily JS-driven, a “static mirror” may miss content. In that case, the best path is exporting a static ZIP from Siteswan (if available) and placing it into `docs/`.

