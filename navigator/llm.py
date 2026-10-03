"""Thin LLM client: forced tool-use for structured output, prompt caching on the document
bundle, content-hash response cache, JSONL audit log. Provider: Anthropic (key from .env)."""
from __future__ import annotations
import hashlib, json, os, pathlib, time

ROOT = pathlib.Path(__file__).resolve().parents[1]
CACHE = ROOT / "cache/llm"
AUDIT = ROOT / "logs/audit.jsonl"

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except Exception:
    pass

PROVIDER = os.environ.get("NAV_PROVIDER") or ("openai" if os.environ.get("OPENAI_API_KEY") else "anthropic")
MODEL = os.environ.get("NAV_MODEL") or ("gpt-4.1" if PROVIDER == "openai" else "claude-sonnet-5-5")
MAX_TOKENS = int(os.environ.get("NAV_MAX_TOKENS", "16000"))


def _key(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:24]


def call_tool(system: str, bundle: str, instruction: str, tool: dict, *, tag: str,
              dry_run: bool = False, use_cache: bool = True, temperature: float = 0.0) -> dict:
    """Return {'input': <tool input dict>, 'usage': {...}, 'cache_hit': bool, 'key': str}."""
    payload = {"provider": PROVIDER, "model": MODEL, "system": system, "bundle": bundle, "instruction": instruction,
               "tool": tool, "temperature": temperature}
    k = _key(payload)
    CACHE.mkdir(parents=True, exist_ok=True)
    AUDIT.parent.mkdir(parents=True, exist_ok=True)
    cp = CACHE / f"{k}.json"
    if use_cache and cp.exists():
        out = json.loads(cp.read_text())
        out["cache_hit"] = True
        return out
    if dry_run:
        return {"input": None, "usage": {"est_input_tokens": (len(system) + len(bundle) + len(instruction)) // 4},
                "cache_hit": False, "key": k, "dry_run": True}

    if PROVIDER == "openai":
        return _call_openai(system, bundle, instruction, tool, tag=tag, key=k, cache_path=cp, temperature=temperature)
    import anthropic
    client = anthropic.Anthropic()
    t0 = time.time()
    for attempt in range(4):
        try:
            resp = client.messages.create(
                model=MODEL, max_tokens=MAX_TOKENS, temperature=temperature,
                system=[{"type": "text", "text": system}],
                tools=[tool], tool_choice={"type": "tool", "name": tool["name"]},
                messages=[{"role": "user", "content": [
                    {"type": "text", "text": bundle, "cache_control": {"type": "ephemeral"}},
                    {"type": "text", "text": instruction}]}])
            break
        except (anthropic.RateLimitError, anthropic.APIConnectionError, anthropic.InternalServerError) as e:
            if attempt == 3:
                raise
            time.sleep(2 ** attempt * 3)
    block = next(b for b in resp.content if b.type == "tool_use")
    usage = {k2: getattr(resp.usage, k2, None) for k2 in
             ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")}
    out = {"input": block.input, "usage": usage, "cache_hit": False, "key": k,
           "stop_reason": resp.stop_reason, "model": MODEL}
    cp.write_text(json.dumps(out, ensure_ascii=False, indent=1))
    with open(AUDIT, "a") as f:
        f.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "tag": tag, "key": k, "model": MODEL,
                            "usage": usage, "secs": round(time.time() - t0, 1),
                            "stop_reason": resp.stop_reason}) + "\n")
    return out


def _log(tag, k, usage, secs, stop):
    with open(AUDIT, "a") as f:
        f.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "tag": tag, "key": k, "provider": PROVIDER,
                            "model": MODEL, "usage": usage, "secs": secs, "stop_reason": stop}) + "\n")


def _call_openai(system, bundle, instruction, tool, *, tag, key, cache_path, temperature):
    """OpenAI chat completions with a forced function call. Long shared prefixes (system + bundle)
    are cached automatically by the API."""
    import openai
    client = openai.OpenAI()
    fn = {"type": "function", "function": {"name": tool["name"], "description": tool["description"],
                                           "parameters": tool["input_schema"]}}
    kwargs = dict(model=MODEL, tools=[fn], tool_choice={"type": "function", "function": {"name": tool["name"]}},
                  messages=[{"role": "system", "content": system},
                            {"role": "user", "content": bundle + "\n\n" + instruction}],
                  max_completion_tokens=MAX_TOKENS)
    if temperature is not None:
        kwargs["temperature"] = temperature
    t0 = time.time()
    for attempt in range(5):
        try:
            resp = client.chat.completions.create(**kwargs)
            break
        except openai.BadRequestError as e:
            msg = str(e).lower()
            if "temperature" in msg and "temperature" in kwargs:
                kwargs.pop("temperature"); continue          # some reasoning models reject it
            raise
        except (openai.RateLimitError, openai.APIConnectionError, openai.InternalServerError):
            if attempt == 4:
                raise
            time.sleep(2 ** attempt * 3)
    ch = resp.choices[0]
    calls = ch.message.tool_calls or []
    args = json.loads(calls[0].function.arguments) if calls else None
    u = resp.usage
    usage = {"input_tokens": u.prompt_tokens, "output_tokens": u.completion_tokens,
             "cache_read_input_tokens": getattr(getattr(u, "prompt_tokens_details", None), "cached_tokens", None)}
    out = {"input": args, "usage": usage, "cache_hit": False, "key": key, "stop_reason": ch.finish_reason,
           "model": MODEL, "provider": PROVIDER}
    if args is not None:
        cache_path.write_text(json.dumps(out, ensure_ascii=False, indent=1))
    _log(tag, key, usage, round(time.time() - t0, 1), ch.finish_reason)
    return out


if __name__ == "__main__":
    import sys
    if "--list-models" in sys.argv:
        if PROVIDER == "openai":
            import openai
            ids = sorted(m.id for m in openai.OpenAI().models.list().data)
        else:
            import anthropic
            ids = sorted(m.id for m in anthropic.Anthropic().models.list().data)
        print(PROVIDER, "\n".join(ids))
