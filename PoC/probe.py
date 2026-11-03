import urllib.request,urllib.error,concurrent.futures,json,re,pathlib,datetime
from html.parser import HTMLParser
sites='''Наш Партнёр|our-partner.ru|HoReCa
Мос-Милк|mos-milk.ru|Молочная продукция
Ингредико|www.ingredico.ru|Ингредиенты
Фуд Фреш|food-fresh.ru|HoReCa
Horecafoods|horecafoods.ru|HoReCa
Густорико|opentgp.ru/food/horeca/|HoReCa
AGAT FOODS|agatfoods.ru|HoReCa
АРОСА|arosa.ru|HoReCa
Неос Ингредиентс|neos-ingredients.ru|Ингредиенты
GEOVITA|geo-vita.com|Упаковка
Ingred PRO|ingredpro.ru|Барные ингредиенты
Коррекс|www.korrex-td.ru|Упаковка
Tasty Coffee|tastycoffee.ru|Кофе
SFT Trading|sfttrading.ru|Кофе
Союзснаб|ssnab.ru|Ингредиенты
OSQ Group|osqgroup.ru/about/|Упаковка
Гиорд|www.giord.ru|Ингредиенты
Мистерия|www.mystery.ru/catalogs/|Упаковка
Уральский завоз|www.bazaopt.ru|Продукты
Уралагротэкс|uragrotex.ru|Продукты
Фуд Экспресс|food-exress.ru/feedback/|Продукты
ГрандПродуктЕкб|grandproduktekb.ru|Продукты
ТК ДИП|tkdip.ru|Продукты
Наш Союз|nash-souz.ru|Молочная и мясная продукция
Global Foods|globalfoods.ru|HoReCa'''
class Parser(HTMLParser):
 def __init__(self):super().__init__();self.links=[];self.title='';self.intitle=False;self.ld=0
 def handle_starttag(self,t,a):
  a=dict(a)
  if t=='a':self.links.append(a.get('href',''))
  if t=='title':self.intitle=True
  if t=='script' and a.get('type')=='application/ld+json':self.ld+=1
 def handle_endtag(self,t):
  if t=='title':self.intitle=False
 def handle_data(self,d):
  if self.intitle:self.title+=d

def check(line):
 name,domain,category=line.split('|');u='https://'+domain;r={'name':name,'url':u,'category':category,'checked_at':datetime.datetime.now(datetime.timezone.utc).isoformat()}
 try:
  req=urllib.request.Request(u,headers={'User-Agent':'SupplierResearch/0.1 (single-page availability check)'})
  try:res=urllib.request.urlopen(req,timeout=22)
  except urllib.error.HTTPError as e:res=e
  b=res.read(3000000);s=b.decode(res.headers.get_content_charset() or 'utf-8',errors='replace');p=Parser();p.feed(s)
  r.update(status=res.status,final_url=res.url,bytes=len(b),title=p.title,jsonld_blocks=p.ld,contacts=[x for x in p.links if x.startswith(('tel:','mailto:'))][:8],useful_links=list(dict.fromkeys(x for x in p.links if re.search('contact|kontakt|catalog|katalog|deliver|dostav|price|pra[yi]s|\\.pdf|\\.xlsx',x,re.I)))[:15])
 except Exception as e:r['error']=str(e)
 return r
if __name__=='__main__':
 with concurrent.futures.ThreadPoolExecutor(max_workers=6) as e:results=list(e.map(check,sites.splitlines()))
 out=pathlib.Path(__file__).parent
 (out/'results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
 for r in results:print(r['name'],r.get('status',r.get('error')), 'contacts',len(r.get('contacts',[])),'links',len(r.get('useful_links',[])),r.get('title','')[:80])
