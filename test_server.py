import io
import json
import os
import unittest
import server
from unittest.mock import patch
import threading
from http.server import ThreadingHTTPServer
from urllib.error import URLError
from urllib.request import Request, urlopen

class FakeResponse(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *args): self.close()

class AnalysisTests(unittest.TestCase):
    def test_returns_only_valid_cited_sources(self):
        search = {"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": "검색 자료", "annotations": [
            {"type": "url_citation", "url": "https://example.org/a", "title": "자료 A"},
            {"type": "url_citation", "url": "https://example.org/a", "title": "중복"},
            {"type": "url_citation", "url": "javascript:alert(1)", "title": "위험"}]}]}]}
        structured = {"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps({"title": "기사", "summary": "요약", "issues": []})}]}]}
        replies = iter([search, structured])
        calls = []
        def opener(request, timeout):
            request_data = json.loads(request.data)
            calls.append(request_data)
            if len(calls) == 1:
                self.assertEqual(request_data["tools"], [{"type": "web_search"}])
                self.assertEqual(request_data["tool_choice"], "required")
            return FakeResponse(json.dumps(next(replies)).encode())
        result = server.analyze("기사 본문", "test-key", opener)
        self.assertEqual(result["sources"], [{"id": "S1", "url": "https://example.org/a", "title": "자료 A"}])

    def test_incomplete_response_is_not_presented_as_success(self):
        def opener(request, timeout):
            return FakeResponse(b'{"status":"incomplete","output":[]}')
        with self.assertRaises(ValueError):
            server.analyze("기사", "test-key", opener)

    def test_returns_structured_issues_with_server_source_ids(self):
        search = {"status": "completed", "output": [{"type": "message", "content": [
            {"type": "output_text", "text": "기사 본문과 정책 설명", "annotations": [
                {"type": "url_citation", "url": "https://news.example/story", "title": "원문"},
                {"type": "url_citation", "url": "https://agency.example/policy", "title": "공식 설명"}]}]}]}
        structured = {"status": "completed", "output": [{"type": "message", "content": [
            {"type": "output_text", "text": json.dumps({
                "title": "정책 기사", "summary": "정책 변경을 다룬 기사", "issues": [{
                    "question": "정책을 확대해야 하는가", "perspectives": [{
                        "actor": "정부", "claim": "정책 확대", "evidence": "공식 계획 발표",
                        "sourceIds": ["S2"]}], "note": None}]})}]}]}
        replies = iter([search, structured])
        requests = []
        def opener(request, timeout):
            requests.append(json.loads(request.data))
            return FakeResponse(json.dumps(next(replies)).encode())

        result = server.analyze("https://news.example/story", "test-key", opener, source_url="https://news.example/story")

        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[0]["tool_choice"], "required")
        self.assertNotIn("tools", requests[1])
        self.assertEqual(result["version"], 1)
        self.assertEqual(result["sources"], [
            {"id": "S1", "url": "https://news.example/story", "title": "원문"},
            {"id": "S2", "url": "https://agency.example/policy", "title": "공식 설명"}])
        self.assertEqual(result["issues"][0]["perspectives"][0]["sourceIds"], ["S2"])

    def test_url_without_matching_original_citation_is_unavailable(self):
        search = {"status": "completed", "output": [{"type": "message", "content": [
            {"type": "output_text", "text": "다른 기사", "annotations": [
                {"type": "url_citation", "url": "https://other.example/story", "title": "다른 기사"}]}]}]}
        calls = []
        def opener(request, timeout):
            calls.append(request)
            return FakeResponse(json.dumps(search).encode())
        with self.assertRaises(server.ArticleUnavailable):
            server.analyze("https://news.example/story", "test-key", opener, source_url="https://news.example/story")
        self.assertEqual(len(calls), 1)

    def test_unknown_source_ids_are_removed_from_perspectives(self):
        search = {"status": "completed", "output": [{"type": "message", "content": [
            {"type": "output_text", "text": "검색 자료", "annotations": [
                {"type": "url_citation", "url": "https://agency.example/policy", "title": "공식 자료"}]}]}]}
        structured = {"status": "completed", "output": [{"type": "message", "content": [
            {"type": "output_text", "text": json.dumps({"title": "제목", "summary": "요약", "issues": [{
                "question": "쟁점", "perspectives": [{"actor": "근거 없음", "claim": "주장", "evidence": "근거", "sourceIds": ["S9"]}], "note": "자료 부족"}]})}]}]}
        replies = iter([search, structured])
        def opener(request, timeout):
            return FakeResponse(json.dumps(next(replies)).encode())
        result = server.analyze("기사 본문", "test-key", opener)
        self.assertEqual(result["issues"][0]["perspectives"], [])


class HandlerTests(unittest.TestCase):
    def setUp(self):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.httpd.server_port}"

    def tearDown(self):
        self.httpd.shutdown()
        self.thread.join()
        self.httpd.server_close()

    def post(self, payload):
        request = Request(self.base_url + "/api/analyze", data=json.dumps(payload).encode(),
                          headers={"Content-Type": "application/json", "Host": "localhost:8765"})
        try:
            response = urlopen(request)
            return response.status, json.loads(response.read())
        except Exception as error:
            if not hasattr(error, "code"):
                raise
            try:
                return error.code, json.loads(error.read())
            finally:
                error.close()

    def test_accepts_url_text_and_legacy_article_payloads(self):
        cases = [
            ({"url": "https://news.example/story"}, "https://news.example/story", "https://news.example/story"),
            ({"url": "https://ignored.example/story", "text": "붙여넣은 본문"}, "붙여넣은 본문", None),
            ({"article": "기존 본문"}, "기존 본문", None),
        ]
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}), patch("server.analyze", return_value={"version": 1}) as analyze_mock:
            for payload, article, source_url in cases:
                with self.subTest(payload=payload):
                    status, response = self.post(payload)
                    self.assertEqual(status, 200)
                    self.assertEqual(response, {"version": 1})
                    self.assertEqual(analyze_mock.call_args.args[0], article)
                    self.assertEqual(analyze_mock.call_args.kwargs["source_url"], source_url)

    def test_invalid_input_has_stable_error_code(self):
        status, response = self.post({"url": "javascript:alert(1)"})
        self.assertEqual(status, 400)
        self.assertEqual(response["code"], "INVALID_INPUT")

    def test_article_unavailable_maps_to_422(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}), patch(
            "server.analyze", side_effect=server.ArticleUnavailable("원문 없음")
        ):
            status, response = self.post({"url": "https://news.example/story"})
        self.assertEqual(status, 422)
        self.assertEqual(response["code"], "ARTICLE_UNAVAILABLE")

    def test_timeout_and_upstream_failure_have_distinct_statuses(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}), patch("server.analyze", side_effect=TimeoutError):
            timeout_status, timeout_response = self.post({"text": "기사 본문"})
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}), patch("server.analyze", side_effect=URLError("offline")):
            failure_status, failure_response = self.post({"text": "기사 본문"})
        self.assertEqual((timeout_status, timeout_response["code"]), (504, "ANALYSIS_TIMEOUT"))
        self.assertEqual((failure_status, failure_response["code"]), (502, "ANALYSIS_FAILED"))

if __name__ == "__main__": unittest.main()
