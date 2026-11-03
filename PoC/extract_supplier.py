"""CPU-only HTML extraction. Product JSON-LD stays separate from page text."""
from discover import ROOT
from html.parser import HTMLParser
import json,re
class LD(HTMLParser):
 def __init__(self):super().__init__();self.on=False;self.buf='';self.items=[];self.errors=0
 def handle_starttag(self,t,a):
  if t=='script' and dict(a).get('type')=='application/ld+json':self.on=True;self.buf=''
 def handle_data(self,d):
  if self.on:self.buf+=d
 def handle_endtag(self,t):
  if t=='script' and self.on:
   try:self.items.append(json.loads(self.buf))
   except ValueError:self.errors+=1
   self.on=False

def nodes(x):
 if isinstance(x,dict):
  yield x
  for v in x.values():yield from nodes(v)
 elif isinstance(x,list):
  for v in x:yield from nodes(v)
results=[]
for r in json.loads((ROOT/'enrichment.json').read_text()):
 if 'ExbDetails' in r['url'] or r.get('status')!=200:continue
 p=LD();p.feed((ROOT/r['snapshot']).read_bytes().decode('utf-8',errors='replace'))
 results.append(dict(source_url=r['url'],contacts=[x for x in r['links'] if x.startswith(('mailto:','tel:'))],jsonld=p.items,jsonld_errors=p.errors,products=[x for item in p.items for x in nodes(item) if x.get('@type')=='Product'],condition_evidence=[line for line in r['text'].splitlines() if re.search('минимальн|доставк|оптом|₽|руб\\.',line,re.I)][:30]))
(ROOT/'supplier_facts.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
for r in results:print(r['source_url'],'contacts',len(r['contacts']),'JSON-LD',len(r['jsonld']),'products',len(r['products']),'conditions',len(r['condition_evidence']))
