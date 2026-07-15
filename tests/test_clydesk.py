import asyncio
import importlib.util
import os

import pytest

from clydesk import comfy, router, skills
from clydesk.comfy import parse_tag_json
from clydesk.ui_page import data_url

# ---------------------------------------------------------------- router
IMAGE_PROMPTS = [
    "draw me a picture of a fox",
    "generate 3 images of cats in space",
    "make a wallpaper of mountains",
]
NOT_IMAGE_PROMPTS = [
    "How do I draw a circle in matplotlib?",
    "draw conclusions from this data",
    "what's the base image of this Dockerfile",
    "can you illustrate with an example",
    "create a function that resizes images",
    "make a picture of the architecture diagram",  # veto: diagram/architecture
]


@pytest.mark.parametrize("prompt", IMAGE_PROMPTS)
def test_image_route_positives(prompt):
    assert router.quick_route(prompt, False) == "image"


@pytest.mark.parametrize("prompt", NOT_IMAGE_PROMPTS)
def test_image_route_negatives(prompt):
    assert router.quick_route(prompt, False) != "image"


def test_calc_route():
    assert router.quick_route("what is 23*(4+5)?", False) == "calc"
    assert router.quick_route("2025-06-30 - 2025-07-02", False) is None
    assert router.quick_route("hello there", True) == "vision"


def test_refinement_detection():
    assert router.is_image_refinement("make it darker")
    assert router.is_image_refinement("regenerate")
    assert not router.is_image_refinement("what's the capital of France?")


PROOF_PROMPTS = [
    "Prove that the sum of two even numbers is even",
    "Show that sqrt(2) is irrational",
    "Is it true that there are infinitely many primes?",
    "prove the theorem: for all n, n + 0 = n",
]
NOT_PROOF_PROMPTS = [
    "what is 23*(4+5)?",          # arithmetic, not a proof
    "prove you can write good code",  # 'prove' but not mathematical
    "show me a picture of a cat",
    "what's the weather like",
]


@pytest.mark.parametrize("prompt", PROOF_PROMPTS)
def test_proof_request_positives(prompt):
    assert router.is_proof_request(prompt)


@pytest.mark.parametrize("prompt", NOT_PROOF_PROMPTS)
def test_proof_request_negatives(prompt):
    assert not router.is_proof_request(prompt)


# ---------------------------------------------------------------- skills
@pytest.fixture(scope="module")
def loaded_skills():
    s, errs = skills.load_skills()
    assert not errs, errs
    return s


def test_all_skills_present(loaded_skills):
    expected = {"calculator", "convert_units", "current_datetime",
                "web_search", "fetch_page", "remember", "search_knowledge",
                "lean_check"}
    assert expected <= set(loaded_skills)


def test_calculator_math(loaded_skills):
    calc = loaded_skills["calculator"]
    assert calc.run({"expression": "2^10 + 5"}) == "1029"
    assert calc.run({"expression": "solve(x**2 - 4, x)"}) == "[-2, 2]"


@pytest.mark.parametrize("evil", [
    "solve(x-1,x) and ().__class__.__base__.__subclasses__()",
    "__import__('os').system('id')",
    "getattr(1, 'x')",
    "[c for c in ().__class__.__mro__]",
    "'a'.join(['b'])",
])
def test_calculator_rejects_escapes(loaded_skills, evil):
    result = loaded_skills["calculator"].run({"expression": evil})
    assert result.startswith("Error"), (evil, result)


def test_units_plural_and_temperature(loaded_skills):
    units = loaded_skills["convert_units"]
    assert units.run({"value": 100, "from_unit": "celsius",
                      "to_unit": "fahrenheit"}) == "100 celsius = 212 fahrenheit"
    assert "8.04672" in units.run({"value": 5, "from_unit": "miles",
                                   "to_unit": "km"})
    assert "kilometer" in units.run({"value": 3, "from_unit": "meters",
                                     "to_unit": "kilometers"})


def test_skill_subprocess_timeout(loaded_skills):
    async def bomb():
        return await loaded_skills["calculator"].run_async(
            {"expression": "9^9^9"})
    result = asyncio.run(bomb())
    assert "timed out" in result


# ---------------------------------------------------------------- lean_check
def _lean_module():
    path = os.path.join(skills.SKILLS_DIR, "lean_check.py")
    spec = importlib.util.spec_from_file_location("lean_check_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_LEAN = _lean_module()


def _lean_ready() -> bool:
    conf = _LEAN._lean_conf()
    proj = os.path.expanduser(conf["project_dir"])
    has_proj = (os.path.isfile(os.path.join(proj, "lakefile.toml"))
                or os.path.isfile(os.path.join(proj, "lakefile.lean")))
    return has_proj and _LEAN._find_lake(conf["elan_bin"]) is not None


lean_required = pytest.mark.skipif(
    not _lean_ready(), reason="Lean/Mathlib project not set up")


def test_lean_cheat_detection():
    assert _LEAN._uses_cheat("n = n := by sorry") == "sorry"
    assert _LEAN._uses_cheat("n = n := by admit") == "admit"
    # substrings inside identifiers are not cheats
    assert _LEAN._uses_cheat("theorem sorrymaker : True := trivial") is None
    assert _LEAN._uses_cheat("exact rfl") is None


def test_lean_check_guards(loaded_skills):
    lc = loaded_skills["lean_check"]
    assert lc.run({"code": ""}).startswith("Error")
    assert "refusing" in lc.run({"code": "#eval IO.Process.run cmd"})
    assert "too long" in lc.run({"code": "x" * 20001})


@lean_required
def test_lean_verifies_valid_proof(loaded_skills):
    code = "theorem t : 2 + 2 = 4 := by decide"
    assert loaded_skills["lean_check"].run({"code": code}).startswith("✅")


@lean_required
def test_lean_rejects_false_claim(loaded_skills):
    code = "theorem t : (2 : Nat) + 2 = 5 := by decide"
    assert "NOT VERIFIED" in loaded_skills["lean_check"].run({"code": code})


@lean_required
def test_lean_rejects_sorry(loaded_skills):
    code = "theorem t (n : Nat) : n = n := by sorry"
    assert "NOT A REAL PROOF" in loaded_skills["lean_check"].run({"code": code})


# ------------------------------------------------------------ tool loop
class _FakeOllama:
    """Yields scripted (kind, payload) events per chat() call."""

    def __init__(self, scripts):
        self.scripts = list(scripts)
        self.calls = []

    async def chat(self, model, messages, tools=None):
        self.calls.append({"tools": tools, "messages": list(messages)})
        for event in self.scripts.pop(0):
            yield event


def _orch_with(monkeypatch, ollama):
    from clydesk.agent import Orchestrator
    orch = Orchestrator.__new__(Orchestrator)
    orch.cfg = {"max_tool_rounds": 5}
    orch.ollama = ollama
    orch.skills, _ = skills.load_skills()
    return orch


def test_tool_loop_executes_skill_then_answers(monkeypatch):
    ollama = _FakeOllama([
        [("tool_calls", [{"id": "1", "name": "calculator",
                          "arguments": {"expression": "2+2"}}]),
         ("done", {"prompt_tokens": 1, "completion_tokens": 1})],
        [("text", "The answer is 4."),
         ("done", {"prompt_tokens": 2, "completion_tokens": 3})],
    ])
    orch = _orch_with(monkeypatch, ollama)
    messages = [{"role": "system", "content": "s"},
                {"role": "user", "content": "what is 2+2?"}]
    sink, events = [], []

    async def go():
        await orch._run_tool_loop("m", messages, sink, lambda k, p: events.append((k, p)))

    asyncio.run(go())
    assert "".join(sink) == "The answer is 4."
    tool_msg = next(m for m in messages if m["role"] == "tool")
    assert tool_msg["content"] == "4" and tool_msg["name"] == "calculator"


def test_tool_loop_retries_without_tools_on_unsupported(monkeypatch):
    from clydesk.ollama import OllamaError

    def script_with_error():
        raise OllamaError("model does not support tools")
        yield  # pragma: no cover

    class _Ollama(_FakeOllama):
        async def chat(self, model, messages, tools=None):
            self.calls.append({"tools": tools})
            if tools is not None:
                raise OllamaError("registry: model does not support tools")
            for event in [("text", "hi"), ("done", {})]:
                yield event

    ollama = _Ollama([None])
    orch = _orch_with(monkeypatch, ollama)
    sink = []

    async def go():
        await orch._run_tool_loop("m", [{"role": "user", "content": "hi"}],
                                  sink, lambda k, p: None)

    asyncio.run(go())
    assert "".join(sink) == "hi"
    assert [c["tools"] is not None for c in ollama.calls] == [True, False]


# ---------------------------------------------------------------- misc
def test_tag_json_quality_prefix():
    t = parse_tag_json('{"count": 2, "positive": "fox, forest", "negative": ""}')
    assert t["positive"].startswith(comfy.QUALITY_PREFIX)
    assert t["count"] == 2
    assert "low quality" in t["negative"]


def test_tag_json_garbage_fallback():
    t = parse_tag_json("sure! here are some tags for you")
    assert t["count"] == 1 and t["positive"].startswith(comfy.QUALITY_PREFIX)


def test_data_url_mime_detection():
    assert data_url("/9j/4AAQ").startswith("data:image/jpeg")
    assert data_url("iVBORw0").startswith("data:image/png")
    assert data_url("UklGRabc").startswith("data:image/webp")


# ------------------------------------------------------- security hardening

def _sensitive_orch(monkeypatch, script, cfg=None):
    ollama = _FakeOllama(script)
    orch = _orch_with(monkeypatch, ollama)
    orch.cfg = {"max_tool_rounds": 5, **(cfg or {})}
    # a fake sensitive skill that records if it ever ran
    ran = {"count": 0}

    async def fake_run_async(args):
        ran["count"] += 1
        return "ACTUATED"

    sk = skills.Skill("bedroom_light", "d", {"type": "object"},
                      lambda a: "ACTUATED", "/x", sensitive=True)
    sk.run_async = fake_run_async
    orch.skills = {"bedroom_light": sk}
    return orch, ran


def _light_call():
    return [
        [("tool_calls", [{"id": "1", "name": "bedroom_light",
                          "arguments": {"action": "on"}}]),
         ("done", {})],
        [("text", "done"), ("done", {})],
    ]


def test_sensitive_skill_blocked_without_approver(monkeypatch):
    orch, ran = _sensitive_orch(monkeypatch, _light_call())
    messages = [{"role": "user", "content": "turn on the light"}]

    async def go():
        await orch._run_tool_loop("m", messages, [], lambda k, p: None)

    asyncio.run(go())
    assert ran["count"] == 0  # no approver → fail closed
    tool_msg = next(m for m in messages if m["role"] == "tool")
    assert "did not approve" in tool_msg["content"]


def test_sensitive_skill_runs_when_approved(monkeypatch):
    orch, ran = _sensitive_orch(monkeypatch, _light_call())
    messages = [{"role": "user", "content": "turn on the light"}]

    async def approver(name, args):
        assert name == "bedroom_light" and args == {"action": "on"}
        return True

    async def go():
        await orch._run_tool_loop("m", messages, [], lambda k, p: None,
                                  approver)

    asyncio.run(go())
    assert ran["count"] == 1
    assert next(m for m in messages if m["role"] == "tool")["content"] == "ACTUATED"


def test_sensitive_skill_denied_by_approver(monkeypatch):
    orch, ran = _sensitive_orch(monkeypatch, _light_call())
    messages = [{"role": "user", "content": "x"}]

    async def approver(name, args):
        return False

    async def go():
        await orch._run_tool_loop("m", messages, [], lambda k, p: None,
                                  approver)

    asyncio.run(go())
    assert ran["count"] == 0
    assert "did not approve" in next(m for m in messages
                                     if m["role"] == "tool")["content"]


def test_approval_can_be_disabled_by_config(monkeypatch):
    orch, ran = _sensitive_orch(monkeypatch, _light_call(),
                                cfg={"require_skill_approval": False})
    messages = [{"role": "user", "content": "x"}]

    async def go():
        await orch._run_tool_loop("m", messages, [], lambda k, p: None)

    asyncio.run(go())
    assert ran["count"] == 1  # gate off → runs even with no approver


def test_nonsensitive_skill_never_prompts(monkeypatch):
    ollama = _FakeOllama([
        [("tool_calls", [{"id": "1", "name": "calculator",
                          "arguments": {"expression": "2+2"}}]), ("done", {})],
        [("text", "4"), ("done", {})],
    ])
    orch = _orch_with(monkeypatch, ollama)
    called = {"approver": 0}

    async def approver(name, args):
        called["approver"] += 1
        return False

    messages = [{"role": "user", "content": "2+2"}]

    async def go():
        await orch._run_tool_loop("m", messages, [], lambda k, p: None,
                                  approver)

    asyncio.run(go())
    assert called["approver"] == 0  # calculator is not sensitive
    assert next(m for m in messages if m["role"] == "tool")["content"] == "4"


def test_sensitive_flag_loaded_from_skill_meta():
    loaded, _ = skills.load_skills()
    assert loaded["fetch_page"].sensitive
    assert loaded["web_search"].sensitive
    assert not loaded["calculator"].sensitive


def test_html_sanitizer_strips_xss():
    from clydesk.sanitize import sanitize_html
    dirty = ('<p>ok</p><script>alert(1)</script>'
             '<img src=x onerror="steal()">'
             '<a href="javascript:evil()">click</a>')
    clean = sanitize_html(dirty)
    assert "<script" not in clean.lower()
    assert "onerror" not in clean.lower()
    assert "javascript:" not in clean.lower()
    assert "<p>ok</p>" in clean


def test_html_sanitizer_keeps_markdown_output():
    import markdown2

    from clydesk.sanitize import sanitize_html
    html = markdown2.markdown(
        "# Title\n\n**bold** and `code`\n\n| a | b |\n|---|---|\n| 1 | 2 |",
        extras=["fenced-code-blocks", "tables"])
    clean = sanitize_html(html)
    assert "<h1>" in clean and "<strong>" in clean
    assert "<table>" in clean and "<code>" in clean


@pytest.mark.parametrize("host,private", [
    ("127.0.0.1", True),
    ("localhost", True),
    ("10.0.0.5", True),
    ("192.168.1.1", True),
    ("169.254.169.254", True),   # cloud metadata endpoint
    ("::1", True),
    ("0.0.0.0", True),
    ("8.8.8.8", False),
    ("93.184.216.34", False),    # example.com
])
def test_fetch_page_ssrf_classifier(host, private):
    spec = importlib.util.spec_from_file_location(
        "fp", os.path.join(os.path.dirname(os.path.dirname(__file__)),
                           "skills", "fetch_page.py"))
    fp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fp)
    assert fp._is_private_host(host) is private


def test_fetch_page_rejects_internal_url():
    spec = importlib.util.spec_from_file_location(
        "fp2", os.path.join(os.path.dirname(os.path.dirname(__file__)),
                            "skills", "fetch_page.py"))
    fp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fp)
    out = fp.run({"url": "http://localhost:11434/api/tags"})
    assert out.startswith("Error") and "SSRF" in out


# ---------------------------------------------------------------- db
def test_db_roundtrip(tmp_path, monkeypatch):
    from clydesk import db

    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "chats.db"))

    async def flow():
        await db.init_db()
        cid = await db.create_chat("My chat")
        await db.save_message(cid, "user", "hello world")
        await db.save_message(cid, "assistant", "hi!", kind="text",
                              extra={"route": "chat"})
        msgs = await db.get_messages(cid)
        assert [m["content"] for m in msgs] == ["hello world", "hi!"]
        assert msgs[0]["extra"] == {}
        assert msgs[1]["extra"]["route"] == "chat"
        assert (await db.list_chats())[0]["title"] == "My chat"
        await db.rename_chat(cid, "Renamed")
        hits = await db.search_chats("hello")
        assert hits and hits[0]["id"] == cid and hits[0]["title"] == "Renamed"
        assert await db.search_chats("zzz_no_match") == []
        await db.delete_chat(cid)
        assert await db.list_chats() == []
        # ON DELETE CASCADE must have removed the chat's messages too
        assert await db.get_messages(cid) == []

    asyncio.run(flow())
