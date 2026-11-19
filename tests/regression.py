"""Regression test for the Malee Social Listening dashboard.
Usage: python3 tests/regression.py [URL]   (default: local docs/ via http.server on :8765)
Needs: playwright + Chrome. Exits 1 on any failure."""
import asyncio,json,sys,os,re,collections
from playwright.async_api import async_playwright
URL=sys.argv[1] if len(sys.argv)>1 else 'http://localhost:8765/'
CHROME=os.environ.get('CHROME','/usr/bin/google-chrome')
here=os.path.dirname(os.path.abspath(__file__));D=json.load(open(os.path.join(here,'..','docs','data.json')))
fails=[]
def ok(c,m):
    print(('PASS ' if c else 'FAIL ')+m)
    if not c: fails.append(m)
async def cnt(pg): return int((await pg.text_content('#count')).split()[0])
async def reset(pg): await pg.click('#reset'); await pg.wait_for_timeout(150)
async def main():
    # data checks
    ok(len({r['id'] for r in D})==len(D),'unique stable ids')
    ok(len({r['url'] for r in D})==len(D),'no duplicate source urls')
    ok(all(r['url'].startswith('https://') for r in D),'all urls https')
    ok(not any('&amp;' in r['text'] for r in D),'no double-escaped entities in text')
    ok(not any(r['date_precision'].startswith('unknown') for r in D),'no undated items')
    async with async_playwright() as p:
        b=await p.chromium.launch(executable_path=CHROME,args=['--no-sandbox'])
        ctx=await b.new_context(viewport={'width':1366,'height':900},accept_downloads=True)
        pg=await ctx.new_page();errs=[]
        pg.on('console',lambda m: errs.append(m.text) if m.type=='error' else None);pg.on('pageerror',lambda e: errs.append(str(e)))
        await pg.goto(URL,wait_until='networkidle');await pg.wait_for_timeout(800)
        ok(await cnt(pg)==len(D),'total count matches data.json')
        mal=sum('Malee' in r['brand'] for r in D)
        await pg.select_option('#fBrand','Malee');await pg.wait_for_timeout(150);ok(await cnt(pg)==mal,'brand filter')
        await pg.select_option('#fPlat','X');await pg.select_option('#fSent','neg');await pg.wait_for_timeout(150)
        ok(await cnt(pg)==sum('Malee' in r['brand'] and r['platform']=='X' and r['sentiment']=='neg' for r in D),'brand+platform+sentiment combo')
        await reset(pg);await pg.check('#fComp');await pg.select_option('#fTopic','quality/contamination');await pg.wait_for_timeout(150)
        n=sum(r['complaint'] and 'quality/contamination' in r['tags'] for r in D);ok(await cnt(pg)==n and await pg.locator('#cBody tr').count()==n,'complaints+topic combo and tracker rows')
        await reset(pg);await pg.fill('#fQ','น้ำมะพร้าว');await pg.wait_for_timeout(400)
        ok(await cnt(pg)==sum('น้ำมะพร้าว' in (r['text']+r['en']+r['product']) for r in D),'Thai text search')
        await reset(pg);d0=min(r['date'][:10] for r in D);d1=max(r['date'][:10] for r in D)
        for sel,v in (('#fFrom',d1),('#fTo',d1)):await pg.fill(sel,v);await pg.dispatch_event(sel,'change')
        await pg.wait_for_timeout(150);ok(await cnt(pg)==sum(r['date'][:10]==d1 for r in D),'date range single-day edge (inclusive)')
        await pg.fill('#fFrom','2030-01-01');await pg.dispatch_event('#fFrom','change');await pg.wait_for_timeout(150)
        ok(await cnt(pg)==0 and 'No mentions' in await pg.text_content('#feedBox') and 'No complaints' in await pg.text_content('#cBody'),'empty state')
        ok(await pg.text_content('#kM')=='0','KPI zero on empty')
        await reset(pg)
        # chart vs data
        sov=await pg.evaluate("charts.cSov.data.datasets[0].data.reduce((a,b)=>a+b,0)")
        ok(sov==sum(len([b for b in r['brand'] if b!='Category']) for r in D),'share-of-voice chart totals')
        plat=await pg.evaluate("charts.cPlat.data.datasets[0].data.reduce((a,b)=>a+b,0)");ok(plat==len(D),'platform chart totals')
        # CSV
        async with pg.expect_download() as dl: await pg.click('#csv')
        raw=open(await (await dl.value).path(),'rb').read()
        ok(raw.startswith(b'\xef\xbb\xbf'),'CSV has UTF-8 BOM');txt=raw.decode('utf-8-sig')
        ok(not re.search(r'(^|,)"[=+\-@]',txt,re.M),'CSV cells guarded against formula injection')
        ok('น้ำ' in txt,'CSV keeps Thai text')
        # status persistence
        sel=pg.locator('#cBody select[data-k=status]').first;sid=await sel.get_attribute('data-id');await sel.select_option('Responded')
        await pg.reload(wait_until='networkidle');await pg.wait_for_timeout(400)
        ok(await pg.eval_on_selector(f'select[data-id="{sid}"][data-k=status]','e=>e.value')=='Responded','status persists after reload')
        # corrupt storage
        for bad in ['null','[]','not json{','{"x":5}','{"%s":{"status":"<img>"}}'%sid]:
            await pg.evaluate("v=>localStorage.setItem('malee-sl-v1',v)",bad);e0=len(errs)
            await pg.reload(wait_until='networkidle');await pg.wait_for_timeout(300)
            ok(await cnt(pg)==len(D) and len(errs)==e0,f'survives corrupt localStorage {bad[:12]}')
        await pg.evaluate("localStorage.removeItem('malee-sl-v1')")
        # XSS: inject a hostile record through the same render path
        await pg.evaluate("""()=>{window.__x=0;DATA.unshift({...DATA[0],id:'MXSS',text:'<img src=x onerror="window.__x=1">',en:'<script>window.__x=1</script>',product:'<b>p</b>',author_display:'<i>a</i>',url:'https://example.com/"><img src=x onerror=window.__x=1>'});render()}""")
        await pg.wait_for_timeout(300);ok(await pg.evaluate('window.__x')==0 and await pg.locator('#feedBox img').count()==0,'snippets are HTML-escaped (no XSS)')
        await pg.reload(wait_until='networkidle')
        # accessibility basics
        unl=await pg.evaluate("[...document.querySelectorAll('select,input')].filter(e=>!e.closest('label')&&!e.getAttribute('aria-label')).length");ok(unl==0,'every form control has a label')
        ok(await pg.evaluate("[...document.querySelectorAll('#kw > *')].every(e=>e.tagName==='BUTTON')"),'keyword chips are keyboard buttons')
        ok(await pg.evaluate("[...document.querySelectorAll('a[target=_blank]')].every(a=>/noopener/.test(a.rel))"),'external links use rel=noopener')
        ok(await pg.locator('#recs .rec').count()>=4,'recommendations section present')
        # ---- recency labels ----
        B=await pg.evaluate("[0,7,8,30,31,365,366,4000,null].map(bucket)")
        ok(B==['new','new','recent','recent','year','year','old','old','unk'],'recency label boundaries 7/8/30/31/365/366 '+str(B))
        ok('Old · 2013' in await pg.evaluate("rbadge('2013-07-01')") and 'Date unknown' in await pg.evaluate("rbadge('',true)"),'Old shows year; undated shows Date unknown')
        ok(await pg.evaluate("[relt(0),relt(1),relt(3),relt(45),relt(800),relt(3,true)]")==['today','yesterday','3 days ago','1 month ago','2 years ago','~3 days ago'],'relative time text')
        today=await pg.evaluate('TODAY');import datetime as dt
        ok(today==dt.datetime.now(dt.timezone(dt.timedelta(hours=7))).date().isoformat(),'today computed in Bangkok time')
        def ag(r,t=today):
            if r['date_precision'].startswith('unknown'):return None
            return max(0,(dt.date.fromisoformat(t)-dt.date.fromisoformat(r['date'][:10])).days)
        exp={'7':sum(1 for r in D if ag(r) is not None and ag(r)<=7),'30':sum(1 for r in D if ag(r) is not None and ag(r)<=30),'365':sum(1 for r in D if ag(r) is not None and ag(r)<=365),'old':sum(1 for r in D if ag(r) is None or ag(r)>365)}
        ok(await pg.text_content('#kW')==str(exp['7']),'New this week KPI')
        for k,v in exp.items():
            await pg.click(f'.rc[data-rc="{k}"]');await pg.wait_for_timeout(150)
            ok(await cnt(pg)==v and await pg.get_attribute(f'.rc[data-rc="{k}"]','aria-pressed')=='true',f'recency chip {k} -> {v}')
        await pg.click('.rc[data-rc="7"]');await pg.select_option('#fBrand','Malee');await pg.wait_for_timeout(150)
        ok(await cnt(pg)==sum(1 for r in D if 'Malee' in r['brand'] and ag(r) is not None and ag(r)<=7),'recency + brand combo')
        await pg.click('.rc[data-rc="old"]');await pg.select_option('#fBrand','');await pg.wait_for_timeout(150)
        ok(await pg.locator('#feedBox .m.is-old').count()==await pg.locator('#feedBox .m').count()>0,'old items are faded (is-old)')
        await reset(pg);ok(await cnt(pg)==len(D) and await pg.get_attribute('.rc[data-rc=""]','aria-pressed')=='true','reset clears recency chip')
        ages=await pg.eval_on_selector_all('#feedBox .m','e=>e.map(x=>+x.dataset.age)')
        ok(ages==sorted(ages) and len(ages)>1,'feed sorted newest first by default')
        await pg.select_option('#fSort','old');await pg.wait_for_timeout(150)
        ages2=await pg.eval_on_selector_all('#feedBox .m','e=>e.map(x=>+x.dataset.age)')
        ok(ages2==sorted(ages2,reverse=True) and ages2[0]==max(ag(r) for r in D if ag(r) is not None),'sort toggle: oldest first')
        await pg.select_option('#fSort','new');await pg.wait_for_timeout(150)
        ok(await pg.locator('#cBody .rb').count()==await pg.locator('#cBody tr').count(),'every complaint row has a recency badge')
        r1=await pg.text_content('#recs .rec:nth-of-type(1) .rlatest');ok('Old · 2024' in r1,'rec 1 (mould) evidence shows Old: '+r1.strip()[:60])
        ok(await pg.locator('#recs a[data-d] + .rb').count()==await pg.locator('#recs a[data-d]').count()>0,'every rec evidence link has a badge')
        ok(await pg.locator('#legend .rb').count()==5,'legend shows all 5 labels')
        pt=await b.new_page();await pt.goto(URL+'?today=2026-11-26',wait_until='networkidle')
        ok(await pt.evaluate('TODAY')=='2026-11-26' and await pt.text_content('#kW')==str(sum(1 for r in D if ag(r,'2026-11-26') is not None and ag(r,'2026-11-26')<=7)),'labels recompute for another date (?today=)');await pt.close()
        ok(not errs,'no console errors '+str(errs[:3]))
        # mobile overflow
        m=await b.new_page(viewport={'width':360,'height':780},is_mobile=True);await m.goto(URL,wait_until='networkidle');await m.wait_for_timeout(600)
        ow=await m.evaluate("document.documentElement.scrollWidth");ok(await m.locator('.rc').count()==5,'recency chips on mobile');ok(ow<=360,f'no horizontal overflow at 360px (scrollWidth {ow})')
        wide=await m.evaluate("[...document.querySelectorAll('main *')].filter(e=>e.getBoundingClientRect().right>362&&!e.closest('.tbl')).length");ok(wide==0,f'no element wider than viewport outside scroll tables ({wide})')
        await b.close()
    print(f'\n{len(fails)} failure(s)');sys.exit(1 if fails else 0)
asyncio.run(main())
