from __future__ import annotations


SYNONYMS: dict[str, list[str]] = {
    "四六级": ["CET", "大学英语四级", "大学英语六级", "全国大学英语四、六级考试", "英语四六级"],
    "四级": ["CET4", "大学英语四级", "全国大学英语四级考试"],
    "六级": ["CET6", "大学英语六级", "全国大学英语六级考试"],
    "转专业": ["专业调整", "本科生转专业", "院内转专业", "转入专业", "转出专业"],
    "校历": ["教学日历", "学期安排", "放假安排", "上课时间", "开学时间"],
    "缓考": ["课程考核缓考", "考试延期", "因病缓考", "缓考申请"],
    "补考": ["课程补考", "补考安排", "重修考试", "考试安排"],
    "评教": ["网上评教", "教学评价", "课程评价", "学生评教"],
    "辅修": ["辅修专业", "微专业", "第二专业", "辅修报名"],
    "选课": ["课程选课", "补退选", "选课系统", "选课安排"],
    "成绩": ["成绩查询", "成绩复核", "成绩单", "绩点"],
    "成绩复核": ["成绩复查", "成绩核查", "复核申请", "查分", "成绩异议"],
    "准考证": ["准考证打印", "打印准考证", "CET准考证", "四六级准考证"],
    "成绩单": ["电子成绩单", "中文成绩单", "英文成绩单", "成绩证明", "打印成绩单"],
    "毕业": ["毕业审核", "学位申请", "毕业设计", "毕业论文", "离校"],
    "毕业审核": ["毕业资格审核", "毕业资格", "毕业预审核", "毕业资格审查", "学分核对"],
    "毕业设计": ["毕业论文", "毕设", "毕业设计（论文）"],
    "毕业生学分核对": ["毕业班", "选课学分核对", "学分核对", "毕业生", "毕业班同学"],
    "奖学金": ["奖助学金", "评奖评优", "助学金", "荣誉称号"],
    "保研": ["推荐免试", "推免", "免试研究生", "推荐优秀应届本科毕业生"],
    "推免": ["保研", "推荐免试", "免试研究生", "推荐优秀应届本科毕业生"],
    "微专业": ["辅修", "辅修专业", "第二专业", "微专业报名"],
}


def expand_terms(query: str) -> list[str]:
    expanded = [query]
    query_lower = query.lower()
    matched_keys = [key for key in SYNONYMS if key.lower() in query_lower]
    specific_keys = [
        key
        for key in matched_keys
        if not any(key != other and key.lower() in other.lower() for other in matched_keys)
    ]
    for key in specific_keys:
        values = SYNONYMS[key]
        expanded.extend(values)
    deduped: list[str] = []
    seen: set[str] = set()
    for item in expanded:
        normalized = item.strip()
        if normalized and normalized.lower() not in seen:
            seen.add(normalized.lower())
            deduped.append(normalized)
    return deduped
