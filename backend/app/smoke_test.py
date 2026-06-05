from __future__ import annotations

from .ai.answerer import Answerer
from .ai.planner import QueryPlanner
from .models import UserProfile
from .search.engine import SearchEngine


def main() -> None:
    query = "计算机学院大二能不能转专业"
    profile = UserProfile(college="计算机科学与工程学院", grade="2024级", student_type="本科生")
    plan = QueryPlanner().plan(query, profile)
    hits = SearchEngine().search(plan, profile, 5)
    answer = Answerer().answer(query, plan, hits)
    print(plan.model_dump_json(indent=2))
    print("hits:", [(hit.title, hit.score) for hit in hits])
    print(answer.answer[:600])


if __name__ == "__main__":
    main()
