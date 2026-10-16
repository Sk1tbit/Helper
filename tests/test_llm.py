import pytest

from conftest import ollama_reply, openai_reply
from jarvis.config import Config
from jarvis.llm import LLM, LLMError, extract_text_tool_calls

TOOLS = [{"name": "open_app", "description": "d", "input_schema": {"type": "object", "properties": {}}}]


def cfg_for(url, backend="openai", **kw):
    c = Config()
    c.llm_backend, c.llm_url, c.model, c.vision_model = backend, url, "test-model", ""
    for k, v in kw.items():
        setattr(c, k, v)
    return c


def test_openai_roundtrip_and_wire_format(lmstudio):
    srv = lmstudio([openai_reply("", [("open_app", {"name": "стим"})]), openai_reply("<think>хм</think>Готово.")])
    llm = LLM(cfg_for(srv.url))
    r1 = llm.chat([{"role": "user", "content": "открой стим"}], TOOLS)
    assert r1["tool_calls"][0]["name"] == "open_app" and r1["tool_calls"][0]["arguments"] == {"name": "стим"}
    hist = [{"role": "user", "content": "x"}, {"role": "assistant", "content": "", "tool_calls": r1["tool_calls"]},
            {"role": "tool", "tool_call_id": r1["tool_calls"][0]["id"], "name": "open_app", "content": "ok"}]
    r2 = llm.chat(hist, TOOLS)
    assert r2["content"] == "Готово."  # размышления вырезаны
    body = srv.requests[-1][2]
    assert body["messages"][1]["tool_calls"][0]["function"]["arguments"] == '{"name": "стим"}'
    assert body["messages"][2] == {"role": "tool", "tool_call_id": r1["tool_calls"][0]["id"], "content": "ok"}
    assert body["chat_template_kwargs"] == {"enable_thinking": False}


def test_ollama_roundtrip(fake_server):
    script = [ollama_reply("", [("open_app", {"name": "стим"})])]
    srv = fake_server(lambda m, p, b: (200, script.pop(0)) if m == "POST" else (200, {"models": [{"name": "test-model"}]}))
    llm = LLM(cfg_for(srv.url, "ollama"))
    r = llm.chat([{"role": "user", "content": "x"}], TOOLS)
    assert r["tool_calls"][0]["arguments"] == {"name": "стим"}
    body = srv.requests[-1][2]
    assert body["options"]["num_ctx"] == 8192 and body["think"] is False


def test_no_think_fallback_only_when_fields_are_the_problem(lmstudio):
    # Сервер отвергает лишние поля — повторяем без них и запоминаем.
    srv = lmstudio([(400, {"error": "Unrecognized key chat_template_kwargs"}), openai_reply("ok"), openai_reply("ok2")])
    llm = LLM(cfg_for(srv.url))
    assert llm.chat([{"role": "user", "content": "x"}])["content"] == "ok"
    assert llm._no_think_ok is False
    assert llm.chat([{"role": "user", "content": "x"}])["content"] == "ok2"
    assert "chat_template_kwargs" not in srv.requests[-1][2]


def test_no_think_not_disabled_by_unrelated_400(lmstudio):
    srv = lmstudio([(400, {"error": "bad tool schema"}), (400, {"error": "bad tool schema"}), openai_reply("ok")])
    llm = LLM(cfg_for(srv.url))
    with pytest.raises(LLMError):
        llm.chat([{"role": "user", "content": "x"}])
    assert llm._no_think_ok is None  # не решили, что сервер «не понимает»
    llm.chat([{"role": "user", "content": "x"}])
    assert "chat_template_kwargs" in srv.requests[-1][2]


@pytest.mark.parametrize("payload", [b"not json", {"weird": 1}, {"choices": []}, {"choices": [{"message": None}]}])
def test_garbage_responses_raise_llmerror(lmstudio, payload):
    srv = lmstudio([(200, payload)])
    with pytest.raises(LLMError):
        LLM(cfg_for(srv.url)).chat([{"role": "user", "content": "x"}])


def test_context_overflow_detected(lmstudio):
    srv = lmstudio([(400, {"error": {"message": "This model's maximum context length is 8192 tokens, exceeded"}})])
    with pytest.raises(LLMError) as e:
        LLM(cfg_for(srv.url)).chat([{"role": "user", "content": "x"}])
    assert e.value.context_overflow


def test_tools_not_supported_message(lmstudio):
    srv = lmstudio([(400, {"error": "gemma does not support tools"})] * 2)
    with pytest.raises(LLMError) as e:
        LLM(cfg_for(srv.url)).chat([{"role": "user", "content": "x"}], TOOLS)
    assert "не умеет управлять" in e.value.spoken


def test_connection_error():
    with pytest.raises(LLMError) as e:
        LLM(cfg_for("http://127.0.0.1:1")).chat([{"role": "user", "content": "x"}])
    assert "Нет связи" in e.value.spoken


def test_text_tool_call_is_parsed(lmstudio):
    srv = lmstudio([openai_reply('<tool_call>\n{"name": "open_app", "arguments": {"name": "стим"}}\n</tool_call>')])
    r = LLM(cfg_for(srv.url)).chat([{"role": "user", "content": "x"}], TOOLS)
    assert r["tool_calls"][0]["name"] == "open_app" and r["content"] == ""


@pytest.mark.parametrize("text,n", [
    ('<tool_call>{"name": "open_app", "arguments": {"name": "a"}}</tool_call>', 1),
    ('```json\n{"name":"open_app","arguments":{"name":"a"}}\n```', 1),
    ('[TOOL_CALLS][{"name":"open_app","arguments":{"name":"a"}}]', 1),
    ('{"name": "open_app", "parameters": {"name": "a"}}', 1),
    ('{"name": "unknown_tool", "arguments": {}}', 0),
    ("open_yandex_music.", 0), ('Привет! {"a":1}', 0),
])
def test_extract_text_tool_calls(text, n):
    calls, _ = extract_text_tool_calls(text, {"open_app"})
    assert len(calls) == n


def test_check_finds_lmstudio_when_config_says_ollama(lmstudio):
    srv = lmstudio([])
    llm = LLM(cfg_for(srv.url, "ollama"))
    assert llm.check() is None and llm.backend == "openai"


def test_check_lists_models_when_missing(lmstudio):
    srv = lmstudio([], models=("qwen-a", "text-embedding-x"))
    msg = LLM(cfg_for(srv.url)).check()
    assert "qwen-a" in msg and "embedding" not in msg


def test_local_server_bypasses_proxy():
    assert LLM(cfg_for("http://localhost:1234")).session.trust_env is False
    assert LLM(cfg_for("https://api.example.com")).session.trust_env is True
