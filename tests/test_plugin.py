"""The plugin as a package: manifests, components, project neutrality, state location,
init, the Codex generator and the CLI. Offline; projects are throwaway Git repos."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import tomllib
import unittest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
os.environ.pop('FACTORY_HOME', None)
from factory.harness.agents import load as load_agents  # noqa: E402
from factory.scripts import home  # noqa: E402

CLI = REPO / 'bin' / 'factory'
ROLES = {'spec-writer', 'builder', 'observer', 'approver', 'reviewer-code', 'reviewer-security',
         'reviewer-ux', 'reviewer-ui', 'reviewer-data'}
UPSTREAM = json.loads((REPO / 'upstream-manifest.json').read_text())


def git(cwd, *args):
    return subprocess.run(['git', '-C', str(cwd), '-c', 'user.name=t', '-c', 'user.email=t@t', *args],
                          check=True, capture_output=True, text=True).stdout.strip()


def frontmatter(path):
    text = path.read_text()
    assert text.startswith('---\n'), path
    head = text[4:].partition('\n---\n')[0]
    return dict(line.split(':', 1) for line in head.splitlines() if ':' in line)


class ManifestTests(unittest.TestCase):
    def test_plugin_and_marketplace_manifests(self):
        plugin = json.loads((REPO / '.claude-plugin' / 'plugin.json').read_text())
        market = json.loads((REPO / '.claude-plugin' / 'marketplace.json').read_text())
        self.assertRegex(plugin['name'], r'^[a-z0-9-]+$')
        self.assertRegex(plugin['version'], r'^\d+\.\d+\.\d+$')
        self.assertEqual([entry['name'] for entry in market['plugins']], [plugin['name']])
        self.assertEqual(market['plugins'][0]['source'], './')
        self.assertTrue(market['owner']['name'])

    def test_hook_points_at_a_real_script_through_the_plugin_root(self):
        hooks = json.loads((REPO / 'hooks' / 'hooks.json').read_text())['hooks']
        command = hooks['PreToolUse'][0]['hooks'][0]['command']
        self.assertIn('${CLAUDE_PLUGIN_ROOT}', command)
        script = command.split('${CLAUDE_PLUGIN_ROOT}/')[1].rstrip('"')
        self.assertTrue((REPO / script).is_file(), script)
        self.assertNotIn('CLAUDE_PROJECT_DIR', command)

    def test_every_skill_has_frontmatter_matching_its_folder(self):
        skills = sorted(path for path in (REPO / 'skills').iterdir() if path.is_dir())
        self.assertEqual(len(skills), 9)
        for skill in skills:
            meta = frontmatter(skill / 'SKILL.md')
            self.assertEqual(meta['name'].strip(), skill.name)
            self.assertTrue(meta['description'].strip(), skill.name)

    def test_every_role_loads(self):
        agents = load_agents()
        self.assertEqual(set(agents), ROLES)
        self.assertEqual(agents['observer'].model, 'claude-opus-5-5')
        for name, agent in agents.items():
            self.assertNotIn('${', agent.instructions, name)


class NeutralityTests(unittest.TestCase):
    """Plugin-owned text carries no project; project rules belong in .factory/profile.md."""

    def owned(self):
        vendored = {REPO / path for path in UPSTREAM['files']}
        for path in [*REPO.glob('agents/*.md'), *REPO.glob('skills/**/*'), *REPO.glob('factory/**/*'),
                     *REPO.glob('templates/*'), CLI]:
            if path.is_file() and path not in vendored and '__pycache__' not in path.parts:
                yield path

    def test_no_project_names_or_project_paths(self):
        banned = re.compile(r'Flave|tasteprofile|diner|Gmail|catalog_oahu|codex/<slug>', re.I)
        for path in self.owned():
            text = path.read_text(errors='replace')
            self.assertIsNone(banned.search(text), f'{path.relative_to(REPO)}: {banned.search(text)}')

    def test_skills_reach_scripts_through_the_plugin_root(self):
        text = (REPO / 'skills' / 'factory' / 'SKILL.md').read_text()
        self.assertIn('${CLAUDE_PLUGIN_ROOT}/bin/factory', text)
        self.assertNotRegex(text, r'python3 factory/scripts')

    def test_vendored_skills_are_byte_identical_to_the_pinned_upstream(self):
        for path, digest in UPSTREAM['files'].items():
            self.assertEqual(hashlib.sha256((REPO / path).read_bytes()).hexdigest(), digest, path)


class HomeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name).resolve() / 'project'
        self.project.mkdir()
        git(self.project, 'init', '-q')
        (self.project / 'a.py').write_text('a = 1\n')
        git(self.project, 'add', '.')
        git(self.project, 'commit', '-qm', 'base')

    def test_state_defaults_to_the_projects_factory_folder(self):
        self.assertEqual(home.state_root(self.project), self.project / '.factory')

    def test_a_worktree_shares_the_main_checkouts_state(self):
        worktree = Path(self.temp.name).resolve() / 'wt'
        git(self.project, 'worktree', 'add', '-q', '-b', 'x', str(worktree))
        self.assertEqual(home.project_root(worktree), self.project)
        self.assertEqual(home.state_root(worktree), self.project / '.factory')

    def test_config_overrides_and_rejects_unknown_keys(self):
        (self.project / '.factory').mkdir()
        (self.project / '.factory' / 'config.json').write_text('{"state_dir": "factory"}')
        self.assertEqual(home.state_root(self.project), self.project / 'factory')
        self.assertEqual(home.config(self.project)['branch_prefix'], 'factory/')
        (self.project / '.factory' / 'config.json').write_text('{"branch_prefx": "x/"}')
        with self.assertRaises(ValueError):
            home.config(self.project)

    def test_profile_sections_are_case_insensitive_and_scoped(self):
        (self.project / '.factory').mkdir()
        home.profile_path(self.project).write_text('# P\n\n## All Roles\n- shared\n\n## Builder\n- b\n\n## observer\n')
        self.assertEqual(home.profile(self.project, 'builder'), '### All roles\n- shared\n\n### builder\n- b')
        self.assertEqual(home.profile(self.project, 'observer'), '### All roles\n- shared')
        self.assertEqual(home.profile(self.project / 'missing', 'builder'), '')

    def cli(self, *args, ok=True):
        result = subprocess.run([str(CLI), *args], cwd=self.project, capture_output=True, text=True,
                                env={**os.environ, 'CLAUDE_PROJECT_DIR': ''})
        self.assertEqual(result.returncode == 0, ok, result.stdout + result.stderr)
        return result

    def test_init_creates_profile_and_ignores_once_and_never_overwrites(self):
        self.cli('init')
        profile = home.profile_path(self.project)
        self.assertEqual(profile.read_text(), (REPO / 'templates' / 'profile.md').read_text())
        profile.write_text('# mine\n')
        self.cli('init')
        self.assertEqual(profile.read_text(), '# mine\n')
        ignore = (self.project / '.gitignore').read_text().splitlines()
        self.assertEqual(ignore.count('/.factory/jobs/'), 1)
        self.assertEqual(ignore.count('/.worktrees/'), 1)
        ignored = lambda path: subprocess.run(['git', '-C', str(self.project), 'check-ignore', '-q', path]).returncode == 0
        self.assertTrue(ignored('.factory/jobs/x/job.json'))
        self.assertFalse(ignored('.factory/profile.md'))  # the profile is committed with the project

    def test_cli_state_lands_in_the_project(self):
        job = json.loads(self.cli('state', 'new', 'Synthetic task').stdout)
        self.assertTrue((self.project / '.factory' / 'jobs' / job['id'] / 'job.json').is_file())
        self.assertEqual(json.loads(self.cli('status', job['id']).stdout)['stage'], 'spec')

    def test_cli_help_and_unknown_command(self):
        self.assertIn('usage: factory', self.cli('help').stdout)
        self.cli('nonsense', ok=False)


class CodexTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name).resolve()

    def install(self, *extra, ok=True):
        result = subprocess.run([str(CLI), 'codex', 'install', str(self.project), *extra],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode == 0, ok, result.stdout + result.stderr)
        return result

    def test_generates_agents_skills_and_hook_from_the_same_sources(self):
        self.install()
        agents = load_agents()
        for name, agent in agents.items():
            data = tomllib.loads((self.project / '.codex' / 'agents' / f'{name}.toml').read_text())
            self.assertEqual(data['name'], name)
            self.assertEqual(data['developer_instructions'].strip(), agent.instructions.strip())
        self.assertNotIn('AGENTS.md, AGENTS.md', (self.project / '.codex' / 'agents' / 'builder.toml').read_text())
        skill = (self.project / '.agents' / 'skills' / 'factory' / 'SKILL.md').read_text()
        self.assertNotIn('${CLAUDE_PLUGIN_ROOT}', skill)
        self.assertIn(str(CLI), skill)
        self.assertTrue(skill.startswith('---\nname: factory\n'))
        self.assertTrue((self.project / '.agents' / 'skills' / 'unslop' / 'LICENSE').is_file())
        hook = json.loads((self.project / '.codex' / 'hooks.json').read_text())
        command = hook['hooks']['PreToolUse'][0]['hooks'][0]['command']
        self.assertIn(str(REPO / 'factory' / 'scripts' / 'destructive_guard.py'), command)

    def test_rerun_updates_its_own_files_but_never_a_users_file(self):
        self.install()
        self.install()  # idempotent
        mine = self.project / '.codex' / 'agents' / 'builder.toml'
        mine.write_text('name = "builder"\n# hand-written\n')
        self.install(ok=False)
        self.assertIn('hand-written', mine.read_text())
        self.install('--force')
        self.assertNotIn('hand-written', mine.read_text())


class ReviewRegressionTests(unittest.TestCase):
    """One check per finding of the independent review of the port."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.project = self.base / 'project'
        self.project.mkdir()
        git(self.project, 'init', '-q')
        (self.project / 'a.py').write_text('a = 1\n')
        git(self.project, 'add', '.')
        git(self.project, 'commit', '-qm', 'base')

    def cli(self, *args, cwd=None, ok=True, env=None):
        result = subprocess.run([str(CLI), *args], cwd=cwd or self.project, capture_output=True, text=True,
                                env={**os.environ, **(env or {})})
        self.assertEqual(result.returncode == 0, ok, result.stdout + result.stderr)
        return result

    def test_a_projects_own_factory_package_cannot_shadow_the_plugin(self):
        (self.project / 'factory' / 'harness').mkdir(parents=True)
        (self.project / 'factory' / '__init__.py').write_text('')
        (self.project / 'factory' / 'harness' / '__init__.py').write_text('')
        (self.project / 'factory' / 'harness' / '__main__.py').write_text('raise SystemExit("HIJACKED")\n')
        result = self.cli('init')
        self.assertNotIn('HIJACKED', result.stdout + result.stderr)
        self.assertTrue(home.profile_path(self.project).is_file())

    def test_project_option_reaches_every_subcommand(self):
        elsewhere = self.base / 'elsewhere'
        elsewhere.mkdir()
        self.cli('init', '--project', str(self.project), cwd=elsewhere)
        self.assertTrue(home.profile_path(self.project).is_file())
        job = json.loads(self.cli('state', 'new', 'T').stdout)
        status = json.loads(self.cli('status', '--project', str(self.project), job['id'], cwd=elsewhere).stdout)
        self.assertEqual(status['id'], job['id'])

    def test_current_directory_wins_over_claude_project_dir(self):
        other = self.base / 'other'
        other.mkdir()
        git(other, 'init', '-q')
        job = json.loads(self.cli('state', 'new', 'T', env={'CLAUDE_PROJECT_DIR': str(other)}).stdout)
        self.assertTrue((self.project / '.factory' / 'jobs' / job['id']).is_dir())
        self.assertFalse((other / '.factory').exists())

    def test_separate_git_dir_resolves_to_the_working_tree_top(self):
        repo, gitdir = self.base / 'sep', self.base / 'sep.git'
        repo.mkdir()
        subprocess.run(['git', 'init', '-q', f'--separate-git-dir={gitdir}', str(repo)], check=True)
        (repo / 'sub').mkdir()
        self.assertEqual(home.project_root(repo / 'sub'), repo)

    def test_bad_config_exits_cleanly(self):
        (self.project / '.factory').mkdir()
        (self.project / '.factory' / 'config.json').write_text('{"nope": 1}')
        result = self.cli('state', 'list', ok=False)
        self.assertIn('unknown keys', result.stderr)
        self.assertNotIn('Traceback', result.stderr)

    def test_profile_parsing_edge_cases(self):
        text = ('## All roles\n- one\n\n## builder\n- b1\n\n```md\n## not a heading\n```\n\n'
                '## All roles\n- two\n\n## observer (isolated)\n- lost\n')
        rules = home.profile(None, 'builder', text=text)
        self.assertIn('- one', rules)
        self.assertIn('- two', rules)  # a repeated heading adds, never replaces
        self.assertIn('## not a heading', rules)  # fenced text stays in its section
        issues = home.problems(text)
        self.assertTrue(any('observer (isolated)' in issue for issue in issues), issues)
        self.assertTrue(any('placeholders' in issue for issue in home.problems(
            (REPO / 'templates' / 'profile.md').read_text())))
        self.assertTrue(home.problems(''))
        self.assertEqual(home.problems('## All roles\n- run `make test`\n'), [])


class CodexEdgeTests(unittest.TestCase):
    def test_toml_survives_emoji_and_control_characters(self):
        sys.path.insert(0, str(REPO / 'factory'))
        import codex
        from factory.harness.agents import AgentSpec
        agent = AgentSpec('x', 'emoji \U0001F600 "quoted"', ('Read',), None,
                          'bell \x07 tab \t quotes """ backslash \\ end')
        data = tomllib.loads(codex.agent_toml(agent))
        self.assertEqual(data['description'], agent.description)
        self.assertEqual(data['developer_instructions'].strip(), agent.instructions)

    def test_updated_plugin_files_replace_old_output_but_hand_edits_stay(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            subprocess.run([str(CLI), 'codex', 'install', str(project)], check=True, capture_output=True)
            copied = project / '.agents' / 'skills' / 'unslop' / 'LICENSE'
            ledger = json.loads((project / '.codex' / 'factory-generated.json').read_text())
            self.assertIn('.agents/skills/unslop/LICENSE', ledger)
            # simulate an older plugin's output: ledger and file agree, content differs from today's
            copied.write_text('old generated text\n')
            ledger['.agents/skills/unslop/LICENSE'] = hashlib.sha256(b'old generated text\n').hexdigest()
            (project / '.codex' / 'factory-generated.json').write_text(json.dumps(ledger))
            subprocess.run([str(CLI), 'codex', 'install', str(project)], check=True, capture_output=True)
            self.assertEqual(copied.read_bytes(), (REPO / 'skills' / 'unslop' / 'LICENSE').read_bytes())
            copied.write_text('my own edit\n')  # not what was generated: left alone
            result = subprocess.run([str(CLI), 'codex', 'install', str(project)], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(copied.read_text(), 'my own edit\n')


if __name__ == '__main__':
    unittest.main()
