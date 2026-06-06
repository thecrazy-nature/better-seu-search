from __future__ import annotations

import unittest

from bs4 import BeautifulSoup

from backend.app.content_cleaning import clean_body_text, extract_main_text
from backend.app.crawler.seu_sites import PublicSiteCrawler


class ContentCleaningTest(unittest.TestCase):
    def test_extract_main_text_prefers_article_body_over_navigation(self) -> None:
        html = """
        <html>
          <body>
            <nav>简体中文 | English 部门简介 办事平台 教务信息 学籍管理</nav>
            <div class="wp_articlecontent">
              <h1>关于测试通知</h1>
              <p>各位同学：这是通知正文。</p>
              <p>报名时间为6月4日13:00至6月10日16:00。</p>
            </div>
            <footer>版权所有 [网站管理] 技术支持</footer>
          </body>
        </html>
        """

        text = extract_main_text(BeautifulSoup(html, "html.parser"), title="关于测试通知")

        self.assertIn("这是通知正文", text)
        self.assertIn("6月10日16:00", text)
        self.assertNotIn("部门简介", text)
        self.assertNotIn("网站管理", text)

    def test_clean_body_text_keeps_fragmented_dates_and_repeated_steps(self) -> None:
        raw = """
        简体中文 | English
        部门简介
        办事平台
        关于2026-2027学年暑期学校课程预选及重修报名的通知
        2026-05-27
        各位同学：
        2026-2027
        学年暑期学校课程预选及重修报名安排如下：
        6
        月
        4
        日
        13:00~6
        月
        10
        日
        16:00
        登录选课系统，选择“
        2026-2027
        学年暑期学校课程预选”批次，进行选课。
        登录选课系统，选择“
        2026-2027
        学年暑期学校重修选课”轮次，进入“重修课程”界面。
        版权所有 [网站管理]
        """

        text = clean_body_text(raw, title="关于2026-2027学年暑期学校课程预选及重修报名的通知")

        self.assertIn("6月4日", text)
        self.assertIn("6月10日16:00", text)
        self.assertIn("暑期学校课程预选", text)
        self.assertIn("暑期学校重修选课", text)
        self.assertNotIn("部门简介", text)
        self.assertNotIn("网站管理", text)

    def test_clean_body_text_strips_historical_embedded_attachment_excerpt(self) -> None:
        raw = "正文第一段。\n\n附件《名单.pdf》正文摘录：\n第1页：大量名单"

        text = clean_body_text(raw, title="测试")

        self.assertEqual(text, "正文第一段。")

    def test_clean_body_text_drops_short_template_only_shell(self) -> None:
        raw = """
        首页东南大学本科生国际交流宣传视频友情链接
        东南大学本科生国际交流宣传视频叮东！一起走进东南大学国际交流平台
        2021-12-16联系电话：52090230、52090234、52090224
        """

        text = clean_body_text(raw, title="叮东！一起走进东南大学国际交流平台")

        self.assertEqual(text, "")

    def test_crawler_extracts_webplus_embedded_pdf_attachment(self) -> None:
        html = """
        <div class="wp_articlecontent">
          <div class="Article_Content">
            <span class="wp_pdf_player"
                  pdfsrc="/_upload/article/files/a/b/demo.pdf"
                  sudyfile-attr="{'title':'东南大学测试通知.pdf'}"></span>
          </div>
        </div>
        """
        crawler = PublicSiteCrawler(max_pages_per_site=1, delay_seconds=0)
        try:
            attachments = crawler._extract_attachments(
                BeautifulSoup(html, "html.parser"),
                "https://jwc.seu.edu.cn/2026/0603/c21681a569982/page.htm",
            )
        finally:
            crawler.client.close()

        self.assertEqual(len(attachments), 1)
        self.assertEqual(attachments[0]["name"], "东南大学测试通知.pdf")
        self.assertEqual(
            attachments[0]["url"],
            "https://jwc.seu.edu.cn/_upload/article/files/a/b/demo.pdf",
        )


if __name__ == "__main__":
    unittest.main()
