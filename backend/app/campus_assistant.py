from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from .config import settings
from .models import UserProfile


OFFICIAL_SOURCES = [
    {
        "source_key": "online_reimbursement_notice",
        "title": "关于全面推行“网上预约报销”业务的通知",
        "url": "https://ins.seu.edu.cn/2015/0603/c26759a301587/page.htm",
        "source_unit": "东南大学财务处",
        "publish_date": "2015-06-03",
        "official_domain": "ins.seu.edu.cn",
        "facts": [
            {
                "slot": "scope",
                "text": "除通知列明的少数现场办理业务外，其他业务均需通过网上预约方式办理。",
                "quote": "除以下业务暂不需“网上预约报销”外，其他业务均需通过网上预约方式办理。",
            },
            {
                "slot": "system",
                "text": "经办人可从东南大学主页进入校园信息门户，在公共服务中点击财务报销，也可从公共服务进入财务平台并登录网上预约报销系统。",
                "quote": "进入“校园信息门户”，点击“公共服务”下的“财务报销”按钮即可进入“网上报销系统”。",
            },
            {
                "slot": "process",
                "text": "经办人点击报销申请，录入项目号、附件张数、联系电话等信息，并按照票据选择业务报销类型。",
                "quote": "经办人点击报销申请，录入项目号、附件张数、联系电话等信息，按照报销票据选择业务报销类型。",
            },
            {
                "slot": "process",
                "text": "填制完成后打印预约报销确认单，由项目负责人、经办人签字，所在院系部门盖章后提交。",
                "quote": "打印确认单，项目负责人、经办人签字，所在院系部门盖章。",
            },
            {
                "slot": "location",
                "text": "预约报销确认单及票据应在工作日送至财务处报销大厅网上报销咨询接单处。",
                "quote": "将预约报销确认单及报销票据送至财务处报销大厅网上报销咨询接单处即可。",
            },
            {
                "slot": "risk",
                "text": "每张预约单只能填写一种业务类型，且单张预约单只能填写一个项目号；多项目列支需打印后手工补充项目号和金额。",
                "quote": "每张预约单只能填写一种业务类型；“网上预约系统”中单张预约单只能填写一个项目号。",
            },
            {
                "slot": "material",
                "text": "专项经费中列支国际交流费、设备费、工程款、转出款、外协设备费等，送交网约单时需一并提交经费预算书。",
                "quote": "在专项经费中列支国际交流费、设备费、工程款、转出款、外协设备费等，送交网约单时需一并提交经费预算书。",
            },
            {
                "slot": "time",
                "text": "网约收单后，最多 5 个工作日内处理完毕。",
                "quote": "网约收单后，将在最多 5 个工作日内处理完毕。",
            },
        ],
    },
    {
        "source_key": "seu_finance_platform",
        "title": "财务平台 - 东南大学公共服务",
        "url": "https://www.seu.edu.cn/103/",
        "source_unit": "东南大学",
        "publish_date": None,
        "official_domain": "www.seu.edu.cn",
        "facts": [
            {
                "slot": "system",
                "text": "东南大学官网公共服务页面提供财务平台入口。",
                "quote": "公共服务 ... 财务平台",
            }
        ],
    },
    {
        "source_key": "invoice_deadline_rule",
        "title": "关于规范发票报销时限的管理规定",
        "url": "https://cwc.seu.edu.cn/_upload/article/files/de/44/2d9ce118446f878768a3f4d0e703/629809af-1ecf-41ba-9ddf-95e68420d8ea.pdf",
        "source_unit": "东南大学财务处",
        "publish_date": "2020-05-11",
        "official_domain": "cwc.seu.edu.cn",
        "facts": [
            {
                "slot": "time",
                "text": "以发票开具日期为准，当年开具的发票必须在当年 12 月 30 日之前完成报销，原则上不能跨年度报销。",
                "quote": "以发票开具日期为准，当年开具的发票，必须在当年 12 月 30 日之前完成报销。",
            },
            {
                "slot": "time",
                "text": "每年第四季度开具的发票，特殊情况下可放宽至次年 6 月 30 日前完成报销。",
                "quote": "每年第四季度开具的发票，因特殊情况无法在当年报销的，可以放宽至次年的 6 月 30 日前完成报销。",
            },
            {
                "slot": "exception",
                "text": "如因项目经费延期到账、师生中长期出国等特殊原因无法按期报销，应在票据有效期限内提出延期报销书面意见，经项目负责人审批后报财务处备案。",
                "quote": "应在票据报销有效期限内提出延期报销的书面意见，项目负责人审批同意后，报财务处备案。",
            },
            {
                "slot": "risk",
                "text": "发票报销还应遵循项目管理要求；项目管理对时限有明确要求的，从其规定。",
                "quote": "发票报销应同时遵循项目管理的要求，项目管理对报销时限有明确要求的，从其规定。",
            },
        ],
    },
    {
        "source_key": "campus_reimbursement_location",
        "title": "财务处关于各校区特定项目报销地点的规定",
        "url": "https://physics.seu.edu.cn/2006/0912/c23199a259474/pagem.htm",
        "source_unit": "东南大学财务处",
        "publish_date": "2006-09-12",
        "official_domain": "physics.seu.edu.cn",
        "facts": [
            {
                "slot": "location",
                "text": "项目代码以 6、7、8 开头的在研科研项目，必须由项目负责人自行选择固定在一个校区办理财务报销业务，确认后原则上不得随意更改。",
                "quote": "项目代码以“6”“7”“8”开头的在研科研项目，必须由项目负责人自行选择固定在一个校区办理财务报销业务。",
            },
            {
                "slot": "location",
                "text": "除规定项目类别外，其余项目可在各校区办理报销业务。",
                "quote": "除以上规定的项目类别外，其余项目可在各校区办理报销业务。",
            },
        ],
    },
    {
        "source_key": "oic_teachers_students_abroad",
        "service_type": "overseas_application",
        "title": "师生出国（境）",
        "url": "https://oic.seu.edu.cn/18918/list.htm",
        "source_unit": "东南大学国际合作处",
        "publish_date": None,
        "official_domain": "oic.seu.edu.cn",
        "facts": [
            {
                "slot": "system",
                "text": "东南大学国际合作处师生出国（境）栏目提供学生因公申报说明、教师因公申报说明、流程与说明、签证材料、教师和学生出国（境）申报入口。",
                "quote": "学生因公申报说明；教师因公申报说明；流程与说明；签证材料；教师出国（境）申报；学生出国（境）申报",
            },
            {
                "slot": "location",
                "text": "国际合作处通信地址为南京四牌楼2号，办公地点为四牌楼校区老图书馆一楼。",
                "quote": "通信地址：南京，四牌楼2号，东南大学 国际合作处 邮编：210096；办公地点：四牌楼校区老图书馆一楼",
            },
        ],
    },
    {
        "source_key": "oic_student_abroad_application",
        "service_type": "overseas_application",
        "title": "学生赴国外及港澳申报",
        "url": "https://oic.seu.edu.cn/xsfgwjgasb/list.htm",
        "source_unit": "东南大学国际合作处",
        "publish_date": None,
        "official_domain": "oic.seu.edu.cn",
        "facts": [
            {
                "slot": "material",
                "text": "学生用公费执行出国（境）任务，申报时应填写《因公临时出国任务和财务核算审批意见表》。",
                "quote": "学生用公费执行出国（境）任务，请申报时一定填写“因公临时出国任务和财务核算审批意见表20140630.doc”。",
            },
            {
                "slot": "material",
                "text": "学生应在线填写并打印《东南大学学生出国（境）申请表》，且要求正反双面打印在一张纸上。",
                "quote": "请在线填写并打印 东南大学学生出国（境）申请表.doc（一定要正反双面打印在一张纸上）。",
            },
            {
                "slot": "process",
                "text": "在外经费来源为导师科研项目的，需填写项目号并由导师签字同意，申请表和邀请函复印件经院系领导签字盖章后报国际合作处。",
                "quote": "在外经费来源为导师科研项目的需填写项目号并需导师签字同意，经院系领导签字盖章后的申请表及邀请函复印件一份报国际合作处。",
            },
            {
                "slot": "visa",
                "text": "学生需持因私护照出国（境）并自行办理有关签证手续；签证过程中如需办理学校相关文件，应打印领馆网站相关页面并在申请批准后至国际合作处办理。",
                "quote": "学生需执因私护照出国（境）并自己办理有关的签证手续。签证过程中，需办理学校相关文件的，需将领馆网站的相关页面打印，再至国际合作处办理（待申请批准后才能办理有关签证文件）。",
            },
            {
                "slot": "after_return",
                "text": "学生归国后应至国际合作处领取批件，再至财务处报销。",
                "quote": "学生归国后请至国际合作处领取批件，至财务处报销。",
            },
            {
                "slot": "material",
                "text": "出国（境）如使用985经费、211经费、重点学科建设经费，应附经费计划书。",
                "quote": "出国（境）如使用985经费、211经费、重点学科建设经费，请附经费计划书。",
            },
        ],
    },
    {
        "source_key": "oic_exchange_project_application",
        "service_type": "overseas_application",
        "title": "项目申请",
        "url": "https://oic.seu.edu.cn/18958/list.htm",
        "source_unit": "东南大学国际合作处",
        "publish_date": None,
        "official_domain": "oic.seu.edu.cn",
        "facts": [
            {
                "slot": "process",
                "text": "本科项目可通过校园信息门户，从教学服务中的“出国申请（教务处）”进入教务处报名系统，选择项目并填写个人报名信息。",
                "quote": "通过 http://my.seu.edu.cn/ 登录校园信息门户，从页面左方的“教学服务”—“出国申请（教务处）”进入教务处报名系统；选择项目并填写个人报名信息。",
            },
            {
                "slot": "process",
                "text": "本科项目还需根据项目通知要求，将申请材料以纸质或电子方式寄给外方联络人，并在外方网页上网申。",
                "quote": "根据项目通知中的具体要求，同时将申请材料以纸质或者电子的方式寄给外方联络人，在外方网页上进行网申。",
            },
            {
                "slot": "process",
                "text": "研究生项目可登录东南大学研究生院网站，在通知公告中找到对应项目申报通知，并按通知要求填写申请表提交。",
                "quote": "登录东南大学研究生院网站：http://seugs.seu.edu.cn/，在通知公告中找到对应的项目申报通知；根据通知要求填写相应的申请表并提交。",
            },
            {
                "slot": "contact",
                "text": "国际合作处项目申请页面列有教务处、学生处、研究生院和国际合作处相关联络老师及电话。",
                "quote": "教务处联络老师：秦老师（负责 CSC 项目）：（025）52090230；张老师（负责学生报名和学生选拔）：（025）52090224；研究生院联络老师：朱老师：（025）83795781；国际合作处联络老师：赵老师：（025）52090195",
            },
        ],
    },
    {
        "source_key": "undergraduate_abroad_system_guide",
        "service_type": "overseas_application",
        "title": "本科生出国（境）交流学习管理系统操作指南",
        "url": "https://me.seu.edu.cn/ug/2020/0914/c29192a345639/page.htm",
        "source_unit": "东南大学机械工程学院",
        "publish_date": "2020-04-23",
        "official_domain": "me.seu.edu.cn",
        "facts": [
            {
                "slot": "system",
                "text": "本科生出国（境）交流学习管理系统入口为网上办事大厅。",
                "quote": "我校启用本科生出国（境）交流学习管理系统（入口：网上办事大厅 http://ehall.seu.edu.cn）。",
            },
            {
                "slot": "process",
                "text": "自2018级起，出国交流本科生须在出国前于系统填报出国交流项目申请、校级资助申请、派出申请，并上传签证信息、出国承诺书。",
                "quote": "自2018级起出国交流 本科生 须在出国前于本系统填报出国交流项目申请、校级资助申请、派出申请（含上传签证信息、出国承诺书）。",
            },
            {
                "slot": "after_return",
                "text": "本科生回国后应在系统填报返校申请，上传出入境信息、交流心得总结，并办理学分认定申请。",
                "quote": "回国后于系统填报返校申请（含上传出入境信息、交流心得总结）以及学分认定申请。",
            },
        ],
    },
    {
        "source_key": "graduate_international_conference_steps",
        "service_type": "overseas_application",
        "title": "东南大学研究生国际学术交流基金实施办法及报销指南",
        "url": "https://ic.seu.edu.cn/2025/0227/c50791a519624/page.psp",
        "source_unit": "东南大学集成电路学院",
        "publish_date": "2025-02-27",
        "official_domain": "ic.seu.edu.cn",
        "facts": [
            {
                "slot": "process",
                "text": "研究生出国（境）参加国际会议申请资助时，应先在网上办事服务大厅的研究生国际交流中提交境外参加国际会议申请，经学生本人申请、导师审核、院系审核、研究生院审核。",
                "quote": "东南大学网上办事服务大厅—研究生国际交流—境外参加国际会议（根据情况选择非顶会 或 顶会）—学生本人申请—导师审核—院系审核—研究生院审核。",
            },
            {
                "slot": "process",
                "text": "研究生国际会议还需在网上办事服务大厅进行学生因公出国申报，经学生本人申请、导师审核、学院副书记审核、国际合作处审核。",
                "quote": "网上办事服务大厅—学生因公出国申报—学生本人 申请—导师审核—学院副书记审核—国际合作处审核",
            },
            {
                "slot": "risk",
                "text": "研究生出国（境）参加国际会议应在出国前完成申请；会后补交申请不予报销。",
                "quote": "请在出国前完成申请，会后补交申请不予报销",
            },
            {
                "slot": "risk",
                "text": "学生应在出国前在因公出国境系统中申报，否则回来后无法报销。",
                "quote": "在出国前一定要在因公出国境系统中申报，否则回来后无法报销",
            },
        ],
    },
]


class ReimbursementRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    project_name: str | None = None
    project_code: str | None = None
    expense_type: str | None = None
    invoice_date: str | None = None
    payment_target: str | None = None
    profile: UserProfile = Field(default_factory=UserProfile)


class OverseasApplicationRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    applicant_type: str | None = None
    destination: str | None = None
    visit_type: str | None = None
    funding_source: str | None = None
    start_date: str | None = None
    profile: UserProfile = Field(default_factory=UserProfile)


class AssistantSource(BaseModel):
    ref: str
    title: str
    url: str
    source_unit: str
    publish_date: str | None = None
    quote: str
    slot: str


class ReimbursementResponse(BaseModel):
    dataset: Literal["campus_assistant"] = "campus_assistant"
    answer: str
    project_name: str | None = None
    project_code: str | None = None
    expense_type: str | None = None
    materials: list[str] = Field(default_factory=list)
    steps: list[str] = Field(default_factory=list)
    actors: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)
    systems: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    sources: list[AssistantSource] = Field(default_factory=list)


class OverseasApplicationResponse(BaseModel):
    dataset: Literal["campus_assistant"] = "campus_assistant"
    answer: str
    applicant_type: str | None = None
    destination: str | None = None
    visit_type: str | None = None
    funding_source: str | None = None
    materials: list[str] = Field(default_factory=list)
    steps: list[str] = Field(default_factory=list)
    actors: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)
    systems: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    sources: list[AssistantSource] = Field(default_factory=list)


@dataclass
class AssistantFact:
    id: int
    slot: str
    text: str
    quote: str
    title: str
    url: str
    source_unit: str
    publish_date: str | None


class CampusAssistantStore:
    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or settings.assistant_db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def init_db(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                PRAGMA journal_mode = WAL;

                CREATE TABLE IF NOT EXISTS assistant_sources (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_key TEXT NOT NULL UNIQUE,
                    title TEXT NOT NULL,
                    url TEXT NOT NULL,
                    source_unit TEXT NOT NULL,
                    publish_date TEXT,
                    official_domain TEXT NOT NULL,
                    dataset_name TEXT NOT NULL DEFAULT 'campus_assistant',
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS assistant_facts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_id INTEGER NOT NULL,
                    service_type TEXT NOT NULL,
                    slot TEXT NOT NULL,
                    fact_text TEXT NOT NULL,
                    quote TEXT NOT NULL,
                    keywords_json TEXT NOT NULL DEFAULT '[]',
                    dataset_name TEXT NOT NULL DEFAULT 'campus_assistant',
                    FOREIGN KEY(source_id) REFERENCES assistant_sources(id) ON DELETE CASCADE
                );

                CREATE VIRTUAL TABLE IF NOT EXISTS assistant_facts_fts USING fts5(
                    fact_text,
                    quote,
                    slot,
                    content='assistant_facts',
                    content_rowid='id',
                    tokenize='unicode61'
                );

                CREATE TRIGGER IF NOT EXISTS assistant_facts_ai AFTER INSERT ON assistant_facts BEGIN
                    INSERT INTO assistant_facts_fts(rowid, fact_text, quote, slot)
                    VALUES (new.id, new.fact_text, new.quote, new.slot);
                END;

                CREATE TRIGGER IF NOT EXISTS assistant_facts_ad AFTER DELETE ON assistant_facts BEGIN
                    INSERT INTO assistant_facts_fts(assistant_facts_fts, rowid, fact_text, quote, slot)
                    VALUES ('delete', old.id, old.fact_text, old.quote, old.slot);
                END;

                CREATE TRIGGER IF NOT EXISTS assistant_facts_au AFTER UPDATE ON assistant_facts BEGIN
                    INSERT INTO assistant_facts_fts(assistant_facts_fts, rowid, fact_text, quote, slot)
                    VALUES ('delete', old.id, old.fact_text, old.quote, old.slot);
                    INSERT INTO assistant_facts_fts(rowid, fact_text, quote, slot)
                    VALUES (new.id, new.fact_text, new.quote, new.slot);
                END;
                """
            )
            self._seed(conn)

    def _seed(self, conn: sqlite3.Connection) -> None:
        for source in OFFICIAL_SOURCES:
            conn.execute(
                """
                INSERT INTO assistant_sources (
                    source_key, title, url, source_unit, publish_date, official_domain, dataset_name
                )
                VALUES (?, ?, ?, ?, ?, ?, 'campus_assistant')
                ON CONFLICT(source_key) DO UPDATE SET
                    title = excluded.title,
                    url = excluded.url,
                    source_unit = excluded.source_unit,
                    publish_date = excluded.publish_date,
                    official_domain = excluded.official_domain,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    source["source_key"],
                    source["title"],
                    source["url"],
                    source["source_unit"],
                    source["publish_date"],
                    source["official_domain"],
                ),
            )
            source_id = conn.execute(
                "SELECT id FROM assistant_sources WHERE source_key = ?",
                (source["source_key"],),
            ).fetchone()["id"]
            conn.execute("DELETE FROM assistant_facts WHERE source_id = ?", (source_id,))
            service_type = str(source.get("service_type") or "finance_reimbursement")
            for fact in source["facts"]:
                keywords = self._keywords_for_fact(str(fact["slot"]), str(fact["text"]))
                conn.execute(
                    """
                    INSERT INTO assistant_facts (
                        source_id, service_type, slot, fact_text, quote, keywords_json, dataset_name
                    )
                    VALUES (?, ?, ?, ?, ?, ?, 'campus_assistant')
                    """,
                    (
                        source_id,
                        service_type,
                        fact["slot"],
                        fact["text"],
                        fact["quote"],
                        json.dumps(keywords, ensure_ascii=False),
                    ),
                )

    @staticmethod
    def _keywords_for_fact(slot: str, text: str) -> list[str]:
        base = ["报销", "财务", "项目", "费用", slot]
        for term in ["网上预约", "发票", "项目号", "签字", "盖章", "财务平台", "财务处", "预算书", "校区"]:
            if term in text:
                base.append(term)
        return list(dict.fromkeys(base))

    def search_facts(
        self,
        query: str,
        slots: list[str] | None = None,
        limit: int = 16,
        service_type: str = "finance_reimbursement",
    ) -> list[AssistantFact]:
        slots = slots or []
        token_query = self._fts_query(query)
        rows: list[sqlite3.Row] = []
        with self.connect() as conn:
            if token_query:
                try:
                    rows = conn.execute(
                        """
                        SELECT f.*, s.title, s.url, s.source_unit, s.publish_date,
                               bm25(assistant_facts_fts, 1.5, 2.0, 0.5) AS rank
                        FROM assistant_facts_fts
                        JOIN assistant_facts f ON f.id = assistant_facts_fts.rowid
                        JOIN assistant_sources s ON s.id = f.source_id
                        WHERE assistant_facts_fts MATCH ?
                          AND f.service_type = ?
                        ORDER BY rank
                        LIMIT ?
                        """,
                        (token_query, service_type, limit),
                    ).fetchall()
                except Exception:
                    rows = []
            if len(rows) < limit:
                slot_placeholders = ",".join("?" for _ in slots)
                slot_filter = f"OR f.slot IN ({slot_placeholders})" if slots else ""
                params: list[Any] = [service_type, f"%{query}%"]
                params.extend(slots)
                params.append(limit)
                rows.extend(
                    conn.execute(
                        f"""
                        SELECT f.*, s.title, s.url, s.source_unit, s.publish_date
                        FROM assistant_facts f
                        JOIN assistant_sources s ON s.id = f.source_id
                        WHERE f.service_type = ?
                          AND (f.fact_text LIKE ? {slot_filter})
                        ORDER BY f.id
                        LIMIT ?
                        """,
                        params,
                    ).fetchall()
                )
        seen: set[int] = set()
        facts: list[AssistantFact] = []
        for row in rows:
            if int(row["id"]) in seen:
                continue
            seen.add(int(row["id"]))
            facts.append(
                AssistantFact(
                    id=int(row["id"]),
                    slot=str(row["slot"]),
                    text=str(row["fact_text"]),
                    quote=str(row["quote"]),
                    title=str(row["title"]),
                    url=str(row["url"]),
                    source_unit=str(row["source_unit"]),
                    publish_date=row["publish_date"],
                )
            )
        return facts[:limit]

    @staticmethod
    def _fts_query(text: str) -> str:
        tokens = re.findall(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]{2,}", text or "")
        tokens.extend(["报销", "财务", "项目", "费用"])
        cleaned = []
        for token in list(dict.fromkeys(tokens))[:12]:
            token = token.replace('"', " ").strip()
            if token:
                cleaned.append(f'"{token}"')
        return " OR ".join(cleaned)

    def stats(self) -> dict[str, int | str]:
        with self.connect() as conn:
            sources = conn.execute("SELECT COUNT(*) AS n FROM assistant_sources").fetchone()["n"]
            facts = conn.execute("SELECT COUNT(*) AS n FROM assistant_facts").fetchone()["n"]
            services = conn.execute("SELECT COUNT(DISTINCT service_type) AS n FROM assistant_facts").fetchone()["n"]
        return {
            "dataset": "campus_assistant",
            "database": str(self.db_path),
            "sources": int(sources),
            "facts": int(facts),
            "services": int(services),
        }


class ReimbursementAssistant:
    def __init__(self, store: CampusAssistantStore | None = None) -> None:
        self.store = store or CampusAssistantStore()

    def answer(self, payload: ReimbursementRequest) -> ReimbursementResponse:
        self.store.init_db()
        project_name = payload.project_name or self._extract_project_name(payload.question)
        project_code = payload.project_code or self._extract_project_code(payload.question)
        expense_type = payload.expense_type or self._extract_expense_type(payload.question)
        slots = ["scope", "system", "process", "material", "location", "time", "risk", "exception"]
        query = " ".join(
            item
            for item in [
                payload.question,
                project_name,
                project_code,
                expense_type,
                payload.profile.college,
                payload.profile.campus,
            ]
            if item
        )
        facts = self.store.search_facts(query, slots=slots, limit=18, service_type="finance_reimbursement")
        by_slot = self._facts_by_slot(facts)
        sources = self._sources_from_facts(facts)

        materials = [
            "合法有效的发票或报销票据",
            "项目号",
            "附件张数、联系电话等网上预约信息",
            "网上预约报销确认单",
            "项目负责人签字、经办人签字、所在院系或部门盖章",
        ]
        materials.extend(fact.text for fact in by_slot.get("material", []))
        materials = self._dedupe(materials)

        steps = [
            "确认费用是否属于项目预算和可报销范围；如果项目管理对时限或材料有更具体要求，应先按项目要求执行。",
            "登录东南大学校园信息门户或东南大学公共服务中的财务平台，进入网上预约报销系统。",
            "点击报销申请，录入项目号、附件张数、联系电话等信息，并按票据类型选择业务报销类型。",
            "按系统页面说明填写报销信息；如为对公支付，准确填写收款单位、银行账号、开户行等信息。",
            "打印预约报销确认单，交项目负责人、经办人签字，并由所在院系或部门盖章。",
            "在工作日将确认单和报销票据送至财务处报销大厅网上报销咨询接单处。",
            "等待财务审核；若票据存在问题，审核人员会通过预约单预留联系方式联系经办人。",
        ]

        actors = self._dedupe(["经办人", "项目负责人", "所在院系或部门", "财务处服务窗口/审核人员"])
        locations = self._dedupe([fact.text for fact in by_slot.get("location", [])] or ["财务处报销大厅网上报销咨询接单处"])
        systems = self._dedupe(
            [
                "东南大学校园信息门户：http://my.seu.edu.cn/",
                "东南大学公共服务财务平台：https://www.seu.edu.cn/103/",
                *[fact.text for fact in by_slot.get("system", [])],
            ]
        )
        warnings = self._warnings(payload, project_code, expense_type, by_slot)
        missing_fields = self._missing_fields(project_name, project_code, expense_type, payload.invoice_date, payload.payment_target)
        answer = self._render_answer(
            project_name=project_name,
            project_code=project_code,
            expense_type=expense_type,
            materials=materials,
            steps=steps,
            actors=actors,
            locations=locations,
            systems=systems,
            warnings=warnings,
            missing_fields=missing_fields,
            sources=sources,
        )
        return ReimbursementResponse(
            answer=answer,
            project_name=project_name,
            project_code=project_code,
            expense_type=expense_type,
            materials=materials,
            steps=steps,
            actors=actors,
            locations=locations,
            systems=systems,
            warnings=warnings,
            missing_fields=missing_fields,
            sources=sources,
        )

    @staticmethod
    def _extract_project_name(question: str) -> str | None:
        patterns = [
            r"项目\s*([A-Za-z0-9_\-\u4e00-\u9fff]{2,40})\s*产生",
            r"我的\s*([A-Za-z0-9_\-\u4e00-\u9fff]{2,40})\s*项目",
            r"项目名[称]?[：:]\s*([A-Za-z0-9_\-\u4e00-\u9fff]{2,40})",
        ]
        for pattern in patterns:
            match = re.search(pattern, question)
            if match:
                value = match.group(1).strip(" ，,。；;")
                return None if value.lower() in {"xxx", "xx"} else value
        return None

    @staticmethod
    def _extract_project_code(question: str) -> str | None:
        match = re.search(r"(?:项目号|项目代码|经费号)[：:\s]*([A-Za-z0-9\-]{4,24})", question)
        if match:
            return match.group(1)
        match = re.search(r"\b([678]\d{5,})\b", question)
        return match.group(1) if match else None

    @staticmethod
    def _extract_expense_type(question: str) -> str | None:
        expense_terms = ["差旅", "材料", "设备", "版面费", "劳务", "会议", "外协", "工程款", "国际交流", "转出款"]
        for term in expense_terms:
            if term in question:
                return term
        return None

    @staticmethod
    def _facts_by_slot(facts: list[AssistantFact]) -> dict[str, list[AssistantFact]]:
        output: dict[str, list[AssistantFact]] = {}
        for fact in facts:
            output.setdefault(fact.slot, []).append(fact)
        return output

    @staticmethod
    def _sources_from_facts(facts: list[AssistantFact]) -> list[AssistantSource]:
        sources: list[AssistantSource] = []
        seen: set[tuple[str, str]] = set()
        for fact in facts:
            key = (fact.url, fact.quote)
            if key in seen:
                continue
            seen.add(key)
            sources.append(
                AssistantSource(
                    ref=f"[{len(sources) + 1}]",
                    title=fact.title,
                    url=fact.url,
                    source_unit=fact.source_unit,
                    publish_date=fact.publish_date,
                    quote=fact.quote,
                    slot=fact.slot,
                )
            )
        return sources[:8]

    @staticmethod
    def _warnings(
        payload: ReimbursementRequest,
        project_code: str | None,
        expense_type: str | None,
        by_slot: dict[str, list[AssistantFact]],
    ) -> list[str]:
        warnings = [fact.text for fact in by_slot.get("risk", [])]
        warnings.extend(fact.text for fact in by_slot.get("time", []))
        if project_code and project_code[0] in {"6", "7", "8"}:
            warnings.append("你的项目号疑似以 6/7/8 开头，若属于在研科研项目，报销校区可能需要由项目负责人固定选择。")
        if expense_type in {"设备", "工程款", "外协", "国际交流", "转出款"}:
            warnings.append("该费用类型可能涉及专项经费预算书，请随网约单一并准备预算依据。")
        if payload.invoice_date:
            deadline_warning = ReimbursementAssistant._invoice_deadline_warning(payload.invoice_date)
            if deadline_warning:
                warnings.append(deadline_warning)
        return ReimbursementAssistant._dedupe(warnings)[:8]

    @staticmethod
    def _invoice_deadline_warning(invoice_date: str) -> str | None:
        try:
            parsed = datetime.strptime(invoice_date, "%Y-%m-%d").date()
        except ValueError:
            return "发票日期格式建议使用 YYYY-MM-DD；系统暂时无法判断是否临近报销时限。"
        today = date.today()
        if parsed.year < today.year:
            return "发票开具年份早于当前年份，可能已涉及跨年度报销限制，请先核对是否仍在有效期限内。"
        if parsed.month >= 10:
            return "该发票属于第四季度开具，若当年无法完成报销，特殊情况下通常需在次年 6 月 30 日前完成。"
        return "当年开具的发票原则上应在当年 12 月 30 日前完成报销。"

    @staticmethod
    def _missing_fields(
        project_name: str | None,
        project_code: str | None,
        expense_type: str | None,
        invoice_date: str | None,
        payment_target: str | None,
    ) -> list[str]:
        missing = []
        if not project_name:
            missing.append("项目名称")
        if not project_code:
            missing.append("项目号/经费号")
        if not expense_type:
            missing.append("费用类型")
        if not invoice_date:
            missing.append("发票开具日期")
        if not payment_target:
            missing.append("付款对象：个人或单位")
        return missing

    @staticmethod
    def _render_answer(
        *,
        project_name: str | None,
        project_code: str | None,
        expense_type: str | None,
        materials: list[str],
        steps: list[str],
        actors: list[str],
        locations: list[str],
        systems: list[str],
        warnings: list[str],
        missing_fields: list[str],
        sources: list[AssistantSource],
    ) -> str:
        subject = project_name or "这个项目"
        expense = f"的{expense_type}费用" if expense_type else "产生的这笔费用"
        lines = [
            f"**结论：{subject}{expense}一般应通过东南大学网上预约报销流程办理。**",
        ]
        if project_code:
            lines.append(f"我识别到的项目号/经费号：{project_code}。")
        lines.extend(["", "所需材料："])
        lines.extend(f"{index}. {item}" for index, item in enumerate(materials, start=1))
        lines.extend(["", "办理流程："])
        lines.extend(f"{index}. {item}" for index, item in enumerate(steps, start=1))
        lines.extend(["", "相关人物/单位："])
        lines.extend(f"- {item}" for item in actors)
        lines.extend(["", "申请入口/办理入口："])
        lines.extend(f"- {item}" for item in systems)
        lines.extend(["", "地点："])
        lines.extend(f"- {item}" for item in locations)
        if warnings:
            lines.extend(["", "注意事项："])
            lines.extend(f"- {item}" for item in warnings)
        if missing_fields:
            lines.extend(["", "为了给出更精确的学院/校区/材料版本，还需要你补充："])
            lines.extend(f"- {item}" for item in missing_fields)
        lines.extend(["", "参考来源："])
        for source in sources[:6]:
            date_part = f"，{source.publish_date}" if source.publish_date else ""
            lines.append(f"{source.ref} {source.source_unit}：《{source.title}》{date_part}，{source.url}")
        return "\n".join(lines)

    @staticmethod
    def _dedupe(values: list[str]) -> list[str]:
        output: list[str] = []
        seen: set[str] = set()
        for value in values:
            text = re.sub(r"\s+", " ", str(value or "")).strip()
            if text and text not in seen:
                seen.add(text)
                output.append(text)
        return output


class OverseasApplicationAssistant:
    def __init__(self, store: CampusAssistantStore | None = None) -> None:
        self.store = store or CampusAssistantStore()

    def answer(self, payload: OverseasApplicationRequest) -> OverseasApplicationResponse:
        self.store.init_db()
        applicant_type = payload.applicant_type or payload.profile.student_type or self._extract_applicant_type(payload.question)
        destination = payload.destination or self._extract_destination(payload.question)
        visit_type = payload.visit_type or self._extract_visit_type(payload.question)
        funding_source = payload.funding_source or self._extract_funding_source(payload.question)
        slots = ["system", "process", "material", "location", "contact", "visa", "after_return", "risk"]
        query = " ".join(
            item
            for item in [
                payload.question,
                applicant_type,
                destination,
                visit_type,
                funding_source,
                payload.profile.college,
                payload.profile.campus,
            ]
            if item
        )
        facts = self.store.search_facts(query, slots=slots, limit=24, service_type="overseas_application")
        by_slot = ReimbursementAssistant._facts_by_slot(facts)
        sources = ReimbursementAssistant._sources_from_facts(facts)

        materials = [
            "出国（境）申请表或系统申报信息",
            "邀请函或外方项目录取/邀请材料",
            "护照信息及签证相关材料",
        ]
        if funding_source:
            materials.append("经费来源说明或项目号")
        materials.extend(fact.text for fact in by_slot.get("material", []))
        materials = ReimbursementAssistant._dedupe(materials)

        steps = self._steps(applicant_type, visit_type, by_slot)
        actors = ReimbursementAssistant._dedupe(
            [
                "申请人本人",
                "导师或项目负责人",
                "所在院系",
                "研究生院/教务处/学生处",
                "国际合作处",
            ]
        )
        locations = ReimbursementAssistant._dedupe([fact.text for fact in by_slot.get("location", [])])
        systems = self._systems(applicant_type, visit_type, [fact.text for fact in by_slot.get("system", [])])
        warnings = self._warnings(payload, by_slot)
        missing_fields = self._missing_fields(applicant_type, destination, visit_type, funding_source, payload.start_date)
        answer = self._render_answer(
            applicant_type=applicant_type,
            destination=destination,
            visit_type=visit_type,
            funding_source=funding_source,
            materials=materials,
            steps=steps,
            actors=actors,
            locations=locations,
            systems=systems,
            warnings=warnings,
            missing_fields=missing_fields,
            sources=sources,
        )
        return OverseasApplicationResponse(
            answer=answer,
            applicant_type=applicant_type,
            destination=destination,
            visit_type=visit_type,
            funding_source=funding_source,
            materials=materials,
            steps=steps,
            actors=actors,
            locations=locations,
            systems=systems,
            warnings=warnings,
            missing_fields=missing_fields,
            sources=sources,
        )

    @staticmethod
    def _steps(applicant_type: str | None, visit_type: str | None, by_slot: dict[str, list[AssistantFact]]) -> list[str]:
        steps: list[str] = []
        if applicant_type and "本科" in applicant_type:
            steps.append("先确认所申请项目属于本科项目、4+2项目还是本科生出国（境）交流学习管理系统中的交流项目。")
        elif applicant_type and "研究生" in applicant_type:
            steps.append("先确认项目通知来源：研究生院通知、研究生国际交流、国际会议资助或学院转发通知。")
        else:
            steps.append("先确认你的身份类别：本科生、研究生、教师，因不同身份对应的系统和审核链路不同。")
        if visit_type and "国际会议" in visit_type:
            steps.append("如为研究生出国（境）参加国际会议，先在网上办事服务大厅的研究生国际交流中提交境外参加国际会议申请并完成导师、院系、研究生院审核。")
        steps.extend(fact.text for fact in by_slot.get("process", []))
        steps.append("在出国前完成校内出国（境）申报审批；需要签证支持材料的，在申请批准后再按国际合作处要求办理。")
        steps.append("出行期间保留邀请、审批、签证、出入境等材料；回国后按系统或国际合作处要求办理返校、批件领取、学分认定或报销等后续事项。")
        return ReimbursementAssistant._dedupe(steps)

    @staticmethod
    def _extract_applicant_type(question: str) -> str | None:
        for term in ["本科生", "研究生", "硕士", "博士", "教师", "学生"]:
            if term in question:
                return "研究生" if term in {"硕士", "博士"} else term
        return None

    @staticmethod
    def _systems(applicant_type: str | None, visit_type: str | None, source_systems: list[str]) -> list[str]:
        systems = [
            "东南大学校园信息门户：http://my.seu.edu.cn/",
            "东南大学网上办事大厅：http://ehall.seu.edu.cn/",
            "国际合作处师生出国（境）栏目：https://oic.seu.edu.cn/18918/list.htm",
            "国际合作处学生赴国外及港澳申报：https://oic.seu.edu.cn/xsfgwjgasb/list.htm",
        ]
        if applicant_type and "本科" in applicant_type:
            systems.append("本科项目申请入口：校园信息门户 http://my.seu.edu.cn/ → 教学服务 → 出国申请（教务处）")
            systems.append("本科生出国（境）交流学习管理系统入口：网上办事大厅 http://ehall.seu.edu.cn/")
        if applicant_type and "研究生" in applicant_type:
            systems.append("研究生项目通知入口：东南大学研究生院 http://seugs.seu.edu.cn/")
        if visit_type and "国际会议" in visit_type:
            systems.append("研究生国际会议申请入口：网上办事服务大厅 → 研究生国际交流 → 境外参加国际会议")
            systems.append("学生因公出国申报入口：网上办事服务大厅 → 学生因公出国申报")
        systems.extend(source_systems)
        return ReimbursementAssistant._dedupe(systems)

    @staticmethod
    def _extract_destination(question: str) -> str | None:
        match = re.search(r"去\s*([\u4e00-\u9fffA-Za-z]{2,20})(?:参加|交流|访学|开会|出国|申请)", question)
        if match:
            return match.group(1)
        for term in ["国外", "境外", "港澳", "台湾", "美国", "英国", "日本", "韩国", "德国", "法国", "新加坡"]:
            if term in question:
                return term
        return None

    @staticmethod
    def _extract_visit_type(question: str) -> str | None:
        for term in ["国际会议", "学期交流", "交换", "双学位", "访学", "联合培养", "CSC", "短期交流", "长期交流"]:
            if term in question:
                return term
        return None

    @staticmethod
    def _extract_funding_source(question: str) -> str | None:
        for term in ["导师科研项目", "科研项目", "985经费", "211经费", "重点学科建设经费", "CSC", "自费", "公费"]:
            if term in question:
                return term
        return None

    @staticmethod
    def _warnings(payload: OverseasApplicationRequest, by_slot: dict[str, list[AssistantFact]]) -> list[str]:
        warnings = [fact.text for fact in by_slot.get("risk", [])]
        warnings.extend(fact.text for fact in by_slot.get("visa", []))
        if payload.start_date:
            try:
                start = datetime.strptime(payload.start_date, "%Y-%m-%d").date()
                if start <= date.today():
                    warnings.append("你填写的出发日期不晚于今天；出国（境）审批通常要求出国前完成，请立即核对是否还能补办。")
            except ValueError:
                warnings.append("出发日期建议使用 YYYY-MM-DD；系统暂时无法判断是否临近审批时限。")
        return ReimbursementAssistant._dedupe(warnings)[:8]

    @staticmethod
    def _missing_fields(
        applicant_type: str | None,
        destination: str | None,
        visit_type: str | None,
        funding_source: str | None,
        start_date: str | None,
    ) -> list[str]:
        missing = []
        if not applicant_type:
            missing.append("申请人身份：本科生/研究生/教师")
        if not destination:
            missing.append("出访目的地")
        if not visit_type:
            missing.append("出访类型：国际会议/交换/访学/联合培养/CSC等")
        if not funding_source:
            missing.append("经费来源：自费/公费/导师科研项目/专项经费等")
        if not start_date:
            missing.append("计划出发日期")
        return missing

    @staticmethod
    def _render_answer(
        *,
        applicant_type: str | None,
        destination: str | None,
        visit_type: str | None,
        funding_source: str | None,
        materials: list[str],
        steps: list[str],
        actors: list[str],
        locations: list[str],
        systems: list[str],
        warnings: list[str],
        missing_fields: list[str],
        sources: list[AssistantSource],
    ) -> str:
        subject = applicant_type or "你"
        target = f"去{destination}" if destination else "出国（境）"
        purpose = f"办理{visit_type}" if visit_type else "办理出国（境）申请"
        lines = [f"**结论：{subject}{target}{purpose}，应先完成校内出国（境）申报审批，再办理签证/派出/返校等后续事项。**"]
        if funding_source:
            lines.append(f"我识别到的经费来源：{funding_source}。")
        lines.extend(["", "所需材料："])
        lines.extend(f"{index}. {item}" for index, item in enumerate(materials, start=1))
        lines.extend(["", "办理流程："])
        lines.extend(f"{index}. {item}" for index, item in enumerate(steps, start=1))
        lines.extend(["", "相关人物/单位："])
        lines.extend(f"- {item}" for item in actors)
        if systems:
            lines.extend(["", "申请入口/办理入口："])
            lines.extend(f"- {item}" for item in systems)
        if locations:
            lines.extend(["", "地点："])
            lines.extend(f"- {item}" for item in locations)
        if warnings:
            lines.extend(["", "注意事项："])
            lines.extend(f"- {item}" for item in warnings)
        if missing_fields:
            lines.extend(["", "为了给出更精确的申请路径，还需要你补充："])
            lines.extend(f"- {item}" for item in missing_fields)
        lines.extend(["", "参考来源："])
        for source in sources[:8]:
            date_part = f"，{source.publish_date}" if source.publish_date else ""
            lines.append(f"{source.ref} {source.source_unit}：《{source.title}》{date_part}，{source.url}")
        return "\n".join(lines)
