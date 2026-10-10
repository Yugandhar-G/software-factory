"""Agent definitions: the plugin's agents/*.md is the single source for every role."""
from dataclasses import dataclass
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[2]
AGENTS = PLUGIN / 'agents'


@dataclass(frozen=True)
class AgentSpec:
    name: str
    description: str
    tools: tuple
    model: str | None
    instructions: str


def parse(path):
    # Claude Code substitutes the plugin root in agent bodies; SDK sessions get the same text.
    text = Path(path).read_text().replace('${CLAUDE_PLUGIN_ROOT}', str(PLUGIN))
    if not text.startswith('---\n'):
        raise ValueError(f'{path.name}: missing frontmatter')
    head, _, body = text[4:].partition('\n---\n')
    meta = {}
    for line in head.splitlines():
        key, sep, value = line.partition(':')
        if sep:
            meta[key.strip()] = value.strip()
    if meta.get('name') != path.stem:
        raise ValueError(f'{path.name}: name must match the file name')
    tools = tuple(tool.strip() for tool in meta.get('tools', '').split(',') if tool.strip())
    if not tools:
        raise ValueError(f'{path.name}: no tools listed')
    model = meta.get('model') or None
    return AgentSpec(meta['name'], meta.get('description', ''), tools,
                     None if model == 'inherit' else model, body.strip())


def load(directory=AGENTS):
    return {spec.name: spec for spec in map(parse, sorted(Path(directory).glob('*.md')))}
