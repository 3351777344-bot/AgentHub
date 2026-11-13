"""Tests for scoring denominators and the public regression data contract."""

from uuid import uuid4

from agenthub.evaluations.models import EvaluationResult
from agenthub.evaluations.schemas import AgentCase, Observation, RagCase, TraceStep
from agenthub.evaluations.scoring import score_case, summarize


def test_macro_recall_micro_citations_and_missing_usage():
    first, second, distractor = uuid4(), uuid4(), uuid4()
    case = RagCase(
        kind="rag",
        key="gold",
        question="when?",
        knowledge_base_id=uuid4(),
        outcome="answered",
        gold_chunk_ids=[first, second],
        answer_contains=["18:30"],
    )
    observation = Observation(
        answer="１８:３０",
        outcome="answered",
        retrieved_chunk_ids=[first, first, distractor],
        context_chunk_ids=[first],
        cited_chunk_ids=[first, first, distractor],
        latency_ms=10,
        completion_tokens=0,
    )
    score, failure = score_case(case, observation, execution_ok=True)
    assert failure == "scoring"
    assert score.recall_at_k == 0.5 and score.answer_terms_match
    assert score.citations_correct == 1 and score.citations_total == 2
    report = summarize(
        [
            EvaluationResult(
                status="failed", score=score.model_dump(), observation=observation.model_dump()
            ),
            EvaluationResult(status="skipped"),
        ],
        12,
    )
    assert report.task_success_rate == 0
    assert report.recall_at_k == 0.5 and report.recall_cases == 1
    assert report.citation_precision == 0.5 and report.citations_total == 2
    assert report.prompt_tokens.total is None and report.prompt_tokens.reported_cases == 0
    assert report.completion_tokens.total == 0 and report.completion_tokens.reported_cases == 1


def test_tool_denial_and_order_are_independent_of_answer_match():
    case = AgentCase(
        kind="agent",
        key="tool",
        question="try tools",
        expected_tools=[{"name": "add_integers", "version": 1}],
        expected_denials=["agent_tool_forbidden"],
        answer_contains=["42"],
    )
    observation = Observation(
        answer="42",
        steps=[
            TraceStep(
                ordinal=2,
                kind="tool",
                status="rejected",
                error_code="agent_tool_forbidden",
                requested_alias="private_export__v1",
            ),
            TraceStep(
                ordinal=3,
                kind="tool",
                status="completed",
                error_code=None,
                tool={"name": "add_integers", "version": 1},
            ),
        ],
    )
    assert score_case(case, observation, execution_ok=True)[1] is None
    observation.steps.pop(0)
    score, stage = score_case(case, observation, execution_ok=True)
    assert score.answer_terms_match and not score.tool_selection_match and stage == "tool"


def test_api_schema_and_authentication(client):
    spec = client.get("/openapi.json").json()
    path = "/api/v1/evaluations/datasets"
    assert set(spec["paths"][path]) == {"get", "post"}
    assert spec["paths"][path]["get"]["security"]
    assert client.get(path).status_code == 401
    assert client.post(path, content=b" " * 524289).status_code == 413
