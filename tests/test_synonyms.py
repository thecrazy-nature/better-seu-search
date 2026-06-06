from __future__ import annotations

import unittest

from backend.app.search.synonyms import expand_terms


class SynonymExpansionTest(unittest.TestCase):
    def test_keeps_only_high_confidence_aliases(self) -> None:
        terms = expand_terms("四六级报名")

        self.assertIn("CET", terms)
        self.assertIn("全国大学英语四、六级考试", terms)

    def test_does_not_expand_broad_graduation_topic(self) -> None:
        terms = expand_terms("毕业通知")

        self.assertEqual(terms, ["毕业通知"])
        self.assertNotIn("毕业设计", terms)
        self.assertNotIn("离校", terms)

    def test_does_not_expand_business_topics_that_bge_should_cover(self) -> None:
        for query in ("校历安排", "成绩复核", "评教入口", "毕业设计通知", "成绩单打印"):
            with self.subTest(query=query):
                self.assertEqual(expand_terms(query), [query])

    def test_keeps_fixed_cet_reverse_alias(self) -> None:
        terms = expand_terms("CET报名")

        self.assertIn("四六级", terms)
        self.assertIn("大学英语四级", terms)

    def test_keeps_fixed_recommendation_aliases(self) -> None:
        terms = expand_terms("保研资格")

        self.assertIn("推免", terms)
        self.assertIn("推荐免试", terms)


if __name__ == "__main__":
    unittest.main()
