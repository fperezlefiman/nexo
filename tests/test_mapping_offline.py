"""Offline tests: the contract does not depend on the engine being up (ADR 0002)."""

import json

from nexo.adapters.opencode import _result, _sum_tokens
from nexo.errors import AuthFailed, ModelRateLimited, ModelUnavailable, SessionLost

RECORDED = {
    "id": "msg_20f388a2d001SwSwUKnElrlaYf",
    "time": {"created": 1795551693363, "streamed": 1795551694389, "completed": 1795551694390},
    "type": "assistant",
    "agent": "build",
    "model": {"id": "deepseek-v4.1-flash-free", "providerID": "opencode", "variant": "default"},
    "content": [{"type": "text", "text": "NEXO_F0_OK"}],
    "finish": "stop",
    "cost": 0,
    "tokens": {"input": 6212, "output": 8, "reasoning": 0, "cache": {"read": 1536, "write": 0}},
}


def test_result_parses_recorded_assistant_message():
    result = _result([RECORDED], latency_ms=1500)
    assert result.text == "NEXO_F0_OK"
    assert result.model_id == "deepseek-v4.1-flash-free"
    assert result.provider_id == "opencode"
    assert result.tokens == {"input": 6212, "output": 8}
    assert result.cost == 0
    assert result.latency_ms == 1500


def test_result_joins_multiple_assistant_messages():
    second = dict(RECORDED, content=[{"type": "text", "text": " más"}], tokens={"input": 1, "output": 2})
    result = _result([RECORDED, second], latency_ms=10)
    assert result.text == "NEXO_F0_OK más"
    assert result.tokens == {"input": 6213, "output": 10}


def test_tokens_are_summed_across_calls():
    assert _sum_tokens([RECORDED, RECORDED]) == {"input": 12424, "output": 16}


def test_classify_auth():
    assert isinstance(OpenCodeRuntimeClassify(401, '{"message":"Authentication required"}', "/api/session"), AuthFailed)


def test_classify_rate_limit_free_tier():
    err = OpenCodeRuntimeClassify(429, '{"_tag":"FreeUsageLimitError"}', "/api/session/x/prompt")
    assert isinstance(err, ModelRateLimited)


def test_classify_missing_model():
    err = OpenCodeRuntimeClassify(404, '{"message":"model not found"}', "/api/session")
    assert isinstance(err, ModelUnavailable)


def test_classify_lost_session():
    err = OpenCodeRuntimeClassify(404, '{"message":"not found"}', "/api/session/ses_abc/prompt")
    assert isinstance(err, SessionLost)


def test_recorded_fixture_is_valid_json():
    payload = json.loads(json.dumps(RECORDED))
    assert payload["type"] == "assistant"


from nexo.adapters.opencode import OpenCodeRuntime as _RT  # noqa: E402

OpenCodeRuntimeClassify = _RT._classify
