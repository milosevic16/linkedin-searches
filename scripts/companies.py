"""The company roster: who this dashboard serves, and where each one's files live.

Each company is a COMPLETE, standalone config in companies/<slug>.yml. There is
deliberately no shared-defaults layer for anything that costs money.

Why it is built that way: store.fingerprint() decides whether a build searches
again (~$0.62), re-drafts stored posts (~$0.42), or just re-renders (free).
Four of the six inputs to the draft fingerprint are exactly the settings that
look global and invite casual edits — model, score_model, min_relevance,
max_enriched. Had those lived in one shared file, nudging min_relevance by one
digit would invalidate EVERY company's drafts at once and bill for all of them
from a single commit. Repeating them per company makes the cost legible: two
companies means two deliberate edits and two bills you chose to pay.

config.yml keeps only the roster and the refresh endpoint — nothing any
fingerprint reads. registry() enforces that, so the property cannot rot.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

_SLUG_RE = re.compile(r"[a-z0-9][a-z0-9_-]*")

ROOT = Path(__file__).resolve().parent.parent
COMPANY_DIR = ROOT / "companies"
REGISTRY_PATH = ROOT / "config.yml"

# Left in a company file's profile or voice, this marks copy that has not been
# written yet. Gathering against it would pay full price for posts judged
# against a fictional company, so build.py refuses. A marker beats a single
# "ready: yes" flag because it still catches a half-finished edit — profile
# rewritten, voice still boilerplate.
PLACEHOLDER = "FILL-THIS-IN"

# Every key store.fingerprint() reads. None may appear in config.yml: a
# fingerprinted key in a shared file bills every company for one edit.
_COST_KEYS = frozenset(
    {
        "keywords",
        "profile",
        "voice",
        "commenters",
        "model",
        "score_model",
        "search_model",
        "search",
        "max_enriched",
        "min_relevance",
    }
)


class ConfigError(RuntimeError):
    """The company setup is wrong in a way no run can work around."""


def _read_yaml(path: Path) -> dict:
    rel = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path
    if not path.exists():
        raise ConfigError(f"{rel} does not exist.")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"{rel} is not valid YAML: {exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError(f"{rel} must be a mapping of settings, not {type(data).__name__}.")
    return data


@dataclass(frozen=True)
class Company:
    """One company, its config, and every path derived from it."""

    slug: str
    cfg: dict
    is_default: bool

    @property
    def name(self) -> str:
        return str(self.cfg.get("name") or self.slug).strip()

    @property
    def config_path(self) -> Path:
        return COMPANY_DIR / f"{self.slug}.yml"

    @property
    def config_rel(self) -> str:
        return f"companies/{self.slug}.yml"

    @property
    def data_path(self) -> Path:
        """Gathered posts, committed back by the workflow."""
        return ROOT / "data" / f"{self.slug}.json"

    @property
    def seen_path(self) -> Path:
        """URLs already shown, so a repeat does not re-flag as NEW.

        This lives in git rather than only on the published site. It used to be
        read back over HTTPS from the live Pages deploy, which made it fragile
        in two ways worth remembering: a transient 5xx read as "nothing seen
        yet" and wiped it, and a deploy that rendered one company would publish
        an artifact without the other's file — deleting that company's history
        as a side effect of refreshing this one.
        """
        return ROOT / "data" / f"{self.slug}-seen.json"

    @property
    def url_path(self) -> str:
        """Where the page sits under the site root. The default company gets the
        root itself, so the original bookmark keeps working."""
        return "" if self.is_default else f"{self.slug}/"

    @property
    def site_dir(self) -> Path:
        site = ROOT / "site"
        return site if self.is_default else site / self.slug

    def link_to(self, other: "Company") -> str:
        """A relative link from this company's page to another's, so the switcher
        works on the live site and on a locally opened file alike."""
        if other.slug == self.slug:
            return "./"
        up = "" if self.is_default else "../"
        return (up + other.url_path) or "./"

    def is_placeholder(self) -> bool:
        text = f"{self.cfg.get('profile') or ''}\n{self.cfg.get('voice') or ''}"
        return PLACEHOLDER in text or not text.strip()


def registry() -> dict:
    """config.yml — the roster and the refresh endpoint, and nothing that costs."""
    reg = _read_yaml(REGISTRY_PATH)

    stray = sorted(_COST_KEYS & set(reg))
    if stray:
        raise ConfigError(
            "config.yml contains " + ", ".join(stray) + ", which decide whether a build "
            "spends money. Shared here, one edit would bill every company at once. "
            "Move them into companies/<slug>.yml."
        )

    slugs, seen = [], set()
    for raw in reg.get("companies") or []:
        slug = str(raw).strip()
        if not slug or slug in seen:
            continue
        # Every path in Company is built from this — the config file, the data
        # files, the output directory, the URL. Keep it to characters that
        # cannot climb out of a directory or mean something to a URL.
        if not _SLUG_RE.fullmatch(slug):
            raise ConfigError(
                f"“{slug}” is not a usable company name. Use lowercase letters, "
                "digits, hyphens and underscores only — it becomes a file path "
                "and a URL."
            )
        seen.add(slug)
        slugs.append(slug)
    if not slugs:
        raise ConfigError(
            "config.yml lists no companies. Add at least one slug under `companies:` "
            "with a matching file in companies/."
        )
    reg["companies"] = slugs
    return reg


def load(slug: str, *, is_default: bool) -> Company:
    cfg = _read_yaml(COMPANY_DIR / f"{slug}.yml")
    # So the modules that only ever see a cfg dict can name the right file when
    # they warn about a setting. Pointing at config.yml would send someone to a
    # file that no longer holds any of these.
    cfg["config_file"] = f"companies/{slug}.yml"
    return Company(slug=slug, cfg=cfg, is_default=is_default)


def load_all(reg: dict | None = None) -> list[Company]:
    """Every company, in roster order. The first is the default."""
    reg = reg or registry()
    return [load(slug, is_default=(i == 0)) for i, slug in enumerate(reg["companies"])]


def select(companies: list[Company], slug: str | None) -> Company:
    """Resolve a --company argument against the roster."""
    if not slug:
        return companies[0]
    wanted = slug.strip().lower()
    for company in companies:
        if company.slug.lower() == wanted:
            return company
    known = ", ".join(c.slug for c in companies)
    raise ConfigError(f"Unknown company “{slug}”. This dashboard serves: {known}.")
