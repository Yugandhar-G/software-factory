"""The factory graph in code.

    spec-writer -> builder -> commit -> removal audit -> observer -> reviewers (parallel)
                     ^           NOT_DONE / CHANGES / unexplained removal      |
                     +-------------------------------------------------- approver -> ready

The orchestrator is this module, not a model. It alone talks to the observer,
and the observer's prompt is built only from the user's verbatim task, paths and
SHAs (observer_prompt has no other inputs). Every run is logged to the job's
runs.jsonl with cost, turns and transcript; a budget and pass limits stop
runaway loops. All job state changes go through factory/scripts/state.py.

The plugin supplies code and roles; the project supplies its state directory,
branch prefix and profile (.factory/), and each role's profile section is
appended to its instructions here, so project rules reach the agent in code.
"""
import asyncio
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import sys

from ..scripts import home
from .agents import load as load_agents
from .policy import for_role
from .runner import AgentCall

LENSES = ('code', 'security', 'ux', 'ui', 'data')
OPTIONAL_LENSES = {'ui'}


class Halt(Exception):
    """The job stops here; the message becomes its recorded blocker."""


class StateError(Exception):
    pass


@dataclass
class Limits:
    budget_usd: float = 25.0
    max_builds: int = 8
    max_observer_passes: int = 5
    parallel_reviews: int = 3


def first_line(path):
    lines = Path(path).read_text().splitlines() if Path(path).is_file() else []
    return lines[0].strip() if lines else ''


def section(text, heading):
    match = re.search(rf'^## {re.escape(heading)}\s*\n(.*?)(?=^## |\Z)', text, re.M | re.S)
    return match.group(1).strip() if match else ''


def blocker(path, who):
    """A short blocker: the report's Reason line (or its opening), plus where to read more."""
    text = Path(path).read_text() if Path(path).is_file() else ''
    reason = next((line[len('Reason:'):].strip() for line in text.splitlines() if line.startswith('Reason:')), '')
    return f'{who}: {reason or text[:400].strip()} (see {path})'


def lenses_from(spec):
    match = re.search(r'^Review lenses?:\s*(.+)$', spec.read_text(), re.M | re.I) if spec.is_file() else None
    named = re.findall(r'[a-z]+', match.group(1).lower()) if match else []
    return list(dict.fromkeys(['code', 'security'] + [lens for lens in named if lens in LENSES]))


def observer_prompt(task, root, job_dir, worktree, base, head, number):
    """Everything the observer is told. Nothing from any other agent goes in."""
    return (f'Did we accomplish the given task end to end?\n\n'
            f'Given task (the user\'s words, verbatim):\n<<<\n{task}\n>>>\n\n'
            f'Project root: {root}\nJob directory: {job_dir}\nWorktree: {worktree}\n'
            f'Base SHA: {base}\nCurrent HEAD: {head}\nPass: {number}\n\n'
            f'Write {job_dir}/observer/pass-{number}.md and the same content to '
            f'{job_dir}/observer/final.md.')


class Orchestrator:
    def __init__(self, root, runner, limits=None, log=print, agents_dir=None):
        self.root = Path(root).resolve()
        self.config = home.config(self.root)
        self.home = home.state_root(self.root)
        self.scripts = Path(home.__file__).resolve().parent
        self.runner, self.limits, self.log = runner, limits or Limits(), log
        self.agents = load_agents(agents_dir) if agents_dir else load_agents()
        self.profiles = {}  # job id -> the profile text fixed for that job

    # -- plumbing ---------------------------------------------------------
    def state(self, *args):
        result = subprocess.run([sys.executable, str(self.scripts / 'state.py'),
                                 '--root', str(self.home), *args], capture_output=True, text=True)
        if result.returncode:
            raise StateError(result.stderr.strip())
        return json.loads(result.stdout)

    def git(self, cwd, *args):
        result = subprocess.run(['git', '-C', str(cwd), *args], capture_output=True, text=True)
        if result.returncode:
            raise Halt(f'git {" ".join(args)}: {result.stderr.strip()}')
        return result.stdout.strip()

    def job_dir(self, job_id):
        return self.home / 'jobs' / job_id

    def ledger(self, job_dir):
        path = job_dir / 'runs.jsonl'
        return [json.loads(line) for line in path.read_text().splitlines()] if path.is_file() else []

    def spent(self, job_dir):
        return sum(run['cost_usd'] for run in self.ledger(job_dir))

    def next_number(self, job_dir, prefix):
        return 1 + sum(1 for run in self.ledger(job_dir) if run['label'].startswith(prefix + '-'))

    async def call(self, job, name, prompt, label, report=None):
        job_dir = self.job_dir(job['id'])
        if self.spent(job_dir) >= self.limits.budget_usd:
            raise Halt(f'Budget of ${self.limits.budget_usd:.2f} spent')
        agent = self.agents[name]
        rules = home.profile(self.root, name, text=self.profile_text(job))
        if rules:  # the project's rules for this role, delivered by code rather than by instruction
            agent = replace(agent, instructions=f'{agent.instructions}\n\n## Project profile '
                            f'({home.profile_path(self.root)})\n\n{rules}')
        role = name.split('-')[0] if name.startswith('reviewer-') else name
        policy = for_role(role, agent.tools, job_dir=job_dir, worktree=job['worktree'], report=report,
                          protected=self.protected(job_dir))
        transcript = job_dir / 'runs' / f'{label}.jsonl'
        self.log(f'-> {label} ({agent.model or "session model"})')
        try:
            result = await self.runner.run(AgentCall(agent, prompt, Path(job['worktree']), policy, label,
                                                     transcript, (self.root, job_dir)))
        except Exception as error:  # SDK, auth or network failure: record it, keep the job resumable
            raise Halt(f'{label} crashed: {type(error).__name__}: {error}') from error
        with open(job_dir / 'runs.jsonl', 'a') as stream:
            stream.write(json.dumps(dict(
                at=datetime.now(timezone.utc).isoformat(), agent=name, label=label,
                cost_usd=result.cost_usd, turns=result.turns, duration_ms=result.duration_ms,
                session_id=result.session_id, is_error=result.is_error,
                transcript=str(transcript.relative_to(job_dir)))) + '\n')
        if result.is_error:
            raise Halt(f'{label} failed: {result.text[:300]}')
        return result

    def context(self, job, **extra):
        lines = [f'Project root: {self.root}', f'Job directory: {self.job_dir(job["id"])}',
                 f'Worktree: {job["worktree"]}', f'Branch: {job["branch"]}', f'Base SHA: {job["base_sha"]}']
        return '\n'.join(lines + [f'{key.replace("_", " ").capitalize()}: {value}' for key, value in extra.items()])

    def protected(self, job_dir):
        """Project rules the builder may neither read nor rewrite mid-job."""
        return (home.profile_path(self.root), self.root / '.factory' / 'config.json', job_dir / 'profile.md')

    def profile_text(self, job):
        """The job's profile, fixed when the job started: from the base commit when tracked there,
        otherwise the working copy at that moment. Nothing an agent writes later can change it."""
        if job['id'] in self.profiles:
            return self.profiles[job['id']]
        result = subprocess.run(['git', '-C', str(self.root), 'show', f'{job["base_sha"]}:{home.PROFILE.as_posix()}'],
                                capture_output=True, text=True)
        snapshot = self.job_dir(job['id']) / 'profile.md'
        if result.returncode == 0:
            text = result.stdout  # a commit cannot be rewritten by any agent
        else:
            if not snapshot.is_file():
                live = home.profile_path(self.root)
                snapshot.write_text(live.read_text() if live.is_file() else '')
                if live.is_file():
                    self.log(f'profile: {home.PROFILE} is not committed at base; using a job snapshot. '
                             'Commit it so no agent can alter the rules mid-job.')
            text = snapshot.read_text()
        for issue in home.problems(text):
            self.log(f'profile: {issue}')
        self.profiles[job['id']] = text
        return text

    # -- entry points -----------------------------------------------------
    async def start(self, task):
        jobs = self.home / 'jobs'
        probe = subprocess.run(['git', '-C', str(self.root), 'check-ignore', '-q', str(jobs / 'probe' / 'job.json')])
        if probe.returncode != 0:
            raise StateError(f'{jobs} is not gitignored; run `factory init` (or ignore it) so job '
                             'artifacts never land in a commit')
        job = self.state('new', task)
        base = self.git(self.root, 'rev-parse', 'HEAD')
        worktree = self.root / self.config['worktrees_dir'] / job['id']
        branch = self.config['branch_prefix'] + job['id'].split('-', 2)[2]
        self.git(self.root, 'worktree', 'add', '-q', '-b', branch, str(worktree), base)
        self.state('bind', job['id'], '--worktree', str(worktree), '--branch', branch, '--base', base)
        self.log(f'job {job["id"]} on {branch}')
        return await self.drive(job['id'])

    async def drive(self, job_id):
        try:
            job = self.state('show', job_id)
            if job['stage'] in {'ready', 'delivered'}:
                return job
            if not job['worktree']:
                raise Halt('Job has no bound worktree')
            if not (self.job_dir(job_id) / 'spec.md').is_file():
                await self.write_spec(job)
            await self.loop(job_id)
        except Halt as halt:
            self.log(f'blocked: {halt}')
            try:
                self.state('stage', job_id, 'blocked', '--reason', str(halt)[:2000])
            except StateError:
                pass
        return self.state('show', job_id)

    # -- nodes ------------------------------------------------------------
    async def write_spec(self, job):
        spec = self.job_dir(job['id']) / 'spec.md'
        prompt = (f'{self.context(job)}\n\nGiven task (verbatim):\n<<<\n{job["feature"]}\n>>>\n\n'
                  f'Write {spec}. Include a line "Review lenses: code, security" plus ux, data or ui '
                  'where they apply, and an "## Expected removals" section. If the task is not '
                  'actionable (empty, a placeholder, or missing a decision only the user can make), '
                  'make the first line "STATUS: BLOCKED" and the second "Reason: <the question for '
                  'the user>", and stop.')
        await self.call(job, 'spec-writer', prompt, f'spec-{self.next_number(self.job_dir(job["id"]), "spec")}', spec)
        if not spec.is_file():
            raise Halt('spec-writer did not write spec.md')
        if first_line(spec) == 'STATUS: BLOCKED':
            blocked = spec.with_name('spec-blocked.md')
            spec.rename(blocked)  # a resume after the user answers writes a fresh spec
            raise Halt(blocker(blocked, 'Spec needs the user'))

    async def loop(self, job_id):
        job_dir = self.job_dir(job_id)
        instructions = 'Implement the spec.'
        if self.next_number(job_dir, 'build') > 1:
            instructions = 'Resume: inspect the worktree and build.md, then finish the spec.'
        for _ in range(self.limits.max_builds):
            job = self.state('stage', job_id, 'build')
            number = self.next_number(job_dir, 'build')
            prompt = (f'{self.context(job)}\nSpec: {job_dir / "spec.md"}\n\n{instructions}\n\n'
                      f'Write {job_dir / "build.md"} beginning STATUS: READY or STATUS: BLOCKED. '
                      f'Record every removal of existing code in {job_dir / "removals.md"}. '
                      'The orchestrator commits your worktree changes.')
            await self.call(job, 'builder', prompt, f'build-{number}')
            status = first_line(job_dir / 'build.md')
            if status == 'STATUS: BLOCKED':
                raise Halt(blocker(job_dir / 'build.md', 'Builder blocked'))
            if status != 'STATUS: READY':
                raise Halt('build.md does not begin with a STATUS line')
            head = self.commit(job, number)
            # A rework that leaves HEAD where the observer and reviewers already passed it (for
            # example, the approver asked only for job-artifact fixes) goes straight back to the
            # approver: re-running them would judge identical code and burn the pass/round limits.
            unchanged = head == self.state('show', job_id)['reviewed_head']
            instructions = (self.audit(job_id, head)
                            or (None if unchanged and self.observed_done(job_dir, head) else await self.observe(job, head))
                            or (None if unchanged and self.reviews_hold(job_id, head) else await self.review(job_id, head))
                            or await self.approve(job_id, head))
            if instructions is None:
                self.state('ready', job_id, '--head', head)
                self.log(f'ready at {head[:10]}; spent ${self.spent(job_dir):.2f}')
                return
        raise Halt(f'{self.limits.max_builds} build passes without reaching ready')

    def commit(self, job, number):
        worktree = job['worktree']
        if self.git(worktree, 'status', '--porcelain'):
            self.git(worktree, 'add', '-A')
            self.git(worktree, 'commit', '-q', '-m', f'factory: build pass {number}\n\nJob: {job["id"]}')
        head = self.git(worktree, 'rev-parse', 'HEAD')
        if head == self.git(worktree, 'rev-parse', job['base_sha']):
            raise Halt('Builder reported READY without changing anything')
        return head

    def audit(self, job_id, head):
        """None when every removal is explained; otherwise instructions for the builder."""
        job_dir = self.job_dir(job_id)
        result = subprocess.run([sys.executable, str(self.scripts / 'guard.py'),
                                 '--root', str(self.home), job_id, '--head', head],
                                capture_output=True, text=True)
        if result.returncode == 0:
            return None
        report = json.loads((job_dir / 'guard.json').read_text()) if (job_dir / 'guard.json').is_file() else {}
        if report.get('head') != head:
            raise Halt(f'Removal audit failed: {result.stderr.strip()}')
        lines = [f'- {item["kind"]}: {item["approve"]}' for item in report['findings'] if not item['explained']]
        lines += [f'- incomplete removals.md line: {problem}' for problem in report['ledger_problems']]
        return ('Existing code was removed without a recorded reason. For each item, find why it '
                'existed (git log -S/-L, git blame), make sure the bug it handled stays covered, and '
                'add a removals.md line (existed-for / why-safe / regression-check), or restore it:\n'
                + '\n'.join(lines))

    @staticmethod
    def observed_done(job_dir, head):
        final = job_dir / 'observer' / 'final.md'
        return (final.is_file() and first_line(final) == 'OBSERVER: DONE'
                and f'HEAD: {head}' in final.read_text().splitlines())

    def reviews_hold(self, job_id, head):
        try:
            self.state('check', job_id, '--head', head)
            return True
        except StateError:
            return False

    async def observe(self, job, head):
        job_dir = self.job_dir(job['id'])
        number = self.next_number(job_dir, 'observer')
        final = job_dir / 'observer' / 'final.md'
        if number > self.limits.max_observer_passes:
            gaps = section(final.read_text(), 'Gaps') if final.is_file() else ''
            raise Halt(f'Task still not done after {number - 1} observer passes. Open gaps:\n{gaps}')
        prompt = observer_prompt(job['feature'], self.root, job_dir, job['worktree'],
                                 job['base_sha'], head, number)
        await self.call(job, 'observer', prompt, f'observer-{number}')
        text = final.read_text() if final.is_file() else ''
        if f'HEAD: {head}' not in text.splitlines():
            raise Halt('Observer report missing or not for the current HEAD')
        critique = section(text, 'Workflow critique')
        if critique:
            self.log(f'observer critique (pass {number}): {critique[:600]}')
        verdict = first_line(final)
        if verdict == 'OBSERVER: DONE':
            return None
        if verdict == 'OBSERVER: BLOCKED':
            raise Halt('Needs the user: ' + (section(text, 'Answer') or text[:800]))
        if verdict == 'OBSERVER: NOT_DONE':
            return 'The task is not done end to end yet. Close these gaps:\n' + (section(text, 'Gaps') or text[:1500])
        raise Halt('Observer report has no valid first line')

    async def review(self, job_id, head):
        job_dir = self.job_dir(job_id)
        lenses = lenses_from(job_dir / 'spec.md')
        required = [lens for lens in lenses if lens not in OPTIONAL_LENSES]
        try:
            job = self.state('round', job_id, '--head', head, '--assigned', *lenses, '--required', *required)
        except StateError as error:
            raise Halt(str(error))
        number = job['round']
        slots = asyncio.Semaphore(self.limits.parallel_reviews)

        async def one(lens):
            report = job_dir / f'round-{number}' / f'review-{lens}.md'
            prompt = (f'{self.context(job, reviewed_head=head, round=number)}\nSpec: {job_dir / "spec.md"}\n'
                      f'Build report: {job_dir / "build.md"}\nRemovals: {job_dir / "removals.md"}\n\n'
                      f'Review the diff {job["base_sha"][:12]}..{head[:12]} and write {report}.')
            async with slots:
                await self.call(job, f'reviewer-{lens}', prompt, f'review-{lens}-r{number}', report)

        outcomes = await asyncio.gather(*(one(lens) for lens in lenses), return_exceptions=True)
        failures = [outcome for outcome in outcomes if isinstance(outcome, BaseException)]
        if failures:
            raise Halt(f'Review round {number} incomplete: {failures[0]}')
        try:
            self.state('check', job_id, '--head', head)
            return None
        except StateError as error:
            reports = {lens: job_dir / f'round-{number}' / f'review-{lens}.md' for lens in lenses}
            verdicts = {lens: first_line(path).removeprefix('VERDICT: ') for lens, path in reports.items()}
            if not set(verdicts.values()) <= {'PASS', 'CHANGES', 'SKIP', 'BLOCKED'}:
                raise Halt(f'Review round {number} invalid: {error}')
            if 'BLOCKED' in verdicts.values() or 'CHANGES' not in verdicts.values():
                raise Halt(f'Review round {number}: {error}')
            return f'Reviewers asked for changes (round {number}):\n\n' + '\n\n'.join(
                f'## {lens} review\n{reports[lens].read_text()}' for lens, verdict in verdicts.items()
                if verdict == 'CHANGES')

    async def approve(self, job_id, head):
        job_dir = self.job_dir(job_id)
        try:
            job = self.state('stage', job_id, 'approve')
        except StateError as error:
            raise Halt(str(error))
        decision = job_dir / 'decision.md'
        prompt = (f'{self.context(job, reviewed_head=head, round=job["round"])}\nSpec: {job_dir / "spec.md"}\n'
                  f'Build report: {job_dir / "build.md"}\nReviews: {job_dir / ("round-" + str(job["round"]))}\n'
                  f'Removal audit: {job_dir / "guard.json"}\n\nWrite {decision}.')
        await self.call(job, 'approver', prompt, f'approve-{self.next_number(job_dir, "approve")}', decision)
        text = decision.read_text() if decision.is_file() else ''
        if f'HEAD: {head}' not in text.splitlines():
            raise Halt('decision.md missing or not for the current HEAD')
        verdict = first_line(decision)
        if verdict == 'READY':
            return None
        if verdict == 'CHANGES':
            return 'The approver asked for changes:\n' + text
        raise Halt(blocker(decision, 'Approver blocked'))
