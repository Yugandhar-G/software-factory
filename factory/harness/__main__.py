"""Run the factory graph on the Claude Agent SDK.

  factory run "<the user's task, verbatim>"
  factory resume <job-id>
  factory status <job-id>
  factory init

(bin/factory wraps python -m factory.harness.) Run inside the project; the
project root is the main checkout of the current Git repository, or --project.
run/resume need claude-agent-sdk (bin/factory setup) and a logged-in Claude
Code; status and init need neither. Delivery (PR, merge) stays a separate,
user-authorized step after the job reaches ready.
"""
import argparse
import asyncio
from collections import Counter
import json
from pathlib import Path

from ..scripts import home
from .graph import Limits, Orchestrator, StateError

TEMPLATE = home.PLUGIN / 'templates' / 'profile.md'


def status(root, job_id):
    orchestrator = Orchestrator(root, runner=None)  # reads state only; no SDK needed
    job = orchestrator.state('show', job_id)
    runs = orchestrator.ledger(orchestrator.job_dir(job_id))
    print(json.dumps(dict(id=job['id'], stage=job['stage'], round=job['round'], branch=job['branch'],
                          worktree=job['worktree'], blocker=job['blocker'],
                          runs=dict(Counter(run['agent'] for run in runs)),
                          spent_usd=round(sum(run['cost_usd'] for run in runs), 4)), indent=2))


def init(root):
    """Give a project its profile and keep job state and worktrees out of Git. Never overwrites."""
    created = []
    profile = home.profile_path(root)
    if not profile.exists():
        profile.parent.mkdir(parents=True, exist_ok=True)
        profile.write_text(TEMPLATE.read_text())
        created.append(str(profile))
    config = home.config(root)
    ignored = (f'/{config["worktrees_dir"].strip("/")}/', f'/{config["state_dir"].strip("/")}/jobs/')
    gitignore = root / '.gitignore'
    present = gitignore.read_text().splitlines() if gitignore.is_file() else []
    missing = [line for line in ignored if line not in present]
    if missing:
        with open(gitignore, 'a') as stream:
            stream.write(('\n' if present and present[-1] else '') + '# software factory\n' + '\n'.join(missing) + '\n')
        created.append(f'{gitignore} (+{", ".join(missing)})')
    print(json.dumps(dict(project=str(root), state=str(home.state_root(root)), config=config,
                          created=created), indent=2))
    if created:
        print(f'Fill in {profile}: the reviewers and observer apply it on every job.')


def main():
    parser = argparse.ArgumentParser(prog='factory', description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument('--project', type=Path, help='project root (default: the current Git repository)')
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('run', 'resume'):
        command = sub.add_parser(name, parents=[common])
        command.add_argument('task' if name == 'run' else 'job')
        command.add_argument('--budget-usd', type=float, default=Limits.budget_usd)
        command.add_argument('--max-builds', type=int, default=Limits.max_builds)
        command.add_argument('--max-observer-passes', type=int, default=Limits.max_observer_passes)
        command.add_argument('--parallel-reviews', type=int, default=Limits.parallel_reviews)
    sub.add_parser('status', parents=[common]).add_argument('job')
    sub.add_parser('init', parents=[common])
    args = parser.parse_args()
    try:
        root = home.project_root(args.project)
        if args.command == 'status':
            return status(root, args.job)
        if args.command == 'init':
            return init(root)
        from .runner import SDKRunner
        try:
            import claude_agent_sdk  # noqa: F401  (fail before creating a job or worktree)
        except ImportError:
            parser.exit(1, 'claude-agent-sdk is not installed. Run:\n'
                           f'  {home.PLUGIN / "bin" / "factory"} setup\n'
                           'then retry; bin/factory run/resume use that environment.\n')
        limits = Limits(args.budget_usd, args.max_builds, args.max_observer_passes, args.parallel_reviews)
        orchestrator = Orchestrator(root, SDKRunner(), limits)
        job = asyncio.run(orchestrator.start(args.task) if args.command == 'run'
                          else orchestrator.drive(args.job))
        print(json.dumps(dict(id=job['id'], stage=job['stage'], blocker=job['blocker']), indent=2))
    except (StateError, ValueError) as error:
        parser.exit(1, f'{error}\n')


if __name__ == '__main__':
    main()
