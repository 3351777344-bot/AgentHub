"""Version 1 rule scoring. Human reviews are counted independently."""

import math
import unicodedata

from agenthub.evaluations.models import EvaluationResult
from agenthub.evaluations.schemas import (
    FailureStage,
    Observation,
    RagCase,
    Report,
    Sample,
    Score,
    Usage,
)

SCORING_VERSION = 1


def normalize(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def score_case(
    case: Sample, observed: Observation, *, execution_ok: bool
) -> tuple[Score, FailureStage | None]:
    score = Score()
    if case.answer_contains:
        answer = normalize(observed.answer or "")
        score.answer_terms_match = all(normalize(term) in answer for term in case.answer_contains)
    if isinstance(case, RagCase):
        gold = set(case.gold_chunk_ids)
        cited = set(observed.cited_chunk_ids)
        score.citations_total = len(cited)
        score.citations_correct = len(cited & gold)
        if gold:
            score.recall_at_k = len(set(observed.retrieved_chunk_ids) & gold) / len(gold)
        score.outcome_match = observed.outcome == case.outcome
        if not execution_ok:
            return score, None
        if gold and not set(observed.context_chunk_ids) & gold:
            return score, "retrieval"
        if (
            not score.outcome_match
            or score.answer_terms_match is False
            or cited - gold
            or (gold and not cited)
        ):
            return score, "scoring"
        return score, None

    steps = [step for step in observed.steps if step.kind == "tool"]
    actual_tools = [step.tool for step in steps if step.status == "completed"]
    actual_denials = [
        step.error_code for step in steps if step.status == "rejected" and step.tool is None
    ]
    expected_denials = case.expected_denials
    score.tool_selection_match = (
        execution_ok
        and actual_tools == case.expected_tools
        and sorted(code or "" for code in actual_denials) == sorted(expected_denials)
        and len(steps) == len(actual_tools) + len(actual_denials)
    )
    if not execution_ok:
        return score, None
    if not score.tool_selection_match:
        return score, "tool"
    return score, "scoring" if score.answer_terms_match is False else None


def percentile(values: list[int], percent: float) -> int | None:
    return values[math.ceil(percent * len(values)) - 1] if values else None


def summarize(rows: list[EvaluationResult], reserved: int) -> Report:
    scores = [Score.model_validate(row.score) for row in rows if row.score is not None]
    observed = [
        Observation.model_validate(row.observation) for row in rows if row.observation is not None
    ]
    recalls = [score.recall_at_k for score in scores if score.recall_at_k is not None]
    tools = [
        score.tool_selection_match for score in scores if score.tool_selection_match is not None
    ]
    latencies = sorted(item.latency_ms for item in observed if item.latency_ms is not None)
    cited = sum(score.citations_total for score in scores)
    correct = sum(score.citations_correct for score in scores)
    passed = sum(row.status == "passed" for row in rows)
    reviews = [row.review for row in rows if row.review is not None]

    def usage(field: str) -> Usage:
        values = [value for item in observed if (value := getattr(item, field)) is not None]
        return Usage(total=sum(values) if values else None, reported_cases=len(values))

    return Report(
        total=len(rows),
        passed=passed,
        failed=sum(row.status == "failed" for row in rows),
        skipped=sum(row.status == "skipped" for row in rows),
        task_success_rate=passed / len(rows),
        recall_at_k=sum(recalls) / len(recalls) if recalls else None,
        recall_cases=len(recalls),
        citation_precision=correct / cited if cited else None,
        citations_correct=correct,
        citations_total=cited,
        tool_selection_rate=sum(tools) / len(tools) if tools else None,
        agent_cases=len(tools),
        latency_p50_ms=percentile(latencies, 0.50),
        latency_p95_ms=percentile(latencies, 0.95),
        latency_cases=len(latencies),
        prompt_tokens=usage("prompt_tokens"),
        completion_tokens=usage("completion_tokens"),
        embedding_tokens=usage("embedding_tokens"),
        completion_tokens_reserved=reserved,
        human_reviewed=len(reviews),
        human_passed=sum(review["passed"] for review in reviews),
    )
