# Site checks

Run before and after any change to the site to see what else moved.
Both need the local server: `PATH=/opt/homebrew/opt/ruby/bin:$PATH bundle exec jekyll serve --port 4011`

## Setup (once)

    python3 -m venv _tools/.venv
    _tools/.venv/bin/pip install playwright pillow
    _tools/.venv/bin/playwright install chromium

## Use

    _tools/.venv/bin/python _tools/sitecheck.py snapshot before    # before the change
    _tools/.venv/bin/python _tools/sitecheck.py snapshot after     # after it
    _tools/.venv/bin/python _tools/sitecheck.py diff before after  # prints counts, writes runs/diffs/before..after/report.html

`snapshot` crawls from the home page (plus the pages nothing links to, listed in `SEEDS` in `linkcheck.py`), and at 390, 1280, 1560 and 1920px wide records for every page:

- a full-page screenshot (iframes masked, animations off)
- for each text block: font, size, line spacing, column width and characters per line
- the vertical gap between consecutive blocks, marked when an image is on either side
- images that are squashed, cropped or missing alt text
- elements squeezed to zero width, horizontal overflow, and content drawn over the fixed header
- the heading outline, console errors and failed requests
- every link and asset (`linkcheck.py`), including links whose case doesn't match the file: the Mac serves `/Blog/` for `blog/`, GitHub Pages doesn't. Sites that block bots or time out are reported as "unverified"; a domain that no longer resolves, refuses connections or has an invalid certificate is reported as broken

`diff` lists only what changed, page by page and width by width, with before, after and a highlighted difference side by side, opened at the first changed row. Tolerances are in `TOL` in `sitecheck.py`.

Elements are matched between runs by their place in the page structure and their text (or an image's `src`), not by position, so adding a paragraph shows as one added block rather than renumbering everything after it. A block whose text changed is paired with its counterpart by structure and order, and reported as "text edited" with its other measurements still compared. Elements with no text and no `src` (rules, spacers) can only be told apart by order, so inserting one renumbers the ones after it. Runs taken before this change (20 October 2026) used positional keys: diffing one of those against a newer run lists every block as added and removed, so take a fresh baseline.

Offline, every external link fails its DNS lookup; the check notices and reports them as unverified rather than broken.

If a page fails to load or measure, the snapshot carries on, names the failed captures in its summary line, and a diff lists them as "only in one run". Run names are limited to letters, digits, dot, dash and underscore.

`linkcheck.py` also runs alone with system Python: `python3 _tools/linkcheck.py`.

Runs take about 2½ minutes and 300 MB each; delete old ones from `_tools/runs/`.
