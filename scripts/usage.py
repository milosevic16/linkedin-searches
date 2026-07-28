"""Count what a run actually costs.

Every Claude call in this project reports its token usage; nothing was
reading it, so the only way to learn what a run cost was to watch the
balance drop. This accumulates usage per step and prints a table at the
end of the build, with a deliberately CONSERVATIVE price estimate — it
uses list prices and ignores promotional discounts, so the real bill
should come in at or under the number printed.

Prices are USD per million tokens, from the public pricing page. If they
change, edit _PRICES; nothing else needs to know.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# USD per million tokens: (input, output).
_PRICES = {
    "claude-opus-5": (5.00, 25.00),
    "claude-opus-4-8": (5.00, 25.00),
    "claude-sonnet-5": (3.00, 15.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
}
_FALLBACK_PRICE = (5.00, 25.00)  # assume Opus-tier if we don't recognise the model

# Cached input is billed at a fraction of the normal input rate; writing to
# the cache costs a premium. Both are multipliers on the input price.
_CACHE_READ_MULT = 0.10
_CACHE_WRITE_MULT = 1.25

# Server-side web search, USD per search.
_SEARCH_PRICE = 0.01

# Only for showing a familiar number next to the dollars. Approximate and
# not worth chasing — the point is the order of magnitude.
_USD_TO_EUR = 0.92


@dataclass
class _Step:
    model: str = ""
    calls: int = 0
    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0
    searches: int = 0

    @property
    def usd(self) -> float:
        price_in, price_out = _PRICES.get(self.model, _FALLBACK_PRICE)
        per_token = price_in / 1_000_000
        return (
            self.input * per_token
            + self.output * price_out / 1_000_000
            + self.cache_read * per_token * _CACHE_READ_MULT
            + self.cache_write * per_token * _CACHE_WRITE_MULT
            + self.searches * _SEARCH_PRICE
        )


@dataclass
class Usage:
    """Accumulates usage across a build. One instance per run."""

    steps: dict[str, _Step] = field(default_factory=dict)

    def record(self, step: str, model: str, response) -> None:
        """Fold one API response into the running totals. Never raises —
        a missing usage field must not take down a build."""
        entry = self.steps.setdefault(step, _Step(model=model))
        entry.model = model or entry.model
        entry.calls += 1
        u = getattr(response, "usage", None)
        if u is None:
            return
        entry.input += int(getattr(u, "input_tokens", 0) or 0)
        entry.output += int(getattr(u, "output_tokens", 0) or 0)
        entry.cache_read += int(getattr(u, "cache_read_input_tokens", 0) or 0)
        entry.cache_write += int(getattr(u, "cache_creation_input_tokens", 0) or 0)
        server = getattr(u, "server_tool_use", None)
        if server is not None:
            entry.searches += int(getattr(server, "web_search_requests", 0) or 0)

    @property
    def total_usd(self) -> float:
        return sum(s.usd for s in self.steps.values())

    @property
    def total_eur(self) -> float:
        return self.total_usd * _USD_TO_EUR

    def summary(self) -> dict:
        """A small dict for the data file and the page footer."""
        return {
            "usd": round(self.total_usd, 4),
            "eur": round(self.total_eur, 4),
            "calls": sum(s.calls for s in self.steps.values()),
            "searches": sum(s.searches for s in self.steps.values()),
            "steps": {
                name: {
                    "model": s.model,
                    "calls": s.calls,
                    "input": s.input,
                    "output": s.output,
                    "cache_read": s.cache_read,
                    "cache_write": s.cache_write,
                    "searches": s.searches,
                    "usd": round(s.usd, 4),
                }
                for name, s in self.steps.items()
            },
        }

    def report(self) -> str:
        """A table for the build log."""
        if not self.steps:
            return "No Claude calls were made this run — nothing to bill."

        head = (
            f"{'step':<10}{'model':<18}{'calls':>6}{'in':>9}{'cache wr':>10}"
            f"{'cache rd':>10}{'out':>8}{'srch':>6}{'USD':>9}"
        )
        lines = [head, "-" * len(head)]
        for name, s in self.steps.items():
            lines.append(
                f"{name:<10}{s.model:<18}{s.calls:>6}{s.input:>9,}{s.cache_write:>10,}"
                f"{s.cache_read:>10,}{s.output:>8,}{s.searches:>6}{s.usd:>9.4f}"
            )
        lines.append("-" * len(head))
        lines.append(
            f"{'TOTAL':<10}{'':<18}{sum(s.calls for s in self.steps.values()):>6}"
            f"{'':>9}{'':>10}{'':>10}{'':>8}{sum(s.searches for s in self.steps.values()):>6}"
            f"{self.total_usd:>9.4f}"
        )
        lines.append(f"≈ EUR {self.total_eur:.4f} at {_USD_TO_EUR} EUR/USD (list prices, no discounts)")
        lines.append(self._cache_verdict())
        return "\n".join(lines)

    def _cache_verdict(self) -> str:
        """Whether caching actually paid for itself.

        Reads are cheap but writes carry a premium, so caching only wins if
        enough of what gets written is read back. Reporting the read discount
        alone (which this used to do) overstates the benefit — badly, when
        each call caches a fresh prefix that later calls never reuse.
        """
        if not any(s.cache_read or s.cache_write for s in self.steps.values()):
            return "No caching activity this run — if that persists, caching is not working."

        saved = 0.0
        for s in self.steps.values():
            price_in = _PRICES.get(s.model, _FALLBACK_PRICE)[0] / 1_000_000
            would_have_cost = (s.cache_read + s.cache_write) * price_in
            actually_cost = (
                s.cache_read * price_in * _CACHE_READ_MULT
                + s.cache_write * price_in * _CACHE_WRITE_MULT
            )
            saved += would_have_cost - actually_cost

        if saved >= 0:
            return f"Prompt caching was worth USD {saved:.4f} this run (net of the write premium)."
        return (
            f"Prompt caching COST USD {-saved:.4f} this run — more was written to the cache "
            f"than got read back. Worth reviewing where the breakpoints sit."
        )
