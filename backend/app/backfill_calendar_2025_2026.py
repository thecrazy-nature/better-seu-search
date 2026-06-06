from __future__ import annotations

from datetime import date

from .models import SourceDocument
from .storage import DocumentStore


CALENDAR_URL = "https://jwc.seu.edu.cn/dndx2025w2026xnxl/list.htm"
CALENDAR_ARTICLE_URL = "https://jwc.seu.edu.cn/2025/0424/c44492a526066/page.htm"
CALENDAR_IMAGE_URL = "https://jwc.seu.edu.cn/_upload/article/images/c3/32/c3b5a51f41ddab0e8f2e66069bf2/34ca0425-40bf-437e-9b5c-a1ff4fe46495.jpg"
HOLIDAY_2025_PDF_URL = "https://jwc.seu.edu.cn/_upload/article/files/c3/32/c3b5a51f41ddab0e8f2e66069bf2/085e7089-99a5-4988-8915-e7985f6fe645.pdf"
HOLIDAY_2026_PDF_URL = "https://jwc.seu.edu.cn/_upload/article/files/c3/32/c3b5a51f41ddab0e8f2e66069bf2/29abd5e8-e65f-492c-866e-0978dd0dc4f4.pdf"
CLASS_TIME_PDF_URL = "https://jwc.seu.edu.cn/_upload/article/files/c3/32/c3b5a51f41ddab0e8f2e66069bf2/0ba0d8c2-fa9e-4e0e-8b4b-333d815d15b8.pdf"


CALENDAR_TEXT = """东南大学2025-2026学年校历

本文是教务处发布的东南大学2025-2026学年校历。官网原文页面包含校历图片、2025-2026学年节假日安排、2025-2026学年上课时间安排等附件。

2025-2026学年暑期学校：2025年8月25日至2025年9月21日。
2025-2026学年秋季学期：2025年9月22日至2026年1月25日。
2025-2026学年寒假：2026年1月26日至2026年3月1日。
2025-2026学年春季学期：2026年3月2日至2026年7月5日。
2025-2026学年暑假：2026年7月6日至2026年8月23日。

秋季学期和暑期学校重要安排：
1. 教职工上班：2025年8月20日。
2. 本科生暑期学校：2025年8月25日至2025年9月21日；研究生教学由导师或课题组安排。
3. 在校生注册：2025年8月25日至2025年8月29日。
4. 新生报到注册：本科新生2025年8月30日；研究生新生2025年9月17日。
5. 本科新生军训：2025年8月31日至2025年9月20日。
6. 校学位评定委员会会议：2025年9月11日。
7. 新生开学典礼：2025年9月19日。
8. 秋季学期上课：2025年9月22日。
9. 国庆节、中秋节：2025年10月1日至2025年10月8日放假调休；2025年9月28日和2025年10月11日分别补2025年10月7日和2025年10月8日的课或班。
10. 校运会：2025年11月7日至2025年11月8日，停课不补。
11. 校学位评定委员会会议：2025年12月10日。
12. 停课复习考试：2026年1月12日至2026年1月25日。
13. 学生寒假：2026年1月26日至2026年3月1日。
14. 教职工轮休：2026年1月28日至2026年2月26日。

春季学期和暑假重要安排：
1. 教职工上班：2026年2月27日。
2. 春季学期上课：2026年3月2日。
3. 在校生注册：2026年3月2日至2026年3月6日。
4. 2026级春博报到：2026年3月11日；上课：2026年3月16日。
5. 校学位评定委员会会议：2026年3月18日。
6. 停课复习考试：2026年6月22日至2026年7月5日。
7. 毕业离校手续：本科生2026年6月27日至2026年7月3日；研究生为毕业之日起两周内。
8. 校学位评定委员会会议：2026年6月29日。
9. 毕业典礼：2026年7月1日至2026年7月2日。
10. 学生暑假：2026年7月6日至2026年8月23日。
11. 教职工轮休：2026年7月13日至2026年8月19日。
12. 教职工上班：2026年8月20日。

查询提示：
- 用户查询“校历原文”“2025-2026校历”“东南大学校历”“寒假什么时候放”“暑假什么时候放”“春季学期什么时候上课”“秋季学期什么时候上课”“停课复习考试时间”“毕业典礼时间”“校运会时间”“新生报到时间”等，均可引用本文。
- 发布时间应使用官网文章发布时间：2025年4月24日；正文中的日期是校历安排、学期时间、假期时间、注册时间、考试复习时间或活动时间，不是文章发布时间。
"""


CALENDAR_AI_METADATA = {
    "topics": ["校历", "学期安排", "寒假", "暑假", "注册", "考试", "毕业", "开学"],
    "keywords": [
        "东南大学2025-2026学年校历",
        "2025-2026学年校历",
        "寒假",
        "暑假",
        "暑期学校",
        "秋季学期",
        "春季学期",
        "注册",
        "停课复习考试",
        "毕业典礼",
        "校运会",
        "新生报到",
    ],
    "business_actions": ["查询校历", "查询放假时间", "查询开学时间", "查询考试复习时间", "查询注册时间"],
    "audience": ["本科生", "研究生", "教职工", "新生", "在校生"],
    "answerable_questions": [
        "2025-2026学年校历原文在哪里？",
        "今年寒假什么时候放？",
        "2026年寒假从哪天到哪天？",
        "2026年暑假从哪天到哪天？",
        "2025年秋季学期什么时候上课？",
        "2026年春季学期什么时候上课？",
        "2025-2026学年停课复习考试是什么时候？",
        "2026年毕业典礼是什么时候？",
        "2025年校运会是什么时候？",
        "新生什么时候报到注册？",
    ],
    "official_terms": ["校历", "学年", "学期", "寒假", "暑假", "停课复习考试"],
    "attachment_summaries": [
        {
            "name": "东南大学2025-2026学年校历图片",
            "purpose": "图片型校历原文，包含学期、假期、注册、考试、毕业和活动安排。",
            "summary": "图片内容已转写入正文，便于检索和回答。",
        },
        {
            "name": "关于2025年部分节假日放假调休安排的通知.pdf",
            "purpose": "2025年节假日放假调休附件。",
            "summary": "用于核对2025年国庆节、中秋节等节假日调休安排。",
        },
        {
            "name": "关于2026年部分节假日安排的通知.pdf",
            "purpose": "2026年节假日放假安排附件。",
            "summary": "用于核对2026年法定节假日安排。",
        },
        {
            "name": "上课时间安排表.pdf",
            "purpose": "上课作息时间附件。",
            "summary": "用于查询学年上课时间安排。",
        },
    ],
}


def build_calendar_document() -> SourceDocument:
    return SourceDocument(
        title="东南大学2025-2026学年校历",
        url=CALENDAR_URL,
        source="教务处",
        category="校历",
        publish_date=date(2025, 4, 24),
        body=CALENDAR_TEXT,
        attachments=[
            {
                "name": "东南大学2025-2026学年校历.jpg",
                "url": CALENDAR_IMAGE_URL,
                "text": CALENDAR_TEXT,
                "source_page": CALENDAR_URL,
                "content_type": "image/jpeg",
                "ocr_note": "图片校历由人工校对转写，正文已包含主要日期。",
            },
            {
                "name": "东南大学2025-2026学年校历正式文章页",
                "url": CALENDAR_ARTICLE_URL,
                "text": "教务处正式文章页，发布时间为2025年4月24日，展示东南大学2025-2026学年校历图片和相关附件。",
            },
            {
                "name": "关于2025年部分节假日放假调休安排的通知.pdf",
                "url": HOLIDAY_2025_PDF_URL,
                "text": "2025年部分节假日放假调休安排附件，用于核对国庆节、中秋节等2025年节假日和调休安排。",
            },
            {
                "name": "关于2026年部分节假日安排的通知.pdf",
                "url": HOLIDAY_2026_PDF_URL,
                "text": "2026年部分节假日安排附件，用于核对2026年法定节假日安排。",
            },
            {
                "name": "上课时间安排表.pdf",
                "url": CLASS_TIME_PDF_URL,
                "text": "上课时间安排附件，用于查询2025-2026学年上课作息时间。",
            },
        ],
        applicable_colleges=[],
        applicable_grades=["2025-2026学年"],
        student_types=["本科生", "研究生", "教职工"],
        topics=["校历", "寒假", "暑假", "学期安排", "考试安排"],
        keywords=[
            "东南大学2025-2026学年校历",
            "2025-2026学年校历",
            "校历原文",
            "寒假",
            "暑假",
            "暑期学校",
            "秋季学期",
            "春季学期",
            "停课复习考试",
            "毕业典礼",
            "注册",
            "新生报到",
            "教职工上班",
        ],
    )


def backfill_calendar() -> dict[str, object]:
    store = DocumentStore()
    store.init_db()
    store.delete_documents_by_urls([CALENDAR_ARTICLE_URL])
    doc = build_calendar_document()
    count = store.upsert_documents([doc])
    with store.connect() as conn:
        row = conn.execute("SELECT id FROM documents WHERE url = ?", (doc.url,)).fetchone()
        doc_id = int(row["id"]) if row else None
    if doc_id is not None:
        store.update_document_ai_metadata(doc_id, CALENDAR_AI_METADATA)
    return {"upserted": count, "document_id": doc_id, "url": doc.url}


def main() -> None:
    print(backfill_calendar())


if __name__ == "__main__":
    main()
