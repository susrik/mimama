"""Tests for mimama.core.query."""

import json
import os
from datetime import date, datetime
from pathlib import Path

import pytest

from mimama.config import QueryConfig, UserConfig
from mimama.core.notes import NoteStore
from mimama.core.query import (
    QueryError,
    QueryPlan,
    _excerpt,
    plan_query,
    prefilter,
    rerank,
    run_query,
)
from tests.fakes import FakeLLMClient

USER = UserConfig(name="Erik Reid", aliases=["Erik"])
SETTINGS = QueryConfig(max_candidates=10, excerpt_chars=300)
TODAY = date(2026, 8, 30)


def set_mtime(path: Path, day: date) -> None:
    ts = datetime(day.year, day.month, day.day, 12, 0).timestamp()
    os.utime(path, (ts, ts))


@pytest.fixture()
def store(tmp_path: Path) -> NoteStore:
    eurohpc = tmp_path / "projects" / "eurohpc"
    eurohpc.mkdir(parents=True)
    recent = eurohpc / "2026-08-29-sync.md"
    recent.write_text("EuroHPC sync. Erik presented the plan.", encoding="utf-8")
    old = eurohpc / "2026-08-01-kickoff.md"
    old.write_text("EuroHPC kickoff. We decided to use litellm.", encoding="utf-8")
    other = tmp_path / "personal"
    other.mkdir()
    journal = other / "journal.md"
    journal.write_text("dear diary, nothing about supercomputers", encoding="utf-8")
    set_mtime(journal, date(2026, 8, 29))
    return NoteStore(root=tmp_path)


def plan_json(**overrides: object) -> str:
    data: dict[str, object] = {
        "keywords": ["eurohpc"],
        "date_from": None,
        "date_to": None,
        "subtree": None,
        "semantic_question": None,
    }
    data.update(overrides)
    return json.dumps(data)


class TestPlanQuery:
    def test_valid_plan(self) -> None:
        llm = FakeLLMClient(responses=[plan_json(date_from="2026-08-28")])
        plan = plan_query(llm, "eurohpc notes in the last 2 days", USER, today=TODAY)
        assert plan.keywords == ["eurohpc"]
        assert plan.date_from == date(2026, 8, 28)

    def test_prompt_contains_today_and_user(self) -> None:
        llm = FakeLLMClient(responses=[plan_json()])
        plan_query(llm, "q", USER, today=TODAY)
        _, user_prompt = llm.calls[0]
        assert "2026-08-30" in user_prompt
        assert "Erik Reid" in user_prompt

    def test_unconfigured_user(self) -> None:
        llm = FakeLLMClient(responses=[plan_json()])
        plan_query(llm, "q", UserConfig(), today=TODAY)
        _, user_prompt = llm.calls[0]
        assert "not configured" in user_prompt

    def test_json_extracted_from_chatter(self) -> None:
        llm = FakeLLMClient(responses=["Here is the plan:\n" + plan_json() + "\nok?"])
        assert plan_query(llm, "q", USER, today=TODAY).keywords == ["eurohpc"]

    def test_retry_then_success(self) -> None:
        llm = FakeLLMClient(responses=["not json at all", plan_json()])
        assert plan_query(llm, "q", USER, today=TODAY).keywords == ["eurohpc"]
        assert len(llm.calls) == 2

    def test_two_failures_raise(self) -> None:
        llm = FakeLLMClient(responses=["nope", "still nope"])
        with pytest.raises(QueryError, match="valid query plan"):
            plan_query(llm, "q", USER, today=TODAY)


class TestPrefilter:
    def test_keyword_filter(self, store: NoteStore) -> None:
        plan = QueryPlan(keywords=["eurohpc"])
        paths = [c.path for c in prefilter(store, plan)]
        assert paths == [
            "projects/eurohpc/2026-08-29-sync.md",
            "projects/eurohpc/2026-08-01-kickoff.md",
        ]

    def test_date_range_filter(self, store: NoteStore) -> None:
        plan = QueryPlan(
            keywords=["eurohpc"], date_from=date(2026, 8, 28), date_to=date(2026, 8, 30)
        )
        paths = [c.path for c in prefilter(store, plan)]
        assert paths == ["projects/eurohpc/2026-08-29-sync.md"]

    def test_no_keywords_dates_only(self, store: NoteStore) -> None:
        plan = QueryPlan(date_from=date(2026, 8, 28))
        paths = {c.path for c in prefilter(store, plan)}
        assert paths == {
            "projects/eurohpc/2026-08-29-sync.md",
            "personal/journal.md",
        }

    def test_subtree_scope(self, store: NoteStore) -> None:
        plan = QueryPlan(subtree="personal")
        assert [c.path for c in prefilter(store, plan)] == ["personal/journal.md"]

    def test_bad_subtree_falls_back_to_root(self, store: NoteStore) -> None:
        plan = QueryPlan(keywords=["eurohpc"], subtree="does/not/exist")
        assert len(prefilter(store, plan)) == 2

    def test_sorted_newest_first(self, store: NoteStore) -> None:
        candidates = prefilter(store, QueryPlan(keywords=["eurohpc"]))
        assert candidates[0].note_date > candidates[1].note_date

    def test_binaryish_skipped(self, store: NoteStore) -> None:
        (store.root / "blob.md").write_bytes(b"\xff\xfe\x00eurohpc")
        candidates = prefilter(store, QueryPlan(keywords=["eurohpc"]))
        assert all("blob" not in c.path for c in candidates)


class TestExcerpt:
    def test_short_content_unchanged(self) -> None:
        assert _excerpt("short", ["x"], 100) == "short"

    def test_centred_on_keyword(self) -> None:
        content = "a" * 500 + "NEEDLE" + "b" * 500
        result = _excerpt(content, ["needle"], 100)
        assert "NEEDLE" in result
        assert result.startswith("...") and result.endswith("...")
        assert len(result) <= 106

    def test_no_keyword_hit_uses_head(self) -> None:
        content = "HEAD" + "x" * 500
        result = _excerpt(content, ["missing"], 100)
        assert result.startswith("HEAD")


class TestRerank:
    def test_selects_and_justifies(self, store: NoteStore) -> None:
        candidates = prefilter(store, QueryPlan(keywords=["eurohpc"]))
        llm = FakeLLMClient(
            responses=[
                json.dumps(
                    [
                        {
                            "path": "projects/eurohpc/2026-08-01-kickoff.md",
                            "reason": "decision about litellm",
                        }
                    ]
                )
            ]
        )
        ranked = rerank(llm, "where did we decide on litellm?", candidates,
                        ["eurohpc"], SETTINGS)
        assert len(ranked) == 1
        info, reason = ranked[0]
        assert info.path == "projects/eurohpc/2026-08-01-kickoff.md"
        assert reason == "decision about litellm"

    def test_unknown_paths_dropped(self, store: NoteStore) -> None:
        candidates = prefilter(store, QueryPlan(keywords=["eurohpc"]))
        llm = FakeLLMClient(
            responses=[json.dumps([{"path": "hallucinated.md", "reason": "x"}])]
        )
        assert rerank(llm, "q", candidates, [], SETTINGS) == []

    def test_candidates_capped(self, store: NoteStore) -> None:
        candidates = prefilter(store, QueryPlan(keywords=["eurohpc"]))
        llm = FakeLLMClient(responses=["[]"])
        rerank(llm, "q", candidates, [], QueryConfig(max_candidates=1, excerpt_chars=50))
        _, prompt = llm.calls[0]
        assert prompt.count("### ") == 1

    def test_retry_then_failure(self, store: NoteStore) -> None:
        candidates = prefilter(store, QueryPlan(keywords=["eurohpc"]))
        llm = FakeLLMClient(responses=["bad", "also bad"])
        with pytest.raises(QueryError, match="valid rerank result"):
            rerank(llm, "q", candidates, [], SETTINGS)


class TestRunQuery:
    def test_filter_only_one_llm_call(self, store: NoteStore) -> None:
        llm = FakeLLMClient(
            responses=[plan_json(date_from="2026-08-28", date_to="2026-08-30")]
        )
        plan, results = run_query(store, llm, "eurohpc last 2 days", USER, SETTINGS,
                                  today=TODAY)
        assert len(llm.calls) == 1  # no semantic stage
        assert [r.path for r in results] == ["projects/eurohpc/2026-08-29-sync.md"]
        assert results[0].reason == ""
        assert plan.semantic_question is None

    def test_semantic_query_two_llm_calls(self, store: NoteStore) -> None:
        llm = FakeLLMClient(
            responses=[
                plan_json(semantic_question="where did we decide on litellm?"),
                json.dumps(
                    [
                        {
                            "path": "projects/eurohpc/2026-08-01-kickoff.md",
                            "reason": "the litellm decision",
                        }
                    ]
                ),
            ]
        )
        _, results = run_query(store, llm, "the meeting where we decided...",
                               USER, SETTINGS, today=TODAY)
        assert len(llm.calls) == 2
        assert results[0].path == "projects/eurohpc/2026-08-01-kickoff.md"
        assert results[0].reason == "the litellm decision"

    def test_semantic_with_no_candidates_skips_rerank(self, store: NoteStore) -> None:
        llm = FakeLLMClient(
            responses=[
                plan_json(keywords=["nonexistentterm"], semantic_question="q?")
            ]
        )
        _, results = run_query(store, llm, "q", USER, SETTINGS, today=TODAY)
        assert results == []
        assert len(llm.calls) == 1

    def test_results_capped_without_semantic(self, store: NoteStore) -> None:
        llm = FakeLLMClient(responses=[plan_json()])
        _, results = run_query(
            store, llm, "q", USER, QueryConfig(max_candidates=1, excerpt_chars=100),
            today=TODAY,
        )
        assert len(results) == 1

    def test_snippets_single_line(self, store: NoteStore) -> None:
        llm = FakeLLMClient(responses=[plan_json()])
        _, results = run_query(store, llm, "q", USER, SETTINGS, today=TODAY)
        assert all("\n" not in r.snippet for r in results)
