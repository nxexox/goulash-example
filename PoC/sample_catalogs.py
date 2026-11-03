from discover import *
records=[]
def get(u):
 try:
  r=fetch(u);records.append(r);print(r['status'],u,flush=True);return r
 except Exception as e:records.append({'url':u,'error':str(e)});print(e,flush=True);return {}
base=json.loads((ROOT/'sources.json').read_text()) if (ROOT/'sources.json').exists() else [json.loads(p.read_text()) for p in ROOT.glob('*.json') if p.name!='sources.json']
for r in base:
 u=r.get('url','')
 if 'icatalog' in u:
  for x in [x for x in r.get('links',[]) if '/exhibitors/' in x][:5]:get(x);time.sleep(.5)
 elif u=='https://sppiunion.ru/':
  for x in [x for x in r.get('links',[]) if '/uchastniki/id' in x][:5]:get(x);time.sleep(.5)
 elif 'world-food.ru/' in u:
  for x in r.get('links',[]):
   if 'exhibitors-itegroup' in x:get(x)
(ROOT/'catalog_samples.json').write_text(json.dumps(records,ensure_ascii=False,indent=2))
for r in records:
 print('\nURL',r['url']);print(r.get('text','')[:5000]);print('EXTERNAL', [x for x in r.get('links',[]) if urlsplit(x).hostname!=urlsplit(r['url']).hostname][:25])
