"""Offline deterministic catalog extraction and idempotent SQLite ingestion."""
from discover import ROOT
import json,re,sqlite3,hashlib
from urllib.parse import urlsplit
VERSION='catalog-v1'
def extract(r):
 if r.get('status')!=200:return None
 text=r['text'];lines=text.splitlines();url=r['url']
 if '/exhibitors/' in url:
  body=text.split('Стенд:',1)[-1].split('Предоставленная информация',1)[0]
  def field(label):
   m=re.search(r'(?:^|\n)'+re.escape(label)+r':\n([^\n]+)',body);return m.group(1) if m else None
  site=field('Сайт');name=lines[0].split(': контакты')[0]
  description=body.split('Описание:\n',1)[-1].split('Рубрики:',1)[0] if 'Описание:\n' in body else ''
  categories=body.split('Рубрики:\n',1)[-1].splitlines() if 'Рубрики:\n' in body else []
  city=field('Город')
 elif '/uchastniki/id' in url:
  name=lines[0];body=text.split('РЕЕСТР участников СППИ',1)[-1].split('Возврат к списку',1)[0]
  sites=[x for x in r['links'] if urlsplit(x).scheme in ('http','https') and urlsplit(x).hostname not in ('sppiunion.ru','www.sppiunion.ru')]
  site=sites[0] if len(sites)==1 else None;description=body;categories=[];city=None
 elif '/ExbDetails/' in url:
  body=text.split('ВЕРНУТЬСЯ К СПИСКУ УЧАСТНИКОВ\n',1)[-1].split('Modal title',1)[0]
  parts=body.splitlines();name=parts[0];city=None
  m=re.search(r'https?://[^\s]+',body);site=m.group(0) if m else None
  categories=body.split('Павильон',1)[0].splitlines()[2:]
  description=body.split('О компании\n',1)[-1]
 else:return None
 if not site:return None
 host=(urlsplit(site).hostname or '').lower().removeprefix('www.')
 return dict(source_url=url,domain=host,website=site,name=name,city=city,description=description,categories=categories,emails=sorted(set(re.findall(r'[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}',body))),evidence=body,parser_version=VERSION,status='candidate')
def ingest(db,records):
 counts={'inserted':0,'updated':0,'unchanged':0}
 for r in records:
  payload=json.dumps(r,ensure_ascii=False,sort_keys=True);h=hashlib.sha256(payload.encode()).hexdigest();old=db.execute('select hash from observations where source_url=?',(r['source_url'],)).fetchone()
  state='inserted' if old is None else 'unchanged' if old[0]==h else 'updated';counts[state]+=1
  if state!='unchanged':db.execute('insert into observations values(?,?,?) on conflict(source_url) do update set hash=excluded.hash,payload=excluded.payload',(r['source_url'],h,payload))
 db.commit();return counts
if __name__=='__main__':
 records=[v for r in (json.loads((ROOT/'catalog_samples.json').read_text())+json.loads((ROOT/'enrichment.json').read_text())) if (v:=extract(r))]
 (ROOT/'candidates.json').write_text(json.dumps(records,ensure_ascii=False,indent=2))
 db=sqlite3.connect(ROOT/'suppliers.sqlite');db.execute('create table if not exists observations(source_url text primary key,hash text,payload text)')
 first=ingest(db,records);second=ingest(db,records);assert second['unchanged']==len(records)
 # Synthetic mutation proves update behavior, without changing real evidence.
 test=sqlite3.connect(':memory:');test.execute('create table observations(source_url text primary key,hash text,payload text)');ingest(test,records)
 changed=[dict(records[0],description=records[0]['description']+' SYNTHETIC TEST')];mutation=ingest(test,changed);assert mutation['updated']==1
 summary=dict(records=len(records),domains=len(set(x['domain'] for x in records)),first=first,replay=second,synthetic_change=mutation)
 (ROOT/'pipeline_report.json').write_text(json.dumps(summary,indent=2));print(json.dumps(summary))
 for x in records:print(x['name'],x['domain'],x['city'])
