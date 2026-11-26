import io
import json
import unittest
from urllib.error import URLError, HTTPError
from unittest.mock import patch
import server

class FakeResponse(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *args): self.close()

def completion(value, finish='stop'):
    return {'choices': [{'finish_reason': finish, 'message': {'content': json.dumps(value), 'reasoning_content': 'private reasoning'}}]}

class AnalysisTests(unittest.TestCase):
    def test_rate_limit_retries_then_succeeds(self):
        calls = []
        def opener(request, timeout):
            calls.append(timeout)
            if len(calls) == 1:
                raise HTTPError(request.full_url, 429, 'limited', {'Retry-After': '1'}, io.BytesIO(b''))
            return FakeResponse(json.dumps(completion({'ok': True})).encode())
        with patch('server.time.sleep') as sleep:
            server._exaone([], 'fake', opener)
        self.assertEqual(calls, [180, 180])
        sleep.assert_called_once_with(1)

    def test_long_rate_limit_returns_clear_failure(self):
        def opener(request, timeout):
            raise HTTPError(request.full_url, 429, 'limited', {'Retry-After': '3600'}, io.BytesIO(b''))
        with self.assertRaises(server.RateLimited), patch('server.time.sleep') as sleep:
            server._exaone([], 'fake', opener)
        sleep.assert_not_called()

    def test_search_and_analysis_keep_only_retrieved_source_ids(self):
        replies = iter([
            {'results': [
                {'title': '공식 발표', 'url': 'https://example.org/a', 'content': '확대 계획'},
                {'title': '중복', 'url': 'https://example.org/a'},
                {'url': 'javascript:alert(1)'}]},
            completion({'title': '정책', 'summary': '요약', 'issues': [{'question': '확대?', 'perspectives': [{'actor': '정부', 'claim': '확대', 'evidence': '계획', 'sourceIds': ['S1', 'S99']}], 'note': None}]}),
        ])
        calls = []
        def opener(request, timeout):
            calls.append((request.full_url, json.loads(request.data)))
            return FakeResponse(json.dumps(next(replies)).encode())
        result = server.analyze('기사 본문', 'fake-exaone', 'fake-tavily', opener=opener)
        self.assertEqual(result['version'], 1)
        self.assertEqual(result['sources'], [{'id': 'S1', 'title': '공식 발표', 'url': 'https://example.org/a'}])
        self.assertEqual(result['issues'][0]['perspectives'][0]['sourceIds'], ['S1'])
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1][0], server.EXAONE_URL)
        self.assertEqual(calls[0][0], server.TAVILY_URL + '/search')
        self.assertIn('확대 계획', calls[1][1]['messages'][1]['content'])
        self.assertNotIn('private reasoning', json.dumps(result))

    def test_url_requires_successful_body_extraction(self):
        def opener(request, timeout):
            self.assertTrue(request.full_url.endswith('/extract'))
            return FakeResponse(b'{"results":[],"failed_results":[]}')
        with self.assertRaises(server.ArticleUnavailable):
            server.analyze('https://example.org/a', 'fake', 'fake', source_url='https://example.org/a', opener=opener)

    def test_truncated_model_response_is_rejected(self):
        def opener(request, timeout):
            return FakeResponse(json.dumps(completion({}, 'length')).encode())
        with self.assertRaises(server.AnalysisFailure):
            server.analyze('기사', 'fake', 'fake', opener=opener)

    def test_network_error_is_preserved(self):
        def opener(request, timeout):
            raise URLError('offline')
        with self.assertRaises(URLError):
            server.analyze('기사', 'fake', 'fake', opener=opener)

if __name__ == '__main__':
    unittest.main()
