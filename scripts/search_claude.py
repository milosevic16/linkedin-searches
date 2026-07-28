"""Find recent public LinkedIn posts using Claude's built-in web search tool.

Requires ANTHROPIC_API_KEY only — there is no second search provider to set up.

Anti-hallucination guard: URLs are taken from the `web_search_tool_result`
blocks the tool actually returned. Claude's own JSON is used only to attach an
author and a readable snippet to those URLs; anything referencing a URL the
search did not return is discarded.
"""

from __future__ import annotations

import json
import os
import re

# The tool version that supports dynamic filtering (Opus 5 / Sonnet 5 / 4.6+).
WEB_SEARCH_TOOL = "web_search_20260209"

_PROMPT = """Search LinkedIn for recent public posts about: {keyword}

Run {n_searches} web search{plural} to find posts published in the last {days} days.

Each search returns a limited number of results, so make your queries DIFFERENT \
from one another to widen the net rather than repeating one phrasing. Vary them by:
- the wording people actually use for this topic (synonyms, the acronym vs the full term)
- the angle (news/announcement, opinion/debate, practical how-it-affects-us)
- adding a recency word such as the current month or year
A good query looks like: site:linkedin.com/posts {keyword}

Then reply with ONLY a JSON array (no prose before or after) describing the \
LinkedIn posts you found. One object per post:

[
  {{"url": "<exact URL from the search results>",
    "author": "<person or company who posted, or empty string>",
    "title": "<the post's opening line or headline>",
    "snippet": "<2-3 sentences of what the post actually says>"}}
]

Rules:
- Only linkedin.com URLs that appeared in your search results. Never invent or \
guess a URL, and never modify one.
- Only individual posts or articles (/posts/ or /pulse/ URLs). Skip profile \
pages, company pages, job listings on /jobs/, and search result pages.
- Skip anything clearly older than {days} days.
- Keep the post's original language in the title and snippet.
- If you found nothing suitable, reply with exactly: []
"""

_SEARCH_TITLE_RE = re.compile(r"^(.{2,80}?)\s+on LinkedIn:?\s*(.*)$", re.S)
_POSTED_BY_RE = re.compile(r"^(.*?)\s*\|\s*(.{2,80}?)\s+posted on the topic", re.S | re.I)


def _split_search_title(title: str) -> tuple[str, str]:
    """Search-result titles carry the author. Two observed shapes:
    "<Author> on LinkedIn: <text>" and "<text> | <Author> posted on the topic".
    """
    m = _SEARCH_TITLE_RE.match(title or "")
    if m:
        return m.group(1).strip(), (m.group(2) or "").strip()
    m = _POSTED_BY_RE.match(title or "")
    if m:
        return m.group(2).strip(), m.group(1).strip()
    return "", (title or "").strip()


def _normalize(url: str) -> str:
    return (url or "").split("?")[0].split("#")[0].rstrip("/")


def _is_post_url(url: str, include_articles: bool) -> bool:
    if "linkedin.com" not in url:
        return False
    if "/posts/" in url:
        return True
    return include_articles and "/pulse/" in url


def _extract_json_array(text: str):
    """Pull a JSON array out of the model's reply, tolerating stray prose/fences."""
    if not text:
        return []
    fenced = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", text, re.S)
    candidate = fenced.group(1) if fenced else None
    if candidate is None:
        start, end = text.find("["), text.rfind("]")
        candidate = text[start : end + 1] if 0 <= start < end else None
    if not candidate:
        return []
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def search_posts(cfg: dict) -> tuple[list[dict], list[str]]:
    """Return (posts, warnings). Each post: url, title, author, snippet, keywords."""
    warnings: list[str] = []
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return [], ["Search is not configured (ANTHROPIC_API_KEY missing)."]

    import anthropic

    client = anthropic.Anthropic()
    model = cfg.get("model") or "claude-opus-5"
    search_cfg = cfg.get("search") or {}
    days = int(search_cfg.get("notable_days", 90))
    n_searches = max(1, min(5, int(search_cfg.get("searches_per_keyword", 2))))
    include_articles = bool(search_cfg.get("include_articles", True))

    posts: dict[str, dict] = {}

    for keyword in cfg.get("keywords") or []:
        keyword = str(keyword).strip()
        if not keyword:
            continue

        messages = [
            {
                "role": "user",
                "content": _PROMPT.format(
                    keyword=keyword,
                    days=days,
                    n_searches=n_searches,
                    plural="es" if n_searches != 1 else "",
                ),
            }
        ]
        tools = [
            {
                "type": WEB_SEARCH_TOOL,
                "name": "web_search",
                "max_uses": n_searches + 2,  # headroom for query refinement
                "allowed_domains": ["linkedin.com"],
                # Bypass "dynamic filtering". By default this tool version runs
                # search inside code execution, which prunes results before they
                # reach the model — good for research questions, wrong for us:
                # we want every raw result so no candidate post is silently
                # dropped, and direct calls keep the result blocks top-level.
                "allowed_callers": ["direct"],
            }
        ]

        found_urls: dict[str, dict] = {}  # normalized url -> {title, page_age}
        response = None
        try:
            for _ in range(4):  # resume loop for pause_turn
                response = client.messages.create(
                    model=model,
                    max_tokens=8000,
                    tools=tools,
                    messages=messages,
                )
                # Harvest the authoritative URLs from the tool's own results.
                for block in response.content:
                    if getattr(block, "type", "") != "web_search_tool_result":
                        continue
                    results = block.content
                    if not isinstance(results, list):  # error object, not results
                        code = getattr(results, "error_code", "unknown")
                        warnings.append(f"Web search error for “{keyword}”: {code}")
                        continue
                    for r in results:
                        if getattr(r, "type", "") != "web_search_result":
                            continue
                        url = _normalize(getattr(r, "url", ""))
                        if url and _is_post_url(url, include_articles):
                            found_urls.setdefault(
                                url,
                                {
                                    "title": (getattr(r, "title", "") or "").strip(),
                                    "page_age": getattr(r, "page_age", None),
                                },
                            )

                if response.stop_reason != "pause_turn":
                    break
                messages.append({"role": "assistant", "content": response.content})
            else:
                warnings.append(f"Search for “{keyword}” did not finish — results may be partial.")
        except anthropic.RateLimitError:
            warnings.append("Claude API rate limit hit during search — results are incomplete.")
            break
        except anthropic.APIStatusError as exc:
            warnings.append(f"Claude API error {exc.status_code} while searching “{keyword}”.")
            continue
        except anthropic.APIConnectionError:
            warnings.append("Could not reach the Claude API while searching — results are incomplete.")
            break

        if response is None:
            continue
        if response.stop_reason == "refusal":
            warnings.append(f"Claude declined the search for “{keyword}”.")
            continue

        text = "".join(b.text for b in response.content if getattr(b, "type", "") == "text")
        described = {}
        for item in _extract_json_array(text):
            if not isinstance(item, dict):
                continue
            url = _normalize(str(item.get("url", "")))
            if url in found_urls:  # ← the guard: must be a real search result
                described[url] = item

        # Only posts Claude vouched for. It has read the results and applied the
        # recency/quality rules; the raw result set is full of years-old posts,
        # so iterating found_urls here would flood the dashboard with them.
        # found_urls remains the whitelist — an invented URL still can't get in.
        for url, desc in described.items():
            meta = found_urls[url]
            title = (desc.get("title") or meta["title"] or "").strip()
            author = (desc.get("author") or "").strip()
            if not author:  # search titles look like "<Author> on LinkedIn: <text>"
                author, title = _split_search_title(title)
            post = posts.setdefault(
                url,
                {
                    "url": url,
                    "title": title,
                    "author": author,
                    "snippet": (desc.get("snippet") or "").strip(),
                    "keywords": [],
                    "is_article": "/pulse/" in url,
                    "page_age": meta.get("page_age"),
                },
            )
            if keyword not in post["keywords"]:
                post["keywords"].append(keyword)

    return list(posts.values()), warnings
