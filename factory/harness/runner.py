"""How one agent run happens. SDKRunner is the only code that imports the SDK."""
import dataclasses
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Protocol

from .agents import AgentSpec
from .policy import Policy


@dataclass(frozen=True)
class AgentCall:
    agent: AgentSpec
    prompt: str
    cwd: Path
    policy: Policy
    label: str
    transcript: Path
    extra_dirs: tuple = ()


@dataclass(frozen=True)
class RunResult:
    text: str
    cost_usd: float = 0.0
    turns: int = 0
    duration_ms: int = 0
    session_id: str | None = None
    is_error: bool = False


class AgentRunner(Protocol):
    async def run(self, call: AgentCall) -> RunResult: ...


def plain(message):
    """A JSON-safe record of one SDK message for the transcript."""
    if dataclasses.is_dataclass(message):
        return {'type': type(message).__name__, **dataclasses.asdict(message)}
    return {'type': type(message).__name__, 'value': str(message)}


class SDKRunner:
    """Runs each agent as its own Claude Agent SDK session.

    The PreToolUse gate returns an explicit allow or deny from the role policy
    for every tool call, so no run waits on a permission prompt and no tool
    outside the policy executes. Project settings load too, so CLAUDE.md and
    the project work-loss hook apply exactly as in interactive sessions.
    """

    def __init__(self, max_turns=120):
        self.max_turns = max_turns

    async def run(self, call):
        try:
            from claude_agent_sdk import (AssistantMessage, ClaudeAgentOptions, HookMatcher,
                                          ResultMessage, TextBlock, query)
        except ImportError as error:
            raise RuntimeError('claude-agent-sdk is not installed; run bin/factory setup') from error

        async def gate(input_data, tool_use_id, context):
            allowed, reason = call.policy.decide(input_data.get('tool_name', ''),
                                                 input_data.get('tool_input') or {})
            return {'hookSpecificOutput': {'hookEventName': 'PreToolUse',
                                           'permissionDecision': 'allow' if allowed else 'deny',
                                           'permissionDecisionReason': reason}}

        options = ClaudeAgentOptions(
            system_prompt={'type': 'preset', 'preset': 'claude_code', 'append': call.agent.instructions},
            tools=list(call.agent.tools),           # the only tools that exist for this run
            allowed_tools=list(call.agent.tools),
            model=call.agent.model,
            cwd=str(call.cwd),
            add_dirs=[str(path) for path in call.extra_dirs],
            setting_sources=['project'],
            strict_mcp_config=True,                 # no account connectors inside factory agents
            max_turns=self.max_turns,
            hooks={'PreToolUse': [HookMatcher(matcher=None, hooks=[gate])]},
        )
        texts, result = [], None
        call.transcript.parent.mkdir(parents=True, exist_ok=True)
        with open(call.transcript, 'w') as stream:
            async for message in query(prompt=call.prompt, options=options):
                stream.write(json.dumps(plain(message), default=str) + '\n')
                if isinstance(message, AssistantMessage):
                    texts += [block.text for block in message.content if isinstance(block, TextBlock)]
                elif isinstance(message, ResultMessage):
                    result = message
        if result is None:
            return RunResult('\n'.join(texts), is_error=True)
        return RunResult(text=result.result or '\n'.join(texts), cost_usd=result.total_cost_usd or 0.0,
                         turns=result.num_turns, duration_ms=result.duration_ms,
                         session_id=result.session_id, is_error=result.is_error)
