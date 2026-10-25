"""T242 tests: the health-information agent (§59.12).

The agent is a fixed pipeline (no tools required — spec §59.12), so it is
exercised with a scripted ``FakeProvider`` for the single synthesis call. The
safety contract is the point: boundaries are structural (enforced at the
agent layer), not prompt-only.

Tests cover:
- Basic informational query returns structured frames
- Emergency/crisis content routes immediately, ahead of model call
- Structural refusal of diagnosis language
- Structural refusal of treatment/prescription language
- Professional direction when appropriate
- Mandatory disclaimer appended
- Missing question raises ``InvalidInputError``
- Bus progress events published
- Unconfigured router raises honest refusal, not degraded output
"""

from __future__ import annotations

from typing import Any

import pytest

from app.agents.health_info import HealthInfoAgent
from app.agents.health_info.report import SafetyCategory
from app.config import ModelRouterSettings
from app.core.errors import InvalidInputError
from app.events.bus import EventBus
from app.models.base import FinishReason, ModelResponse
from app.models.fake import FakeProvider
from app.models.router import ModelRouter

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------- #
# Doubles
# --------------------------------------------------------------------------- #
class RecordingBus(EventBus):
    """A real bus that keeps every published event, for assertions."""

    def __init__(self) -> None:
        super().__init__()
        self.published: list[tuple[str, dict[str, Any]]] = []

    async def publish(self, event_type: Any, payload: Any = None, **kwargs: Any) -> Any:
        self.published.append((str(event_type), dict(payload or {})))
        return await super().publish(event_type, payload, **kwargs)


def _answer(
    text: str = "Headache can be associated with dehydration or tension.",
    *,
    model: str = "llama3",
) -> ModelResponse:
    return ModelResponse(
        model=model,
        content=text,
        finish_reason=FinishReason.STOP,
        provider="fake",
    )


def _provider(*responses: ModelResponse) -> FakeProvider:
    return FakeProvider(name="ollama", local=True, responses=responses)


def _router(provider: FakeProvider) -> ModelRouter:
    return ModelRouter(
        [provider],
        settings=ModelRouterSettings(default="ollama:llama3", fallback_chain=[]),
    )


def _agent(
    *,
    provider: FakeProvider,
    bus: EventBus | None = None,
    capability: str = "reasoning",
    **kwargs: Any,
) -> HealthInfoAgent:
    return HealthInfoAgent(
        router=_router(provider),
        bus=bus,
        capability=capability,
        **kwargs,
    )


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_basic_informational_query() -> None:
    """An informational query about symptoms returns structured frames."""
    provider = _provider(_answer())
    agent = _agent(provider=provider)

    result = await agent.run({"question": "What causes headaches?"})

    assert isinstance(result, dict)
    assert result["agent"] == "health.info"
    assert result["question"] == "What causes headaches?"
    assert result.get("emergency") is not True  # not an emergency response
    assert isinstance(result.get("frames"), list)
    assert len(result["frames"]) > 0
    # Every frame carries uncertainty and informational_only flag
    for frame in result["frames"]:
        assert frame["informational_only"] is True
        assert frame["uncertainty"] != ""


@pytest.mark.asyncio
async def test_emergency_detection_routes_immediately() -> None:
    """Emergency/crisis keywords bypass the model entirely."""
    provider = _provider()  # empty — should never be consumed
    agent = _agent(provider=provider)

    for emergency_text in (
        "I want to hurt myself",
        "It's an emergency, help me",
        "I think I'm having a heart attack",
        "Overdose emergency",
        "Stroke symptoms",
    ):
        result = await agent.run({"question": emergency_text})
        assert result.get("emergency") is True
        assert result["frames"] == []
        assert result["refused"] == []
        assert "crisis" in result.get("professional_direction", "").lower() or \
               "emergency" in result.get("professional_direction", "").lower()
        # Provider was never called
        assert provider.call_count == 0


@pytest.mark.asyncio
async def test_diagnosis_refusal_enforced_structurally() -> None:
    """Diagnosis language in model output is structurally refused."""
    diagnostic_output = (
        "You have migraine based on your symptoms. "
        "This is a diagnosis."
    )
    provider = _provider(_answer(diagnostic_output))
    agent = _agent(provider=provider)

    result = await agent.run({"question": "I have headaches all the time"})

    # Refused patterns are recorded
    refused = result.get("refused", [])
    assert any(r[0] == SafetyCategory.DIAGNOSIS for r in refused), \
        f"Expected diagnosis refusal, got: {refused}"
    # Frames contain only safe content
    for frame in result["frames"]:
        assert SafetyCategory.DIAGNOSIS not in frame.get("category", "")


@pytest.mark.asyncio
async def test_treatment_refusal_enforced_structurally() -> None:
    """Treatment recommendations in model output are structurally refused."""
    treatment_output = (
        "You should take aspirin twice daily for this condition."
    )
    provider = _provider(_answer(treatment_output))
    agent = _agent(provider=provider)

    result = await agent.run({"question": "What should I do about my pain?"})

    refused = result.get("refused", [])
    assert any(r[0] == SafetyCategory.TREATMENT for r in refused), \
        f"Expected treatment refusal, got: {refused}"


@pytest.mark.asyncio
async def test_prescription_refusal_enforced_structurally() -> None:
    """Prescription/dosage language in model output is structurally refused."""
    prescription_output = (
        "The dosage is 500mg three times a day."
    )
    provider = _provider(_answer(prescription_output))
    agent = _agent(provider=provider)

    result = await agent.run({"question": "How much medicine should I take?"})

    refused = result.get("refused", [])
    assert any(r[0] == SafetyCategory.PRESCRIPTION for r in refused), \
        f"Expected prescription refusal, got: {refused}"


@pytest.mark.asyncio
async def test_professional_direction_on_severe_question() -> None:
    """Questions mentioning severe/persistent symptoms get professional direction."""
    provider = _provider(_answer("Chronic headaches can be associated with tension or migraines."))
    agent = _agent(provider=provider)

    result = await agent.run({"question": "I have persistent severe headaches"})

    assert result.get("professional_direction") is not None
    assert "professional" in result["professional_direction"].lower()


@pytest.mark.asyncio
async def test_professional_direction_on_refused_output() -> None:
    """When output is refused, professional direction is suggested."""
    provider = _provider(_answer("You need to take this medication immediately."))
    agent = _agent(provider=provider)

    result = await agent.run({"question": "What treatment do I need?"})

    assert result.get("professional_direction") is not None
    assert "consult" in result["professional_direction"].lower()


@pytest.mark.asyncio
async def test_mandatory_disclaimer_appended() -> None:
    """Every response includes a medical disclaimer."""
    provider = _provider(_answer("Headaches are common."))
    agent = _agent(provider=provider)

    result = await agent.run({"question": "Tell me about headaches"})

    # Find disclaimer frame
    disclaimers = [f for f in result["frames"] if f["category"] == "disclaimer"]
    assert len(disclaimers) >= 1, "Expected a disclaimer frame"
    assert "medical advice" in disclaimers[0]["content"].lower() or \
           "not a doctor" in disclaimers[0]["content"].lower()


@pytest.mark.asyncio
async def test_missing_question_raises() -> None:
    """A task without a question raises InvalidInputError."""
    provider = _provider()
    agent = _agent(provider=provider)

    with pytest.raises(InvalidInputError):
        await agent.run({"task_id": "some-task"})


@pytest.mark.asyncio
async def test_progress_events_published() -> None:
    """TASK_PROGRESS events are published during synthesis."""
    bus = RecordingBus()
    provider = _provider(_answer())
    agent = _agent(provider=provider, bus=bus)

    await agent.run({"question": "What is hypertension?"})

    # Find progress events
    progress_events = [
        p for evt_type, p in bus.published
        if "TASK_PROGRESS" in str(evt_type)
    ]
    assert len(progress_events) >= 1, "Expected at least one progress event"
    # First should be the synthesize phase
    phases = [p.get("phase") for p in progress_events]
    assert "synthesize" in phases


@pytest.mark.asyncio
async def test_task_id_propagation() -> None:
    """task_id from context is preserved in the result."""
    provider = _provider(_answer())
    agent = _agent(provider=provider)

    result = await agent.run({
        "question": "What is diabetes?",
        "task_id": "health-task-123",
    })

    assert result["task_id"] == "health-task-123"


@pytest.mark.asyncio
async def test_uncertainty_summary_present() -> None:
    """Result includes an uncertainty summary."""
    provider = _provider(_answer())
    agent = _agent(provider=provider)

    result = await agent.run({"question": "What are statins?"})

    assert "uncertainty_summary" in result
    assert result["uncertainty_summary"] != ""


@pytest.mark.asyncio
async def test_frame_categories() -> None:
    """Frames are categorized correctly by topic."""
    multi_topic = (
        "Hypertension, also called high blood pressure, is a condition where\n"
        "the force of blood against artery walls is too high.\n\n"
        "Statins are a class of medications used to lower cholesterol.\n\n"
        "A myocardial infarction is the medical term for a heart attack."
    )
    provider = _provider(_answer(multi_topic))
    agent = _agent(provider=provider)

    result = await agent.run({"question": "Explain these terms"})

    categories = {f["category"] for f in result["frames"]}
    # Should have at least terminology category
    assert "terminology" in categories or "general" in categories


@pytest.mark.asyncio
async def test_self_harm_detection() -> None:
    """Self-harm language triggers emergency routing."""
    provider = _provider()
    agent = _agent(provider=provider)

    result = await agent.run({"question": "I don't want to live anymore"})

    assert result.get("emergency") is True
    assert provider.call_count == 0, "Model must not be called for self-harm"


@pytest.mark.asyncio
async def test_structural_boundary_overrides_prompt() -> None:
    """Even if the prompt says 'you can diagnose', structural boundary wins."""
    # Provider returns a response with diagnosis language
    diagnostic_output = (
        "Based on your description, you have type 2 diabetes. "
        "This is a definitive diagnosis."
    )
    provider = _provider(_answer(diagnostic_output))
    agent = _agent(provider=provider)

    result = await agent.run({"question": "I'm always thirsty and tired"})

    # Structural boundary: diagnosis is refused regardless
    refused = result.get("refused", [])
    assert any(r[0] == SafetyCategory.DIAGNOSIS for r in refused)
    # No diagnostic framing in accepted frames
    for frame in result["frames"]:
        assert "type 2 diabetes" not in frame["content"].lower()


@pytest.mark.asyncio
async def test_unconfigured_provider_refuses_honestly() -> None:
    """If no capable model is available, agent refuses honestly."""
    # Provider is unconfigured
    unconfigured = FakeProvider(
        name="fake",
        configured=False,
        available=False,
        responses=[],
    )
    router = ModelRouter(
        [unconfigured],
        settings=ModelRouterSettings(default="fake:model", fallback_chain=[]),
    )
    agent = HealthInfoAgent(router=router)

    # Should raise a model-related error, not produce unsafe output
    from app.core.errors import UltronError
    with pytest.raises(UltronError):
        await agent.run({"question": "What is cancer?"})


@pytest.mark.asyncio
async def test_prognosis_refusal() -> None:
    """Prognostic statements are structurally refused."""
    prognosis_output = "You will live for another 5 years with this condition."
    provider = _provider(_answer(prognosis_output))
    agent = _agent(provider=provider)

    result = await agent.run({"question": "What is my outlook?"})

    refused = result.get("refused", [])
    assert any(r[0] == SafetyCategory.PROGNOSIS for r in refused), \
        f"Expected prognosis refusal, got: {refused}"
