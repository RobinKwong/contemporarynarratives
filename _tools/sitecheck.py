"""Catch knock-on effects of a site change: screenshot and measure every page at
several widths, then diff two runs and report only what moved.

  .venv/bin/python sitecheck.py snapshot NAME        # -> runs/NAME/
  .venv/bin/python sitecheck.py diff OLD NEW         # -> runs/diff-OLD-NEW/report.html

Needs the local Jekyll server on localhost:4011. Prints counts only; detail goes to files."""
import asyncio, hashlib, json, re, sys, shutil
from pathlib import Path
from urllib.parse import urlparse
from PIL import Image, ImageChops
from playwright.async_api import async_playwright
import linkcheck

HERE = Path(__file__).parent
RUNS = HERE / "runs"
WIDTHS = {"phone": 390, "1280": 1280, "1560": 1560, "1920": 1920}
VIEW_H = {"phone": 844}
CONCURRENCY = 4
Image.MAX_IMAGE_PIXELS = None

# Tolerances: a change smaller than this is noise, not a change.
TOL = {"px": 1.5, "cpl": 1.5, "lh": 2, "pixels": 40}

FREEZE_CSS = """
*, *::before, *::after { transition: none !important; animation: none !important; caret-color: transparent !important; }
#cookie-notice { display: none !important; }
"""

# Runs in the page; returns every measurement for one page at one width.
MEASURE_JS = r"""
async () => {
  const vw = document.documentElement.clientWidth, vh = window.innerHeight;
  const path = el => {
    const parts = [];
    for (; el && el !== document.body && el.nodeType === 1; el = el.parentElement) {
      if (el.id) { parts.unshift('#' + el.id); break; }
      let i = 1, s = el;
      while ((s = s.previousElementSibling)) if (s.tagName === el.tagName) i++;
      parts.unshift(el.tagName.toLowerCase() + ':' + i);
    }
    return parts.join('>');
  };
  const hash = s => { let h = 0; for (const c of s) h = (h * 31 + c.charCodeAt(0)) | 0; return (h >>> 0).toString(36); };
  const visible = el => {
    const cs = getComputedStyle(el);
    return cs.display !== 'none' && cs.visibility !== 'hidden' && el.getClientRects().length > 0;
  };
  const box = el => { const r = el.getBoundingClientRect(); return {x: Math.round(r.left + scrollX), y: Math.round(r.top + scrollY), w: Math.round(r.width), h: Math.round(r.height)}; };
  const text = el => (el.innerText || '').replace(/\s+/g, ' ').trim();
  const key = el => path(el) + '|' + hash(text(el).slice(0, 80));
  const header = document.querySelector('.site-header');
  const inHeader = el => header && header.contains(el);
  const inFooter = el => !!el.closest('footer');

  // Text blocks: font, line spacing, column width, characters per line.
  const blocks = [];
  for (const el of document.querySelectorAll('p,li,h1,h2,h3,h4,h5,h6,blockquote,figcaption,dd,dt,td')) {
    if (!visible(el) || inHeader(el)) continue;
    const t = text(el); if (!t) continue;
    if (el.querySelector('p,li,h1,h2,h3,h4,h5,h6,blockquote')) continue;   // only leaf blocks
    const cs = getComputedStyle(el), fs = parseFloat(cs.fontSize);
    let lh = cs.lineHeight === 'normal' ? null : parseFloat(cs.lineHeight);
    const range = document.createRange(); range.selectNodeContents(el);
    const tops = new Set();
    for (const r of range.getClientRects()) if (r.width > 1 && r.height > 1) tops.add(Math.round(r.top / 4));
    const lines = Math.max(tops.size, 1);
    blocks.push({key: key(el), tag: el.tagName.toLowerCase(), text: t.slice(0, 60), box: box(el),
      fs, lh: lh ? Math.round(lh / fs * 100) : null, lines, chars: t.length,
      cpl: lines >= 3 ? Math.round(t.length / (lines - 0.5)) : null,
      font: cs.fontFamily.split(',')[0].replace(/["']/g, ''), weight: cs.fontWeight, color: cs.color,
      footer: inFooter(el)});
  }

  // Vertical gaps between consecutive content blocks in document order.
  const isImg = el => /^(IMG|FIGURE|PICTURE|IFRAME|VIDEO|SVG)$/.test(el.tagName) ||
    (el.tagName === 'P' && el.querySelector('img,iframe,video') && !text(el));
  const flowSel = 'p,h1,h2,h3,h4,h5,h6,ul,ol,blockquote,figure,img,iframe,video,table,pre,hr,.post-teaser';
  const cand = [...document.querySelectorAll(flowSel)].filter(el => visible(el) && !inHeader(el) && !inFooter(el));
  const set = new Set(cand);
  const flow = cand.filter(el => { for (let a = el.parentElement; a; a = a.parentElement) if (set.has(a)) return false; return true; });
  const gaps = [];
  for (let i = 0; i + 1 < flow.length; i++) {
    const a = flow[i].getBoundingClientRect(), b = flow[i + 1].getBoundingClientRect();
    if (a.width < 2 || b.width < 2) continue;
    if (Math.min(a.right, b.right) - Math.max(a.left, b.left) <= 0) continue;   // side by side, not stacked
    gaps.push({key: key(flow[i]) + ' -> ' + key(flow[i + 1]),
      kinds: (isImg(flow[i]) ? 'img' : 'text') + '-' + (isImg(flow[i + 1]) ? 'img' : 'text'),
      gap: Math.round(b.top - a.bottom), y: Math.round(a.bottom + scrollY)});
  }

  // Images: distortion, cropping, alt text.
  const images = [];
  for (const img of document.querySelectorAll('img')) {
    if (!visible(img)) continue;
    const r = img.getBoundingClientRect(), cs = getComputedStyle(img);
    const nat = img.naturalWidth && img.naturalHeight ? img.naturalWidth / img.naturalHeight : null;
    // Measure the content box: content images carry percentage padding, which is not distortion.
    const pad = k => parseFloat(cs['padding' + k]) + parseFloat(cs['border' + k + 'Width']);
    const cw = r.width - pad('Left') - pad('Right'), ch = r.height - pad('Top') - pad('Bottom');
    const ren = ch > 0 ? cw / ch : null;
    const ratioOff = nat && ren ? Math.abs(ren / nat - 1) : 0;
    let clip = 0;
    for (let a = img.parentElement; a && a !== document.body; a = a.parentElement) {
      const acs = getComputedStyle(a);
      if (acs.overflow !== 'visible' || acs.overflowX !== 'visible' || acs.overflowY !== 'visible') {
        const ar = a.getBoundingClientRect();
        const vis = Math.max(0, Math.min(r.right, ar.right) - Math.max(r.left, ar.left)) * Math.max(0, Math.min(r.bottom, ar.bottom) - Math.max(r.top, ar.top));
        if (r.width * r.height) clip = Math.max(clip, 1 - vis / (r.width * r.height));
      }
    }
    images.push({key: key(img), src: img.getAttribute('src'), box: box(img), natural: [img.naturalWidth, img.naturalHeight],
      broken: img.complete && img.naturalWidth === 0, fit: cs.objectFit,
      squashed: ratioOff > 0.03 && (cs.objectFit === 'fill' || !cs.objectFit) ? Math.round(ratioOff * 100) : 0,
      cropped: Math.round(Math.max(clip, cs.objectFit === 'cover' ? ratioOff / (1 + ratioOff) : 0) * 100),
      alt: img.hasAttribute('alt') ? img.getAttribute('alt') : null});
  }

  // Collapsed: has content inside but renders under 2px wide (or tall with no float children explaining it).
  const collapsed = [];
  for (const el of document.querySelectorAll('body *')) {
    if (!visible(el) || /^(SCRIPT|STYLE|BR|WBR|SOURCE|META|LINK)$/.test(el.tagName)) continue;
    const r = el.getBoundingClientRect();
    if ((r.width < 2 || r.height < 2) && (text(el) || el.querySelector('img'))) {
      const cs = getComputedStyle(el);
      if (cs.position === 'absolute' && (cs.clip !== 'auto' || cs.clipPath !== 'none')) continue;  // screen-reader-only text
      // Zero height alone is normal (floated children, a closed menu); a squeezed column is not.
      if (r.width >= 2) continue;
      if (r.height < 2 && (cs.display === 'inline' || !text(el))) continue;
      if (el.parentElement && collapsed.length && collapsed[collapsed.length - 1].el && collapsed[collapsed.length - 1].el.contains(el)) continue;
      collapsed.push({el, key: key(el), w: Math.round(r.width), h: Math.round(r.height), text: text(el).slice(0, 60)});
    }
  }
  collapsed.forEach(c => delete c.el);

  // Horizontal overflow: the page, and the outermost elements sticking out past the viewport.
  const overflow = [];
  for (const el of document.querySelectorAll('body *')) {
    if (!visible(el)) continue;
    const r = el.getBoundingClientRect();
    if (r.right > vw + 1 || r.left < -1) {
      let clipped = false;
      for (let a = el.parentElement; a && a !== document.body; a = a.parentElement) {
        const ox = getComputedStyle(a).overflowX;
        if (ox !== 'visible') { clipped = true; break; }
      }
      if (clipped) continue;
      if (overflow.length && overflow[overflow.length - 1].el.contains(el)) continue;
      overflow.push({el, key: key(el), left: Math.round(r.left), right: Math.round(r.right)});
    }
  }
  overflow.forEach(o => delete o.el);

  // Content drawn over the fixed header, at scroll steps down the page.
  const overHeader = {};
  if (header && /fixed|sticky/.test(getComputedStyle(header).position)) {
    const H = document.documentElement.scrollHeight;
    for (let y = 0; y < H; y += Math.round(vh / 2)) {
      window.scrollTo(0, y);
      await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
      const hr = header.getBoundingClientRect();
      for (let i = 1; i < 12; i++) {
        const x = hr.left + hr.width * i / 12, yy = hr.top + hr.height / 2;
        const el = document.elementFromPoint(x, yy);
        if (el && !header.contains(el)) overHeader[key(el)] = (overHeader[key(el)] || 0) + 1;
      }
    }
    window.scrollTo(0, 0);
  }

  const headings = [...document.querySelectorAll('h1,h2,h3,h4,h5,h6')].filter(visible)
    .map(h => ({level: +h.tagName[1], text: text(h).slice(0, 60)}));

  return {doc: {height: document.documentElement.scrollHeight, scrollW: document.documentElement.scrollWidth, vw},
    title: document.title, blocks, gaps, images, collapsed, overflow,
    overHeader: Object.entries(overHeader).map(([key, n]) => ({key, hits: n})), headings};
}
"""

def slug(url):
    p = urlparse(url).path.strip("/") or "home"
    return re.sub(r"[^a-z0-9]+", "-", p.lower()).strip("-")

async def capture(browser, url, wname, outdir, sem):
    async with sem:
        w = WIDTHS[wname]
        ctx = await browser.new_context(viewport={"width": w, "height": VIEW_H.get(wname, 900)},
                                        device_scale_factor=1, reduced_motion="reduce",
                                        is_mobile=(wname == "phone"), has_touch=(wname == "phone"))
        page = await ctx.new_page()
        errors = []
        page.on("console", lambda m: m.type == "error" and errors.append({"kind": "console", "text": m.text[:300]}))
        page.on("pageerror", lambda e: errors.append({"kind": "pageerror", "text": str(e)[:300]}))
        page.on("requestfailed", lambda r: errors.append({"kind": "requestfailed", "text": r.url[:200]}))
        page.on("response", lambda r: r.status >= 400 and r.url.startswith(linkcheck.BASE) and
                errors.append({"kind": f"http {r.status}", "text": r.url[:200]}))
        try:
            await page.goto(url, wait_until="load", timeout=45000)
            await page.wait_for_load_state("networkidle", timeout=8000)
        except Exception as e:
            if "networkidle" not in str(e) and "Timeout 8000" not in str(e):
                errors.append({"kind": "goto", "text": str(e)[:300]})
        await page.add_style_tag(content=FREEZE_CSS)
        # Load lazy images, then come back to the top.
        await page.evaluate("""async () => {
            document.querySelectorAll('img[loading=lazy]').forEach(i => i.loading = 'eager');
            for (let y = 0; y < document.documentElement.scrollHeight; y += 600) { scrollTo(0, y); await new Promise(r => setTimeout(r, 20)); }
            scrollTo(0, 0); await document.fonts.ready;
            await Promise.all([...document.images].map(i => i.complete ? 0 : new Promise(r => { i.onload = i.onerror = r; })));
        }""")
        await page.wait_for_timeout(200)
        m = await page.evaluate(MEASURE_JS)
        m["errors"] = errors
        m["url"] = url
        name = f"{slug(url)}@{wname}"
        await page.screenshot(path=str(outdir / "shots" / f"{name}.png"), full_page=True,
                              mask=[page.locator("iframe")], animations="disabled")
        (outdir / "data" / f"{name}.json").write_text(json.dumps(m))
        await ctx.close()
        return name, m

def issues(m):
    """Problems present on one page at one width, keyed so two runs can be compared."""
    out = {}
    if m["doc"]["scrollW"] > m["doc"]["vw"] + 1:
        out["page overflows horizontally"] = f'{m["doc"]["scrollW"]}px wide in a {m["doc"]["vw"]}px window'
    for o in m["overflow"]: out[f'overflow: {o["key"]}'] = f'{o["left"]}..{o["right"]}px'
    for o in m["overHeader"]: out[f'over header: {o["key"]}'] = f'{o["hits"]} hits'
    for c in m["collapsed"]: out[f'collapsed: {c["key"]}'] = f'{c["w"]}x{c["h"]}px "{c["text"]}"'
    for i in m["images"]:
        if i["broken"]: out[f'broken image: {i["src"]}'] = ""
        if i["squashed"]: out[f'squashed image: {i["src"]}'] = f'{i["squashed"]}% off its ratio'
        if i["alt"] is None: out[f'no alt: {i["src"]}'] = ""
    for e in m["errors"]:
        t = re.sub(r"\?.*", "", e["text"])
        t = re.sub(r"challenge-platform/.*", "challenge-platform/…", t)
        t = re.sub(r"[0-9a-f]{12,}", "…", t)
        out[f'{e["kind"]}: {t[:120]}'] = ""
    return out

async def snapshot(name):
    outdir = RUNS / name
    if outdir.exists(): shutil.rmtree(outdir)
    (outdir / "shots").mkdir(parents=True); (outdir / "data").mkdir()
    pages, ids, refs = linkcheck.crawl()
    urls = {}
    for u in sorted(pages, key=len, reverse=True):   # prefer the trailing-slash form
        if pages[u] in (200, 404): urls.setdefault(slug(u), u)
    urls = sorted(urls.values())
    links = linkcheck.check_links(pages, ids, refs)
    (outdir / "links.json").write_text(json.dumps(links, indent=1))
    sem = asyncio.Semaphore(CONCURRENCY)
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        results = await asyncio.gather(*[capture(browser, u, w, outdir, sem) for u in urls for w in WIDTHS])
        await browser.close()
    (outdir / "pages.json").write_text(json.dumps(urls, indent=1))
    n_issues = sum(len(issues(m)) for _, m in results)
    n = lambda c: sum(r["class"] == c for r in links["bad"])
    print(f"snapshot {name}: {len(urls)} pages x {len(WIDTHS)} widths = {len(results)} captures; "
          f"{n_issues} layout/console issues; links: {links['refs']} references, {n('bad')} broken, {n('unverified')} unverified")

# ---------------------------------------------------------------- diff

def pixel_diff(a_path, b_path, out_path):
    """Return (changed pixel count, first changed row, last changed row, heights) and write a highlight image."""
    a, b = Image.open(a_path).convert("RGB"), Image.open(b_path).convert("RGB")
    w, h = max(a.width, b.width), max(a.height, b.height)
    A = Image.new("RGB", (w, h), "white"); A.paste(a)
    B = Image.new("RGB", (w, h), "white"); B.paste(b)
    d = ImageChops.difference(A, B).convert("L").point(lambda v: 255 if v > 24 else 0)
    count = sum(d.histogram()[255:])
    bbox = d.getbbox()
    if count > TOL["pixels"]:
        faded = Image.blend(B, Image.new("RGB", (w, h), "white"), 0.6)
        faded.paste(Image.new("RGB", (w, h), (230, 0, 60)), mask=d)
        faded.save(out_path)
    return count, bbox, (a.height, b.height)

def index(items, field="key"):
    return {i[field]: i for i in items}

def measure_diff(ma, mb):
    """Measurement changes between two captures of the same page and width."""
    ch = []
    if abs(ma["doc"]["height"] - mb["doc"]["height"]) > TOL["px"]:
        ch.append(("page height", f'{ma["doc"]["height"]} → {mb["doc"]["height"]}px', None))
    ba, bb = index(ma["blocks"]), index(mb["blocks"])
    groups = {}
    for k in ba.keys() & bb.keys():
        x, y = ba[k], bb[k]
        for f, tol, unit in (("fs", .2, "px"), ("lh", TOL["lh"], "%"), ("cpl", TOL["cpl"], " chars")):
            if x[f] is not None and y[f] is not None and abs(x[f] - y[f]) > tol:
                groups.setdefault(f, []).append((x, y))
        if abs(x["box"]["w"] - y["box"]["w"]) > TOL["px"]: groups.setdefault("w", []).append((x, y))
        if x["font"] != y["font"] or x["weight"] != y["weight"] or x["color"] != y["color"]:
            groups.setdefault("style", []).append((x, y))
    label = {"fs": "font size", "lh": "line spacing", "cpl": "characters per line", "w": "text column width", "style": "font/weight/colour"}
    for f, pairs in groups.items():
        def med(vals): vals = sorted(vals); return vals[len(vals) // 2]
        if f == "style":
            summary = f'{len(pairs)} blocks, e.g. "{pairs[0][0]["text"][:40]}"'
        else:
            src = "box" if f == "w" else None
            get = (lambda b: b["box"]["w"]) if f == "w" else (lambda b: b[f])
            summary = f'{len(pairs)} blocks, median {med([get(a) for a, _ in pairs])} → {med([get(b) for _, b in pairs])}'
        ch.append((label[f], summary, [(a["text"], a.get(f) if f not in ("w", "style") else a["box"]["w"],
                                         b.get(f) if f not in ("w", "style") else b["box"]["w"]) for a, b in pairs[:30]]))
    ga, gb = index(ma["gaps"]), index(mb["gaps"])
    moved = [(k, ga[k], gb[k]) for k in ga.keys() & gb.keys() if abs(ga[k]["gap"] - gb[k]["gap"]) > TOL["px"]]
    if moved:
        img = [x for x in moved if "img" in x[1]["kinds"]]
        ch.append(("gaps between blocks", f'{len(moved)} changed ({len(img)} next to an image)',
                   [(f'{a["kinds"]} at y={b["y"]}: {k.split(" -> ")[0].split("|")[0][-40:]}', a["gap"], b["gap"])
                    for k, a, b in sorted(moved, key=lambda x: ("img" not in x[1]["kinds"], x[2]["y"]))[:30]]))
    added, removed = len(bb.keys() - ba.keys()), len(ba.keys() - bb.keys())
    if added or removed:
        ch.append(("text blocks added/removed", f'+{added} / -{removed}', None))
    ia, ib = index(ma["images"]), index(mb["images"])
    for k in ia.keys() & ib.keys():
        x, y = ia[k], ib[k]
        if abs(x["box"]["w"] - y["box"]["w"]) > TOL["px"] or abs(x["box"]["h"] - y["box"]["h"]) > TOL["px"] or x["cropped"] != y["cropped"]:
            ch.append(("image size/crop", f'{x["src"]}: {x["box"]["w"]}x{x["box"]["h"]} crop {x["cropped"]}% → '
                       f'{y["box"]["w"]}x{y["box"]["h"]} crop {y["cropped"]}%', None))
    ha = [(h["level"], h["text"]) for h in ma["headings"]]; hb = [(h["level"], h["text"]) for h in mb["headings"]]
    if ha != hb: ch.append(("heading outline", f'{len(ha)} → {len(hb)} headings, or levels changed', None))
    return ch

def esc(s): return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def diff(old, new):
    A, B = RUNS / old, RUNS / new
    out = RUNS / f"diff-{old}-{new}"
    if out.exists(): shutil.rmtree(out)
    (out / "px").mkdir(parents=True)
    names = sorted({p.stem for p in (A / "data").glob("*.json")} | {p.stem for p in (B / "data").glob("*.json")})
    rows, counts = [], {"changed": 0, "unchanged": 0, "new issues": 0, "resolved issues": 0, "only in one run": 0}
    for name in names:
        fa, fb = A / "data" / f"{name}.json", B / "data" / f"{name}.json"
        if not (fa.exists() and fb.exists()):
            counts["only in one run"] += 1
            rows.append((name, "only in " + (old if fa.exists() else new), [], {}, {}, None)); continue
        ma, mb = json.loads(fa.read_text()), json.loads(fb.read_text())
        px, bbox, heights = pixel_diff(A / "shots" / f"{name}.png", B / "shots" / f"{name}.png", out / "px" / f"{name}.png")
        ch = measure_diff(ma, mb)
        ia, ib = issues(ma), issues(mb)
        new_i = {k: v for k, v in ib.items() if k not in ia}
        gone_i = {k: v for k, v in ia.items() if k not in ib}
        counts["new issues"] += len(new_i); counts["resolved issues"] += len(gone_i)
        if px > TOL["pixels"] or ch or new_i or gone_i:
            counts["changed"] += 1
            rows.append((name, f"{px} pixels differ", ch, new_i, gone_i, (bbox, heights) if px > TOL["pixels"] else None))
        else:
            counts["unchanged"] += 1
    la, lb = json.loads((A / "links.json").read_text()), json.loads((B / "links.json").read_text())
    lk = lambda l: {(r["page"], r["url"]): r for r in l["bad"]}
    lka, lkb = lk(la), lk(lb)
    link_new = [lkb[k] for k in lkb.keys() - lka.keys()]
    link_fixed = [lka[k] for k in lka.keys() - lkb.keys()]
    write_report(out, old, new, rows, counts, link_new, link_fixed, lb)
    print(f"diff {old} → {new}: {len(names)} captures, {counts['changed']} changed, {counts['unchanged']} unchanged, "
          f"{counts['only in one run']} only in one run; issues +{counts['new issues']} / -{counts['resolved issues']}; "
          f"links +{len(link_new)} broken/unverified / -{len(link_fixed)}")
    print(f"report: {out / 'report.html'}")

def write_report(out, old, new, rows, counts, link_new, link_fixed, links_now):
    rel = lambda run, name: f"../{run}/shots/{name}.png"
    h = [f"""<!doctype html><meta charset=utf-8><title>sitecheck {old} → {new}</title>
<style>
body{{font:15px/1.45 -apple-system,system-ui,sans-serif;margin:24px;color:#111;background:#fff}}
h1{{font-size:22px}} h2{{font-size:17px;margin:32px 0 6px;border-top:1px solid #ccc;padding-top:14px}}
table{{border-collapse:collapse;margin:6px 0}} td,th{{padding:2px 10px 2px 0;vertical-align:top;text-align:left}}
.trio{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin:10px 0}}
.trio div{{max-height:900px;overflow:auto;border:1px solid #ddd}} .trio img{{width:100%;display:block}}
.trio p{{margin:0;font-weight:600;position:sticky;top:0;background:#fff;padding:2px 4px}}
.new{{color:#b00}} .gone{{color:#070}} details{{margin:4px 0}} summary{{cursor:pointer}}
</style><h1>sitecheck: {esc(old)} → {esc(new)}</h1>
<p>{counts['changed']} captures changed, {counts['unchanged']} unchanged, {counts['only in one run']} only in one run.
Issues: {counts['new issues']} new, {counts['resolved issues']} resolved.
Links: {len(link_new)} newly broken or unverified, {len(link_fixed)} no longer failing
({sum(r['class']=='bad' for r in links_now['bad'])} broken and {sum(r['class']=='unverified' for r in links_now['bad'])} unverified in {esc(new)}).</p>"""]
    if link_new or link_fixed:
        h.append("<h2>Links</h2><table>")
        for r in link_new: h.append(f"<tr class=new><td>+ {esc(r['class'])}</td><td>{esc(r['status'])}</td><td>{esc(r['page'])}</td><td>{esc(r['url'])}</td></tr>")
        for r in link_fixed: h.append(f"<tr class=gone><td>− fixed</td><td>{esc(r['status'])}</td><td>{esc(r['page'])}</td><td>{esc(r['url'])}</td></tr>")
        h.append("</table>")
    for name, px, ch, new_i, gone_i, shot in rows:
        h.append(f"<h2 id='{esc(name)}'>{esc(name)} <small style='font-weight:400'>{esc(px)}</small></h2>")
        if new_i or gone_i:
            h.append("<table>")
            for k, v in new_i.items(): h.append(f"<tr class=new><td>+ new</td><td>{esc(k)}</td><td>{esc(v)}</td></tr>")
            for k, v in gone_i.items(): h.append(f"<tr class=gone><td>− resolved</td><td>{esc(k)}</td><td>{esc(v)}</td></tr>")
            h.append("</table>")
        if ch:
            h.append("<table>")
            for label, summary, detail in ch:
                h.append(f"<tr><th>{esc(label)}</th><td>{esc(summary)}")
                if detail:
                    h.append("<details><summary>detail</summary><table>" + "".join(
                        f"<tr><td>{esc(t)[:60]}</td><td>{esc(a)} → {esc(b)}</td></tr>" for t, a, b in detail) + "</table></details>")
                h.append("</td></tr>")
            h.append("</table>")
        if shot:
            bbox, (ha, hb) = shot
            note = f"heights {ha} → {hb}px; first change at y={bbox[1]}" if bbox else ""
            y0 = bbox[1] if bbox else 0
            h.append(f"<p>{note}</p><div class=trio data-y={y0}>"
                     f"<div><p>{esc(old)}</p><a href='{rel(old, name)}'><img loading=lazy src='{rel(old, name)}'></a></div>"
                     f"<div><p>{esc(new)}</p><a href='{rel(new, name)}'><img loading=lazy src='{rel(new, name)}'></a></div>"
                     f"<div><p>difference</p><a href='px/{name}.png'><img loading=lazy src='px/{name}.png'></a></div></div>")
    # Open each trio scrolled to just above the first changed row.
    h.append("""<script>
document.querySelectorAll('.trio').forEach(t => t.querySelectorAll('img').forEach(img => {
  const go = () => { img.parentElement.parentElement.scrollTop = Math.max(0, +t.dataset.y * img.clientWidth / img.naturalWidth - 80); };
  img.loading = 'eager'; img.complete ? go() : img.addEventListener('load', go);
}));
</script>""")
    (out / "report.html").write_text("\n".join(h))

if __name__ == "__main__":
    cmd = sys.argv[1:] or ["help"]
    if cmd[0] == "snapshot" and len(cmd) == 2: asyncio.run(snapshot(cmd[1]))
    elif cmd[0] == "diff" and len(cmd) == 3: diff(cmd[1], cmd[2])
    else: print(__doc__)
