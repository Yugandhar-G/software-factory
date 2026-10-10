"""Where a project's factory lives: its root, config, profile and job state.

The plugin holds the code; each project holds its own state. Job state goes to
<project>/.factory by default (FACTORY_HOME or .factory/config.json
"state_dir" override it), and worktrees inside a project resolve to the main
checkout, so every session of one repository shares one job store.
"""
import json
import os
from pathlib import Path
import re
import subprocess

PLUGIN = Path(__file__).resolve().parents[2]
DEFAULTS = {'state_dir': '.factory', 'branch_prefix': 'factory/', 'worktrees_dir': '.worktrees'}
ROLES = ('spec-writer', 'builder', 'observer', 'approver', 'reviewer-code', 'reviewer-security',
         'reviewer-ux', 'reviewer-ui', 'reviewer-data')
PROFILE = Path('.factory') / 'profile.md'


def git(cwd, *args):
    result = subprocess.run(['git', '-C', str(cwd), *args], capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else None


def project_root(start=None):
    """The main checkout of the repository containing start (default: the current directory)."""
    start = Path(start or Path.cwd()).resolve()
    common = git(start, 'rev-parse', '--path-format=absolute', '--git-common-dir')
    if common is None:
        return start
    if Path(common).name == '.git':
        return Path(common).parent
    # separate git dir or submodule: the working tree's top level is the project
    return Path(git(start, 'rev-parse', '--show-toplevel') or start)


def config(project):
    path = Path(project) / '.factory' / 'config.json'
    loaded = json.loads(path.read_text()) if path.is_file() else {}
    unknown = set(loaded) - set(DEFAULTS)
    if unknown:
        raise ValueError(f'{path}: unknown keys {sorted(unknown)}')
    return {**DEFAULTS, **loaded}


def state_root(project=None):
    if os.environ.get('FACTORY_HOME'):
        return Path(os.environ['FACTORY_HOME']).resolve()
    project = project_root(project)
    return (project / config(project)['state_dir']).resolve()


def profile_path(project):
    return Path(project) / PROFILE


def sections(text):
    """Profile sections by lowercased heading. Repeated headings add up; fenced blocks are body text."""
    found, name, fenced = {}, None, False
    for line in text.splitlines():
        if line.lstrip().startswith('```'):
            fenced = not fenced
        heading = None if fenced else re.fullmatch(r'## +(.+?)\s*', line)
        if heading:
            name = heading.group(1).strip().lower()
            found.setdefault(name, [])
        elif name is not None:
            found[name].append(line)
    return {key: '\n'.join(lines).strip() for key, lines in found.items()}


def profile(project, role, text=None):
    """The project's rules for one role: its "All roles" section plus the role's own section."""
    if text is None:
        path = profile_path(project)
        text = path.read_text() if path.is_file() else ''
    found = sections(text)
    parts = [(title, found.get(title.lower(), '')) for title in ('All roles', role)]
    return '\n\n'.join(f'### {title}\n{body}' for title, body in parts if body)


def problems(text):
    """Why a profile would give agents less than it should; empty when it looks filled in."""
    if not text.strip():
        return ['no .factory/profile.md: agents run on generic rules only (run `factory init`)']
    found = sections(text)
    issues = [f'heading "## {name}" matches no role' for name in found
              if name not in {'all roles', *ROLES}]
    placeholders = [name for name, body in found.items() if re.search(r'<[^>\n]+>', body)]
    if placeholders:
        issues.append('unfilled <placeholders> in: ' + ', '.join(placeholders))
    return issues
