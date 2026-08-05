"""Fetch recent LinkedIn posts by keyword, via Apify.

Web search cannot do this job: search engines index LinkedIn months late, so
nothing they return is recent enough to be worth commenting on. This instead
calls a scraping actor that queries LinkedIn's own post search, which is where
recent posts actually exist.

Deliberately kept behind one small module. These vendors are not permanent —
Proxycurl, the biggest of them, was sued by LinkedIn and shut down in 2025 —
so swapping providers should mean rewriting this file and nothing else.

Filters run outermost-first, because each one is cheaper than the next:

  1. At source. `postedLimit` is passed to LinkedIn's own search, so stale
     posts are never returned and never billed.
  2. In code, on the date. The exact age cut, against the date decoded from
     each post's URL. Billing already happened, but no model tokens have.
  3. In code, on the keyword. Attributed from the actor's own `query` field
     where present, so it is exact rather than guessed at from the text.
  4. Only then, a model.

A model asked to "skip old posts" cannot see a date and will not, which is how
a post from 2023 once reached the dashboard with a relevance of 7.
"""

from __future__ import annotations

import http.client
import json
import os
import re
import urllib.error
import urllib.request

from postdate import (age_hours, date_disagreement_hours, describe_window, max_age_hours,
                      post_datetime, source_side_limit)

# How far two date sources must diverge before the post is treated as a
# reshare rather than a rounding difference between a timestamp and an id.
_RESHARE_HOURS = 6.0

ACTOR = "harvestapi~linkedin-post-search"
ENDPOINT = f"https://api.apify.com/v2/acts/{ACTOR}/run-sync-get-dataset-items"

# The actor's published rate. Billed on posts RETURNED, not posts kept, so the
# filters below save model tokens but not this — posts_per_keyword and the
# source-side limit are the only dials that move it.
USD_PER_1000_POSTS = 2.00

_TIMEOUT_S = 540
_MAX_PER_KEYWORD = 100      # a typo in a company config must not be a $100 run

_URL_KEYS = ("linkedinUrl", "url", "postUrl", "shareLinkedinUrl", "post_url", "link", "permalink")
_TEXT_KEYS = ("content", "text", "postContent", "post_text", "description", "commentary")
_ID_KEYS = ("entityId", "shareUrn", "id")
_DATE_KEYS = ("postedAt", "posted_at", "publishedAt", "published_at", "date",
              "postedDate", "createdAt", "time", "postedAtTimestamp")
_AUTHOR_KEYS = ("author", "authorName", "author_name", "actor", "profile", "user")
# Deliberately no "title": on LinkedIn that is the job headline, so an author
# without a name renders as "Partner at Foo LLP | Crypto disputes".
_NAME_KEYS = ("name", "fullName", "full_name", "displayName", "username")
_NESTED_TEXT_KEYS = ("text", "content", "commentary", "body")

_WORD_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = {"and", "the", "for", "with", "from", "into", "that", "this", "are", "was"}


class SearchFailed(RuntimeError):
    """The fetch did not run to completion. Distinct from "ran, found nothing"
    — the caller must not treat this as an empty result and overwrite good
    stored data with it."""


def _first(raw: dict, keys) -> object | None:
    for k in keys:
        v = raw.get(k)
        if v not in (None, "", [], {}):
            return v
    return None


def _every(raw: dict, keys) -> list:
    """Every present value for these keys, not just the first.

    Dating needs all of them. `entityId` and `shareUrn` are both ids, but on a
    reshare they name different posts, and stopping at the first hides the one
    that says how old the content really is.
    """
    return [v for k in keys if (v := raw.get(k)) not in (None, "", [], {})]


def _as_text(value) -> str:
    """Post bodies sometimes arrive as an object rather than a string."""
    if isinstance(value, dict):
        return str(_first(value, _NESTED_TEXT_KEYS) or "").strip()
    if isinstance(value, (list, tuple)):
        return " ".join(_as_text(v) for v in value).strip()
    return str(value or "").strip()


def _author_name(raw: dict) -> str:
    value = _first(raw, _AUTHOR_KEYS)
    if isinstance(value, (list, tuple)):
        value = value[0] if value else None
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        name = _first(value, _NAME_KEYS)
        if isinstance(name, str):
            return name.strip()
        first = str(value.get("firstName") or "").strip()
        last = str(value.get("lastName") or "").strip()
        if first or last:
            return f"{first} {last}".strip()
    return ""


def _terms(keyword: str) -> list[str]:
    return [w for w in _WORD_RE.findall(keyword.lower()) if len(w) > 2 and w not in _STOPWORDS]


def _keywords_from_query(raw: dict, by_lower: dict[str, str]) -> list[str]:
    """The actor echoes the search that produced each post. Since the queries
    we send are exactly the configured keywords, that is an exact attribution
    — no guessing from the post text."""
    q = raw.get("query")
    candidates = []
    if isinstance(q, dict):
        candidates = [q.get("search"), q.get("query"), q.get("keywords")]
    elif isinstance(q, str):
        candidates = [q]
    for c in candidates:
        if isinstance(c, str) and (hit := by_lower.get(c.strip().lower())):
            return [hit]
    return []


def _keywords_from_text(text: str, keywords: list[str]) -> list[str]:
    """Fallback attribution, used only when the actor did not tell us which
    query produced the post.

    Terms are weighted by how distinctive they are across the keyword set.
    "crypto" appears in most of these keywords, so on its own it says nothing
    — matching on it tagged a post about divorce disclosure as "MiCA
    compliance crypto". "mica", "divorce" and "ransomware" each appear in one
    keyword, so they are what actually identify a topic.

    Whole-word matching throughout, so "Dorado" is not a hit for "DORA".
    """
    words = set(_WORD_RE.findall(text.lower()))
    if not words:
        return []

    frequency: dict[str, int] = {}
    for kw in keywords:
        for term in set(_terms(kw)):
            frequency[term] = frequency.get(term, 0) + 1

    matched = []
    for kw in keywords:
        terms = _terms(kw)
        if not terms:
            continue
        distinctive = [t for t in terms if frequency.get(t, 0) == 1]
        present = [t for t in terms if t in words]
        if distinctive:
            # A topic is identified by its rare terms; the common ones only
            # corroborate.
            if any(t in words for t in distinctive):
                matched.append(kw)
        elif len(present) >= max(1, len(terms) - 1):
            # No term is unique to this keyword, so require nearly all of it.
            matched.append(kw)
    return matched


def _report_shape(items: list[dict], warnings: list[str]) -> None:
    """Say what the actor actually returned, so a changed output schema shows
    up as a message naming the field, rather than as an empty dashboard with
    misleading advice about widening the date window."""
    sample = next((i for i in items if isinstance(i, dict)), None)
    if sample is None:
        warnings.append("Apify returned no readable objects — the actor's output format may have changed.")
        return
    print(f"Apify item fields: {', '.join(sorted(sample.keys()))}")
    print(f"Apify query field: {sample.get('query')!r}")
    print(f"Apify date field : {_first(sample, _DATE_KEYS)!r}")

    # Reshares are why dates are taken as the OLDEST of every id, url and date
    # the post carries: the reshare's own id is minutes old while the text it
    # wraps is days old. Show the fields that can reveal one. ("share" not
    # "shared" — the first version of this check looked for "shared" and so
    # reported "none" while shareUrn and shareLinkedinUrl were sitting there.)
    reshare_fields = sorted(
        k for k in sample
        if any(w in k.lower() for w in ("share", "repost", "original", "parent", "type", "header"))
    )
    print(f"Apify reshare-ish fields: {reshare_fields or 'none'}")
    for k in ("type", "header", "entityId", "shareUrn"):
        if k in sample:
            print(f"  sample {k}: {str(sample[k])[:120]!r}")

    # How often the two ids actually disagree. This is the number that says
    # whether reshares are a real part of the haul or a one-off.
    differing = sum(
        1 for i in items
        if isinstance(i, dict) and i.get("shareUrn") and i.get("entityId")
        and str(i["shareUrn"]).rsplit(":", 1)[-1] != str(i["entityId"]).rsplit(":", 1)[-1]
    )
    print(f"Apify posts whose shareUrn differs from entityId: {differing} of {len(items)}")

    probe = items[:20]
    for label, keys in (("URL", _URL_KEYS), ("text", _TEXT_KEYS), ("date/id", _DATE_KEYS + _ID_KEYS)):
        if not any(isinstance(i, dict) and _first(i, keys) is not None for i in probe):
            warnings.append(
                f"Apify results carry no recognisable {label} field "
                f"(saw: {', '.join(sorted(sample.keys()))}). The actor's output format has "
                f"changed — scripts/search_apify.py needs its field names updated."
            )


def _call_actor(token: str, queries: list[str], per_keyword: int,
                sort_by: str, posted_limit: str, max_items: int) -> list[dict]:
    payload = {
        "searchQueries": queries,
        # PER QUERY, not a total for the run. Passing the product of the two
        # cost $4.41 instead of $0.44 on the first real run: 2,204 posts.
        "maxPosts": per_keyword,
        "sortBy": sort_by,
        # Filter at the source so stale posts are never returned or billed.
        "postedLimit": posted_limit,
        # Load-bearing: each scraped reaction and comment is billed as its own
        # post and arrives as its own dataset item.
        "scrapeReactions": False,
        "scrapeComments": False,
    }
    # maxItems is a RUN option, not an input field — it caps charged results.
    url = f"{ENDPOINT}?token={token}&maxItems={max_items}"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
        body = resp.read().decode("utf-8", "replace")
    parsed = json.loads(body)
    if not isinstance(parsed, list):
        # run-sync returns an object, not a list, when the run failed, aborted
        # or timed out. Treating that as "no results" silently wiped good data.
        detail = ""
        if isinstance(parsed, dict):
            err = parsed.get("error") or {}
            detail = str(err.get("message") or parsed.get("message") or "")[:200]
        raise SearchFailed(f"Apify run did not complete{': ' + detail if detail else ''}.")
    return parsed


def search_posts(cfg: dict, usage=None) -> tuple[list[dict], list[str]]:
    """Return (posts, warnings).

    Raises SearchFailed if the fetch did not complete, so the caller can keep
    the previous run's posts rather than overwriting them with nothing.
    """
    warnings: list[str] = []
    token = os.environ.get("APIFY_TOKEN", "").strip()
    if not token:
        raise SearchFailed("APIFY_TOKEN is not set — cannot search for posts.")

    keywords, seen = [], set()
    for k in cfg.get("keywords") or []:
        k = str(k).strip()
        if k and k.lower() not in seen:
            seen.add(k.lower())
            keywords.append(k)
    if not keywords:
        return [], []

    search_cfg = cfg.get("search") or {}
    limit_hours = max_age_hours(search_cfg)

    try:
        per_keyword = int(search_cfg.get("posts_per_keyword", 10))
    except (TypeError, ValueError):
        per_keyword = 10
        where = cfg.get("config_file") or "the company's config file"
        warnings.append(f"posts_per_keyword in {where} is not a number — using 10.")
    per_keyword = max(1, min(_MAX_PER_KEYWORD, per_keyword))

    expected = per_keyword * len(keywords)
    budget = float(search_cfg.get("max_usd_per_run", 1.00))
    projected = expected * USD_PER_1000_POSTS / 1000
    if projected > budget:
        raise SearchFailed(
            f"Refusing to search: {expected} posts would cost about ${projected:.2f}, over the "
            f"${budget:.2f} max_usd_per_run limit. Lower posts_per_keyword or raise the limit."
        )

    posted_limit = source_side_limit(limit_hours)
    print(f"Fetching up to {per_keyword} posts for each of {len(keywords)} keywords "
          f"(source-side limit: {posted_limit}, cap ${projected:.2f}).")

    try:
        items = _call_actor(token, keywords, per_keyword, search_cfg.get("sort_by", "date"),
                            posted_limit, max_items=expected)
    except urllib.error.HTTPError as exc:
        hint = "check the APIFY_TOKEN secret is valid" if exc.code in (401, 403) else exc.reason
        raise SearchFailed(f"Apify returned HTTP {exc.code} ({hint}).") from exc
    except (urllib.error.URLError, OSError, http.client.HTTPException,
            TimeoutError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        # A mid-read connection reset on a 540s request is the likeliest
        # failure here, and none of these are URLError.
        raise SearchFailed(f"Apify request failed ({type(exc).__name__}: {exc}).") from exc

    if usage is not None:
        usage.record_external("apify", len(items), USD_PER_1000_POSTS)

    print(f"Apify returned {len(items)} posts (expected up to {expected}).")
    if not items:
        warnings.append("Apify returned no posts at all — either nothing was published in the "
                        "window, or the run failed silently.")
        return [], warnings
    if len(items) > expected * 2:
        warnings.append(
            f"Apify returned {len(items)} posts when {expected} were requested — about "
            f"${len(items) * USD_PER_1000_POSTS / 1000:.2f} rather than ${projected:.2f}. "
            f"Lower posts_per_keyword."
        )
    _report_shape(items, warnings)

    by_lower = {k.lower(): k for k in keywords}
    include_articles = bool(search_cfg.get("include_articles", True))
    posts: dict[str, dict] = {}
    too_old = undatable = unmatched = no_url = articles = reshared = 0

    for raw in items:
        if not isinstance(raw, dict):
            continue

        url = str(_first(raw, _URL_KEYS) or "").split("?")[0].rstrip("/")
        ident = _first(raw, _ID_KEYS)
        if not url and ident:
            # An id is enough to rebuild the canonical URL; dropping the post
            # for want of one field is how a whole run got thrown away once.
            digits = str(ident).rsplit(":", 1)[-1]
            if digits.isdigit():
                url = f"https://www.linkedin.com/feed/update/urn:li:activity:{digits}/"
        if not url:
            no_url += 1
            continue

        # 1. Date filter. EVERY id, url and date field the post carries is
        #    considered, and the OLDEST wins.
        #
        #    All of them, not the first of each: the actor returns both
        #    `entityId` and `shareUrn`, and on a reshare those differ — the
        #    first is the reshare, the second the original it wraps. Reading
        #    only the first is how a six-day-old post reached the dashboard
        #    stamped "just now". Its own `postedAt` is no help there; it
        #    reports the reshare too.
        date_args = (url, *_every(raw, _URL_KEYS), *_every(raw, _ID_KEYS),
                     *_every(raw, _DATE_KEYS))
        when = post_datetime(*date_args)
        if when is None:
            undatable += 1
            continue
        spread = date_disagreement_hours(*date_args)
        if spread >= _RESHARE_HOURS:
            reshared += 1
        age = age_hours(when)
        if age > limit_hours:
            too_old += 1
            continue

        is_article = "/pulse/" in url
        if is_article and not include_articles:
            articles += 1
            continue

        # 2. Keyword filter — exact from the actor's own query where possible.
        text = _as_text(_first(raw, _TEXT_KEYS))
        matched = _keywords_from_query(raw, by_lower) or _keywords_from_text(text, keywords)
        if not matched:
            unmatched += 1
            continue

        # 3. Only what survives all of that goes on to cost model tokens.
        if (existing := posts.get(url)) is not None:
            for kw in matched:
                if kw not in existing["keywords"]:
                    existing["keywords"].append(kw)
            continue
        posts[url] = {
            "url": url,
            "title": (text.split("\n", 1)[0] or "Untitled post")[:180],
            "author": _author_name(raw) or "Unknown author",
            "snippet": text,
            "keywords": matched,
            "is_article": is_article,
            "posted_at": when.isoformat(),
            "age_hours": round(age, 1),
        }

    dropped = []
    if too_old:
        dropped.append(f"{too_old} older than {describe_window(limit_hours)}")
    if unmatched:
        dropped.append(f"{unmatched} matching no keyword")
    if articles:
        dropped.append(f"{articles} articles (include_articles is off)")
    if undatable:
        dropped.append(f"{undatable} with no readable date")
    if no_url:
        dropped.append(f"{no_url} with no URL or id — likely a changed output format")
    if dropped:
        print(f"Filtered out: {', '.join(dropped)}.")
    if reshared:
        # Not an error: these are dated by their original rather than by the
        # reshare, which is the point. Printed so the scale of it is visible —
        # if it ever becomes most of a run, the keywords are pulling recycled
        # content rather than fresh discussion.
        print(f"{reshared} posts looked like reshares (their URL is newer than their "
              f"content by {_RESHARE_HOURS:.0f}h or more); dated by the original.")
    if not posts:
        warnings.append(
            f"Apify returned {len(items)} posts but none survived filtering "
            f"({', '.join(dropped) or 'no reason recorded'})."
        )

    return list(posts.values()), warnings


def probe(cfg: dict) -> int:
    """Fetch real posts and report ONLY what the dating logic makes of them.

    Exists to settle one question with evidence rather than inference: does a
    reshare arrive carrying its original's id, and does taking the oldest
    candidate therefore date it correctly? Answering that needed real actor
    output, and a full gather costs ~$0.62 — two thirds of which is the
    Anthropic scoring and drafting this never reaches. The Apify half alone is
    about $0.22 and is the only part that can answer the question.

    Prints, saves nothing, calls no model.
    """
    token = os.environ.get("APIFY_TOKEN", "").strip()
    if not token:
        print("::error::APIFY_TOKEN is not set — nothing to probe.")
        return 1

    keywords = [str(k).strip() for k in (cfg.get("keywords") or []) if str(k).strip()]
    search_cfg = cfg.get("search") or {}
    per_keyword = max(1, min(_MAX_PER_KEYWORD, int(search_cfg.get("posts_per_keyword", 10))))
    limit_hours = max_age_hours(search_cfg)
    expected = per_keyword * len(keywords)
    print(f"Probing {expected} posts across {len(keywords)} keywords "
          f"(~${expected * USD_PER_1000_POSTS / 1000:.2f}, no model calls).")

    items = _call_actor(token, keywords, per_keyword, str(search_cfg.get("sort_by", "date")),
                        source_side_limit(limit_hours), expected)
    print(f"Apify returned {len(items)} posts.\n")

    differing, disagreeing, would_have_shown = 0, 0, []
    for raw in items:
        if not isinstance(raw, dict):
            continue
        url = str(_first(raw, _URL_KEYS) or "").split("?")[0]
        entity, share = raw.get("entityId"), raw.get("shareUrn")
        ids_differ = (entity and share
                      and str(share).rsplit(":", 1)[-1] != str(entity).rsplit(":", 1)[-1])
        if ids_differ:
            differing += 1

        # What the code used to do: URL, then the FIRST id, then the first date.
        was = post_datetime(url, _first(raw, _ID_KEYS), _first(raw, _DATE_KEYS))
        # What it does now: every url, every id, every date — oldest wins.
        now_ = post_datetime(url, *_every(raw, _URL_KEYS), *_every(raw, _ID_KEYS),
                             *_every(raw, _DATE_KEYS))
        if was and now_ and (was - now_).total_seconds() > 3600:
            disagreeing += 1
            old_age, new_age = age_hours(was), age_hours(now_)
            flipped = old_age <= limit_hours < new_age
            if flipped:
                would_have_shown.append((raw, old_age, new_age))
            print(f"  {'WOULD HAVE BEEN SHOWN AS FRESH' if flipped else 'dates disagree'}")
            print(f"    author   : {_author_name(raw)}")
            print(f"    type     : {raw.get('type')!r}   header: {str(raw.get('header'))[:60]!r}")
            print(f"    entityId : {entity}   shareUrn: {share}")
            print(f"    old logic: {old_age:6.1f}h old     new logic: {new_age:6.1f}h old")
            print(f"    url      : {url[:100]}\n")

    print("─" * 70)
    print(f"posts fetched                         : {len(items)}")
    print(f"posts whose shareUrn != entityId      : {differing}")
    print(f"posts the two dating rules disagree on: {disagreeing}")
    print(f"posts the OLD rule would have shown as fresh that are actually stale: "
          f"{len(would_have_shown)}")
    print("─" * 70)
    if would_have_shown:
        print("VERDICT: reshares are real, the old rule mis-dated them, and the new rule "
              "catches them. The bug is fixed and this run is the proof.")
    elif differing:
        print("VERDICT: reshares exist in this haul but none was mis-dated — their ids "
              "point at posts of the same age. The fix is harmless; the reported post "
              "was something else. Send its URL if it recurs.")
    else:
        print("VERDICT: no reshares in this haul at all, so nothing here exercises the fix. "
              "Inconclusive — not a failure. Re-probe after a day, or send the URL of any "
              "post whose age looks wrong.")
    return 0
