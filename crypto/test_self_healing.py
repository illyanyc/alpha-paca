"""Test the self-healing system — simulates agent failures and verifies recovery."""

import asyncio
import sys

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()


class FakeTimeoutError(Exception):
    pass


class FakeAuthError(Exception):
    pass


class FakeParseError(Exception):
    pass


async def run_healing_test():
    from agents.base import BaseAgent, set_healer, set_state_ref
    from agents.healer import HealerAgent, CircuitState

    state = {
        "agent_statuses": {},
        "healing_events": [],
    }
    healer = HealerAgent()
    set_healer(healer)
    set_state_ref(state)

    console.print(Panel("[bold cyan]Self-Healing Test Suite[/]", border_style="cyan"))

    # -- Test 1: Transient timeout error -> auto-retry
    console.print("\n[bold]Test 1: Transient timeout → retry_immediate[/]")

    class TimeoutAgent(BaseAgent):
        name = "test_timeout_agent"
        call_count = 0

        async def run(self, **kwargs) -> dict:
            self.call_count += 1
            if self.call_count <= 1:
                raise FakeTimeoutError("Connection timed out after 30s")
            return {"ok": True}

    agent1 = TimeoutAgent()
    state["agent_statuses"]["test_timeout_agent"] = "idle"
    result = await agent1.safe_run()

    timeout_breaker = healer.get_breaker("test_timeout_agent")
    healed = result.get("ok") is True
    console.print(f"  Result: {'[green]HEALED' if healed else '[red]FAILED'}[/] (calls: {agent1.call_count})")
    console.print(f"  Circuit: {timeout_breaker.state.value}")
    console.print(f"  Status: {state['agent_statuses'].get('test_timeout_agent')}")
    assert healed, "Timeout agent should have self-healed on retry"
    assert state["agent_statuses"]["test_timeout_agent"] == "healthy"

    # -- Test 2: Rate limit -> backoff retry
    console.print("\n[bold]Test 2: Rate limit → retry_with_backoff[/]")

    class RateLimitAgent(BaseAgent):
        name = "test_ratelimit_agent"
        call_count = 0

        async def run(self, **kwargs) -> dict:
            self.call_count += 1
            if self.call_count <= 1:
                raise Exception("429 Too Many Requests — rate limit exceeded")
            return {"ok": True}

    agent2 = RateLimitAgent()
    state["agent_statuses"]["test_ratelimit_agent"] = "idle"
    result = await agent2.safe_run()

    healed = result.get("ok") is True
    console.print(f"  Result: {'[green]HEALED' if healed else '[red]FAILED'}[/] (calls: {agent2.call_count})")
    assert healed, "Rate limit agent should have self-healed after backoff"

    # -- Test 3: Auth error -> circuit break
    console.print("\n[bold]Test 3: Auth error → circuit_break[/]")

    class AuthAgent(BaseAgent):
        name = "test_auth_agent"

        async def run(self, **kwargs) -> dict:
            raise FakeAuthError("401 Unauthorized — API key expired")

    agent3 = AuthAgent()
    state["agent_statuses"]["test_auth_agent"] = "idle"
    result = await agent3.safe_run()

    auth_breaker = healer.get_breaker("test_auth_agent")
    console.print(f"  Result: error={result.get('error', 'none')}")
    console.print(f"  Circuit: [bold red]{auth_breaker.state.value}[/]")
    console.print(f"  Status: {state['agent_statuses'].get('test_auth_agent')}")
    assert auth_breaker.state == CircuitState.OPEN, "Auth error should open circuit"

    # Second call should be blocked by circuit breaker
    result2 = await agent3.safe_run()
    console.print(f"  Blocked by circuit: {result2.get('error') == 'circuit_open'}")
    assert result2.get("error") == "circuit_open"

    # -- Test 4: Parse error -> skip
    console.print("\n[bold]Test 4: Parse error → skip_cycle[/]")

    class ParseAgent(BaseAgent):
        name = "test_parse_agent"

        async def run(self, **kwargs) -> dict:
            raise FakeParseError("JSON decode error at position 42")

    agent4 = ParseAgent()
    state["agent_statuses"]["test_parse_agent"] = "idle"
    result = await agent4.safe_run()

    console.print(f"  Result: healing={result.get('healing')}")
    console.print(f"  Status: {state['agent_statuses'].get('test_parse_agent')}")

    # -- Test 5: Repeated failures -> escalate to circuit break
    console.print("\n[bold]Test 5: Repeated failures → escalate to circuit_break[/]")

    class FlappingAgent(BaseAgent):
        name = "test_flapping_agent"

        async def run(self, **kwargs) -> dict:
            raise Exception("Something random broke")

    agent5 = FlappingAgent()
    state["agent_statuses"]["test_flapping_agent"] = "idle"

    for i in range(6):
        await agent5.safe_run()

    flap_breaker = healer.get_breaker("test_flapping_agent")
    console.print(f"  After 6 failures — circuit: [bold red]{flap_breaker.state.value}[/]")
    console.print(f"  Failure count: {flap_breaker.failure_count}")
    assert flap_breaker.state == CircuitState.OPEN, "Should be open after repeated failures"

    # -- Summary
    console.print("\n[bold]Healing Event Log:[/]")
    evt_table = Table(border_style="yellow")
    evt_table.add_column("Agent")
    evt_table.add_column("Severity")
    evt_table.add_column("Outcome")
    evt_table.add_column("Message")

    for evt in state["healing_events"]:
        sev = evt.get("severity", "?")
        sev_style = {"critical": "red", "warning": "yellow", "transient": "cyan", "info": "green"}.get(sev, "dim")
        evt_table.add_row(
            evt.get("agent", "?"),
            f"[{sev_style}]{sev}[/]",
            evt.get("outcome", "?"),
            evt.get("message", "")[:60],
        )

    console.print(evt_table)

    console.print(Panel(
        f"[bold green]ALL SELF-HEALING TESTS PASSED[/]\n"
        f"Total healing events: {len(state['healing_events'])}",
        border_style="green",
    ))


if __name__ == "__main__":
    asyncio.run(run_healing_test())
