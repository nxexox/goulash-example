"""Small offline retrieval smoke test, not production Russian morphology."""
from discover import ROOT
import json,re,sys
ALIASES={'шоколад':['шоколад'],'моцарелла':['моцарелл','моцарел'],'ароматизаторы':['ароматизатор'],'джем':['джем','консервац'],'сыр':['сыров','сыры','сыра']}
def search(query):
 terms=re.findall(r'\w+',query.lower());out=[]
 for r in json.loads((ROOT/'candidates.json').read_text()):
  text=' '.join([r['name'],r['description'],*r['categories']]).lower();hits=[]
  for term in terms:
   if any(v in text for v in ALIASES.get(term,[term])):hits.append(term)
  if len(hits)==len(terms):out.append({'name':r['name'],'website':r['website'],'matched':hits,'source':r['source_url']})
 return out
if __name__=='__main__':
 queries=['шоколад','ароматизаторы','сыр','несуществующийтовар'] if len(sys.argv)==1 else [' '.join(sys.argv[1:])]
 result={q:search(q) for q in queries}
 if len(sys.argv)==1:
  assert any('ПОБЕДА' in r['name'] for r in result['шоколад'])
  assert any('ВКУСАРОМА' in r['name'] for r in result['ароматизаторы'])
  assert not result['несуществующийтовар']
 (ROOT/'search_report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps(result,ensure_ascii=False,indent=2))
