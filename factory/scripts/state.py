#!/usr/bin/env python3
"""Durable software-factory artifacts; no builds, Git mutations or external calls."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
from home import state_root  # noqa: E402

STAGES = {'spec', 'build', 'review', 'approve', 'ready', 'blocked', 'delivered'}
LENSES = {'code', 'security', 'ux', 'ui', 'data'}
VERDICTS = {'PASS', 'CHANGES', 'SKIP', 'BLOCKED'}

def timestamp():
    return datetime.now(timezone.utc).isoformat()

def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.state-')
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, indent=2)
            stream.write('\n')
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)

def job_path(root, job_id):
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,100}', job_id):
        raise ValueError('Invalid job id')
    path = root / 'jobs' / job_id
    if path.is_symlink():
        raise ValueError('Job directory cannot be a symlink')
    return path

def load(root, job_id):
    path = job_path(root, job_id)
    return path, json.loads((path / 'job.json').read_text())

def create(root, feature):
    slug = re.sub(r'[^a-z0-9]+', '-', feature.lower()).strip('-')[:45] or 'feature'
    job_id = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-') + slug + '-' + uuid.uuid4().hex[:6]
    path = job_path(root, job_id)
    path.mkdir(parents=True)
    state = dict(id=job_id, feature=feature, stage='spec', round=0,
                 worktree=None, branch=None, base_sha=None, reviewed_head=None,
                 assigned=[], required=[], reviews={}, blocker=None,
                 delivery=None, history=[], updated_at=timestamp())
    save(path / 'job.json', state)
    return state

def verdict(path, label, head):
    text = path.read_text()
    lines = text.splitlines()
    first = lines[0] if lines else ''
    prefix = 'VERDICT: ' if label == 'review' else ''
    value = first.removeprefix(prefix)
    choices = VERDICTS if label == 'review' else {'READY', 'CHANGES', 'BLOCKED'}
    if not first.startswith(prefix) or value not in choices:
        raise ValueError(f'Invalid {label} first line in {path.name}')
    if f'HEAD: {head}' not in lines:
        raise ValueError(f'{path.name} does not attest reviewed HEAD')
    if value == 'SKIP' and not any(line.startswith('Reason: ') and line[8:].strip() for line in lines):
        raise ValueError('SKIP requires a reason')
    return value

def observed(path, head):
    """The independent observer's final verdict; only the orchestrator relays it."""
    report = path / 'observer' / 'final.md'
    if not report.is_file():
        raise ValueError('Observer has not validated this job (observer/final.md missing)')
    lines = report.read_text().splitlines()
    first = lines[0] if lines else ''
    if first not in {'OBSERVER: DONE', 'OBSERVER: NOT_DONE', 'OBSERVER: BLOCKED'}:
        raise ValueError('Invalid observer first line in final.md')
    if f'HEAD: {head}' not in lines:
        raise ValueError('Observer did not validate this HEAD')
    if first != 'OBSERVER: DONE':
        raise ValueError(f'{first.removeprefix("OBSERVER: ")}: observer says the task is not done')

def guarded(path, head):
    """guard.py must have audited this exact HEAD with every removal explained."""
    report = path / 'guard.json'
    if not report.is_file():
        raise ValueError('Deletion audit missing; run factory/scripts/guard.py')
    result = json.loads(report.read_text())
    if result.get('head') != head:
        raise ValueError('Deletion audit is for another HEAD; rerun guard.py')
    if result.get('status') != 'ok':
        raise ValueError('Code removed without a recorded reason; see guard.json')

def archive_decision(path, tag):
    decision = path/'decision.md'
    if decision.exists():
        target, n = path/f'decision-{tag}.md', 1
        while target.exists():
            n += 1; target = path/f'decision-{tag}-{n}.md'
        decision.rename(target)

def check(root, job_id, head, state=None):
    path, loaded = load(root, job_id)
    state = state or loaded
    if not state['assigned'] or not state['required']:
        raise ValueError('No review round configured')
    if state['reviewed_head'] != head:
        raise ValueError('HEAD changed; start a new review round')
    results = {}
    for lens in state['assigned']:
        report = path / f'round-{state["round"]}' / f'review-{lens}.md'
        if not report.is_file():
            raise ValueError(f'WAIT FOR ALL: missing {report.name}')
        results[lens] = verdict(report, 'review', head)
    for lens, result in results.items():
        if result in {'CHANGES', 'BLOCKED'} or (lens in state['required'] and result != 'PASS'):
            raise ValueError(f'{lens}: {result}; not ready')
    return results

def mutate(root, job_id, operation):
    path = job_path(root, job_id)
    lock = path / '.state.lock'
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise ValueError('Another state writer holds the lock; inspect its owner before recovery')
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(f'{os.getpid()}\n')
        _, state = load(root, job_id)
        before = state['stage']
        operation(path, state)
        state['history'].append(dict(at=timestamp(), before=before, after=state['stage']))
        state['updated_at'] = timestamp()
        save(path / 'job.json', state)
        return state
    finally:
        lock.unlink()

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, help='factory state directory (default: <project>/.factory)')
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('new'); p.add_argument('feature')
    p = sub.add_parser('list')
    for name in ['show', 'check', 'bind', 'stage', 'round', 'ready', 'restore-review', 'delivered']:
        p = sub.add_parser(name); p.add_argument('job')
        if name in ['check', 'round', 'ready', 'restore-review']: p.add_argument('--head', required=True)
        if name == 'bind':
            for field in ['worktree', 'branch', 'base']: p.add_argument('--'+field, required=True)
        if name == 'stage':
            p.add_argument('value', choices=['spec', 'build', 'approve', 'blocked'])
            p.add_argument('--reason')
        if name == 'round':
            p.add_argument('--assigned', nargs='+', choices=sorted(LENSES), required=True)
            p.add_argument('--required', nargs='+', choices=sorted(LENSES), required=True)
        if name == 'delivered': p.add_argument('--evidence', required=True)
    args = parser.parse_args()
    try:
        root = (args.root or state_root()).resolve()
        if args.command == 'new': result = create(root, args.feature)
        elif args.command == 'list':
            result = [json.loads(p.read_text()) for p in sorted((root/'jobs').glob('*/job.json'))]
        elif args.command == 'show': result = load(root, args.job)[1]
        elif args.command == 'check': result = check(root, args.job, args.head)
        else:
            def operation(path, state):
                if state['stage'] == 'delivered': raise ValueError('Delivered job is immutable; create a new job')
                if args.command == 'bind':
                    worktree = Path(args.worktree)
                    if not worktree.is_absolute() or not worktree.is_dir():
                        raise ValueError('Worktree must be an existing absolute directory')
                    binding = dict(worktree=str(worktree), branch=args.branch, base_sha=args.base)
                    changed = any(state[key] != value for key, value in binding.items())
                    state.update(binding)
                    if changed and state['round']:
                        state.update(stage='build', reviewed_head=None, reviews={}, blocker=None)
                elif args.command == 'stage':
                    if args.value == 'blocked' and not args.reason: raise ValueError('Blocked needs a reason')
                    if args.value == 'approve':
                        state['reviews'] = check(root, args.job, state['reviewed_head'])
                    if args.value in {'spec', 'build'}:
                        # Rework always needs a fresh approval. Reviews attest a HEAD and check()
                        # re-verifies it, so a rebuild that leaves HEAD unchanged keeps them;
                        # a new spec changes what was reviewed against, so it drops them.
                        archive_decision(path, f'before-{args.value}')
                    if args.value == 'spec':
                        state.update(reviewed_head=None, reviews={})
                    state.update(stage=args.value, blocker=args.reason)
                elif args.command == 'restore-review':
                    # Recovery for jobs whose reviewed HEAD was dropped by a rebuild that changed
                    # nothing: allowed only when every report of the current round attests --head.
                    if not state['round']: raise ValueError('No review round to restore')
                    previous = state['reviewed_head']; state['reviewed_head'] = args.head
                    try:
                        state['reviews'] = check(root, args.job, args.head, state)
                    except ValueError:
                        state['reviewed_head'] = previous; raise
                elif args.command == 'round':
                    if not state['worktree']: raise ValueError('Bind worktree first')
                    if not (path/'spec.md').is_file() or not (path/'build.md').is_file():
                        raise ValueError('spec.md and build.md required')
                    build_lines = (path/'build.md').read_text().splitlines()
                    if not build_lines or build_lines[0] != 'STATUS: READY':
                        raise ValueError('Builder is not ready')
                    if not set(args.required) <= set(args.assigned) or not {'code','security'} <= set(args.required):
                        raise ValueError('Assigned must include required; code/security are required')
                    if state['round'] >= 3: raise ValueError('Three rounds exhausted; retain unresolved findings')
                    number = state['round']+1; (path/f'round-{number}').mkdir()
                    # guard.json and observer/final.md run before a round and attest HEAD; keep them
                    archive_decision(path, f'before-round-{number}')
                    state.update(stage='review', round=number, reviewed_head=args.head,
                                 assigned=list(dict.fromkeys(args.assigned)), required=list(dict.fromkeys(args.required)),
                                 reviews={}, blocker=None)
                elif args.command == 'ready':
                    reviews = check(root, args.job, args.head)
                    if verdict(path/'decision.md','decision',args.head) != 'READY':
                        raise ValueError('Approver did not declare READY')
                    guarded(path, args.head)
                    observed(path, args.head)
                    state.update(stage='ready', reviews=reviews, blocker=None)
                elif args.command == 'delivered':
                    if state['stage'] != 'ready': raise ValueError('Delivery requires ready state')
                    state.update(stage='delivered', delivery=args.evidence)
            result = mutate(root, args.job, operation)
        print(json.dumps(result, indent=2))
    except (ValueError, OSError, json.JSONDecodeError) as error:
        parser.exit(1, f'{error}\n')

if __name__ == '__main__': main()
