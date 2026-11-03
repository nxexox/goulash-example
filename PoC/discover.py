"""Bounded public catalog probe. Saves evidence; does not publish suppliers automatically."""
import urllib.request,urllib.error,json,pathlib,hashlib,re,time
from urllib.parse import urljoin,urlsplit,quote
from html.parser import HTMLParser
ROOT=pathlib.Path(__file__).parent/'poc';ROOT.mkdir(exist_ok=True)
class Page(HTMLParser):
 def __init__(self):super().__init__();self.links=[];self.text=[];self.skip=0
 def handle_starttag(self,t,a):
  a=dict(a)
  if t in ('script','style'):self.skip+=1
  if t=='a' and a.get('href'):self.links.append(a['href'])
 def handle_endtag(self,t):
  if t in ('script','style'):self.skip=max(0,self.skip-1)
 def handle_data(self,d):
  if not self.skip and d.strip():self.text.append(d.strip())
def fetch(url):
 req=urllib.request.Request(quote(url,safe=':/?=&%+'),headers={'User-Agent':'SupplierResearchPoC/0.2'})
 try:r=urllib.request.urlopen(req,timeout=20)
 except urllib.error.HTTPError as e:r=e
 b=r.read(5000000);key=hashlib.sha256(url.encode()).hexdigest()[:16];(ROOT/(key+'.raw')).write_bytes(b)
 p=Page();p.feed(b.decode(r.headers.get_content_charset() or 'utf-8',errors='replace'))
 result=dict(url=url,final_url=r.url,status=r.status,sha256=hashlib.sha256(b).hexdigest(),snapshot=key+'.raw',links=sorted(set(urljoin(r.url,x) for x in p.links if not x.startswith(('javascript:','#')))),text='\n'.join(p.text))
 (ROOT/(key+'.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2));return result
sources={
'prodexpo':'https://icatalog.expocentr.ru/ru/exhibitions/870c9c18-e84b-11ef-80ce-a0d3c1fab97f/countries/Россия?page=93',
'worldfood':'https://world-food.ru/ru/media/news/2026/august/12/uchastniki-worldfood-moscow-2026/',
'sppi':'https://sppiunion.ru/',
'neos':'https://neos-ingredients.ru/'}
if __name__=='__main__':
 results=[]
 for name,url in sources.items():
  try:
   r=fetch(url);results.append(dict(source=name,**r));print(name,r['status'],len(r['links']),r['text'][:180],flush=True)
  except Exception as e:results.append(dict(source=name,url=url,error=str(e)));print(name,str(e),flush=True)
 (ROOT/'sources.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
