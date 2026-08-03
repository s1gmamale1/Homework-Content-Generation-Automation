"""A/B harness: CLI-with-API-key vs direct REST, same model + same prompt.

Answers one question empirically: when transport=api, does routing the call
through the provider CLI (with the API key injected, exactly as production does
via agent._spawn) differ from calling the provider's REST endpoint directly?

It sends the SAME raw prompt with the SAME model both ways and compares:
  - output text (exact-match + length)
  - token usage (normalized to the SAME keys the app uses)
  - wall-clock latency

This is a throwaway diagnostic — it is NOT wired into the app. It makes two
real (tiny, ~cents) billed calls per run.

Run:
  # gemini via Vertex service-account (this host) — needs google-auth, pulled
  # transiently so pyproject is untouched:
  uv run --with google-auth --with requests python scripts/api_vs_cli_compare.py

  # gemini with an AI-Studio key, or claude (no extra deps):
  uv run python scripts/api_vs_cli_compare.py --provider claude --model claude-haiku-4-5-20251001
  uv run --with google-auth --with requests python scripts/api_vs_cli_compare.py \
      --provider gemini --model gemini-2.5-flash --prompt "Explain photosynthesis in 3 sentences."
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import time

import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on path

import httpx

import app.config  # noqa: F401 — import triggers load_dotenv(override=False) → .env into os.environ
from app.services import agent, prompts

DEFAULT_PROMPT = "Write a 3-sentence explanation of photosynthesis for a 7th-grade student."

# A realistic content-phase context so we can measure the CLI's fixed system-prompt
# tax as a FRACTION of a real prompt (not the 73-char toy). Plausible extracted
# lesson + source-map digest, sized like a real lesson.extract output.
SAMPLE_LESSON = """\
Lesson: Photosynthesis and Cellular Respiration (Grade 9 Biology)

Photosynthesis is the process by which green plants, algae, and some bacteria
convert light energy into chemical energy stored in glucose. It occurs mainly in
the chloroplasts of leaf cells, where the pigment chlorophyll absorbs light —
most strongly in the red and blue wavelengths, reflecting green. The overall
reaction combines six molecules of carbon dioxide and six of water, using light
energy, to produce one molecule of glucose and six of oxygen.

The process has two linked stages. In the light-dependent reactions, which take
place in the thylakoid membranes, absorbed light splits water (photolysis),
releasing oxygen as a by-product and producing the energy carriers ATP and
NADPH. In the light-independent reactions (the Calvin cycle), which take place in
the stroma, ATP and NADPH are used to fix carbon dioxide into glucose. Although
the Calvin cycle does not require light directly, it depends on the products of
the light reactions, so it effectively stops in prolonged darkness.

Several factors limit the rate of photosynthesis: light intensity, carbon
dioxide concentration, and temperature. At low light, rate rises with intensity;
beyond a point another factor becomes limiting. Enzymes of the Calvin cycle work
fastest within an optimum temperature band and denature when it is too hot.

Cellular respiration is, in many ways, the reverse: cells break down glucose with
oxygen to release energy (ATP), producing carbon dioxide and water. In plants,
photosynthesis and respiration occur together; during daylight, photosynthesis
usually exceeds respiration, giving a net release of oxygen. This complementary
relationship underpins the carbon and oxygen cycles that sustain ecosystems.
"""

SAMPLE_SOURCE_MAP = """\
- c1 | Photosynthesis | converts light energy into chemical energy (glucose) in chloroplasts
- c2 | Chlorophyll | pigment that absorbs red/blue light, reflects green
- c3 | Reactants & products | 6 CO2 + 6 H2O + light -> glucose + 6 O2
- c4 | Light-dependent reactions | in thylakoid membranes; photolysis of water; make ATP + NADPH; release O2
- c5 | Calvin cycle | in stroma; uses ATP/NADPH to fix CO2 into glucose; light-independent
- c6 | Limiting factors | light intensity, CO2 concentration, temperature
- c7 | Enzyme temperature optimum | Calvin-cycle enzymes denature when too hot
- c8 | Cellular respiration | glucose + O2 -> energy (ATP) + CO2 + H2O
- c9 | Net daytime gas exchange | photosynthesis exceeds respiration in daylight -> net O2 release
- c10 | Carbon & oxygen cycles | photosynthesis/respiration sustain ecosystem gas balance
"""


def build_realistic_prompt(provider_name: str, subject: str, phase: str) -> str:
    """Assemble the EXACT prompt the pipeline would send for one content phase,
    via the app's own _build_master_prompt (real phase prompt + lesson context +
    source-map digest). This is what goes to BOTH legs; the CLI adds its system
    prompt on top, the REST call does not."""
    prov = agent.get_provider(provider_name)
    phase_prompt = prompts.get_prompt(subject, phase)
    return agent._build_master_prompt(
        phase_prompt=phase_prompt,
        phase_name=phase,
        lesson_context=SAMPLE_LESSON,
        prior_outputs={},
        difficulty=None,
        schema=None,
        provider_suffix=prov.prompt_suffix(None),
        attachment_preamble=prov.format_attachments([]),
        source_map_digest=SAMPLE_SOURCE_MAP,
    )


# ---- Path A: the CLI, with the API key injected (production's transport=api) ----
async def path_a_cli(provider_name: str, model: str, prompt: str) -> dict:
    prov = agent.get_provider(provider_name)
    t0 = time.monotonic()
    rc, text, usage, stderr = await agent._spawn(
        provider=prov, model=model, prompt=prompt, attachments=[], transport="api",
    )
    return {
        "ok": rc == 0,
        "rc": rc,
        "text": text,
        # _spawn already returns the provider's normalized usage keys.
        "usage": {k: usage.get(k) for k in ("prompt_tokens", "output_tokens", "cached_tokens", "total_tokens")},
        "stderr": (stderr or "")[:600],
        "secs": time.monotonic() - t0,
    }


# ---- Path B: direct REST, mirroring the app's usage normalization ----
def _vertex_access_token() -> str:
    from google.auth.transport.requests import Request
    from google.oauth2 import service_account

    creds = service_account.Credentials.from_service_account_file(
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"],
        scopes=["https://www.googleapis.com/auth/cloud-platform"],
    )
    creds.refresh(Request())
    return creds.token


def _gemini_usage(um: dict) -> dict:
    # Mirror gemini provider parse_envelope: output = candidates + thoughts
    # (Google bills "thoughts" as output); prompt INCLUDES cached.
    cand = um.get("candidatesTokenCount")
    thoughts = um.get("thoughtsTokenCount")
    output = None if (cand is None and thoughts is None) else (cand or 0) + (thoughts or 0)
    return {
        "prompt_tokens": um.get("promptTokenCount"),
        "output_tokens": output,
        "cached_tokens": um.get("cachedContentTokenCount"),
        "total_tokens": um.get("totalTokenCount"),
    }


def path_b_rest(provider_name: str, model: str, prompt: str, max_tokens: int) -> dict:
    t0 = time.monotonic()
    if provider_name == "gemini":
        key = os.environ.get("GEMINI_API_KEY")
        if key:  # AI-Studio
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
            headers = {"x-goog-api-key": key}  # header, not ?key= — robust for new AQ.* keys
            auth = "ai-studio-key"
        else:  # Vertex service-account
            proj = os.environ["GOOGLE_CLOUD_PROJECT"]
            loc = os.environ.get("GOOGLE_CLOUD_LOCATION") or "global"
            host = "aiplatform.googleapis.com" if loc == "global" else f"{loc}-aiplatform.googleapis.com"
            url = f"https://{host}/v1/projects/{proj}/locations/{loc}/publishers/google/models/{model}:generateContent"
            headers = {"Authorization": f"Bearer {_vertex_access_token()}"}
            auth = f"vertex-sa ({loc})"
        body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}]}
        if max_tokens is not None:  # Gemini doesn't require a cap — omit unless asked
            body["generationConfig"] = {"maxOutputTokens": max_tokens}
        r = httpx.post(url, json=body, headers=headers, timeout=120.0)
        secs = time.monotonic() - t0
        r.raise_for_status()
        data = r.json()
        cand = (data.get("candidates") or [{}])[0]
        parts = (cand.get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts if "text" in p)
        return {"ok": True, "text": text, "usage": _gemini_usage(data.get("usageMetadata") or {}),
                "secs": secs, "auth": auth, "raw_usage": data.get("usageMetadata")}

    if provider_name == "claude":
        url = "https://api.anthropic.com/v1/messages"
        headers = {
            "x-api-key": os.environ["ANTHROPIC_API_KEY"],
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        body = {"model": model, "max_tokens": max_tokens or 8192,  # Claude REQUIRES max_tokens
                "messages": [{"role": "user", "content": prompt}]}
        r = httpx.post(url, json=body, headers=headers, timeout=120.0)
        secs = time.monotonic() - t0
        r.raise_for_status()
        data = r.json()
        text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
        u = data.get("usage") or {}
        usage = {
            "prompt_tokens": u.get("input_tokens"),
            "output_tokens": u.get("output_tokens"),
            "cached_tokens": u.get("cache_read_input_tokens"),
            "total_tokens": (u.get("input_tokens") or 0) + (u.get("output_tokens") or 0),
        }
        return {"ok": True, "text": text, "usage": usage, "secs": secs,
                "auth": "anthropic-key", "raw_usage": u}

    raise SystemExit(f"direct REST not implemented for provider {provider_name!r}")


def path_c_sdk(provider_name: str, model: str, prompt: str) -> dict:
    """Path C: the official google-genai SDK (no CLI, no hand-rolled REST). Same
    client handles AI-Studio key OR Vertex SA; token mint/refresh is internal."""
    if provider_name != "gemini":
        raise SystemExit("SDK path implemented for gemini only")
    from google import genai  # lazy — only needed for this leg

    t0 = time.monotonic()
    key = os.environ.get("GEMINI_API_KEY")
    if key:
        client = genai.Client(api_key=key)
        auth = "ai-studio-key (sdk)"
    else:
        loc = os.environ.get("GOOGLE_CLOUD_LOCATION") or "global"
        client = genai.Client(vertexai=True, project=os.environ["GOOGLE_CLOUD_PROJECT"], location=loc)
        auth = f"vertex-sa sdk ({loc})"
    resp = client.models.generate_content(model=model, contents=prompt)
    secs = time.monotonic() - t0
    text = resp.text or ""
    um = resp.usage_metadata
    cand = getattr(um, "candidates_token_count", None)
    thoughts = getattr(um, "thoughts_token_count", None)
    output = None if (cand is None and thoughts is None) else (cand or 0) + (thoughts or 0)
    usage = {
        "prompt_tokens": getattr(um, "prompt_token_count", None),
        "output_tokens": output,
        "cached_tokens": getattr(um, "cached_content_token_count", None),
        "total_tokens": getattr(um, "total_token_count", None),
    }
    return {"ok": True, "text": text, "usage": usage, "secs": secs, "auth": auth}


def _fmt_usage(u: dict) -> str:
    return "  ".join(f"{k.split('_')[0]}={u.get(k)}" for k in
                     ("prompt_tokens", "output_tokens", "cached_tokens", "total_tokens"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", default="gemini", choices=["gemini", "claude"])
    # historical repro — model intentionally pinned (cli retired; needs a both-transport model)
    ap.add_argument("--model", default=None, help="default: gemini-2.5-flash / claude-haiku-4-5-20251001")
    ap.add_argument("--prompt", default=DEFAULT_PROMPT)
    ap.add_argument("--max-tokens", type=int, default=None,
                    help="REST output cap. Omitted for Gemini when unset (uses the model's "
                         "own default); Claude requires one, so it defaults to 8192.")
    ap.add_argument("--realistic", action="store_true",
                    help="assemble a real content-phase prompt via _build_master_prompt")
    ap.add_argument("--subject", default="biology")
    ap.add_argument("--phase", default="case-based-preview")
    ap.add_argument("--only", choices=["both", "cli", "rest", "sdk"], default="both",
                    help="run only one leg (default both = cli+rest); 'sdk' = google-genai SDK")
    args = ap.parse_args()
    # historical repro — model intentionally pinned (cli retired; needs a both-transport model)
    model = args.model or ("gemini-2.5-flash" if args.provider == "gemini" else "claude-haiku-4-5-20251001")

    if args.realistic:
        prompt = build_realistic_prompt(args.provider, args.subject, args.phase)
        print(f"\n=== A/B: {args.provider} / {model} | realistic phase '{args.subject}/{args.phase}' ===")
        print(f"assembled prompt: {len(prompt)} chars (~{len(prompt)//4} tokens est.)\n")
    else:
        prompt = args.prompt
        print(f"\n=== A/B: {args.provider} / {model} ===")
        print(f"prompt: {prompt!r}\n")

    run_a = args.only in ("both", "cli")
    run_b = args.only in ("both", "rest")
    run_c = args.only == "sdk"
    a = b = c = None
    if run_a:
        try:
            a = asyncio.run(path_a_cli(args.provider, model, prompt))
        except Exception as e:  # noqa: BLE001
            a = {"ok": False, "error": repr(e), "secs": 0.0}
    if run_b:
        try:
            b = path_b_rest(args.provider, model, prompt, args.max_tokens)
        except Exception as e:  # noqa: BLE001
            b = {"ok": False, "error": repr(e), "secs": 0.0}
    if run_c:
        try:
            c = path_c_sdk(args.provider, model, prompt)
        except Exception as e:  # noqa: BLE001
            c = {"ok": False, "error": repr(e), "secs": 0.0}

    if a is not None:
        print("── PATH A — CLI (transport=api) " + "─" * 30)
        if a.get("ok"):
            print(f"latency: {a['secs']:.2f}s   usage: {_fmt_usage(a['usage'])}")
            print(f"output ({len(a['text'])} chars):\n{a['text'].strip()}\n")
        else:
            print(f"FAILED: rc={a.get('rc')} err={a.get('error')}\n{a.get('stderr','')}\n")

    if b is not None:
        _cap = args.max_tokens if args.max_tokens is not None else "model-default"
        print(f"── PATH B — direct REST [{b.get('auth','?')}] (cap={_cap}) " + "─" * 14)
        if b.get("ok"):
            print(f"latency: {b['secs']:.2f}s   usage: {_fmt_usage(b['usage'])}")
            print(f"output ({len(b['text'])} chars):\n{b['text'].strip()}\n")
        else:
            print(f"FAILED: {b.get('error')}\n")

    if c is not None:
        print(f"── PATH C — SDK google-genai [{c.get('auth','?')}] " + "─" * 18)
        if c.get("ok"):
            print(f"latency: {c['secs']:.2f}s   usage: {_fmt_usage(c['usage'])}")
            print(f"output ({len(c['text'])} chars):\n{c['text'].strip()}\n")
        else:
            print(f"FAILED: {c.get('error')}\n")

    if a is not None and b is not None and a.get("ok") and b.get("ok"):
        print("── DIFF " + "─" * 50)
        same = a["text"].strip() == b["text"].strip()
        print(f"exact text match: {same}")
        print(f"length: A={len(a['text'])}  B={len(b['text'])}  (Δ {len(a['text']) - len(b['text']):+d} chars)")
        print(f"latency: A={a['secs']:.2f}s  B={b['secs']:.2f}s  (Δ {a['secs'] - b['secs']:+.2f}s)")
        for k in ("prompt_tokens", "output_tokens", "total_tokens"):
            av, bv = a["usage"].get(k), b["usage"].get(k)
            print(f"{k}: A={av}  B={bv}")
        print("\n(Output text will rarely match exactly — LLMs are nondeterministic. "
              "Watch for STRUCTURAL differences: does the CLI add framing/scaffolding the "
              "raw API call doesn't? And compare latency + token counts.)")


if __name__ == "__main__":
    main()
