"""Model coverage: which decision outcomes of a model were exercised by simulations or test cases.

Every block that makes a decision lists its possible *outcomes* (a Switch passes input 0 or input 2; a
comparison is true or false; a saturation is below, within or above its limits; a chart is in each state and
takes each transition; a lookup table uses each interval). During a simulation the outcomes that occur are
counted. Coverage is the fraction of outcomes that occurred at least once. Missed outcomes show which
situations the tests never reach, typically a missing test case.

    from nervus.coverage import Coverage
    from nervus.testing import run_tests

    cov = Coverage()
    run_tests(model, coverage=cov)
    print(cov.percent, cov.missed())
    cov.save_html("coverage.html")

**Condition and MC/DC coverage** go further for logic with several conditions (a Logical Operator with two or more
inputs, e.g. ``enable AND speed_ok AND not_faulted``):

* *condition coverage*: every condition was true at least once and false at least once;
* *MC/DC* (modified condition/decision coverage, unique cause): for every condition, two evaluations that differ
  only in that condition gave different results, so each condition is shown to affect the outcome on its own.
  ISO 26262 recommends it for software of the highest integrity (ASIL D), DO-178C requires it (level A).

For each condition that is not shown yet, :meth:`Coverage.mcdc_missing` names the evaluation a test still needs.
"""

from __future__ import annotations

import html
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class BlockCoverage:
    path: str
    block_type: str
    counts: dict[str, int]

    @property
    def covered(self) -> int:
        return sum(1 for c in self.counts.values() if c > 0)

    @property
    def total(self) -> int:
        return len(self.counts)

    @property
    def missed(self) -> list[str]:
        return [o for o, c in self.counts.items() if c == 0]


def _evaluate(operator: str, conds: tuple[bool, ...]) -> bool:
    if operator == "AND":
        return all(conds)
    if operator == "OR":
        return any(conds)
    if operator == "NAND":
        return not all(conds)
    if operator == "NOR":
        return not any(conds)
    return sum(conds) % 2 == 1  # XOR


@dataclass
class ConditionCoverage:
    """The evaluations of one decision with several conditions: which combinations occurred, and their results."""

    path: str
    operator: str
    """AND, OR, ... for a Logical Operator; the expression for a chart guard."""
    n: int
    seen: dict[tuple[bool, ...], bool] = field(default_factory=dict)
    evaluate: Any = None
    """The decision as a function of its conditions (``None``: from the operator)."""

    def outcome(self, conds: tuple[bool, ...]) -> bool:
        return bool(self.evaluate(conds)) if self.evaluate is not None else _evaluate(self.operator, conds)

    def record(self, conds: tuple[bool, ...]) -> None:
        if conds not in self.seen:
            self.seen[conds] = self.outcome(conds)

    def condition_outcomes(self) -> tuple[int, int]:
        """(covered, total) for condition coverage: each condition true and false."""
        covered = sum(1 for i in range(self.n) for v in (True, False) if any(c[i] == v for c in self.seen))
        return covered, 2 * self.n

    def independent(self, i: int) -> bool:
        """Condition ``i`` was shown to change the result on its own (a unique-cause pair was evaluated)."""
        for c, r in self.seen.items():
            flipped = (*c[:i], not c[i], *c[i + 1 :])
            if flipped in self.seen and self.seen[flipped] != r:
                return True
        return False

    def mcdc(self) -> tuple[int, int]:
        return sum(1 for i in range(self.n) if self.independent(i)), self.n

    def needed(self, i: int) -> str:
        """The evaluations that would show condition ``i`` independently (the cheapest pair: one already seen)."""

        def fmt(c: tuple[bool, ...]) -> str:
            return "(" + ", ".join("1" if v else "0" for v in c) + f") → {int(self.outcome(c))}"

        pairs = []
        for k in range(2 ** (self.n - 1)):  # every setting of the other conditions
            others = [(k >> j) & 1 == 1 for j in range(self.n - 1)]
            a = tuple([*others[:i], True, *others[i:]])
            b = tuple([*others[:i], False, *others[i:]])
            if self.outcome(a) != self.outcome(b):
                missing = [x for x in (a, b) if x not in self.seen]
                pairs.append((len(missing), a, b, missing))
        if not pairs:
            return "cannot be shown"
        _n, a, b, missing = min(pairs, key=lambda p: p[0])
        return f"needs {' and '.join(fmt(x) for x in missing)} (the pair {fmt(a)} / {fmt(b)})"


@dataclass
class Coverage:
    blocks: dict[str, BlockCoverage] = field(default_factory=dict)
    runs: int = 0
    conditions: dict[str, ConditionCoverage] = field(default_factory=dict)
    """Decisions with several conditions, for condition and MC/DC coverage."""

    def register_conditions(self, path: str, operator: str, n: int, evaluate: Any = None) -> None:
        if path not in self.conditions:
            self.conditions[path] = ConditionCoverage(path, operator, n, evaluate=evaluate)

    def record_conditions(self, path: str, evaluations: list[tuple[bool, ...]]) -> None:
        cc = self.conditions[path]
        for conds in evaluations:
            cc.record(conds)

    @property
    def mcdc_percent(self) -> float:
        done = sum(c.mcdc()[0] for c in self.conditions.values())
        total = sum(c.mcdc()[1] for c in self.conditions.values())
        return 100.0 * done / total if total else 100.0

    @property
    def condition_percent(self) -> float:
        done = sum(c.condition_outcomes()[0] for c in self.conditions.values())
        total = sum(c.condition_outcomes()[1] for c in self.conditions.values())
        return 100.0 * done / total if total else 100.0

    def mcdc_missing(self) -> dict[str, list[str]]:
        """For each decision, the conditions not yet shown independent, with the evaluations a test needs."""
        out = {}
        for path, c in self.conditions.items():
            lines = [f"condition {i + 1}: {c.needed(i)}" for i in range(c.n) if not c.independent(i)]
            if lines:
                out[path] = lines
        return out

    def register(self, path: str, block_type: str, outcomes: list[str]) -> BlockCoverage:
        bc = self.blocks.get(path)
        if bc is None:
            bc = self.blocks[path] = BlockCoverage(path, block_type, {o: 0 for o in outcomes})
        for o in outcomes:
            bc.counts.setdefault(o, 0)
        return bc

    def hit(self, path: str, outcomes: list[str]) -> None:
        bc = self.blocks[path]
        for o in outcomes:
            bc.counts[o] = bc.counts.get(o, 0) + 1

    @property
    def covered(self) -> int:
        return sum(b.covered for b in self.blocks.values())

    @property
    def total(self) -> int:
        return sum(b.total for b in self.blocks.values())

    @property
    def percent(self) -> float:
        return 100.0 * self.covered / self.total if self.total else 100.0

    def missed(self) -> dict[str, list[str]]:
        return {p: b.missed for p, b in self.blocks.items() if b.missed}

    def summary(self) -> str:
        lines = [
            f"Decision coverage: {self.covered}/{self.total} outcomes ({self.percent:.0f} %) in {self.runs} run(s)"
        ]
        for path, missed in self.missed().items():
            lines.append(f"  {path}: never {', '.join(missed)}")
        if self.conditions:
            n = sum(c.n for c in self.conditions.values())
            lines.append(f"Condition coverage {self.condition_percent:.0f} %, MC/DC {self.mcdc_percent:.0f} % "
                         f"({n} conditions in {len(self.conditions)} decisions)")  # fmt: skip
            for path, needs in self.mcdc_missing().items():
                lines += [f"  {path}: {x}" for x in needs]
        return "\n".join(lines)

    def to_html(self, title: str = "Model coverage") -> str:
        rows = []
        for b in sorted(self.blocks.values(), key=lambda b: (b.covered == b.total, b.path)):
            pct = 100.0 * b.covered / b.total if b.total else 100.0
            cells = " ".join(
                f'<span class="{"hit" if c else "miss"}">{html.escape(o)}{f" ×{c}" if c else ""}</span>'
                for o, c in b.counts.items()
            )
            rows.append(
                f"<tr><td>{html.escape(b.path)}</td><td>{html.escape(b.block_type)}</td>"
                f'<td class="num">{pct:.0f} %</td><td>{cells}</td></tr>'
            )
        css = (
            "body{font-family:system-ui,sans-serif;margin:24px;color:#1d1d1b;background:#fcfcfb}"
            "table{border-collapse:collapse}td,th{border:1px solid #dcdbd5;padding:5px 8px;text-align:left;vertical-align:top}"
            ".num{text-align:right}.hit,.miss{display:inline-block;margin:2px;padding:1px 6px;border-radius:4px}"
            ".hit{background:#1baf7a22;border:1px solid #1baf7a}.miss{background:#e3494822;border:1px solid #e34948}"
            "@media (prefers-color-scheme: dark){body{background:#1a1a19;color:#f2f2ef}td,th{border-color:#353532}}"
        )
        return f"""<!doctype html><html><head><meta charset="utf-8"><title>{html.escape(title)}</title>
<style>{css}</style></head><body><h1>{html.escape(title)}</h1>
<p><b>{self.percent:.0f} %</b> decision coverage: {self.covered} of {self.total} outcomes exercised in {self.runs} run(s).
Green outcomes occurred (×count); red ones never did.</p>
<table><tr><th>block</th><th>type</th><th>coverage</th><th>outcomes</th></tr>{"".join(rows)}</table>
{self._mcdc_html()}</body></html>
"""

    def _mcdc_html(self) -> str:
        if not self.conditions:
            return ""
        rows = []
        for path, c in sorted(self.conditions.items()):
            done, n = c.mcdc()
            cells = " ".join(
                f'<span class="{"hit" if c.independent(i) else "miss"}">{i + 1}</span>' for i in range(c.n)
            )
            needs = "<br>".join(
                html.escape(f"condition {i + 1}: {c.needed(i)}") for i in range(c.n) if not c.independent(i)
            )
            rows.append(f"<tr><td>{html.escape(path)}</td><td>{html.escape(c.operator)}</td>"
                        f'<td class="num">{done}/{n}</td><td>{cells}</td><td>{needs}</td></tr>')  # fmt: skip
        return (
            f"<h2>Condition and MC/DC coverage</h2><p><b>{self.mcdc_percent:.0f} %</b> MC/DC, "
            f"{self.condition_percent:.0f} % condition coverage. A condition is green when two evaluations that "
            "differ only in it gave different results.</p><table><tr><th>decision</th><th>operator</th>"
            "<th>MC/DC</th><th>conditions</th><th>a test still needs</th></tr>" + "".join(rows) + "</table>"
        )

    def save_html(self, path: str | Path, title: str = "Model coverage") -> Path:
        p = Path(path)
        p.write_text(self.to_html(title), encoding="utf-8")
        return p
