import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from timeout_claim_audit.audit import TIMEOUT, NO_TIMEOUT, extract_claims, extract_evidence, audit, main

FIXTURES = Path(__file__).parent / 'fixtures'

class AuditTests(unittest.TestCase):
    def tree(self, claim, source='def work(): return 1', extra=None):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / 'README.md').write_text(claim)
        if source is not None:
            (root / 'main.py').write_text(source)
        for name, content in (extra or {}).items():
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
        return root

    def run_cli(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            status = main(list(args))
        return status, out.getvalue()

    def test_fixtures(self):
        for name, code, rule in [('good/timeout',0,None),('good/notimeout',0,None),('bad/timeout',2,'documented-timeout-claim-false'),('bad/notimeout',2,'documented-no-timeout-claim-false'),('testonly',2,'documented-timeout-claim-false-test-only')]:
            with self.subTest(name=name):
                status, out = self.run_cli(str(FIXTURES/name), '--json')
                result = json.loads(out)
                self.assertEqual(status, code)
                self.assertEqual(len(result['findings']), int(rule is not None))
                if rule:
                    self.assertEqual(result['findings'][0]['rule'], rule)
                else:
                    self.assertEqual(len(result['verified_claims']), 1)

    def test_medium_high(self):
        status, out = self.run_cli(str(FIXTURES/'testonly'), '--fail-on','HIGH','--json')
        self.assertEqual(status,0)
        self.assertEqual(json.loads(out)['findings'][0]['severity'],'MEDIUM')

    def test_ignore(self):
        root = self.tree('with a timeout','requests.get(url, timeout=3)')
        self.assertFalse(audit(root)['findings'])
        self.assertEqual(audit(root,['main.py'])['findings'][0]['evidence'],[{'kind':'no-source'}])

    def test_both_no_source(self):
        result = audit(self.tree('with a timeout. no timeout.',None))
        self.assertEqual(len(result['findings']),2)
        self.assertTrue(all(f['severity']=='HIGH' for f in result['findings']))

    def test_both_test_only(self):
        result = audit(self.tree('with a timeout. no timeout.',extra={'tests/test_x.py':'f(timeout=3)'}))
        self.assertEqual(len(result['findings']),2)
        self.assertTrue(all(f['severity']=='MEDIUM' for f in result['findings']))

    def test_env_never_opened(self):
        root = self.tree('no timeout',extra={'.env':'SECRET_ENV_MARKER'})
        original = Path.open
        def guarded(path,*args,**kwargs):
            if path.name == '.env':
                raise AssertionError('environment file opened')
            return original(path,*args,**kwargs)
        with patch.object(Path,'open',guarded):
            status, out = self.run_cli(str(root),'--json','--verbose')
        self.assertEqual(status,0)
        self.assertNotIn('SECRET_ENV_MARKER',out)

    def test_symlink(self):
        root = self.tree('with a timeout')
        (root/'link.py').symlink_to(root/'main.py')
        self.assertEqual(len(audit(root)['findings']),1)

    def test_docs(self):
        root = self.tree('timeout setting',extra={'docs/nested/a.md':'with a timeout','OTHER.md':'no timeout','examples/README.md':'no timeout'})
        self.assertEqual([c['family'] for c in audit(root)['claims']],['timeout'])

    def test_parse_error(self):
        self.assertTrue(audit(self.tree('with a timeout','def broken('))['errors'])

    def test_no_claim(self):
        self.assertFalse(audit(self.tree('timeout setting','f(timeout=1)'))['findings'])

    def test_version(self):
        with self.assertRaises(SystemExit) as caught:
            with contextlib.redirect_stdout(io.StringIO()) as output:
                main(['--version'])
        self.assertEqual(caught.exception.code,0)
        self.assertEqual(output.getvalue().strip(),'0.1.0')

    def test_usage_error(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
            main(['--unknown'])
        self.assertEqual(caught.exception.code,1)

    def test_self_audit(self):
        result = audit(Path(__file__).resolve().parents[1])
        self.assertEqual(result['claims'],[])
        self.assertEqual(result['findings'],[])

    def test_all_evidence(self):
        result = audit(self.tree('no timeout','f(timeout=1)\ng(timeout=2)'))
        self.assertEqual(len(result['findings'][0]['evidence']),2)


def claim_test(text, family):
    def test(self):
        self.assertEqual({c['family'] for c in extract_claims(text)}, set() if family is None else {family})
    return test

for index, phrase in enumerate(TIMEOUT):
    setattr(AuditTests,'test_positive_phrase_%02d'%index,claim_test(phrase.upper(),'timeout'))
for index, phrase in enumerate(NO_TIMEOUT):
    setattr(AuditTests,'test_negative_phrase_%02d'%index,claim_test(phrase,'no-timeout'))
for index, (text, family) in enumerate([
    ('```text\nwith a timeout\n```',None),('~~~\nno timeout\n~~~',None),
    ('`with a timeout`','timeout'),('requests never time out','no-timeout'),
    ('the timeout parameter is configurable',None),('with a configurable timeout','timeout'),
    ('timeout',None),('not with a timeout',None),('without timeout protection',None),
    ("requests doesn't time out",'no-timeout'),('timeout protected',None),
    ('has timeout',None),('timeouts of',None),('no time limits',None)]):
    setattr(AuditTests,'test_claim_edge_%02d'%index,claim_test(text,family))

for index, (source, expected) in enumerate([
    ('f(timeout=None)',0),('s.settimeout(None)',0),('asyncio.wait(tasks)',0),
    ('asyncio.wait_for(task, 3)',1),('socket.setdefaulttimeout(3)',1),('signal.alarm(3)',1),
    ('f(timeout=value)',1),('f(timeout=(1,2))',1),('s.settimeout(3)',1),
    ('signal.setitimer(1,2)',1),('asyncio.timeout(3)',1),('asyncio.wait(tasks, timeout=3)',1),
    ('import asyncio; import socket; import signal',0),('threading.Timer(3, f)',0),
    ('if elapsed > limit: pass',0),('from asyncio import wait_for as wf; wf(task, 3)',1),
    ('import socket as s; s.setdefaulttimeout(3)',1),('def unused(): f(timeout=3)',1),
    ('asyncio.wait(tasks, timeout=None)',0),('f(timeout=1); g(timeout=2)',1)]):
    def test(self, source=source, expected=expected):
        evidence, error = extract_evidence(source,'main.py')
        self.assertIsNone(error)
        self.assertEqual(len(evidence),expected)
    setattr(AuditTests,'test_evidence_%02d'%index,test)

for index, name in enumerate(['tests/work.py','fixtures/work.py','test_work.py','work_test.py']):
    def test(self,name=name):
        result=audit(self.tree('with a timeout',extra={name:'f(timeout=3)'}))
        self.assertEqual(result['findings'][0]['severity'],'MEDIUM')
    setattr(AuditTests,'test_testonly_%02d'%index,test)

for index, name in enumerate(['docs/work.py','examples/work.py']):
    def test(self,name=name):
        result=audit(self.tree('with a timeout',extra={name:'f(timeout=3)'}))
        self.assertEqual(result['test_only_evidence'],[])
        self.assertEqual(result['production_evidence'],[])
    setattr(AuditTests,'test_exclusion_%02d'%index,test)
