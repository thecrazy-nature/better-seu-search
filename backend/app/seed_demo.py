from __future__ import annotations

from datetime import date

from .models import SourceDocument
from .storage import DocumentStore


DEMO_DOCS = [
    SourceDocument(
        title="关于2026年上半年全国大学英语四、六级考试报名的通知",
        url="https://jwc.seu.edu.cn/demo/cet-2026.htm",
        source="教务处",
        category="教务信息",
        publish_date=date(2026, 3, 17),
        body=(
            "各院系、各位同学：2026年上半年全国大学英语四、六级考试报名工作即将开始。"
            "报名对象为符合条件的在校本科生和研究生。报名时间为2026年3月20日10:00至"
            "2026年3月25日17:00。请登录报名系统完成报名和缴费，逾期不再补报。"
        ),
        attachments=[{"name": "四六级报名操作说明.pdf", "url": "https://jwc.seu.edu.cn/demo/cet-guide.pdf"}],
        applicable_colleges=[],
        applicable_grades=[],
        student_types=["本科生", "研究生"],
        topics=["四六级"],
        deadline=date(2026, 3, 25),
    ),
    SourceDocument(
        title="关于做好2026年本科生转专业工作的通知",
        url="https://jwc.seu.edu.cn/demo/major-transfer-2026.htm",
        source="教务处",
        category="教务信息",
        publish_date=date(2026, 5, 10),
        body=(
            "为做好2026年本科生转专业工作，现将有关事项通知如下。申请对象为2024级、2025级"
            "符合培养方案要求的本科生。学生须在规定时间内提交申请，经转出学院和转入学院审核。"
            "申请时间为2026年5月15日至2026年5月25日。具体条件以各学院公布的接收方案为准。"
        ),
        attachments=[{"name": "本科生转专业申请表.docx", "url": "https://jwc.seu.edu.cn/demo/transfer-form.docx"}],
        student_types=["本科生"],
        applicable_grades=["2024级", "2025级"],
        topics=["转专业"],
        deadline=date(2026, 5, 25),
    ),
    SourceDocument(
        title="计算机科学与工程学院2026年本科生转专业接收方案",
        url="https://cse.seu.edu.cn/demo/major-transfer-2026.htm",
        source="计算机科学与工程学院",
        category="学院通知",
        publish_date=date(2026, 5, 12),
        body=(
            "根据学校本科生转专业工作安排，计算机科学与工程学院公布2026年转专业接收方案。"
            "面向2024级本科生接收申请，申请学生需完成规定课程学习，并参加学院组织的考核。"
            "材料提交截止时间为2026年5月23日17:00。请同时关注教务处全校通知。"
        ),
        attachments=[{"name": "计算机学院转专业接收方案.pdf", "url": "https://cse.seu.edu.cn/demo/cse-transfer.pdf"}],
        applicable_colleges=["计算机科学与工程学院"],
        applicable_grades=["2024级"],
        student_types=["本科生"],
        topics=["转专业"],
        deadline=date(2026, 5, 23),
    ),
    SourceDocument(
        title="2025-2026学年校历",
        url="https://jwc.seu.edu.cn/demo/calendar-2025-2026.htm",
        source="教务处",
        category="校历",
        publish_date=date(2025, 6, 20),
        body=(
            "2025-2026学年校历公布。秋季学期教学周、考试周、寒假安排和春季学期教学安排详见附件。"
            "请各单位和同学根据校历安排教学、考试和假期计划。"
        ),
        attachments=[{"name": "2025-2026学年校历.pdf", "url": "https://jwc.seu.edu.cn/demo/calendar.pdf"}],
        topics=["校历"],
    ),
    SourceDocument(
        title="本科课程考核缓考申请办理流程",
        url="https://jwc.seu.edu.cn/demo/deferred-exam.htm",
        source="教务处",
        category="办事指南",
        publish_date=date(2025, 9, 1),
        body=(
            "学生因病或其他特殊原因不能参加课程考核的，应在考试前提交缓考申请。"
            "申请材料包括缓考申请表、相关证明材料，经任课教师、所在学院审核后报教务处备案。"
            "未按规定办理手续者，按缺考处理。"
        ),
        attachments=[{"name": "缓考申请表.doc", "url": "https://jwc.seu.edu.cn/demo/deferred-exam-form.doc"}],
        student_types=["本科生"],
        topics=["缓考"],
    ),
]


def seed_demo() -> int:
    store = DocumentStore()
    store.init_db()
    return store.upsert_documents(DEMO_DOCS)


if __name__ == "__main__":
    print(f"seeded {seed_demo()} demo documents")
