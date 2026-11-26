import json, re
import server
import job_store as store

PLAN='Read article. Return JSON only: {"title":"","summary":"","issues":[{"id":"I1","question":"","queries":["search query"]}]}. Up to 2 issues and 4 queries total. Do not decide truth or invent positions.'
DRAFT='Use only article and supplied documents. Return JSON: {"issues":[{"question":"","perspectives":[{"actor":"","claim":"","evidence":"","document_id":"D1","quote":"exact short quote for internal evidence matching","relation":"supports|challenges|context"}]}]}. Up to 3 views per issue. Write evidence as a concise synthesis in your own words: explain what this source says about the issue and attribute the position to its speaker or institution. Do not copy a passage into evidence. Keep quote as a short exact phrase used only for internal validation. Never invent a contrary position.'
REVIEW='Review each perspective against article and exact source context. Return JSON: {"checks":[{"index":0,"verdict":"supported|overstatement|mismatched|insufficient","reason":""}]}. A quote can exist but be unrelated. Do not decide factual truth.'
def parsed(text):
    a,b=text.find('{'),text.rfind('}')
    if a<0 or b<a: raise server.AnalysisFailure('LLM JSON 응답을 읽지 못했습니다.')
    try: return json.loads(text[a:b+1])
    except json.JSONDecodeError as e: raise server.AnalysisFailure('LLM JSON 형식이 올바르지 않습니다.') from e
def call(prompt,data,key):
    return parsed(server._exaone([{'role':'system','content':prompt},{'role':'user','content':json.dumps(data,ensure_ascii=False)}],key))
def norm(s): return re.sub(r'\s+',' ',s).strip()
def run(job_id):
    job=store.get_job(job_id); inp=job['input']; article=inp['text']; exaone=server.os.environ['EXAONE_API_KEY']; tavily=server.os.environ['TAVILY_API_KEY']; url=inp.get('url')
    saved_article=store.saved_stage(job_id,'article')
    if saved_article: article=saved_article['text']
    elif url:
        article=server._extract_article(url,tavily)
        store.checkpoint(job_id,'article',{'text':article})
    if len(article)>50000: raise ValueError('기사 내용은 50,000자 이내로 입력해 주세요.')
    plan=store.saved_stage(job_id,'plan')
    if not plan:
        store.checkpoint(job_id,'plan_started',{})
        plan=call(PLAN,article,exaone)
    issues=plan.get('issues')
    if not isinstance(issues,list) or not issues: raise server.AnalysisFailure('기사 쟁점을 만들지 못했습니다.')
    plan['issues']=issues[:2]; queries=[]
    for issue in plan['issues']:
        queries += [q[:300] for q in issue.get('queries',[]) if isinstance(q,str) and q.strip()]
    queries=list(dict.fromkeys(queries))[:4]; store.checkpoint(job_id,'plan',plan)
    docs=store.load_documents(job_id)
    if not docs:
        if url: docs.append({'id':'D0','title':plan.get('title','입력 기사'),'url':url,'text':article[:3000],'coverage':'full' if len(article)<=3000 else 'excerpt'})
        for query in queries:
            if len(docs)>=8: break
            data=server._post_json(server.TAVILY_URL+'/search',{'query':query,'topic':'general','search_depth':'basic','max_results':5},tavily)
            for item in data.get('results',[]):
                found=item.get('url',''); p=server.urlparse(found)
                if p.scheme not in ('https','http') or not p.netloc or p.username or p.password or any(d['url']==found for d in docs): continue
                docs.append({'id':f'D{len(docs)}','title':str(item.get('title') or found)[:300],'url':found,'text':str(item.get('content') or '')[:2500],'coverage':'snippet_only'})
                if len(docs)>=8: break
    if not docs: raise server.AnalysisFailure('검색 자료를 찾지 못했습니다.')
    store.save_documents(job_id,docs)
    store.checkpoint(job_id,'retrieve',[{k:d[k] for k in ('id','title','url','coverage')} for d in docs])
    draft=store.saved_stage(job_id,'draft')
    if not draft:
        draft=call(DRAFT,{'plan':plan,'article':article[:12000],'documents':docs},exaone)
        store.checkpoint(job_id,'draft',draft)
    views=[v for i in draft.get('issues',[])[:2] for v in i.get('perspectives',[])[:3]]
    dmap={d['id']:d for d in docs}; valid=[]
    for v in views:
        d=dmap.get(v.get('document_id')); q=v.get('quote','')
        if d and isinstance(q,str) and len(norm(q))>=8 and norm(q) in norm(d['text']): valid.append(v)
    review=store.saved_stage(job_id,'review')
    if not review:
        review=call(REVIEW,{'article':article[:8000],'documents':docs,'perspectives':valid},exaone) if valid else {'checks':[]}
        store.checkpoint(job_id,'review',review)
    checks={x.get('index'):x.get('verdict') for x in review.get('checks',[]) if isinstance(x,dict)}
    evidence=[]; output=[]
    for issue in draft.get('issues',[])[:2]:
        ps=[]
        for v in issue.get('perspectives',[])[:3]:
            if v not in valid: continue
            ix=valid.index(v)
            if checks.get(ix) != 'supported': continue
            d=dmap[v['document_id']]; eid='E'+str(len(evidence)+1)
            evidence.append({'id':eid,'documentId':d['id'],'quote':v['quote'],'coverage':d['coverage']})
            ps.append({'actor':str(v.get('actor',''))[:100],'claim':str(v.get('claim',''))[:260],'evidence':str(v.get('evidence',''))[:500],'sourceIds':[d['id']]})
        output.append({'question':str(issue.get('question',''))[:240],'perspectives':ps,'note':'인용 근거를 확보하지 못한 관점은 표시하지 않았습니다.' if len(ps)<len(issue.get('perspectives',[])) else '자료의 입장을 정리했으며 사실 여부를 판정하지 않습니다.'})
    if not any(i['perspectives'] for i in output): raise server.AnalysisFailure('검증을 통과한 근거 연결을 확보하지 못했습니다.')
    result={'version':1,'title':str(plan.get('title') or '기사 쟁점')[:200],'summary':str(plan.get('summary') or '')[:1000],'issues':output,'sources':[{'id':d['id'],'title':d['title'],'url':d['url']} for d in docs],'evidence':evidence,'limitations':['자료 인용 연결을 검토했으며 사실 판정은 하지 않았습니다.']+(['검색 결과 일부 발췌만 확인했습니다.'] if any(d['coverage']=='snippet_only' for d in docs) else [])}
    store.checkpoint(job_id,'result',{'evidenceCount':len(evidence)})
    store.finish(job_id,result)
