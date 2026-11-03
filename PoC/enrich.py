from discover import *
r=json.loads((ROOT/'catalog_samples.json').read_text());w=next(x for x in r if 'exhibitoronline' in x['url'])
urls=[x for x in w['links'] if re.search(r'/ExbDetails/[^/]+$',x)][:3]+['https://food-fresh.ru/','https://www.ingredico.ru/','https://tkdip.ru/']
out=[]
for u in urls:
 try:
  x=fetch(u);out.append(x);print(x['status'],u,flush=True)
 except Exception as e:out.append({'url':u,'error':str(e)})
 time.sleep(.5)
(ROOT/'enrichment.json').write_text(json.dumps(out,ensure_ascii=False,indent=2))
