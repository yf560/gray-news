import json
import re

import server
import job_store as store


PLAN = '''Read the article and identify up to 2 central issues. For each issue, formulate two genuinely competing positions before searching: A and B. They must be opposing claims about the same question, not two unrelated facts. Do not decide which is true. Return JSON only:
{"title":"article title","summary":"neutral article summary","issues":[{"id":"I1","question":"shared question","positions":[{"id":"A","candidate_claim":"concise position A","actor_to_find":"speaker or institution likely to hold it","query":"search query specifically to find evidence for A"},{"id":"B","candidate_claim":"the opposing position B","actor_to_find":"speaker or institution likely to hold it","query":"search query specifically to find evidence for B"}]}]}
Use one distinct, specific query per position. Search both sides of the issue. Candidate claims guide search only; they are not results unless supplied sources support them.'''

DRAFT = '''Use only the supplied article and documents. For each planned position A and B, find whether a source actually expresses it. Return JSON only:
{"issues":[{"id":"I1","perspectives":[{"issue_id":"I1","position_id":"A","actor":"actual speaker or institution","claim":"short representative statement of their position","evidence":"one concise sentence summarizing the source's relevant reason or evidence in your own words","document_id":"D1","quote":"short exact phrase used only for internal validation"}]}]}
Return no more than one perspective for each position. Include a position only when its assigned search documents support it. Do not copy a passage into evidence; do not invent a speaker, counterargument, or supporting reason. The claim should be a concise proposition, often phrased as a clear policy choice or action. If evidence for a position is absent, omit that perspective.'''

REVIEW = 'Review each perspective against its exact source context. Return JSON only: {"checks":[{"index":0,"verdict":"supported|overstatement|mismatched|insufficient","reason":""}]}. A quote can exist but be unrelated. Do not decide factual truth.'


def parsed(text):
    a, b = text.find('{'), text.rfind('}')
    if a < 0 or b < a:
        raise server.AnalysisFailure('LLM JSON 응답을 읽지 못했습니다.')
    try:
        return json.loads(text[a:b + 1])
    except json.JSONDecodeError as exc:
        raise server.AnalysisFailure('LLM JSON 형식이 올바르지 않습니다.') from exc


def call(prompt, data, key):
    messages = [{'role': 'system', 'content': prompt},
                {'role': 'user', 'content': json.dumps(data, ensure_ascii=False)}]
    return parsed(server._exaone(messages, key))


def norm(text):
    return re.sub(r'\s+', ' ', text).strip()


def run(job_id):
    job = store.get_job(job_id)
    inp = job['input']
    article = inp['text']
    exaone = server.os.environ['EXAONE_API_KEY']
    tavily = server.os.environ['TAVILY_API_KEY']
    url = inp.get('url')

    saved_article = store.saved_stage(job_id, 'article')
    if saved_article:
        article = saved_article['text']
    elif url:
        article = server._extract_article(url, tavily)
        store.checkpoint(job_id, 'article', {'text': article})
    if len(article) > 50000:
        raise ValueError('기사 내용은 50,000자 이내로 입력해 주세요.')

    plan = store.saved_stage(job_id, 'plan')
    if not plan:
        store.checkpoint(job_id, 'plan_started', {})
        plan = call(PLAN, article, exaone)
    issues = plan.get('issues')
    if not isinstance(issues, list) or not issues:
        raise server.AnalysisFailure('기사 쟁점을 만들지 못했습니다.')

    issues = issues[:2]
    positions = []
    for issue in issues:
        sides = issue.get('positions', [])
        sides = [side for side in sides if isinstance(side, dict) and side.get('id') in ('A', 'B')]
        sides = {side['id']: side for side in sides}
        if not all(key in sides for key in ('A', 'B')):
            raise server.AnalysisFailure('서로 다른 두 입장을 계획하지 못했습니다.')
        issue['positions'] = [sides['A'], sides['B']]
        for side in issue['positions']:
            query = side.get('query')
            claim = side.get('candidate_claim')
            if not isinstance(query, str) or not query.strip() or not isinstance(claim, str) or not claim.strip():
                raise server.AnalysisFailure('입장별 검색 계획이 올바르지 않습니다.')
            side['search_key'] = f"{issue.get('id')}:{side['id']}"
            positions.append(side)
    plan['issues'] = issues
    store.checkpoint(job_id, 'plan', plan)

    docs = store.load_documents(job_id)
    if not docs:
        if url:
            docs.append({'id': 'D0', 'title': plan.get('title', '입력 기사'), 'url': url,
                         'text': article[:3000], 'coverage': 'full' if len(article) <= 3000 else 'excerpt',
                         'positionIds': []})
        for side in positions:
            response = server._post_json(server.TAVILY_URL + '/search', {
                'query': side['query'][:300], 'topic': 'general',
                'search_depth': 'basic', 'max_results': 5,
            }, tavily)
            kept = 0
            for item in response.get('results', []):
                found = item.get('url', '')
                parsed_url = server.urlparse(found)
                if parsed_url.scheme not in ('https', 'http') or not parsed_url.netloc or parsed_url.username or parsed_url.password:
                    continue
                existing = next((doc for doc in docs if doc['url'] == found), None)
                if existing:
                    if side['search_key'] not in existing['positionIds']:
                        existing['positionIds'].append(side['search_key'])
                    kept += 1
                    if kept >= 2:
                        break
                    continue
                docs.append({'id': f'D{len(docs)}', 'title': str(item.get('title') or found)[:300],
                             'url': found, 'text': str(item.get('content') or '')[:2500],
                             'coverage': 'snippet_only', 'positionIds': [side['search_key']]})
                kept += 1
                if kept >= 2:
                    break
    if not docs:
        raise server.AnalysisFailure('검색 자료를 찾지 못했습니다.')
    store.save_documents(job_id, docs)
    store.checkpoint(job_id, 'retrieve', [{k: d[k] for k in ('id', 'title', 'url', 'coverage')} for d in docs])

    draft = store.saved_stage(job_id, 'draft')
    if not draft:
        draft = call(DRAFT, {'plan': plan, 'article': article[:12000], 'documents': docs}, exaone)
        store.checkpoint(job_id, 'draft', draft)

    dmap = {doc['id']: doc for doc in docs}
    views = [view for issue in draft.get('issues', [])[:2]
             for view in issue.get('perspectives', [])[:2]]
    valid = []
    for view in views:
        doc = dmap.get(view.get('document_id'))
        quote = view.get('quote', '')
        search_key = f"{view.get('issue_id')}:{view.get('position_id')}"
        if (doc and search_key in doc.get('positionIds', []) and isinstance(quote, str)
                and len(norm(quote)) >= 8 and norm(quote) in norm(doc['text'])):
            valid.append(view)

    review = store.saved_stage(job_id, 'review')
    if not review:
        review = call(REVIEW, {'article': article[:8000], 'documents': docs, 'perspectives': valid}, exaone) if valid else {'checks': []}
        store.checkpoint(job_id, 'review', review)
    checks = {item.get('index'): item.get('verdict') for item in review.get('checks', []) if isinstance(item, dict)}

    evidence, output = [], []
    for issue in issues:
        draft_issue = next((item for item in draft.get('issues', []) if item.get('id') == issue.get('id')), {})
        drafted = {view.get('position_id'): view for view in draft_issue.get('perspectives', [])}
        perspectives = []
        for side in issue['positions']:
            view = drafted.get(side['id'])
            if view in valid and checks.get(valid.index(view)) == 'supported':
                doc = dmap[view['document_id']]
                evidence.append({'id': f'E{len(evidence) + 1}', 'documentId': doc['id'],
                                 'quote': view['quote'], 'coverage': doc['coverage']})
                perspectives.append({'actor': str(view.get('actor', ''))[:100],
                                     'claim': str(view.get('claim', ''))[:260],
                                     'evidence': str(view.get('evidence', ''))[:500],
                                     'sourceIds': [doc['id']]})
            else:
                perspectives.append({'actor': '자료 확인 필요',
                                     'claim': f"{side['id']} 입장을 뒷받침할 자료를 찾지 못했습니다.",
                                     'evidence': '검색 자료에서 이 입장을 직접 뒷받침하는 근거를 확인하지 못했습니다.',
                                     'sourceIds': []})
        output.append({'question': str(issue.get('question', ''))[:240],
                       'perspectives': perspectives, 'note': None})

    if not evidence:
        raise server.AnalysisFailure('검증을 통과한 근거 연결을 확보하지 못했습니다.')
    result = {'version': 1, 'title': str(plan.get('title') or '기사 쟁점')[:200],
              'summary': str(plan.get('summary') or '')[:1000], 'issues': output,
              'sources': [{'id': d['id'], 'title': d['title'], 'url': d['url']} for d in docs],
              'evidence': evidence,
              'limitations': ['자료의 입장을 정리했으며 사실 여부를 판정하지 않습니다.']
                              + (['일부 입장을 뒷받침하는 자료를 찾지 못했습니다.']
                                 if any(not p['sourceIds'] for issue in output for p in issue['perspectives']) else [])
                              + (['검색 결과 일부 발췌만 확인했습니다.']
                                 if any(d['coverage'] == 'snippet_only' for d in docs) else [])}
    store.checkpoint(job_id, 'result', {'evidenceCount': len(evidence)})
    store.finish(job_id, result)
