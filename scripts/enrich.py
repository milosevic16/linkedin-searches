"""Score posts for relevance and draft comment suggestions with Claude.

Requires ANTHROPIC_API_KEY. If it is missing, posts are returned unscored
and the dashboard simply lists them without drafts.
"""

from __future__ import annotations

import json
import os

CHUNK_SIZE = 8

# Structured-output schema: guarantees valid JSON back from the model.
_SCHEMA = {
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
                    "comments": {
                        "type": "array",
                        "description": "2-3 ready-to-adapt comment drafts in the post's language.",
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
                "required": ["id", "relevance", "reason", "comments"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["evaluations"],
    "additionalProperties": False,
}

_SYSTEM = """You help two professionals decide which LinkedIn posts to comment on, \
and you draft the comments.

Who they are and why they comment:
{profile}

Comment voice:
{voice}

For every post you receive (only a title/snippet is available, not the full text):
- Score relevance 0-10: would a thoughtful comment from them on this post plausibly \
be seen by their target audience and make them look good? Penalise job ads, \
company self-promotion with no discussion value, and posts that merely mention a \
keyword without substance. Reward posts by relevant people that invite discussion.
- Write 2-3 short comment drafts (each 1-3 sentences) in the SAME LANGUAGE as the \
post: one adding an insight, one asking a good question, one sharing a relatable \
angle or experience. They must stand on their own even if the snippet is partial — \
avoid referring to specifics the snippet does not actually show.
- Keep the "reason" to one plain sentence.
"""


def enrich_posts(posts: list[dict], cfg: dict) -> tuple[list[dict], list[str]]:
    """Attach relevance / reason / comments to each post, in place. Returns (posts, warnings)."""
    warnings: list[str] = []
    if not posts:
        return posts, warnings
    if not os.environ.get("ANTHROPIC_API_KEY"):
        warnings.append("AI scoring is off (ANTHROPIC_API_KEY missing) — posts are listed unscored.")
        return posts, warnings

    import anthropic  # imported lazily so --sample runs need no key/package config

    client = anthropic.Anthropic()
    model = cfg.get("model") or "claude-opus-5"
    system = _SYSTEM.format(
        profile=(cfg.get("profile") or "").strip(),
        voice=(cfg.get("voice") or "").strip(),
    )

    limit = int(cfg.get("max_enriched", 30))
    targets = posts[:limit]
    if len(posts) > limit:
        warnings.append(f"Only the first {limit} of {len(posts)} posts were AI-scored (max_enriched).")

    for start in range(0, len(targets), CHUNK_SIZE):
        chunk = targets[start : start + CHUNK_SIZE]
        payload = [
            {
                "id": start + i,
                "author": p.get("author") or "(unknown author)",
                "title": p.get("title") or "",
                "snippet": p.get("snippet") or "",
                "matched_keywords": p.get("keywords") or [],
            }
            for i, p in enumerate(chunk)
        ]
        try:
            response = client.messages.create(
                model=model,
                max_tokens=8000,
                system=system,
                output_config={
                    "effort": "low",
                    "format": {"type": "json_schema", "schema": _SCHEMA},
                },
                messages=[
                    {
                        "role": "user",
                        "content": "Evaluate these LinkedIn posts:\n"
                        + json.dumps(payload, ensure_ascii=False),
                    }
                ],
            )
        except anthropic.RateLimitError:
            warnings.append("Claude API rate limit hit — some posts are unscored.")
            break
        except anthropic.APIStatusError as exc:
            warnings.append(f"Claude API error {exc.status_code} — some posts are unscored.")
            break
        except anthropic.APIConnectionError:
            warnings.append("Could not reach the Claude API — some posts are unscored.")
            break

        if response.stop_reason == "refusal":
            warnings.append("Claude declined to process one batch of posts — those are unscored.")
            continue
        if response.stop_reason == "max_tokens":
            warnings.append("One AI batch was cut short — some posts in it may be unscored.")

        text = next((b.text for b in response.content if b.type == "text"), "")
        try:
            evaluations = json.loads(text).get("evaluations", [])
        except (json.JSONDecodeError, AttributeError):
            warnings.append("Could not parse one AI batch — those posts are unscored.")
            continue

        for ev in evaluations:
            idx = ev.get("id")
            if not isinstance(idx, int) or not (0 <= idx < len(targets)):
                continue
            post = targets[idx]
            post["relevance"] = max(0, min(10, int(ev.get("relevance", 0))))
            post["reason"] = str(ev.get("reason", "")).strip()
            post["comments"] = [
                {"style": c.get("style", "insight"), "text": str(c.get("text", "")).strip()}
                for c in (ev.get("comments") or [])
                if str(c.get("text", "")).strip()
            ][:3]

    return posts, warnings
