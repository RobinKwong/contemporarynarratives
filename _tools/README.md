# Site checks

Run before and after any change to the site to see what else moved.
Both need the local server: `PATH=/opt/homebrew/opt/ruby/bin:$PATH bundle exec jekyll serve --port 4011 --config _config.yml,_config.local.yml`

## Setup (once)

    python3 -m venv _tools/.venv
    _tools/.venv/bin/pip install playwright pillow
    _tools/.venv/bin/playwright install chromium

## Use

    _tools/.venv/bin/python _tools/sitecheck.py snapshot before    # before the change
    _tools/.venv/bin/python _tools/sitecheck.py snapshot after     # after it
    _tools/.venv/bin/python _tools/sitecheck.py diff before after  # prints counts, writes runs/diff-before-after/report.html

`snapshot` crawls from the home page (plus the pages nothing links to, listed in `SEEDS` in `linkcheck.py`), and at 390, 1280, 1560 and 1920px wide records for every page:

- a full-page screenshot (iframes masked, animations off, cookie notice hidden)
- for each text block: font, size, line spacing, column width and characters per line
- the vertical gap between consecutive blocks, marked when an image is on either side
- images that are squashed, cropped or missing alt text
- elements squeezed to zero width, horizontal overflow, and content drawn over the fixed header
- the heading outline, console errors and failed requests
- every link and asset (`linkcheck.py`); sites that block bots are reported as "unverified", not broken

`diff` lists only what changed, page by page and width by width, with before, after and a highlighted difference side by side, opened at the first changed row. Tolerances are in `TOL` in `sitecheck.py`.

`linkcheck.py` also runs alone with system Python: `python3 _tools/linkcheck.py`.

Runs take about 2½ minutes and 300 MB each; delete old ones from `_tools/runs/`.
