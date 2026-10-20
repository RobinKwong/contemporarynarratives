"""Crawl the local Jekyll site, then check every link and asset it references.
Writes results to linkcheck.json beside this script; prints counts only.
sitecheck.py imports crawl() and check_links() from here."""
import json, re, sys, concurrent.futures as cf
from html.parser import HTMLParser
from urllib.parse import urljoin, urldefrag, urlparse
import urllib.request, urllib.error, ssl

BASE = "http://localhost:4011/"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36"
OUT = __file__.rsplit("/", 1)[0] + "/linkcheck.json"
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

def fetch(url, method="GET"):
    req = urllib.request.Request(url, method=method, headers={"User-Agent": UA, "Accept": "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=20, context=ssl.create_default_context()) as r:
            body = r.read() if method == "GET" and url.startswith(BASE) else b""
            return r.status, r.geturl(), body, r.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        return e.code, url, b"", ""
    except Exception as e:
        return f"ERR {type(e).__name__}: {str(e)[:80]}", url, b"", ""

def crawl():
    """Return (pages {url: status}, ids {url: set}, refs [dict]) for every HTML page reachable from BASE and SEEDS."""
    pages, ids, refs = {}, {}, []
    queue = [BASE] + [BASE + s for s in SEEDS]
    seen = set(queue)
    while queue:
        url = queue.pop()
        st, final, body, ctype = fetch(url)
        if "html" not in ctype: continue
        p = P(); p.feed(body.decode("utf-8", "replace"))
        pages[url] = st; ids[url] = p.ids
        for tag, attr, v in p.links:
            if v.startswith(("mailto:", "tel:", "javascript:", "data:")) or v == "#": continue
            absu = urljoin(url, v)
            refs.append({"page": url[len(BASE)-1:], "tag": tag, "raw": v, "url": absu})
            nofrag, _ = urldefrag(absu)
            if nofrag.startswith(BASE) and tag == "a" and nofrag not in seen and not re.search(r"\.(pdf|png|jpe?g|gif|xml|zip|svg|ico)$", nofrag, re.I):
                seen.add(nofrag); queue.append(nofrag)
    return pages, ids, refs

def check(u):
    if u.startswith(BASE):
        return u, fetch(u)[0]
    st = fetch(u, "HEAD")[0]
    if st != 200:
        st2 = fetch(u, "GET")
        st = st2[0]
    return u, st

def classify(r):
    """'ok', 'unverified' (an external site refusing bots or timing out) or 'bad'."""
    st = r["status"]
    if isinstance(st, int) and 200 <= st < 300: return "ok"
    if not r["url"].startswith(BASE) and (st in UNVERIFIED or str(st).startswith("ERR")): return "unverified"
    return "bad"

def check_links(pages, ids, refs):
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
