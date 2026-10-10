#!/usr/bin/env python3
"""PreToolUse hook: block commands that throw away work with no trace.

Deleting code on purpose is fine; guard.py makes the agent record why. This
hook only stops bulk operations that discard uncommitted edits, untracked
files, stashes or history, which cannot be explained after the fact. Applies
to the main session and every subagent. Exit 2 blocks the call. A safety net,
not a complete shell parser.
"""
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys

SCRATCH = ('/tmp/', '/private/tmp/', '/var/folders/', '/private/var/folders/')
GIT_RULES = [
    (r'\bgit\s+reset\b.*--hard', 'git reset --hard discards work'),
    (r'\bgit\s+clean\b', 'git clean deletes untracked files'),
    (r'\bgit\s+checkout\b.*(\s--\s|\s\.(\s|$))', 'git checkout -- discards edits'),
    (r'\bgit\s+restore\b(?!.*--staged)', 'git restore discards edits'),
    (r'\bgit\s+push\b.*(\s-f\b|--force)', 'force push rewrites shared history'),
    (r'\bgit\s+branch\b.*\s-D\b', 'git branch -D drops unmerged work'),
    (r'\bgit\s+worktree\s+remove\b.*(\s-f\b|--force)', 'forced worktree removal drops edits'),
    (r'\bgit\s+stash\s+(drop|clear)\b', 'dropping a stash loses work'),
    (r'\bgit\s+(filter-branch|filter-repo)\b', 'history rewrite'),
    (r'\bgit\s+update-ref\s+-d\b', 'deleting a ref'),
]

GIT_GLOBALS = re.compile(
    r'\bgit((?:\s+(?:-C|-c)\s+\S+'
    r'|\s+--(?:git-dir|work-tree|namespace|exec-path|config-env)(?:=\S+|\s+\S+)'
    r'|\s+(?:--no-pager|--paginate|-p|-P|--bare|--literal-pathspecs|--no-replace-objects))*)(?=\s)')


def without_git_globals(command):
    """`git -C dir reset --hard` -> `git reset --hard`, so the rules see the subcommand."""
    return GIT_GLOBALS.sub('git', command)


def block(reason):
    print(f'Blocked by factory work-loss guard: {reason}. Delete specific files or code '
          'instead and record why in the job\'s removals.md.', file=sys.stderr)
    sys.exit(2)

def scratch(target, cwd):
    resolved = str((Path(cwd) / target).resolve()) + '/'
    return resolved.startswith(SCRATCH) or '__pycache__' in resolved or resolved.endswith('.pyc/')

def tracked(target, cwd):
    path = (Path(cwd) / target).resolve()
    probe = path if path.is_dir() else path.parent
    if not probe.exists():
        return False
    result = subprocess.run(['git', '-C', str(probe), 'ls-files', '--error-unmatch', str(path)],
                            capture_output=True, text=True)
    return result.returncode == 0

def check_bash(command, cwd):
    normalized = without_git_globals(command)
    for pattern, reason in GIT_RULES:
        if re.search(pattern, normalized):
            block(reason)
    for segment in re.split(r'&&|\|\||;|\||\n', command):
        try:
            words = shlex.split(segment)
        except ValueError:
            words = segment.split()
        if not words:
            continue
        name = Path(words[0]).name
        if name in {'rm', 'unlink', 'rmdir', 'shred'}:
            flags = [w for w in words[1:] if w.startswith('-')]
            targets = [w for w in words[1:] if not w.startswith('-')]
            recursive = any(set(f) & {'r', 'R'} or f == '--recursive' for f in flags)
            for target in targets:
                if (recursive or '*' in target) and (tracked(target, cwd) or not scratch(target, cwd)):
                    block(f'{name} {target} bulk-removes project files')
        if name == 'find' and ('-delete' in words or any(w in {'rm', '/bin/rm'} for w in words)):
            roots = [w for w in words[1:] if not w.startswith('-')][:1] or ['.']
            if tracked(roots[0], cwd) or not scratch(roots[0], cwd):
                block('find -delete removes project files')
        if name == 'mv' and len(words) >= 3 and words[-1] in {'/dev/null'}:
            block('moving files to /dev/null')

def main():
    try:
        event = json.load(sys.stdin)
    except json.JSONDecodeError:
        return
    if event.get('tool_name') == 'Bash':
        check_bash((event.get('tool_input') or {}).get('command', ''), event.get('cwd') or '.')

if __name__ == '__main__': main()
