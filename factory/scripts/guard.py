#!/usr/bin/env python3
"""Removal audit: every deletion since base must be explained before review.

Removing code is allowed. What is not allowed is removing it blind and letting
an old bug return. Compares the job's base SHA with the live worktree (committed
and uncommitted) and requires a line in the job's removals.md for each deleted
file, removed function/class/method or bulk deletion:

  - <path or path::Symbol> | existed-for: <sha> <what it was protecting>
    | why-safe: <why that reason no longer applies> | regression-check: <test>

`existed-for` must name a real commit (found with git log -S/-L or blame), so
the writer has read the history. Read-only on Git; writes guard.json.
"""
import argparse
import ast
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from home import state_root  # noqa: E402
from state import load, save, timestamp  # noqa: E402

BULK_LINES = 20      # a file losing more than this many lines...
BULK_SHARE = 0.5     # ...and more than this share of its base length is flagged

def git(worktree, *args):
    result = subprocess.run(['git', '-C', str(worktree), *args], capture_output=True, text=True)
    if result.returncode:
        raise ValueError(f'git {" ".join(args)} failed: {result.stderr.strip()}')
    return result.stdout

FIELDS = ('existed-for', 'why-safe', 'regression-check')

def ledger(path, worktree):
    """Map each explained removal key to its fields; malformed lines are reported."""
    entries, problems = {}, []
    for line in path.read_text().splitlines() if path.is_file() else []:
        if not line.lstrip().startswith('- '):
            continue
        key, *parts = [part.strip() for part in line.lstrip()[2:].split('|')]
        fields = dict(part.split(':', 1) for part in parts if ':' in part)
        fields = {name.strip().lower(): value.strip() for name, value in fields.items()}
        missing = [name for name in FIELDS if not fields.get(name)]
        sha = (fields.get('existed-for', '').split() or [''])[0]
        if not missing and subprocess.run(['git', '-C', str(worktree), 'cat-file', '-e', sha + '^{commit}'],
                                          capture_output=True).returncode:
            missing.append(f'existed-for commit {sha} not found')
        if missing:
            problems.append(f'{key.strip("`")}: missing {", ".join(missing)}')
        else:
            entries[key.strip('`')] = fields
    return entries, problems

def symbols(source):
    names = set()
    for node in ast.parse(source).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        if isinstance(node, ast.ClassDef):
            names.update(f'{node.name}.{item.name}' for item in node.body
                         if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)))
    return names

def audit(worktree, base):
    findings = []
    changes = git(worktree, 'diff', '--name-status', '-M', base).splitlines()
    numstat = {}
    for row in git(worktree, 'diff', '--numstat', '-M', base).splitlines():
        added, removed, name = row.split('\t', 2)
        numstat[name.split(' => ')[-1].rstrip('}')] = (added, removed)
    for row in changes:
        parts = row.split('\t')
        status, old, new = parts[0][0], parts[1], parts[-1]
        if status == 'D':
            findings.append(dict(kind='deleted_file', path=old, approve=old))
            continue
        if status not in {'M', 'R'}:
            continue
        before = git(worktree, 'show', f'{base}:{old}')
        current = Path(worktree, new)
        after = current.read_text(errors='replace') if current.is_file() else ''
        if old.endswith('.py'):
            try:
                gone = symbols(before) - symbols(after)
            except SyntaxError as error:
                findings.append(dict(kind='unparseable', path=new, detail=str(error), approve=new))
                gone = set()
            findings += [dict(kind='removed_symbol', path=new, symbol=name, approve=f'{new}::{name}')
                         for name in sorted(gone)]
        added, removed = numstat.get(new, ('0', '0'))
        base_lines = before.count('\n') or 1
        if removed != '-' and int(removed) - int(added) > BULK_LINES \
                and (int(removed) - int(added)) / base_lines > BULK_SHARE:
            findings.append(dict(kind='bulk_removal', path=new, net_removed=int(removed) - int(added),
                                 base_lines=base_lines, approve=new))
    return findings

def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--root', type=Path, help='factory state directory (default: <project>/.factory)')
    parser.add_argument('job')
    parser.add_argument('--head', required=True)
    args = parser.parse_args()
    try:
        path, state = load((args.root or state_root()).resolve(), args.job)
        if not state['worktree'] or not state['base_sha']:
            raise ValueError('Bind worktree and base first')
        worktree = Path(state['worktree'])
        actual = git(worktree, 'rev-parse', 'HEAD').strip()
        if actual != args.head:
            raise ValueError(f'Worktree HEAD is {actual}, not {args.head}')
        explained, problems = ledger(path / 'removals.md', worktree)
        findings = audit(worktree, state['base_sha'])
        for item in findings:
            item['explained'] = item['approve'] in explained or item['path'] in explained
        unexplained = [item for item in findings if not item['explained']]
        result = dict(head=args.head, base=state['base_sha'], at=timestamp(),
                      status='unexplained' if unexplained or problems else 'ok',
                      findings=findings, ledger_problems=problems)
        save(path / 'guard.json', result)
        print(json.dumps(result, indent=2))
        if unexplained or problems:
            parser.exit(1, f'{len(unexplained)} unexplained removal(s), {len(problems)} incomplete '
                           'ledger line(s): find why the code existed (git log -S/-L, blame), confirm '
                           'the old bug cannot return, and record it in removals.md\n')
    except (ValueError, OSError, json.JSONDecodeError) as error:
        parser.exit(1, f'{error}\n')

if __name__ == '__main__': main()
