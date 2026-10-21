"""Hello-agent spike: verify the Strands agent loop end-to-end against Bedrock.

Proves four things:
  1. The agent loop runs and returns text.
  2. A custom @tool is discovered and actually invoked by the model.
  3. Lifecycle hooks fire and can observe tool calls.
  4. structured_output returns a validated pydantic object.

Run:  .venv/Scripts/python.exe spikes/hello_agent.py
Requires AWS credentials with bedrock:InvokeModel in the region below.

Kept standalone on purpose — a spike that imports the app is no longer a spike.
It therefore repeats the model defaults from src/agents.py rather than sharing
them; if you change them there, this file does not follow.
"""

from datetime import datetime, timedelta, timezone

from pydantic import BaseModel, Field
from strands import Agent, tool
from strands.hooks import AfterToolCallEvent, BeforeToolCallEvent, HookProvider
from strands.models.bedrock import BedrockModel

MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
REGION = "us-east-1"

# ── 1. custom tool ────────────────────────────────────────────────────────────
# Deliberately domain-relevant: the deadline-radar idea hinges on normalising
# "9/15" style deadlines into an unambiguous local instant.

PT_UTC_OFFSET_HOURS = -7  # PDT in September


@tool
def convert_pt_deadline_to_utc(month: int, day: int, hour_24: int) -> str:
    """Convert a US Pacific Time deadline into UTC and London time.

    Args:
        month: Month of the deadline, 1-12.
        day: Day of the deadline, 1-31.
        hour_24: Hour of the deadline in 24h Pacific Time, 0-23.
    """
    pt = timezone(timedelta(hours=PT_UTC_OFFSET_HOURS))
    deadline = datetime(2026, month, day, hour_24, 0, tzinfo=pt)
    utc = deadline.astimezone(timezone.utc)
    london = deadline.astimezone(timezone(timedelta(hours=1)))  # BST in September
    return (
        f"{deadline:%Y-%m-%d %H:%M} PT "
        f"= {utc:%Y-%m-%d %H:%M} UTC "
        f"= {london:%Y-%m-%d %H:%M} London"
    )


# ── 2. hook provider ──────────────────────────────────────────────────────────
class ToolCallSpy(HookProvider):
    """Observes every tool call — the extension point an audit trail would use."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def register_hooks(self, registry, **kwargs) -> None:
        registry.add_callback(BeforeToolCallEvent, self._before)
        registry.add_callback(AfterToolCallEvent, self._after)

    def _before(self, event: BeforeToolCallEvent) -> None:
        self.calls.append(f"→ calling {event.tool_use['name']} {event.tool_use['input']}")

    def _after(self, event: AfterToolCallEvent) -> None:
        self.calls.append(f"← {event.tool_use['name']} returned")


# ── 3. structured output model ────────────────────────────────────────────────
class DeadlineVerdict(BaseModel):
    hackathon_name: str = Field(description="Name of the hackathon")
    deadline_utc: str = Field(description="Deadline in UTC, format YYYY-MM-DD HH:MM")
    is_ambiguous: bool = Field(description="True if the stated deadline lacked a timezone")


def main() -> None:
    model = BedrockModel(model_id=MODEL_ID, region_name=REGION, temperature=0.0)
    spy = ToolCallSpy()

    agent = Agent(
        model=model,
        tools=[convert_pt_deadline_to_utc],
        hooks=[spy],
        system_prompt=(
            "You are a deadline assistant. When a deadline is given in Pacific Time, "
            "you MUST call the convert_pt_deadline_to_utc tool rather than doing the "
            "arithmetic yourself. Answer in one short sentence."
        ),
    )

    print("=" * 70)
    print("TEST 1 — agent loop + custom tool invocation")
    print("=" * 70)
    result = agent(
        "The Agents for Humans hackathon closes September 14 at 17:00 Pacific Time. "
        "What is that in UTC and London time?"
    )
    print("\n[final text]\n", str(result).strip()[:600])

    print("\n" + "=" * 70)
    print("TEST 2 — lifecycle hooks observed")
    print("=" * 70)
    for line in spy.calls:
        print(" ", line)
    print(f"  tool actually invoked: {any('→' in c for c in spy.calls)}")

    print("\n" + "=" * 70)
    print("TEST 3 — structured output (pydantic)")
    print("=" * 70)
    verdict = agent.structured_output(
        DeadlineVerdict,
        "The Agents for Humans hackathon page says the deadline is 'September 15'. "
        "The official rules say September 14, 17:00 Pacific Time. Fill in the model.",
    )
    print("  type:", type(verdict).__name__)
    print("  ", verdict.model_dump())

    print("\n" + "=" * 70)
    print("TEST 4 — agent introspection")
    print("=" * 70)
    print("  tool_names:", agent.tool_names)


if __name__ == "__main__":
    main()
