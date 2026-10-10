"""factory onboard: the managed AGENTS.md workflow block and the profile generated from AGENTS.md."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from factory import onboard  # noqa: E402
from factory.scripts import home  # noqa: E402

PROJECT_AGENTS = """# AGENTS.md

Intro written by a person.

## Commands

make check

## Factory profile

### All roles

- Verification: `make check`.

### builder

- Small files.

### reviewer-security

- No secrets in logs.
"""


class OnboardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)
        subprocess.run(['git', 'init', '-q', str(self.project)], check=True)

    def agents(self):
        return (self.project / 'AGENTS.md').read_text()

    def write_agents(self, text=PROJECT_AGENTS):
        (self.project / 'AGENTS.md').write_text(text)

    def test_dry_run_writes_nothing_and_reports_changes(self):
        self.write_agents()
        plan = onboard.run(self.project, write=False)
        self.assertEqual(self.agents(), PROJECT_AGENTS)
        self.assertFalse((self.project / '.factory' / 'profile.md').exists())
        self.assertIn('AGENTS.md', plan.changed)
        self.assertIn('.factory/profile.md', plan.changed)

    def test_block_goes_before_the_first_section_and_keeps_everything_else(self):
        self.write_agents()
        onboard.run(self.project, write=True)
        text = self.agents()
        self.assertEqual(text.count(onboard.BEGIN), 1)
        self.assertEqual(text.count(onboard.END), 1)
        self.assertLess(text.index('Intro written by a person.'), text.index(onboard.BEGIN))
        self.assertLess(text.index(onboard.END), text.index('## Commands'))
        outside = text[:text.index(onboard.BEGIN)] + text[text.index(onboard.END) + len(onboard.END):]
        for line in PROJECT_AGENTS.splitlines():
            self.assertIn(line, outside)

    def test_rerun_is_idempotent_and_check_passes(self):
        self.write_agents()
        onboard.run(self.project, write=True)
        first = self.agents()
        plan = onboard.run(self.project, write=True)
        self.assertEqual(self.agents(), first)
        self.assertEqual(plan.changed, [])
        self.assertEqual(onboard.run(self.project, write=False).changed, [])

    def test_a_hand_edited_block_is_replaced_and_check_reports_it(self):
        self.write_agents()
        onboard.run(self.project, write=True)
        edited = self.agents().replace('## Workflow', '## Workflow (edited)')
        self.write_agents(edited)
        self.assertIn('AGENTS.md', onboard.run(self.project, write=False).changed)
        onboard.run(self.project, write=True)
        self.assertNotIn('(edited)', self.agents())

    def test_broken_markers_stop_without_writing(self):
        self.write_agents(PROJECT_AGENTS + '\n' + onboard.BEGIN + '\nhalf a block\n')
        with self.assertRaises(onboard.OnboardError):
            onboard.run(self.project, write=True)
        self.assertNotIn(onboard.END, self.agents())

    def test_missing_agents_file_is_created_with_the_block(self):
        onboard.run(self.project, write=True)
        text = self.agents()
        self.assertTrue(text.startswith('# AGENTS.md\n'))
        self.assertIn(onboard.BEGIN, text)
        self.assertFalse((self.project / '.factory' / 'profile.md').exists())

    def test_block_names_the_branch_prefix_worktrees_and_every_skill(self):
        (self.project / '.factory').mkdir()
        (self.project / '.factory' / 'config.json').write_text(
            json.dumps({'branch_prefix': 'job/', 'worktrees_dir': '.claude/worktrees'}))
        onboard.run(self.project, write=True)
        text = self.agents()
        self.assertIn('`job/<task>`', text)
        self.assertIn('`.claude/worktrees/`', text)
        for skill in sorted(p.name for p in (REPO / 'skills').iterdir() if p.is_dir()):
            self.assertIn(f'`{skill}`', text, skill)

    def test_ruled_out_skills_are_listed_with_their_reason_and_not_as_steps(self):
        (self.project / '.factory').mkdir()
        (self.project / '.factory' / 'config.json').write_text(
            json.dumps({'skills_off': {'greploop': 'no third-party review of this code'}}))
        onboard.run(self.project, write=True)
        block = self.agents().split(onboard.BEGIN)[1].split(onboard.END)[0]
        self.assertIn('`greploop`: no third-party review of this code', block)
        self.assertNotIn('/greploop until', block)

    def test_unknown_ruled_out_skill_is_an_error(self):
        (self.project / '.factory').mkdir()
        (self.project / '.factory' / 'config.json').write_text(json.dumps({'skills_off': {'nope': 'x'}}))
        with self.assertRaises(onboard.OnboardError):
            onboard.run(self.project, write=True)

    def test_profile_is_generated_from_the_factory_profile_section(self):
        self.write_agents()
        onboard.run(self.project, write=True)
        profile = (self.project / '.factory' / 'profile.md').read_text()
        self.assertIn(onboard.PROFILE_MARKER, profile)
        found = home.sections(profile)
        self.assertEqual(set(found), {'all roles', 'builder', 'reviewer-security'})
        self.assertIn('Small files.', home.profile(self.project, 'builder', profile))
        self.assertIn('Verification: `make check`.', home.profile(self.project, 'builder', profile))
        self.assertEqual(home.problems(profile), [])

    def test_a_hand_written_profile_is_left_alone_unless_forced(self):
        self.write_agents()
        (self.project / '.factory').mkdir()
        (self.project / '.factory' / 'profile.md').write_text('## All roles\n\n- mine\n')
        plan = onboard.run(self.project, write=True)
        self.assertEqual((self.project / '.factory' / 'profile.md').read_text(), '## All roles\n\n- mine\n')
        self.assertIn('.factory/profile.md', plan.skipped)
        onboard.run(self.project, write=True, force=True)
        self.assertIn(onboard.PROFILE_MARKER, (self.project / '.factory' / 'profile.md').read_text())

    def test_a_role_heading_that_matches_no_role_is_an_error(self):
        self.write_agents(PROJECT_AGENTS + '\n### reviwer-code\n\n- typo\n')
        with self.assertRaises(onboard.OnboardError):
            onboard.run(self.project, write=True)

    def test_cli_check_exits_one_on_drift_and_zero_when_in_sync(self):
        self.write_agents()
        command = [sys.executable, '-P', str(REPO / 'factory' / 'onboard.py'), str(self.project)]
        self.assertEqual(subprocess.run([*command, '--check'], capture_output=True).returncode, 1)
        self.assertEqual(subprocess.run([*command, '--write'], capture_output=True).returncode, 0)
        self.assertEqual(subprocess.run([*command, '--check'], capture_output=True).returncode, 0)


if __name__ == '__main__':
    unittest.main()
