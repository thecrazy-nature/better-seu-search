from __future__ import annotations


INTENT_PRESETS = {
    "find_document": {
        "label": "找原文",
        "description": "用户想找到通知、公告、原文链接或具体文章。",
        "retrieval_focus": "标题、来源、发布日期、附件名，少总结，优先返回链接。",
        "output_preset": "document_list",
    },
    "answer_question": {
        "label": "问结论",
        "description": "用户想知道一个明确答案。",
        "retrieval_focus": "主题相关正文片段，保留多个权威来源。",
        "output_preset": "sourced_answer",
    },
    "process_guide": {
        "label": "查流程",
        "description": "用户问怎么办、如何申请、需要哪些步骤和材料。",
        "retrieval_focus": "办事指南、流程、申请材料、系统入口、办理地点。",
        "output_preset": "process_steps",
    },
    "deadline_query": {
        "label": "查截止时间",
        "description": "用户问时间、报名时间、截止日期、DDL。",
        "retrieval_focus": "日期、时间范围、截止时间，强制检查是否过期。",
        "output_preset": "deadline_brief",
    },
    "eligibility_query": {
        "label": "查适用对象",
        "description": "用户问自己能不能参加、是否符合条件。",
        "retrieval_focus": "学院、年级、学生类型、专业、申请条件。",
        "output_preset": "eligibility_brief",
    },
    "attachment_query": {
        "label": "找附件",
        "description": "用户找申请表、名单、PDF、Word、Excel 或下载材料。",
        "retrieval_focus": "附件名、附件所属通知、文件类型。",
        "output_preset": "attachment_list",
    },
    "latest_updates": {
        "label": "查最新通知",
        "description": "用户想看今天、本周或最近发布的信息。",
        "retrieval_focus": "发布时间倒序，来源和栏目筛选。",
        "output_preset": "latest_digest",
    },
    "profile_query": {
        "label": "按身份检索",
        "description": "用户按学院、年级、身份、校区查询相关信息。",
        "retrieval_focus": "适用对象匹配，其次主题相关度。",
        "output_preset": "profile_digest",
    },
    "unknown": {
        "label": "无法判断",
        "description": "用户意图不明确。",
        "retrieval_focus": "通用检索并提示用户补充条件。",
        "output_preset": "clarify_or_search",
    },
}


OUTPUT_PRESETS = {
    "document_list": ["我找到的原文", "为什么匹配", "附件", "参考消息源"],
    "sourced_answer": ["结论", "依据", "注意事项", "参考消息源"],
    "process_steps": ["办理步骤", "所需材料", "时间/地点/入口", "注意事项", "参考消息源"],
    "deadline_brief": ["关键时间", "是否过期", "适用对象", "参考消息源"],
    "eligibility_brief": ["简短结论", "适用对象", "条件与限制", "仍需核对", "参考消息源"],
    "attachment_list": ["可下载材料", "所属通知", "适用对象", "参考消息源"],
    "latest_digest": ["最新通知", "即将截止", "可能需要关注", "参考消息源"],
    "profile_digest": ["与你最相关", "全校通用信息", "学院/年级补充", "参考消息源"],
    "clarify_or_search": ["可能相关结果", "建议补充的问题", "参考消息源"],
}


def intent_preset_prompt() -> str:
    lines = []
    for key, item in INTENT_PRESETS.items():
        lines.append(
            f"- {key}: {item['label']}。{item['description']} 检索重点: {item['retrieval_focus']} "
            f"默认 output_preset={item['output_preset']}"
        )
    return "\n".join(lines)


def output_preset_prompt() -> str:
    lines = []
    for key, sections in OUTPUT_PRESETS.items():
        lines.append(f"- {key}: " + " / ".join(sections))
    return "\n".join(lines)


def output_preset_for_intent(intent: str) -> str:
    item = INTENT_PRESETS.get(intent)
    return item["output_preset"] if item else "sourced_answer"
