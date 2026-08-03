"""Usage tracking: an in-memory version of the future `llm_calls` table.

Everything the router does is recorded here, so from day one you can see
exactly where tokens (and money) go. When PostgreSQL lands in phase 2,
this becomes a repository writing the same rows to the DB.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field


@dataclass
class LLMCall:
    sim_minute: int
    agent_id: str
    task_type: str
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    estimated_cost: float
    cache_hit: bool = False


@dataclass
class UsageTracker:
    calls: list[LLMCall] = field(default_factory=list)
    on_record = None  # Callable[[LLMCall], None] | None -- persistence hook

    def record(self, call: LLMCall) -> None:
        self.calls.append(call)
        if self.on_record is not None:
            self.on_record(call)

    # ---- reporting -------------------------------------------------

    @property
    def total_calls(self) -> int:
        return len(self.calls)

    @property
    def cache_hits(self) -> int:
        return sum(1 for c in self.calls if c.cache_hit)

    @property
    def total_cost(self) -> float:
        return sum(c.estimated_cost for c in self.calls)

    def summary(self) -> str:
        if not self.calls:
            return "No LLM calls recorded."

        by_task: Counter[str] = Counter()
        by_model: Counter[str] = Counter()
        by_agent: Counter[str] = Counter()
        cost_by_task: dict[str, float] = defaultdict(float)
        in_tok = out_tok = 0
        total_cost = 0.0

        for c in self.calls:
            by_task[c.task_type] += 1
            by_model[f"{c.provider}/{c.model}"] += 1
            by_agent[c.agent_id] += 1
            cost_by_task[c.task_type] += c.estimated_cost
            in_tok += c.input_tokens
            out_tok += c.output_tokens
            total_cost += c.estimated_cost

        hit_rate = (self.cache_hits / self.total_calls * 100) if self.calls else 0.0

        lines = [
            "AI USAGE",
            f"  Calls: {self.total_calls}  (cache hits: {self.cache_hits}, {hit_rate:.0f}%)",
            f"  Input tokens:  {in_tok:,}",
            f"  Output tokens: {out_tok:,}",
            f"  Estimated cost: ${total_cost:.6f}",
            "  By task:",
        ]
        for task, n in by_task.most_common():
            lines.append(f"    {task:<20} {n:>4} calls   ${cost_by_task[task]:.6f}")
        lines.append("  By model:")
        for model, n in by_model.most_common():
            lines.append(f"    {model:<28} {n:>4} calls")
        lines.append("  By agent:")
        for agent, n in by_agent.most_common():
            lines.append(f"    {agent:<12} {'█' * min(n, 30)} {n}")
        return "\n".join(lines)
