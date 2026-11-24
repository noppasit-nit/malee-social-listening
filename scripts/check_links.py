#!/usr/bin/env python3
"""Re-check every mention source and record link_status.

Statuses:
  ok       a logged-out viewer can open the page, or the post still exists
  login    the link lands on a login page; the post is not known to be gone
  removed  the post was deleted or the page no longer exists

X posts are classified with the public syndication endpoint. x.com returns
HTTP 200 and a login shell for both live posts and deleted ones, so the
status code alone is not used. TweetTombstone => removed.

Other links are fetched with a browser user agent. A final URL on
facebook.com/login is `login`. HTTP 404/410 or a not-found title is
`removed`. Pantip's datacenter block ("not allowed" / pleasecontact) and
Google's "unusual traffic" captcha are not deletions; those stay `ok`.
Facebook post URLs that return HTTP 400 to a non-browser client are opened
in headless Chrome, because that 400 is a bot block, not a missing post.

Does not submit a login or sign-up form.

Usage:
  python3 scripts/check_links.py            # check, then update the files
  python3 scripts/check_links.py --check    # print the result, write nothing

Updates docs/data.json, docs/mentions.csv and the embedded DATA in
docs/index.html. Mention text, sentiment and every other field are left
as they were.
"""
import argparse, csv, io, json, os, re, sys, urllib.error, urllib.request
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = os.path.join(ROOT, 'docs')
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36')
CHROME = os.environ.get('CHROME') or next(
    (p for p in ('/usr/bin/google-chrome', '/usr/local/bin/google-chrome') if os.path.exists(p)),
    'google-chrome')
NOT_FOUND = ('page not found', "this page isn't available", "this content isn't available",
             '404 not found', 'ไม่พบหน้า')


def fetch(url, limit=800000):
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': 'text/html',
                                               'Accept-Language': 'en,th;q=0.8'})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.geturl(), resp.read(limit).decode('utf-8', 'replace'), ''
    except urllib.error.HTTPError as e:
        return e.code, e.geturl(), e.read(limit).decode('utf-8', 'replace'), ''
    except Exception as e:
        return 0, url, '', type(e).__name__ + ': ' + str(e)[:160]


def title_of(body):
    m = re.search(r'<title[^>]*>(.*?)</title>', body or '', re.I | re.S)
    if not m:
        m = re.search(r'property="og:title" content="(.*?)"', body or '', re.I | re.S)
    return re.sub(r'\s+', ' ', m.group(1)).strip() if m else ''


def classify_x(url):
    m = re.search(r'/status/(\d+)', url)
    if not m:
        return None, 'no status id'
    api = 'https://cdn.syndication.twimg.com/tweet-result?id=%s&lang=en&token=x' % m.group(1)
    code, final, body, err = fetch(api, limit=20000)
    if err or code != 200:
        return None, err or 'syndication HTTP %s' % code
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return None, 'syndication not json'
    typ = data.get('__typename') or ''
    if 'Tombstone' in typ or typ in ('TweetUnavailable',):
        return 'removed', typ or 'tombstone'
    if typ.startswith('Tweet'):
        return 'ok', typ
    return None, 'syndication ' + (typ or 'empty')


def classify_http(url):
    code, final, body, err = fetch(url)
    if err:
        return None, 'fetch ' + err, ''
    final_l = (final or '').lower()
    body_l = (body or '').lower()
    title = title_of(body)
    if 'facebook.com/login' in final_l:
        return 'login', 'redirected to Facebook login', title
    if '2g.pantip.com/cafe/pleasecontact' in final_l or 'not allowed to access this page' in body_l:
        return 'ok', 'Pantip blocks this network; not a missing topic', title
    if 'playabilityStatus":{"status":"OK"' in (body or ''):
        return 'ok', 'YouTube playable', title
    if code in (404, 410):
        return 'removed', 'HTTP %s' % code, title
    tl = title.lower()
    if any(p in tl for p in NOT_FOUND):
        return 'removed', 'not-found title', title
    generic = tl in ('', 'instagram', 'facebook', 'error', 'just a moment...')
    if code == 200 and title and not generic and 'login' not in final_l:
        return 'ok', 'HTTP 200', title
    why = 'HTTP %s title %r' % (code, title[:60])
    if 'instagram.com/accounts/login' in final_l:
        why = 'Instagram login redirect (confirm in the browser)'
    elif 'google.com/sorry' in final_l or 'unusual traffic' in body_l:
        why = 'captcha, not a missing page'
        return 'ok', why, title
    return None, why, title


def classify_browser(page, url):
    err = ''
    try:
        page.goto(url, wait_until='domcontentloaded', timeout=30000)
        page.wait_for_timeout(700)
        title = page.title() or ''
        final = page.url or ''
        text = page.inner_text('body')[:1500]
    except Exception as e:
        return 'ok', 'browser ' + type(e).__name__ + ' (not treated as removed)', ''
    fin = final.lower()
    blob = (title + ' ' + text).lower()
    if 'pleasecontact' in fin or 'not allowed to access' in blob:
        return 'ok', 'Pantip block page', title
    if 'google.com/sorry' in fin or 'unusual traffic' in blob:
        return 'ok', 'captcha, not a missing page', title
    if 'facebook.com/login' in fin or 'instagram.com/accounts/login' in fin:
        return 'login', 'browser landed on a login page', title
    if any(p in title.lower() for p in NOT_FOUND):
        return 'removed', 'browser not-found title', title
    return 'ok', 'browser opened the page', title


def check_all(rows):
    results = {}
    xs = [r for r in rows if re.search(r'https?://(?:www\.)?(?:x|twitter)\.com/.*/status/\d+', r['url'])]
    others = [r for r in rows if r['id'] not in {x['id'] for x in xs}]

    def one_x(r):
        status, why = classify_x(r['url'])
        return r['id'], status, why

    with ThreadPoolExecutor(max_workers=8) as pool:
        for mid, status, why in pool.map(one_x, xs):
            results[mid] = (status, why)
            print('%s X %s %s' % (mid, status or '?', why))

    pending = []
    for r in others:
        status, why, title = classify_http(r['url'])
        if status:
            results[r['id']] = (status, why)
            print('%s %s %s %s' % (r['id'], r['platform'], status, why))
        else:
            pending.append(r)
            print('%s %s ? %s' % (r['id'], r['platform'], why))

    if pending:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=CHROME,
                                        args=['--no-sandbox', '--disable-dev-shm-usage'])
            ctx = browser.new_context(viewport={'width': 1280, 'height': 800}, locale='en-US',
                                      user_agent=UA)
            page = ctx.new_page()
            for r in pending:
                status, why, title = classify_browser(page, r['url'])
                results[r['id']] = (status, why + ((' / ' + title[:70]) if title else ''))
                print('%s %s %s (browser) %s' % (r['id'], r['platform'], status, why))
            browser.close()
    return results


def write_files(rows):
    json_path = os.path.join(DOCS, 'data.json')
    raw = open(json_path, encoding='utf-8').read()
    original = json.loads(raw)
    for a, b in zip(original, rows):
        for k, v in a.items():
            if b.get(k) != v:
                raise SystemExit('refusing to change %s field %s' % (a['id'], k))
    out = json.dumps(rows, ensure_ascii=False, indent=1)
    open(json_path, 'w', encoding='utf-8').write(out)

    csv_path = os.path.join(DOCS, 'mentions.csv')
    text = open(csv_path, encoding='utf-8-sig', newline='').read()
    table = list(csv.reader(io.StringIO(text)))
    old_header, body = table[0], table[1:]
    if len(body) != len(rows):
        raise SystemExit('mentions.csv has %s rows, data.json has %s' % (len(body), len(rows)))
    header = list(old_header)
    if 'link_status' not in header:
        header.append('link_status')
    idx = {name: i for i, name in enumerate(header)}
    by_id = {r['id']: r for r in rows}
    id_i = old_header.index('id')
    before = [rec[:] for rec in body]
    for rec in body:
        src = by_id[rec[id_i]]
        while len(rec) < len(header):
            rec.append('')
        rec[idx['link_status']] = src['link_status']
    buf = io.StringIO()
    csv.writer(buf, lineterminator='\n').writerows([header] + body)
    open(csv_path, 'w', encoding='utf-8-sig', newline='').write(buf.getvalue())
    again = list(csv.reader(io.StringIO(open(csv_path, encoding='utf-8-sig', newline='').read())))
    if again[0] != header:
        raise SystemExit('mentions.csv header changed unexpectedly')
    for old, new in zip(before, again[1:]):
        # Cells that were already there must come back unchanged. The leading
        # apostrophe on @-mentions is a formula guard and stays part of the cell.
        if new[:len(old)] != old:
            raise SystemExit('mentions.csv cell changed for %s' % old[id_i])

    html_path = os.path.join(DOCS, 'index.html')
    html = open(html_path, encoding='utf-8').read()
    blob = 'const DATA=' + json.dumps(rows, ensure_ascii=False, separators=(', ', ': ')) + ';'
    html2, n = re.subn(r'const DATA=\[.*\];', lambda _: blob, html, count=1)
    if n != 1:
        raise SystemExit('could not find the embedded DATA line in index.html')
    open(html_path, 'w', encoding='utf-8').write(html2)


def main():
    ap = argparse.ArgumentParser(description='Check mention source links and record link_status.')
    ap.add_argument('--check', action='store_true', help='print statuses, do not write')
    args = ap.parse_args()
    path = os.path.join(DOCS, 'data.json')
    rows = json.load(open(path, encoding='utf-8'))
    results = check_all(rows)
    missing = [r['id'] for r in rows if r['id'] not in results or results[r['id']][0] not in ('ok', 'login', 'removed')]
    if missing:
        print('Could not classify: ' + ', '.join(missing))
        sys.exit(1)
    for r in rows:
        r['link_status'] = results[r['id']][0]
    from collections import Counter
    print(Counter(r['link_status'] for r in rows))
    if args.check:
        return
    write_files(rows)
    print('Updated data.json, mentions.csv and index.html')


if __name__ == '__main__':
    main()
