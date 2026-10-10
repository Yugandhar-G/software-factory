"""Per-role tool policy, enforced in code on every tool call by the runner's gate.

Instructions tell agents what not to touch; this makes it true. A role may use
only the tools its definition lists, write only its own artifacts, and never see
paths hidden from it. The observer cannot read any job artifact except job.json,
guard.json and its own folder; no other role can read the observer's folder or
the run transcripts. Shell commands go through the same work-loss check as the
project hook, and every role except the builder gets a read-only shell.
Heuristic for the shell, exact for file tools.
"""
from dataclasses import dataclass
import json
from pathlib import Path
import re
import subprocess
import sys

HOOK = Path(__file__).resolve().parents[1] / 'scripts' / 'destructive_guard.py'
PATH_KEYS = {'Read': 'file_path', 'Write': 'file_path', 'Edit': 'file_path', 'MultiEdit': 'file_path',
             'NotebookEdit': 'notebook_path', 'Grep': 'path', 'Glob': 'path'}
WRITE_TOOLS = {'Write', 'Edit', 'MultiEdit', 'NotebookEdit'}
SAFE_REDIRECT = re.compile(r'\d?>&\d|\d?>>?\s*/dev/null')
MUTATING = re.compile(
    r'>|\btee\b|\bsed\s+-i|\b(mv|cp|rm|rmdir|touch|mkdir|chmod|ln|truncate|dd)\s'
    r'|\bgit\s+(add|commit|push|merge|rebase|reset|checkout|switch|stash|tag|worktree|apply|am'
    r'|cherry-pick|revert|clean|restore|rm|mv)\b|\bgit\s+branch\s+(?!--show-current)'
    r'|\b(pip|pip3|npm|brew)\s+install\b')
ARTIFACT_NAMES = re.compile(r'spec(-blocked)?\.md|build\.md|evidence\.md|decision(-before-[a-z0-9-]+)?\.md'
                            r'|review-[a-z]+\.md|removals\.md|round-\d|runs/|runs\.jsonl')


def within(path, base):
    return path == base or base in path.parents


def work_loss(command, cwd):
    """Run the project hook's check; returns its block message or None."""
    event = json.dumps({'tool_name': 'Bash', 'tool_input': {'command': command}, 'cwd': str(cwd)})
    result = subprocess.run([sys.executable, str(HOOK)], input=event, capture_output=True, text=True)
    return (result.stderr.strip() or 'blocked') if result.returncode == 2 else None


@dataclass(frozen=True)
class Policy:
    role: str
    cwd: Path
    tools: frozenset
    writable: tuple = ()
    hidden: tuple = ()
    visible: tuple = ()        # exceptions inside hidden
    read_only_shell: bool = True
    shell_forbidden: re.Pattern | None = None

    def resolve(self, value):
        path = Path(value).expanduser()
        return (path if path.is_absolute() else self.cwd / path).resolve()

    def is_hidden(self, path):
        if any(within(path, seen) for seen in self.visible):
            return False
        return any(within(path, secret) for secret in self.hidden)

    def decide(self, tool, data):
        """(allowed, reason) for one tool call."""
        if tool not in self.tools:
            return False, f'{self.role} may not use {tool}'
        key = PATH_KEYS.get(tool)
        if key and data.get(key):
            path = self.resolve(data[key])
            if self.is_hidden(path):
                return False, f'{self.role} may not access {path}'
            if tool in WRITE_TOOLS and not any(within(path, target) for target in self.writable):
                return False, f'{self.role} may only write: ' + ', '.join(map(str, self.writable))
        if tool in {'Glob', 'Grep'}:
            pattern = ' '.join(str(data.get(name, '')) for name in ('pattern', 'glob'))
            if any(str(secret) in pattern for secret in self.hidden):
                return False, f'{self.role} may not search hidden paths'
        if tool == 'Bash':
            command = data.get('command', '')
            scrubbed = command
            for seen in self.visible:
                scrubbed = scrubbed.replace(str(seen), '')
            if any(str(secret) in scrubbed for secret in self.hidden) or \
                    (self.shell_forbidden and self.shell_forbidden.search(scrubbed)):
                return False, f'{self.role} may not read those job files from the shell'
            if self.read_only_shell and MUTATING.search(SAFE_REDIRECT.sub('', command)):
                return False, f'{self.role} has a read-only shell; write only your report with the Write tool'
            blocked = work_loss(command, self.cwd)
            if blocked:
                return False, blocked
        return True, 'allowed by factory policy'


def for_role(role, tools, *, job_dir, worktree, report=None, protected=()):
    """The policy for one agent run. report is the single artifact a read-only role writes;
    protected paths (the project's profile and config, the job's profile snapshot) are hidden
    from the builder, the one role with a writable shell, so it cannot rewrite another role's rules."""
    job_dir, worktree = Path(job_dir).resolve(), Path(worktree).resolve()
    observer, transcripts = job_dir / 'observer', job_dir / 'runs'
    tools = frozenset(tools)
    if role == 'observer':
        return Policy(role, worktree, tools, writable=(observer,), hidden=(job_dir,),
                      visible=(job_dir / 'job.json', job_dir / 'guard.json', observer),
                      shell_forbidden=ARTIFACT_NAMES)
    hidden = (observer, transcripts)
    if role == 'builder':
        return Policy(role, worktree, tools, read_only_shell=False,
                      hidden=hidden + tuple(Path(path).resolve() for path in protected),
                      writable=(worktree, job_dir / 'build.md', job_dir / 'removals.md'))
    if report is None:
        raise ValueError(f'{role} needs its report path')
    return Policy(role, worktree, tools, writable=(Path(report).resolve(),), hidden=hidden)
