import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import harness
import job_store


class HarnessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_db = job_store.DB_PATH
        job_store.DB_PATH = Path(self.temp.name) / "jobs.sqlite"
        job_store.init_db()

    def tearDown(self):
        job_store.DB_PATH = self.old_db
        self.temp.cleanup()

    def test_url_article_is_searched_and_persisted_with_verified_evidence(self):
        article = "시는 버스 요금을 인상한다고 발표했다."
        quote = "서울시는 요금 인상이 불가피하다고 밝혔다."
        plan = {"title": "버스 요금", "summary": "요금 인상 논란", "issues": [
            {"id": "I1", "question": "요금 인상은 필요한가?", "positions": [
                {"id": "A", "candidate_claim": "인상이 필요하다", "actor_to_find": "시", "query": "버스 요금 인상 필요"},
                {"id": "B", "candidate_claim": "인상에 반대한다", "actor_to_find": "시민단체", "query": "버스 요금 인상 반대"}
            ]}
        ]}
        draft = {"issues": [{"id": "I1", "perspectives": [
            {"issue_id": "I1", "position_id": "A", "actor": "시", "claim": "인상이 필요하다는 입장", "evidence": "운영비 부담", "document_id": "D1", "quote": quote}
        ]}]}
        review = {"checks": [{"index": 0, "verdict": "supported", "reason": "인용과 연결됨"}]}
        job, _ = job_store.create_job({"url": "https://news.example/article", "text": ""}, "test-harness")
        job_store.claim_next()
        with patch.dict("os.environ", {"EXAONE_API_KEY": "test", "TAVILY_API_KEY": "test"}), \
             patch("harness.server._extract_article", return_value=article), \
             patch("harness.server._exaone", side_effect=[json.dumps(plan), json.dumps(draft), json.dumps(review)]), \
             patch("harness.server._post_json", side_effect=[{"results": [{
                 "url": "https://source.example/report", "title": "기관 설명", "content": quote
             }]}, {"results": []}]) as search:
            harness.run(job["id"])
            result = job_store.get_job(job["id"])["result"]

        self.assertEqual(search.call_count, 2)
        self.assertEqual(len(job_store.load_documents(job["id"])), 2)
        self.assertEqual(result["issues"][0]["perspectives"][0]["sourceIds"], ["D1"])
        self.assertEqual(result["evidence"][0]["quote"], quote)
        self.assertEqual(len(result["issues"][0]["perspectives"]), 2)
        self.assertEqual(result["issues"][0]["perspectives"][1]["sourceIds"], [])
        self.assertEqual(job_store.saved_stage(job["id"], "review"), review)
        self.assertEqual(job_store.get_job(job["id"])["status"], "completed")


if __name__ == "__main__":
    unittest.main()
