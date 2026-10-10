"""Factory gates and persisted state, using temporary synthetic jobs only."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT=Path(__file__).resolve().parents[1]/'factory/scripts/state.py'
GUARD=SCRIPT.with_name('guard.py')
HOOK=SCRIPT.with_name('destructive_guard.py')

class GateTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.job=json.loads(self.run_cli('new','Synthetic factory gate')['stdout'])['id']
        self.path=self.root/'jobs'/self.job
        self.run_cli('bind',self.job,'--worktree',str(self.root),'--branch','codex/test','--base','base')
        (self.path/'spec.md').write_text('Synthetic acceptance spec\n')
        (self.path/'build.md').write_text('STATUS: READY\nSynthetic evidence\n')
        self.run_cli('round',self.job,'--head','abc','--assigned','code','security','ui','--required','code','security')
    def run_cli(self,*args,ok=True):
        result=subprocess.run([sys.executable,str(SCRIPT),'--root',str(self.root),*args],capture_output=True,text=True)
        self.assertEqual(result.returncode==0,ok,result.stderr)
        return {'stdout':result.stdout,'stderr':result.stderr}
    def reports(self,security='PASS',ui='SKIP',head='abc',observer='DONE',guard='ok'):
        for lens,value in [('code','PASS'),('security',security),('ui',ui)]:
            (self.path/'round-1'/f'review-{lens}.md').write_text(f'VERDICT: {value}\nHEAD: {head}\nReason: no visual change\n')
        (self.path/'decision.md').write_text(f'READY\nHEAD: {head}\nAcceptance verified\n')
        (self.path/'guard.json').write_text(json.dumps({'head':head,'status':guard}))
        (self.path/'observer').mkdir(exist_ok=True)
        (self.path/'observer'/'final.md').write_text(f'OBSERVER: {observer}\nHEAD: {head}\nTasks checked\n')
    def test_observer_must_validate(self):
        self.reports();(self.path/'observer'/'final.md').unlink()
        self.run_cli('ready',self.job,'--head','abc',ok=False)
    def test_observer_not_done_blocks_delivery(self):
        self.reports(observer='NOT_DONE');self.run_cli('ready',self.job,'--head','abc',ok=False)
    def test_observer_must_attest_head(self):
        self.reports();(self.path/'observer'/'final.md').write_text('OBSERVER: DONE\nHEAD: old\n')
        self.run_cli('ready',self.job,'--head','abc',ok=False)
    def test_unapproved_removal_blocks_delivery(self):
        self.reports(guard='unapproved');self.run_cli('ready',self.job,'--head','abc',ok=False)
    def test_deletion_audit_required(self):
        self.reports();(self.path/'guard.json').unlink()
        self.run_cli('ready',self.job,'--head','abc',ok=False)
    def test_wait_for_every_review(self):
        self.run_cli('check',self.job,'--head','abc',ok=False)
        self.assertEqual(json.loads((self.path/'job.json').read_text())['stage'],'review')
    def test_required_skip_blocks_delivery(self):
        self.reports(security='SKIP');self.run_cli('ready',self.job,'--head','abc',ok=False)
    def test_blocking_finding_blocks_delivery(self):
        self.reports(security='CHANGES');self.run_cli('ready',self.job,'--head','abc',ok=False)
    def test_stale_head_invalidates_reviews(self):
        self.reports();self.run_cli('ready',self.job,'--head','def',ok=False)
    def test_report_must_attest_head(self):
        self.reports(head='old');self.run_cli('ready',self.job,'--head','abc',ok=False)
    def test_readiness_and_delivery_persist(self):
        self.reports();self.run_cli('ready',self.job,'--head','abc')
        self.run_cli('delivered',self.job,'--evidence','synthetic local handoff')
        state=json.loads(self.run_cli('show',self.job)['stdout'])
        self.assertEqual(state['stage'],'delivered');self.assertTrue(state['history'])
        self.run_cli('stage',self.job,'build',ok=False)
    def test_delivery_needs_readiness(self):
        self.run_cli('delivered',self.job,'--evidence','not complete',ok=False)
    def test_rebuild_invalidates_prior_approval(self):
        self.reports();self.run_cli('ready',self.job,'--head','abc')
        self.run_cli('stage',self.job,'build');self.run_cli('ready',self.job,'--head','abc',ok=False)
    def test_rebuild_at_same_head_keeps_reviews_but_needs_new_approval(self):
        self.reports();self.run_cli('ready',self.job,'--head','abc')
        self.run_cli('stage',self.job,'build')
        state=json.loads(self.run_cli('show',self.job)['stdout'])
        self.assertEqual(state['reviewed_head'],'abc')
        self.assertTrue((self.path/'decision-before-build.md').is_file())
        self.run_cli('ready',self.job,'--head','abc',ok=False)
        (self.path/'decision.md').write_text('READY\nHEAD: abc\nRe-approved\n')
        self.run_cli('ready',self.job,'--head','abc')
    def test_rebuild_at_new_head_needs_new_round(self):
        self.reports();self.run_cli('stage',self.job,'build')
        (self.path/'decision.md').write_text('READY\nHEAD: def\n')
        self.run_cli('ready',self.job,'--head','def',ok=False)
    def test_respec_drops_reviews(self):
        self.reports();self.run_cli('stage',self.job,'spec')
        state=json.loads(self.run_cli('show',self.job)['stdout'])
        self.assertIsNone(state['reviewed_head']);self.assertEqual(state['reviews'],{})
    def test_restore_review_only_when_reports_attest_head(self):
        self.reports();self.run_cli('stage',self.job,'spec')
        self.run_cli('restore-review',self.job,'--head','def',ok=False)
        self.assertIsNone(json.loads(self.run_cli('show',self.job)['stdout'])['reviewed_head'])
        self.run_cli('restore-review',self.job,'--head','abc')
        state=json.loads(self.run_cli('show',self.job)['stdout'])
        self.assertEqual(state['reviewed_head'],'abc');self.assertEqual(state['reviews']['code'],'PASS')
    def test_restore_review_refuses_changes_verdict(self):
        self.reports(security='CHANGES');self.run_cli('stage',self.job,'spec')
        self.run_cli('restore-review',self.job,'--head','abc',ok=False)
    def test_writer_lock_preserves_state(self):
        before=(self.path/'job.json').read_text();(self.path/'.state.lock').write_text('occupied')
        self.run_cli('stage',self.job,'build',ok=False)
        self.assertEqual((self.path/'job.json').read_text(),before)
    def test_rebinding_invalidates_approval(self):
        self.reports();self.run_cli('ready',self.job,'--head','abc')
        self.run_cli('bind',self.job,'--worktree',str(self.root),'--branch','codex/other','--base','other')
        state=json.loads(self.run_cli('show',self.job)['stdout'])
        self.assertEqual(state['stage'],'build');self.assertIsNone(state['reviewed_head'])
        self.run_cli('delivered',self.job,'--evidence','stale handoff',ok=False)
    def test_empty_build_report_fails_cleanly(self):
        self.run_cli('stage',self.job,'build');(self.path/'build.md').write_text('')
        result=self.run_cli('round',self.job,'--head','next','--assigned','code','security','--required','code','security',ok=False)
        self.assertNotIn('Traceback',result['stderr'])
    def test_new_round_requires_fresh_decision_even_at_same_head(self):
        self.reports();self.run_cli('ready',self.job,'--head','abc')
        self.run_cli('stage',self.job,'build')
        self.run_cli('round',self.job,'--head','abc','--assigned','code','security','--required','code','security')
        for lens in ['code','security']:
            (self.path/'round-2'/f'review-{lens}.md').write_text('VERDICT: PASS\nHEAD: abc\n')
        # the prior approval is archived (by the rebuild, before the round) and never reused
        self.assertFalse((self.path/'decision.md').exists())
        self.assertTrue(list(self.path.glob('decision-before-*.md')))
        self.run_cli('ready',self.job,'--head','abc',ok=False)
    def test_path_traversal_rejected(self):
        self.run_cli('show','../other',ok=False)

def git(cwd,*args):
    return subprocess.run(['git','-C',str(cwd),'-c','user.name=t','-c','user.email=t@t',*args],
                          check=True,capture_output=True,text=True).stdout.strip()

class DeletionGuardTests(unittest.TestCase):
    """guard.py against a real throwaway repository."""
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/'factory';self.repo=Path(self.temp.name)/'repo';self.repo.mkdir()
        git(self.repo,'init','-q')
        (self.repo/'engine.py').write_text('def keep():\n    return 1\n\ndef old():\n    return 2\n\nclass Box:\n    def lid(self):\n        return 3\n')
        (self.repo/'notes.md').write_text('note\n')
        git(self.repo,'add','.');git(self.repo,'commit','-qm','base')
        self.base=git(self.repo,'rev-parse','HEAD')
        result=subprocess.run([sys.executable,str(SCRIPT),'--root',str(self.root),'new','Guard'],capture_output=True,text=True)
        self.job=json.loads(result.stdout)['id'];self.path=self.root/'jobs'/self.job
        subprocess.run([sys.executable,str(SCRIPT),'--root',str(self.root),'bind',self.job,'--worktree',str(self.repo),
                        '--branch','codex/t','--base',self.base],check=True,capture_output=True)
    def guard(self,ok=True):
        head=git(self.repo,'rev-parse','HEAD')
        result=subprocess.run([sys.executable,str(GUARD),'--root',str(self.root),self.job,'--head',head],capture_output=True,text=True)
        self.assertEqual(result.returncode==0,ok,result.stdout+result.stderr)
        return json.loads((self.path/'guard.json').read_text())
    def test_additions_pass(self):
        with open(self.repo/'engine.py','a') as f: f.write('\ndef new():\n    return 4\n')
        git(self.repo,'commit','-qam','add');self.assertEqual(self.guard()['status'],'ok')
    def test_removed_function_and_method_flagged(self):
        (self.repo/'engine.py').write_text('def keep():\n    return 1\n\nclass Box:\n    pass\n')
        git(self.repo,'commit','-qam','drop')
        symbols={f.get('symbol') for f in self.guard(ok=False)['findings']}
        self.assertTrue({'old','Box.lid'}<=symbols)
    def test_uncommitted_file_deletion_flagged(self):
        (self.repo/'notes.md').unlink()
        kinds={f['kind'] for f in self.guard(ok=False)['findings']}
        self.assertIn('deleted_file',kinds)
    def drop_old(self):
        (self.repo/'engine.py').write_text('def keep():\n    return 1\n\nclass Box:\n    def lid(self):\n        return 3\n')
        git(self.repo,'commit','-qam','drop old')
    def test_explained_removal_passes(self):
        self.drop_old()
        (self.path/'removals.md').write_text(f'- `engine.py::old` | existed-for: {self.base[:10]} returned 2 for the '
                                             'legacy caller | why-safe: caller removed | regression-check: tests/test_engine.py\n')
        self.assertEqual(self.guard()['status'],'ok')
    def test_removal_without_history_or_regression_check_fails(self):
        self.drop_old()
        (self.path/'removals.md').write_text('- engine.py::old | why-safe: looked unused\n')
        self.assertTrue(self.guard(ok=False)['ledger_problems'])
    def test_removal_citing_unknown_commit_fails(self):
        self.drop_old()
        (self.path/'removals.md').write_text('- engine.py::old | existed-for: 0123abcd guess | why-safe: x | regression-check: y\n')
        self.assertEqual(self.guard(ok=False)['status'],'unexplained')
    def test_wrong_head_rejected(self):
        result=subprocess.run([sys.executable,str(GUARD),'--root',str(self.root),self.job,'--head','deadbeef'],capture_output=True,text=True)
        self.assertNotEqual(result.returncode,0)

class DestructiveHookTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.repo=Path(self.temp.name);git(self.repo,'init','-q')
        (self.repo/'big.py').write_text(''.join(f'x{i} = {i}\n' for i in range(60)))
        (self.repo/'src').mkdir();(self.repo/'src'/'a.py').write_text('a = 1\n')
        git(self.repo,'add','.');git(self.repo,'commit','-qm','base')
    def hook(self,tool,data):
        event=json.dumps({'tool_name':tool,'tool_input':data,'cwd':str(self.repo)})
        return subprocess.run([sys.executable,str(HOOK)],input=event,capture_output=True,text=True).returncode
    def test_blocks_destructive_git(self):
        for command in ['git reset --hard HEAD~1','git clean -fd','git checkout -- .','git push --force',
                        'git branch -D codex/x','git stash drop','git restore big.py',
                        'git -C . reset --hard','git -c core.pager=cat clean -fd',
                        'git --git-dir=.git --work-tree=. checkout -- .','git --no-pager restore big.py',
                        'git -C src push -f origin x']:
            self.assertEqual(self.hook('Bash',{'command':command}),2,command)
    def test_blocks_bulk_removal(self):
        self.assertEqual(self.hook('Bash',{'command':'ls && rm -rf src'}),2)
        self.assertEqual(self.hook('Bash',{'command':'find . -name "*.py" -delete'}),2)
    def test_allows_deliberate_deletion_and_ordinary_work(self):
        for command in ['rm big.py','git rm big.py','git status','git diff --stat','git checkout -b codex/new',
                        'git restore --staged big.py','rm -rf /tmp/scratch-dir','python3 tests/test_x.py',
                        'git -C . status','git -C src log --oneline']:
            self.assertEqual(self.hook('Bash',{'command':command}),0,command)
    def test_ignores_file_writes(self):
        self.assertEqual(self.hook('Write',{'file_path':'big.py','content':'x = 1\n'}),0)

if __name__=='__main__':unittest.main()
