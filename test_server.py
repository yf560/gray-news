import io
import json
import unittest
import server

class FakeResponse(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *args): self.close()

class AnalysisTests(unittest.TestCase):
    def test_citation_offsets_follow_response_text(self):
        payload = {"status": "completed", "output": [{"type": "message", "content": [
            {"type": "output_text", "text": "첫 의견", "annotations": [{"type": "url_citation", "url": "https://example.org/a", "start_index": 0, "end_index": 3}]},
            {"type": "output_text", "text": "둘째 의견", "annotations": [{"type": "url_citation", "url": "https://example.org/b", "start_index": 0, "end_index": 2}]}]}]}
        result = server.analyze("기사", "test-key", lambda request, timeout: FakeResponse(json.dumps(payload).encode()))
        self.assertEqual([result["text"][c["start"]:c["end"]] for c in result["citations"]], ["첫 의", "둘째"])

    def test_returns_only_valid_cited_sources(self):
        payload = {"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": "쟁점별 의견", "annotations": [
            {"type": "url_citation", "url": "https://example.org/a", "title": "자료 A"},
            {"type": "url_citation", "url": "https://example.org/a", "title": "중복"},
            {"type": "url_citation", "url": "javascript:alert(1)", "title": "위험"}]}]}]}
        def opener(request, timeout):
            request_data = json.loads(request.data)
            self.assertEqual(request_data["tools"], [{"type": "web_search"}])
            self.assertEqual(request_data["tool_choice"], "required")
            return FakeResponse(json.dumps(payload).encode())
        result = server.analyze("기사 본문", "test-key", opener)
        self.assertEqual(result["text"], "쟁점별 의견")
        self.assertEqual(result["sources"], [{"url": "https://example.org/a", "title": "자료 A"}])

    def test_incomplete_response_is_not_presented_as_success(self):
        def opener(request, timeout):
            return FakeResponse(b'{"status":"incomplete","output":[]}')
        with self.assertRaises(ValueError):
            server.analyze("기사", "test-key", opener)

if __name__ == "__main__": unittest.main()
