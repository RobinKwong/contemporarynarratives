"""Crawl the local Jekyll site, then check every link and asset it references.
Writes results to linkcheck.json beside this script; prints counts only.
sitecheck.py imports crawl() and check_links() from here."""
import json, os, re, sys, time, concurrent.futures as cf
from html.parser import HTMLParser
from urllib.parse import urljoin, urldefrag, urlparse, unquote
import urllib.request, urllib.error, ssl, socket

BASE = "http://localhost:4011/"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36"
OUT = __file__.rsplit("/", 1)[0] + "/linkcheck.json"
SITE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "_site")   # what jekyll serve is serving
# Pages nothing links to, so the crawl would never reach them.
SEEDS = ["404.html", "tags/", "search/", "projects/creative-data-exchange/"]
# Sites that answer automated requests with these codes even when the page exists.
UNVERIFIED = {401, 403, 405, 429, 999}

class P(HTMLParser):
    def __init__(s):
        super().__init__(); s.links = []; s.ids = set()
    def handle_starttag(s, tag, a):
        a = dict(a)
        if a.get("id"): s.ids.add(a["id"])
        if a.get("name") and tag == "a": s.ids.add(a["name"])
        if tag == "link" and a.get("rel") in ("preconnect", "dns-prefetch"): return   # a connection hint, not a resource
        for attr in ("href", "src"):
            v = a.get(attr)
            if v and tag in ("a", "img", "iframe", "script", "link", "source", "video", "audio"):
                s.links.append((tag, attr, v.strip()))

ONLINE = True   # set by check_links: offline, every DNS lookup fails and nothing is provably dead

def dead(e):
    """'dns', 'refused' or 'ssl' when the failure means the site is gone or broken for everyone,
    not just refusing a bot; None for timeouts and resets, which bot walls also cause."""
    r = getattr(e, "reason", e)
    if isinstance(r, socket.gaierror): return "dns" if ONLINE else None
    if isinstance(r, ConnectionRefusedError): return "refused"
    if isinstance(r, ssl.SSLCertVerificationError): return "ssl"
    return None

def fetch(url, method="GET"):
    req = urllib.request.Request(url, method=method, headers={"User-Agent": UA, "Accept": "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=20, context=ssl.create_default_context()) as r:
            body = r.read() if method == "GET" and url.startswith(BASE) else b""
            return r.status, r.geturl(), body, r.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        return e.code, url, b"", ""
    except Exception as e:
        return f"ERR {dead(e) or type(e).__name__}: {str(e)[:80]}", url, b"", ""

def crawl():
    """Return (pages {url: status}, ids {url: set}, refs [dict]) for every HTML page reachable from BASE and SEEDS."""
    pages, ids, refs = {}, {}, []
    queue = [BASE] + [BASE + s for s in SEEDS]
    seen = set(queue)
    while queue:
        url = queue.pop()
        st, final, body, ctype = fetch(url)
        if st != 200 and not url.endswith("404.html"):   # the server may be mid-rebuild: try once more
            time.sleep(2); st, final, body, ctype = fetch(url)
        if "html" not in ctype:
            if st != 200: print(f"  crawl: {url} gave {st}", file=sys.stderr)
            continue
        p = P(); p.feed(body.decode("utf-8", "replace"))
        pages[url] = st; ids[url] = p.ids
        for tag, attr, v in p.links:
            if v.startswith(("mailto:", "tel:", "javascript:", "data:")) or v == "#": continue
            absu = urljoin(final, v)   # after a redirect (/about to /about/), relative links resolve against where we landed
            refs.append({"page": url[len(BASE)-1:], "tag": tag, "raw": v, "url": absu})
            nofrag, _ = urldefrag(absu)
            if nofrag.startswith(BASE) and tag == "a" and nofrag not in seen and not re.search(r"\.(pdf|png|jpe?g|gif|xml|zip|svg|ico)$", nofrag, re.I):
                seen.add(nofrag); queue.append(nofrag)
    return pages, ids, refs

def wrong_case(u):
    """The first path segment whose case differs from the file in _site, else None. The Mac's disk
    ignores case, so the local server answers /Blog/ for blog/; GitHub Pages doesn't."""
    d = SITE
    for seg in [unquote(x) for x in urlparse(u).path.split("/") if x]:
        try: names = os.listdir(d)
        except OSError: return None
        if seg in names or seg + ".html" in names:
            d = os.path.join(d, seg); continue
        match = [n for n in names if n.lower() in (seg.lower(), seg.lower() + ".html")]
        return f"{seg} (file is {match[0]})" if match else None
    return None

def check(u):
    if u.startswith(BASE):
        st = fetch(u)[0]
        if st == 200 and (w := wrong_case(u)): st = f"wrong case: {w}"
        return u, st
    st = fetch(u, "HEAD")[0]
    if st != 200:
        st2 = fetch(u, "GET")
        st = st2[0]
    return u, st

def classify(r):
    """'ok', 'unverified' (an external site refusing bots or timing out) or 'bad'."""
    st = r["status"]
    if isinstance(st, int) and 200 <= st < 300: return "ok"
    if str(st).startswith(("ERR dns", "ERR refused", "ERR ssl")): return "bad"   # a dead domain, not a bot wall
    if not r["url"].startswith(BASE) and (st in UNVERIFIED or str(st).startswith("ERR")): return "unverified"
    return "bad"

def check_links(pages, ids, refs):
    global ONLINE
    try: socket.getaddrinfo("example.com", 443); ONLINE = True
    except OSError:
        ONLINE = False; print("  DNS lookups failing (offline?): unreachable sites count as unverified, not broken", file=sys.stderr)
    targets = sorted({urldefrag(r["url"])[0] for r in refs})
    with cf.ThreadPoolExecutor(16) as ex:
        status = dict(ex.map(check, targets))
    # in-page anchors on our own pages
    for r in refs:
        u, frag = urldefrag(r["url"])
        r["status"] = status.get(u)
        if frag and u.startswith(BASE) and u in ids and frag not in ids[u]:
            r["status"] = f"missing anchor #{frag}"
        r["class"] = classify(r)
    return {"pages": len(pages), "refs": len(refs), "targets": len(targets),
            "bad": [r for r in refs if r["class"] != "ok"]}

def main(out=OUT):
    pages, ids, refs = crawl()
    res = check_links(pages, ids, refs)
    json.dump(res, open(out, "w"), indent=1)
    n = lambda c: sum(r["class"] == c for r in res["bad"])
    print(f"pages {res['pages']}, references {res['refs']}, unique targets {res['targets']}, "
          f"broken {n('bad')}, unverified {n('unverified')}")
    return res

if __name__ == "__main__":
    main()
