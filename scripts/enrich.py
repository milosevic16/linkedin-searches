"""Score posts for relevance, then draft comments for the ones worth it.

Two passes on purpose, and on two different models. Scoring is a judgement
call made over every post that survived the date and keyword filters, and it
is cheap work — a small model reading real post text does it well. Drafting
is the output you actually read, and it only runs for posts that cleared the
relevance bar, so the better model is used on a fraction of the input.

Doing both in one call, as this used to, meant paying the drafting model to
also think about posts it was about to discard.
"""

from __future__ import annotations

import json
import os

SCORE_CHUNK = 12   # scoring emits a line per post — batches can be larger
DRAFT_CHUNK = 6    # drafting emits three comments per post — keep batches small

# With a real post body rather than a search snippet, there is enough to judge
# on. The cap only guards against a runaway result.
_TEXT_CHARS = 2000

_SCORE_SCHEMA = {
    "type": "object",
    "properties": {
        "evaluations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer", "description": "The post id from the input."},
                    "relevance": {
                        "type": "integer",
                        "description": "0-10. How worthwhile it is for us to comment on this post.",
                    },
                    "reason": {
                        "type": "string",
                        "description": "One short sentence: why this score, in plain language.",
                    },
                },
                "required": ["id", "relevance", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["evaluations"],
    "additionalProperties": False,
}

_DRAFT_SCHEMA = {
    "type": "object",
    "properties": {
        "drafts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer", "description": "The post id from the input."},
                    "comments": {
                        "type": "array",
                        "description": "Exactly 3 comment drafts written for THIS post, in the post's language.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "style": {
                                    "type": "string",
                                    "enum": ["insight", "question", "experience"],
                                },
                                "text": {"type": "string"},
                            },
                            "required": ["style", "text"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["id", "comments"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["drafts"],
    "additionalProperties": False,
}

_SCORE_SYSTEM = """You decide which LinkedIn posts are worth commenting on.

Who is commenting, and why:
{profile}

For each post, score relevance 0-10: would a thoughtful comment from them on \
this post plausibly be seen by their target audience and make them look good? \
Penalise job ads, company self-promotion with no discussion value, and posts \
that only mention a keyword in passing. Reward posts by relevant people that \
invite discussion. Keep the reason to one plain sentence."""

_DRAFT_SYSTEM = """You draft LinkedIn comments for two professionals.

Who they are and why they comment:
{profile}

Comment voice:
{voice}

For each post, write exactly 3 comment drafts (each 1-3 sentences) in the SAME \
LANGUAGE as the post: one adding an insight, one asking a good question, one \
sharing a relatable angle or experience. Write them FOR THIS SPECIFIC POST — \
they must respond to what this author actually said, not be generic commentary \
on the topic."""


def _payload(posts: list[dict], offset: int) -> str:
    return json.dumps(
        [
            {
                "id": offset + i,
                "author": p.get("author") or "(unknown author)",
                "hours_old": p.get("age_hours"),
                "text": (p.get("snippet") or p.get("title") or "")[:_TEXT_CHARS],
                "matched_keywords": p.get("keywords") or [],
            }
            for i, p in enumerate(posts)
        ],
        ensure_ascii=False,
    )


def _call(client, anthropic, model, system, schema, user_text, effort, usage, step, warnings):
    """One structured-output call. Returns the parsed object, or None."""
    try:
        response = client.messages.create(
            model=model,
            max_tokens=8000,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
            messages=[{"role": "user", "content": user_text}],
        )
    except anthropic.RateLimitError:
        warnings.append(f"Claude rate limit hit during {step} — some posts are incomplete.")
        return None
    except anthropic.APIStatusError as exc:
        warnings.append(f"Claude API error {exc.status_code} during {step}.")
        return None
    except anthropic.APIConnectionError:
        warnings.append(f"Could not reach the Claude API during {step}.")
        return None

    if usage is not None:
        usage.record(step, model, response)
    if response.stop_reason == "refusal":
        warnings.append(f"Claude declined one batch during {step}.")
        return None
    if response.stop_reason == "max_tokens":
        warnings.append(f"One {step} batch was cut short — some posts may be incomplete.")

    text = next((b.text for b in response.content if b.type == "text"), "")
    try:
        return json.loads(text)
    except (json.JSONDecodeError, AttributeError):
        warnings.append(f"Could not parse one {step} batch.")
        return None


def enrich_posts(posts: list[dict], cfg: dict, usage=None) -> tuple[list[dict], list[str]]:
    """Attach relevance / reason, then comments to the posts that earn them."""
    warnings: list[str] = []
    if not posts:
        return posts, warnings
    if not os.environ.get("ANTHROPIC_API_KEY"):
        warnings.append("AI scoring is off (ANTHROPIC_API_KEY missing) — posts are listed unscored.")
        return posts, warnings

    import anthropic  # imported lazily so --sample runs need no key/package config

    client = anthropic.Anthropic()
    profile = (cfg.get("profile") or "").strip()
    voice = (cfg.get("voice") or "").strip()
    score_model = cfg.get("score_model") or "claude-haiku-4-5"
    draft_model = cfg.get("model") or "claude-sonnet-5"
    min_rel = int(cfg.get("min_relevance", 5))

    limit = int(cfg.get("max_enriched", 30))
    targets = posts[:limit]
    if len(posts) > limit:
        warnings.append(f"{limit} of {len(posts)} posts were scored (max_enriched).")

    # ── Pass 1: score everything, on the cheap model ──────────────────
    score_system = _SCORE_SYSTEM.format(profile=profile)
    for start in range(0, len(targets), SCORE_CHUNK):
        chunk = targets[start : start + SCORE_CHUNK]
        parsed = _call(
            client, anthropic, score_model, score_system, _SCORE_SCHEMA,
            "Score these LinkedIn posts:\n" + _payload(chunk, start),
            "low", usage, "score", warnings,
        )
        for ev in (parsed or {}).get("evaluations", []):
            idx = ev.get("id")
            if isinstance(idx, int) and 0 <= idx < len(targets):
                targets[idx]["relevance"] = max(0, min(10, int(ev.get("relevance", 0))))
                targets[idx]["reason"] = str(ev.get("reason", "")).strip()

    # ── Pass 2: draft only for posts that cleared the bar ─────────────
    worth_it = [p for p in targets if isinstance(p.get("relevance"), int) and p["relevance"] >= min_rel]
    print(f"Scored {len(targets)} posts; {len(worth_it)} cleared relevance {min_rel} and get drafts.")
    if not worth_it:
        return posts, warnings

    draft_system = _DRAFT_SYSTEM.format(profile=profile, voice=voice)
    for start in range(0, len(worth_it), DRAFT_CHUNK):
        chunk = worth_it[start : start + DRAFT_CHUNK]
        parsed = _call(
            client, anthropic, draft_model, draft_system, _DRAFT_SCHEMA,
            "Draft comments for these LinkedIn posts:\n" + _payload(chunk, start),
            "low", usage, "draft", warnings,
        )
        for row in (parsed or {}).get("drafts", []):
            idx = row.get("id")
            if not isinstance(idx, int) or not (0 <= idx - start < len(chunk)):
                continue
            chunk[idx - start]["comments"] = [
                {"style": c.get("style", "insight"), "text": str(c.get("text", "")).strip()}
                for c in (row.get("comments") or [])
                if str(c.get("text", "")).strip()
            ][:3]

    return posts, warnings
