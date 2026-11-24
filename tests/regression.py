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
        await pg.goto(URL+'?period=all',wait_until='networkidle');await pg.wait_for_timeout(800)
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
        pt=await b.new_page();await pt.goto(URL+'?today=2026-11-26&period=all',wait_until='networkidle')
        ok(await pt.evaluate('TODAY')=='2026-11-26' and await pt.text_content('#kW')==str(sum(1 for r in D if ag(r,'2026-11-26') is not None and ag(r,'2026-11-26')<=7)),'labels recompute for another date (?today=)');await pt.close()
        # ---- Malee health gauge ----
        RW={'new':1,'recent':.75,'year':.4,'old':.1,'unk':.1}
        def bk(a): return 'unk' if a is None else 'new' if a<=7 else 'recent' if a<=30 else 'year' if a<=365 else 'old'
        def hs(brand,t=today,mx=None):
            num=den=0;rows=[r for r in D if brand in r['brand'] and (mx is None or (ag(r,t) is not None and ag(r,t)<=mx))]
            for r in rows:
                w=RW[bk(ag(r,t))];sev=r['sentiment']=='neg' and r['complaint'] and r.get('severity')=='High'
                v={'pos':1,'neu':0}.get(r['sentiment'],-2 if sev else -1);num+=w*v;den+=w*(2 if sev else 1)
            return len(rows),(int(50+50*num/den+.5) if den else None)
        def bandp(x): return 'Healthy' if x>=60 else 'Watch' if x>=40 else 'At risk'
        await reset(pg);await pg.evaluate("localStorage.removeItem('malee-sl-v1')");await pg.reload(wait_until='networkidle');await pg.wait_for_timeout(400)
        n,sc=hs('Malee')
        ok(await pg.text_content('#gScore')==str(sc),f'gauge score matches formula ({sc}, n={n})')
        ok((await pg.text_content('#gStatus')).startswith(bandp(sc)),'gauge status band '+bandp(sc))
        ok(abs(float(re.search(r'rotate\(([-\d.e]+)',await pg.get_attribute('#gNeedle','transform')).group(1))-(sc-50)*1.8)<.01,'needle angle matches score')
        ok(await pg.evaluate("[band(60)[0],band(59)[0],band(40)[0],band(39)[0],band(null)[0]]")==['ok','watch','watch','risk','fl'],'band boundaries 60/40')
        ok(await pg.evaluate("health('Malee').n")==n,'gauge uses only Malee mentions')
        for b2 in ['Tipco','Doi Kham','if','Cocomax','Chaokoh']:
            n2,s2=hs(b2);row=pg.locator(f'#hComp tr[data-brand="{b2}"]')
            ok(await row.get_attribute('data-score')==str(s2) and f'n={n2}' in await row.text_content() and (('low sample' in await row.text_content())==(n2<10)),f'competitor {b2} score {s2} n={n2}')
        n30,s30=hs('Malee',mx=30);tr=await pg.text_content('#hTr')
        ok(str(s30) in tr and (('▲' in tr) if s30-sc>3 else ('▼' in tr) if s30-sc<-3 else ('►' in tr)),f'30-day trend {s30} vs {sc}: {tr}')
        sevn=sum(1 for r in D if 'Malee' in r['brand'] and r['complaint'] and r.get('severity')=='High')
        ok(await pg.text_content('#hSev')==str(sevn),f'severe open issues {sevn}')
        sel=pg.locator('#cBody tr:has-text("Malee") select[data-k=status]');k=0
        for i in range(await sel.count()):
            rid=await sel.nth(i).get_attribute('data-id');rr=[r for r in D if r['id']==rid][0]
            if rr.get('severity')=='High':await sel.nth(i).select_option('Closed');k=1;break
        if k: ok(await pg.text_content('#hSev')==str(sevn-1),'closing a severe issue lowers the count')
        await pg.evaluate("localStorage.removeItem('malee-sl-v1')")
        ok(await pg.locator('#health details.how summary').count()==1,'formula expander present')
        await pg.click('#hDrvA');await pg.wait_for_timeout(300);tag=await pg.get_attribute('#hDrvA','data-tag')
        ok(await cnt(pg)==sum(1 for r in D if 'Malee' in r['brand'] and r['sentiment']=='neg' and tag in r['tags']) and await pg.input_value('#fTopic')==tag,f'main driver link filters feed to {tag}')
        await reset(pg)
        pt=await b.new_page();await pt.goto(URL+'?today=2028-01-01&period=all',wait_until='networkidle')
        ok(await pt.text_content('#gScore')==str(hs('Malee','2028-01-01')[1]),'gauge respects ?today= override');await pt.close()
        # ---- Why this score (drivers) ----
        def drv(t=today,mx=None):
            rows=[r for r in D if 'Malee' in r['brand'] and (mx is None or (ag(r,t) is not None and ag(r,t)<=mx))];den=0;items=[]
            for r in rows:
                w=RW[bk(ag(r,t))];sev=r['sentiment']=='neg' and r['complaint'] and r.get('severity')=='High'
                v={'pos':1,'neu':0}.get(r['sentiment'],-2 if sev else -1);den+=w*(2 if sev else 1);items.append((r,w,v))
            T={}
            for r,w,v in items:
                if not v: continue
                tags=r['tags'] or ['(untagged)']
                for g in tags: T[('up' if v>0 else 'dn',g)]=T.get(('up' if v>0 else 'dn',g),0)+50*w*v/den/len(tags)
            return T
        T=drv();up=sorted([(g,p) for (sd,g),p in T.items() if sd=='up'],key=lambda x:-x[1]);dn=sorted([(g,p) for (sd,g),p in T.items() if sd=='dn'],key=lambda x:x[1])
        ok(abs(50+sum(T.values())-sc)<=0.5,f'contributions add up to score (50{sum(T.values()):+.2f} = {50+sum(T.values()):.2f} vs {sc})')
        ok(await pg.locator('#why h3').text_content()==f'Why {sc}?','why heading shows score')
        rows_up=await pg.eval_on_selector_all('#why .wcol.up .drow','e=>e.map(x=>[x.dataset.tag,+x.dataset.pts])')
        rows_dn=await pg.eval_on_selector_all('#why .wcol.dn .drow','e=>e.map(x=>[x.dataset.tag,+x.dataset.pts])')
        ok([g for g,_ in rows_up]==[g for g,_ in up[:3]] and all(abs(a[1]-b2[1])<1e-3 for a,b2 in zip(rows_up,up)),f'pushing-up drivers match independent calc {rows_up}')
        ok([g for g,_ in rows_dn]==[g for g,_ in dn[:3]] and all(abs(a[1]-b2[1])<1e-3 for a,b2 in zip(rows_dn,dn)),f'pulling-down drivers match independent calc {rows_dn}')
        eq=await pg.text_content('#whyEq');U=sum(p for _,p in up);Dn=sum(p for _,p in dn)
        ok(f'{abs(U):.1f}' in eq and f'{abs(Dn):.1f}' in eq and f'{50+U+Dn:.1f}' in eq,'equation line shows the sums')
        ok(await pg.locator('#why .drow .ex a[href^="https://"]').count()==len(rows_up)+len(rows_dn),'each driver has a linked example')
        T30=drv(mx=30);s30x=round(50+sum(T30.values())) if T30 else None
        ok(f'30-day score {s30} vs {sc}' in await pg.text_content('#why30') and s30x==s30,'30-day note matches 30-day score')
        dl=pg.locator('#why .wcol.dn .dlink').first;tg=await dl.get_attribute('data-tag');await dl.click();await pg.wait_for_timeout(300)
        ok(await cnt(pg)==sum(1 for r in D if 'Malee' in r['brand'] and r['sentiment']=='neg' and tg in r['tags']),f'driver link filters feed ({tg}, negative)')
        await reset(pg)
        pt=await b.new_page();await pt.goto(URL+'?today=2028-01-01&period=all',wait_until='networkidle');T2=drv('2028-01-01')
        ok(abs(50+sum(T2.values())-int(await pt.text_content('#gScore')))<=0.5 and (await pt.locator('#why h3').text_content())==('Why '+await pt.text_content('#gScore')+'?'),'drivers respect ?today=');await pt.close()
        # ---- brand switcher: every brand vs independent calc ----
        def hsb(brand,t=today,mx=None):
            rows=[r for r in D if brand in r['brand'] and (mx is None or (ag(r,t) is not None and ag(r,t)<=mx))];num=den=0;T={}
            for r in rows:
                w=RW[bk(ag(r,t))];sev=r['sentiment']=='neg' and r['complaint'] and r.get('severity')=='High'
                v={'pos':1,'neu':0}.get(r['sentiment'],-2 if sev else -1);num+=w*v;den+=w*(2 if sev else 1)
            for r in rows:
                w=RW[bk(ag(r,t))];sev=r['sentiment']=='neg' and r['complaint'] and r.get('severity')=='High'
                v={'pos':1,'neu':0}.get(r['sentiment'],-2 if sev else -1)
                if not v: continue
                tags=r['tags'] or ['(untagged)']
                for g in tags:T[('up' if v>0 else 'dn',g)]=T.get(('up' if v>0 else 'dn',g),0)+50*w*v/den/len(tags)
            return len(rows),(int(50+50*num/den+.5) if den else None),T
        await pg.evaluate("localStorage.removeItem('malee-sl-v1')");await pg.reload(wait_until='networkidle');await pg.wait_for_timeout(400)
        for br in ['Malee','Tipco','Doi Kham','if','Cocomax','Chaokoh']:
            await pg.select_option('#hBrand',br);await pg.wait_for_timeout(150)
            n_,s_,T_=hsb(br);n30,s30_,_=hsb(br,mx=30)
            up_=sorted([(g,p) for (sd,g),p in T_.items() if sd=='up'],key=lambda x:-x[1])[:3];dn_=sorted([(g,p) for (sd,g),p in T_.items() if sd=='dn'],key=lambda x:x[1])[:3]
            gu=await pg.eval_on_selector_all('#why .wcol.up .drow','e=>e.map(x=>[x.dataset.tag,+x.dataset.pts])');gd=await pg.eval_on_selector_all('#why .wcol.dn .drow','e=>e.map(x=>[x.dataset.tag,+x.dataset.pts])')
            okb=(await pg.text_content('#gScore')==str(s_) and await pg.get_attribute('#health','data-brand')==br and abs(50+sum(T_.values())-s_)<=0.5
                 and [g for g,_ in gu]==[g for g,_ in up_] and [g for g,_ in gd]==[g for g,_ in dn_] and all(abs(a[1]-c[1])<1e-3 for a,c in zip(gu+gd,up_+dn_))
                 and (await pg.is_visible('#hLow'))==(n_<10) and br in await pg.text_content('#hTitle'))
            t30=await pg.text_content('#why30');tr=await pg.text_content('#hTr')
            ok30=('not enough recent data' in t30 and 'n/a' in tr) if n30<3 else (f'30-day score {s30_} vs {s_}' in t30 and str(s30_) in tr)
            sevb=sum(1 for r in D if br in r['brand'] and r['complaint'] and r.get('severity')=='High')
            ok(okb and ok30 and await pg.text_content('#hSev')==str(sevb) and await pg.get_attribute(f'#hComp tr[data-brand="{br}"]','aria-pressed')=='true',f'brand {br}: score {s_} n={n_} 30d {s30_}(n={n30}) up {[g for g,_ in up_]} down {[g for g,_ in dn_]}')
            if br=='Tipco':
                dl=pg.locator('#why .wcol.dn .dlink').first;tg=await dl.get_attribute('data-tag');await dl.click();await pg.wait_for_timeout(300)
                ok(await pg.input_value('#fBrand')=='Tipco' and await cnt(pg)==sum(1 for r in D if 'Tipco' in r['brand'] and r['sentiment']=='neg' and tg in r['tags']),'feed link filters to the selected brand')
                await reset(pg)
        ok('brand=Chaokoh' in pg.url and 'period=all' in pg.url,'brand choice in URL')
        await pg.click('#hComp tr[data-brand="Doi Kham"]');await pg.wait_for_timeout(150);ok(await pg.input_value('#hBrand')=='Doi Kham','clicking strip selects brand')
        await pg.goto(URL,wait_until='networkidle');await pg.wait_for_timeout(300);ok(await pg.input_value('#hBrand')=='Doi Kham','brand persists in localStorage')
        await pg.select_option('#hBrand','Malee');await pg.wait_for_timeout(100)
        pq=await b.new_page();await pq.goto(URL+'?brand=Cocomax&period=all',wait_until='networkidle');ok(await pq.input_value('#hBrand')=='Cocomax' and await pq.text_content('#gScore')==str(hsb('Cocomax')[1]),'?brand= works');
        await pq.goto(URL+'?brand=Nope',wait_until='networkidle');ok(await pq.input_value('#hBrand') in ['Cocomax','Malee'],'invalid ?brand= ignored');await pq.close()
        # ---- language ----
        WL=set("""Malee Coco Tipco Doi Kham Cocomax Chaokoh Pantip Google Facebook Instagram Lemon8 TikTok YouTube LINE Teams API CSV KPI PETA Thai PBS TCC ISRA News CIRA QR SOP CRM DM QA PR IT R&D CSR FDA UHT NFC PDPA UTC Grok Bot GitHub Actions Meta Graph Research Business Profile Shopee Lazada OA Wisesight Zocial Eye Mandala Brandwatch Talkwalker Meltwater CrowdTangle Thansettakij Post Today The Thaiger TrueID Shorts data json mentions csv MOCK Plus Eleven Matcha UK EN Lemon Maps""".split())
        async def leftovers(pp):
            return await pp.evaluate("""wl=>{const out=[];const w=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT);let n;while(n=w.nextNode()){const e=n.parentElement;if(!e||e.closest('.c,script,style,svg title,option:not(:checked)'))continue;if(!e.getClientRects().length)continue;const cs=getComputedStyle(e);if(cs.visibility==='hidden')continue;(n.textContent.match(/[A-Za-z][A-Za-z&]{2,}/g)||[]).forEach(t=>{if(!wl.includes(t))out.push(t)})}
            document.querySelectorAll('[placeholder],[aria-label],select').forEach(e=>{let v=e.tagName==='SELECT'?(e.selectedOptions[0]||{}).text||'':(e.getAttribute('placeholder')||'')+' '+(e.getAttribute('aria-label')||'');v=v.replace(/\\bM[0-9a-f]{6}\\b/g,'');(v.match(/[A-Za-z][A-Za-z&]{2,}/g)||[]).forEach(t=>{if(!wl.includes(t))out.push('@'+t)})});return [...new Set(out)]}""",list(WL))
        pl=await b.new_page(viewport={'width':1366,'height':900});el=[];pl.on('pageerror',lambda e:el.append(str(e)));pl.on('console',lambda m: el.append(m.text) if m.type=='error' else None)
        await pl.goto(URL+'?lang=th&period=all',wait_until='networkidle');await pl.wait_for_timeout(600)
        ok(await pl.evaluate('document.documentElement.lang')=='th' and 'แดชบอร์ด' in await pl.text_content('h1'),'?lang=th switches the page')
        lo=await leftovers(pl);ok(not lo,f'no untranslated UI strings in TH mode {lo[:20]}')
        ok((await pl.text_content('#gStatus')) in ['ดี','เฝ้าระวัง','เสี่ยง'] and 'ทำไม' in await pl.text_content('#why h3') and 'คะแนน 30 วัน' in await pl.text_content('#why30'),'TH gauge, why panel and 30-day note')
        ok(await pl.text_content('#gScore')==str(sc),'score unchanged in TH')
        await pl.click('.rc[data-rc="old"]');await pl.wait_for_timeout(150);lo2=await leftovers(pl);ok(not lo2,f'no untranslated strings with old items shown {lo2[:10]}')
        await pl.fill('#fFrom','2030-01-01');await pl.dispatch_event('#fFrom','change');await pl.wait_for_timeout(150)
        ok('ไม่มีการพูดถึง' in await pl.text_content('#feedBox') and 'ไม่มีคำร้องเรียน' in await pl.text_content('#cBody'),'TH empty states');lo3=await leftovers(pl);ok(not lo3,f'no untranslated strings in empty state {lo3[:10]}')
        await pl.click('#reset');await pl.select_option('#hBrand','Chaokoh');await pl.wait_for_timeout(150);lo4=await leftovers(pl);ok(not lo4 and 'ข้อมูลล่าสุด' in await pl.text_content('#why30'),f'TH low-sample brand translated {lo4[:10]}')
        await pl.select_option('#hBrand','Malee')
        await pl.goto(URL,wait_until='networkidle');ok(await pl.evaluate('document.documentElement.lang')=='th','language persists in localStorage')
        await pl.click('#lsw button[data-lang="en"]');await pl.wait_for_timeout(200);ok(await pl.evaluate('document.documentElement.lang')=='en' and 'Social Listening' in await pl.text_content('h1') and 'lang=' not in pl.url,'switch back to EN')
        ok(not el,'no console errors in language tests '+str(el[:3]));await pl.close()
        for lg in ['en','th']:
            mm=await b.new_page(viewport={'width':375,'height':800},is_mobile=True);await mm.goto(URL+f'?lang={lg}',wait_until='networkidle');await mm.wait_for_timeout(500)
            w_=await mm.evaluate("document.documentElement.scrollWidth");wd=await mm.evaluate("[...document.querySelectorAll('body *')].filter(e=>e.getBoundingClientRect().right>377&&!e.closest('.tbl,.rrow')).map(e=>e.tagName+'.'+e.className).slice(0,5)")
            ok(w_<=375 and not wd,f'no overflow at 375px in {lg} ({w_}) {wd}');await mm.close()
        pt2=await b.new_page();await pt2.goto(URL+'?today=2028-01-01&lang=th&brand=Tipco&period=all',wait_until='networkidle')
        ok(await pt2.text_content('#gScore')==str(hsb('Tipco','2028-01-01')[1]) and await pt2.evaluate('TODAY')=='2028-01-01','?today= works with ?lang and ?brand');await pt2.close()
        # ---- period: last 12 months (default) vs all time ----
        def inp(r,per,t=today): return per=='all' or (ag(r,t) is not None and ag(r,t)<=365)
        def hsp(brand,per,t=today,mx=None):
            rows=[r for r in D if inp(r,per,t) and brand in r['brand'] and (mx is None or (ag(r,t) is not None and ag(r,t)<=mx))];num=den=0;T={}
            items=[]
            for r in rows:
                w=RW[bk(ag(r,t))];sev=r['sentiment']=='neg' and r['complaint'] and r.get('severity')=='High'
                v={'pos':1,'neu':0}.get(r['sentiment'],-2 if sev else -1);num+=w*v;den+=w*(2 if sev else 1);items.append((r,w,v))
            for r,w,v in items:
                if not v: continue
                tags=r['tags'] or ['(untagged)']
                for g in tags:T[('up' if v>0 else 'dn',g)]=T.get(('up' if v>0 else 'dn',g),0)+50*w*v/den/len(tags)
            sc_=int(50+50*num/den+.5) if den and len(rows)>=3 else None
            return len(rows),sc_,T
        p2=await b.new_page(viewport={'width':1366,'height':900});e2=[];p2.on('pageerror',lambda e:e2.append(str(e)));p2.on('console',lambda m: e2.append(m.text) if m.type=='error' else None)
        await p2.goto(URL,wait_until='networkidle');await p2.wait_for_timeout(500)
        ok(await p2.evaluate('PERIOD')=='12m' and await p2.get_attribute('#ptog button[data-p="12m"]','aria-pressed')=='true','12m is the default')
        n12=sum(1 for r in D if inp(r,'12m'));ok(await cnt(p2)==n12 and await p2.text_content('#kM')==str(n12),f'feed/KPI use 12m ({n12} of {len(D)})')
        ok(not any(await p2.eval_on_selector_all('#feedBox .m','e=>e.map(x=>x.dataset.age===""||+x.dataset.age>365)')),'no old or undated items in 12m feed')
        ok(await p2.evaluate("charts.cPlat.data.datasets[0].data.reduce((a,b)=>a+b,0)")==n12,'charts use 12m')
        for per in ['12m','all']:
            await p2.click(f'#ptog button[data-p="{per}"]');await p2.wait_for_timeout(200)
            for br in ['Malee','Tipco','Doi Kham','if','Cocomax','Chaokoh']:
                await p2.select_option('#hBrand',br);await p2.wait_for_timeout(120)
                n_,s_,T_=hsp(br,per);up_=sorted([(g,p) for (sd,g),p in T_.items() if sd=='up'],key=lambda x:-x[1])[:3];dn_=sorted([(g,p) for (sd,g),p in T_.items() if sd=='dn'],key=lambda x:x[1])[:3]
                gu=await p2.eval_on_selector_all('#why .wcol.up .drow','e=>e.map(x=>[x.dataset.tag,+x.dataset.pts])');gd=await p2.eval_on_selector_all('#why .wcol.dn .drow','e=>e.map(x=>[x.dataset.tag,+x.dataset.pts])')
                strip=await p2.get_attribute(f'#hComp tr[data-brand="{br}"]','data-score')
                sevb=sum(1 for r in D if inp(r,per) and br in r['brand'] and r['complaint'] and r.get('severity')=='High')
                good=(await p2.text_content('#gScore')==(str(s_) if s_ is not None else '–') and strip==(str(s_) if s_ is not None else '') and await p2.text_content('#hSev')==str(sevb)
                      and [g for g,_ in gu]==[g for g,_ in up_] and [g for g,_ in gd]==[g for g,_ in dn_] and all(abs(a[1]-c[1])<1e-3 for a,c in zip(gu+gd,up_+dn_))
                      and (s_ is None or abs(50+sum(T_.values())-s_)<=0.5) and (await p2.is_visible('#hLow'))==(n_<10))
                olds=[r for r in D if br in r['brand'] and r['complaint'] and r.get('severity')=='High' and not inp(r,'12m')]
                note=await p2.is_visible('#hOld');good=good and note==(per=='12m' and bool(olds))
                ok(good,f'{per} {br}: score {s_} n={n_} up {[g for g,_ in up_][:1]} down {[g for g,_ in dn_][:1]} old-severe-note {note}')
        await p2.click('#ptog button[data-p="12m"]');await p2.select_option('#hBrand','Malee');await p2.wait_for_timeout(150)
        ot=await p2.text_content('#hOld');ok('past severe' in ot and '2013' in ot,'old severe note text: '+ot)
        await p2.click('#hOld');await p2.wait_for_timeout(200);ok(await p2.evaluate('PERIOD')=='all' and 'period=all' in p2.url and await cnt(p2)==len(D),'old-issue note switches to All time')
        await p2.reload(wait_until='networkidle');await p2.wait_for_timeout(300);ok(await p2.evaluate('PERIOD')=='all','period persists (URL)')
        await p2.goto(URL,wait_until='networkidle');await p2.wait_for_timeout(300);ok(await p2.evaluate('PERIOD')=='all','period persists (localStorage)')
        await p2.click('#ptog button[data-p="12m"]');await p2.wait_for_timeout(200);ok('period=' not in p2.url,'12m removes ?period from URL')
        ok(await p2.locator('#recs .rec.hist').count()==1 and await p2.locator('#recs .rec:nth-of-type(1).hist .histn').count()==1,'food-safety rec kept visible, marked Historical in 12m')
        await p2.click('.rc[data-rc="old"]');await p2.wait_for_timeout(250)
        ok(await p2.evaluate('PERIOD')=='all' and await cnt(p2)==sum(1 for r in D if ag(r) is None or ag(r)>365),'Older chip in 12m switches to All time')
        await p2.click('#ptog button[data-p="12m"]');await p2.wait_for_timeout(200);ok(await p2.get_attribute('.rc[data-rc=""]','aria-pressed')=='true' and await cnt(p2)==n12,'switching back to 12m clears Older chip')
        async with p2.expect_download() as dl2: await p2.click('#csv')
        dv=await dl2.value;ok('last-12-months' in dv.suggested_filename and sum(1 for x in open(await dv.path(),encoding='utf-8-sig').read().split('\n') if x.startswith('"M'))==n12,'CSV export is period-labelled and 12m only')
        await p2.goto(URL+'?lang=th',wait_until='networkidle');await p2.wait_for_timeout(400);lo5=await leftovers(p2)
        ok(not lo5 and 'ประเด็นร้ายแรงในอดีต' in await p2.text_content('#hOld') and 'ข้อมูลในอดีต' in await p2.text_content('#recs'),f'TH 12m translated {lo5[:10]}')
        await p2.goto(URL+'?lang=en',wait_until='networkidle');ok(not e2,'no console errors in period tests '+str(e2[:3]));await p2.close()
        for lg in ['en','th']:
            for per in ['12m','all']:
                mm=await b.new_page(viewport={'width':375,'height':800},is_mobile=True);await mm.goto(URL+f'?lang={lg}&period={per}',wait_until='networkidle');await mm.wait_for_timeout(400)
                w_=await mm.evaluate("document.documentElement.scrollWidth");wd=await mm.evaluate("[...document.querySelectorAll('body *')].filter(e=>e.getBoundingClientRect().right>377&&!e.closest('.tbl,.rrow')).length")
                ok(w_<=375 and not wd,f'no overflow at 375px {lg}/{per}');await mm.close()
        p3=await b.new_page();await p3.goto(URL+'?today=2028-01-01&brand=Tipco',wait_until='networkidle');n3,s3,_=hsp('Tipco','12m','2028-01-01')
        ok(await p3.text_content('#gScore')==(str(s3) if s3 is not None else '–') and await cnt(p3)==sum(1 for r in D if inp(r,'12m','2028-01-01')),f'?today= works in 12m (Tipco n={n3}, score {s3})');await p3.close()
        ok(not errs,'no console errors '+str(errs[:3]))
        # mobile overflow
        m=await b.new_page(viewport={'width':375,'height':780},is_mobile=True);await m.goto(URL,wait_until='networkidle');await m.wait_for_timeout(600)
        ow=await m.evaluate("document.documentElement.scrollWidth");ok(await m.locator('.rc').count()==5,'recency chips on mobile');ok(ow<=375 and await m.locator('#health svg').is_visible(),f'gauge visible, no horizontal overflow at 375px (scrollWidth {ow})')
        wide=await m.evaluate("[...document.querySelectorAll('main *')].filter(e=>e.getBoundingClientRect().right>377&&!e.closest('.tbl')).length");ok(wide==0,f'no element wider than viewport outside scroll tables ({wide})')
        # complaints must stay readable on a phone: columns used to collapse to ~24px / ~46px and wrap one letter per line
        for lg in ['en','th']:
            cm=await b.new_page(viewport={'width':390,'height':844},is_mobile=True);await cm.goto(URL+f'?lang={lg}&period=all',wait_until='networkidle');await cm.wait_for_timeout(400)
            info=await cm.evaluate("""() => {
              const rows=[...document.querySelectorAll('#cBody tr')].filter(tr=>tr.children.length>5);
              const lines=el=>{const t=[...el.childNodes].find(n=>n.nodeType===3&&n.textContent.trim())||el;const r=document.createRange();r.selectNodeContents(t);return [...r.getClientRects()].filter(x=>x.width>1)};
              return {scroll:document.documentElement.scrollWidth, rows:rows.map(tr=>{
                const id=tr.children[0], issueTd=tr.children[4], issue=issueTd.querySelector('.c');
                const idLines=lines(id), issueLines=lines(issue);
                return {id:id.getBoundingClientRect().width, idText:idLines[0]?idLines[0].width:0, idLines:idLines.length,
                  issue:issueTd.getBoundingClientRect().width, line:issueLines.reduce((m,x)=>Math.max(m,x.width),0)};
              })};
            }""")
            iw=min(r['issue'] for r in info['rows']); lw=min(r['line'] for r in info['rows']); dw=min(r['id'] for r in info['rows']); tw=min(r['idText'] for r in info['rows'])
            ok(len(info['rows'])>=8 and info['scroll']<=390 and iw>=220 and lw>=120 and dw>=120 and tw>=36 and all(r['idLines']==1 for r in info['rows']),
               f'complaint rows readable at 390px {lg}: issue-cell {iw:.0f}px line {lw:.0f}px id-cell {dw:.0f}px id-text {tw:.0f}px scroll {info["scroll"]}')
            sel=cm.locator('#cBody select[data-k=status]').first
            await sel.select_option('Investigating')
            ok(await sel.input_value()=='Investigating','status select works in the phone complaint card')
            await cm.close()
        dk=await b.new_page(viewport={'width':1280,'height':800});await dk.goto(URL,wait_until='networkidle');await dk.wait_for_timeout(400)
        desk=await dk.evaluate("""() => {const td=document.querySelector('#cBody td');const th=document.querySelector('#complaints thead');
          return {td:getComputedStyle(td).display, th:getComputedStyle(th).display, issue:document.querySelector('#cBody td:nth-child(5)').getBoundingClientRect().width, clab:getComputedStyle(document.querySelector('#cBody .clab')).display}}""")
        ok(desk['td']=='table-cell' and desk['th']=='table-header-group' and desk['clab']=='none' and desk['issue']>300, f'desktop complaints table unchanged {desk}')
        await dk.close()
        await b.close()
    print(f'\n{len(fails)} failure(s)');sys.exit(1 if fails else 0)
asyncio.run(main())
