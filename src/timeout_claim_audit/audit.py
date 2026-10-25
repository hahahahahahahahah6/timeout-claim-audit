"""Static claim and timeout primitive analysis; never executes audited code."""
import argparse
import ast
import fnmatch
import json
import re
from pathlib import Path

TIMEOUT = ("with a timeout", "has a timeout", "times out", "time out", "timeout of", "request timeout", "connection timeout", "read timeout", "socket timeout", "timeout support", "configurable timeout", "timeout-protected", "timeout protection")
NO_TIMEOUT = ("no timeout", "without a timeout", "never times out", "never time out", "does not time out", "doesn't time out", "waits indefinitely", "waits forever", "no time limit")
NEGATION = re.compile(r"\b(?:no|not|never|without|doesn't|don't|isn't)\b", re.I)

def extract_claims(text, path="README.md"):
    claims = []
    fence = None
    for number, line in enumerate(text.splitlines(), 1):
        marker = re.match(r"^\s*(`{3,}|~{3,})", line)
        if marker:
            token = marker.group(1)
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = None
            continue
        if fence:
            continue
        for sentence in re.split(r"[.!?]+", line):
            # Inline backticks are formatting, not an exclusion zone.
            sentence = sentence.replace("`", "")
            for family, phrases in (("timeout", TIMEOUT), ("no-timeout", NO_TIMEOUT)):
                if family == "timeout" and NEGATION.search(sentence):
                    continue
                for phrase in phrases:
                    if re.search(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", sentence, re.I):
                        claims.append(dict(family=family, path=path, line=number, text=sentence.strip(), phrase=phrase))
                        break
    return claims

def _name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return _name(node.value) + "." + node.attr
    return ""

def extract_evidence(text, path):
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError) as exc:
        return [], str(exc)
    aliases = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for item in node.names:
                aliases[item.asname or item.name.split('.')[0]] = item.name if item.asname else item.name.split('.')[0]
        elif isinstance(node, ast.ImportFrom) and node.module:
            for item in node.names:
                aliases[item.asname or item.name] = node.module + "." + item.name
    evidence = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _name(node.func)
        head, dot, tail = name.partition('.')
        name = aliases.get(head, head) + (dot + tail if dot else "")
        kwargs = {k.arg: k.value for k in node.keywords}
        bounded = "timeout" in kwargs and not (isinstance(kwargs['timeout'], ast.Constant) and kwargs['timeout'].value is None)
        first = node.args[0] if node.args else kwargs.get('value', kwargs.get('timeout'))
        disabled = isinstance(first, ast.Constant) and first.value is None
        primitive = ((name == 'socket.setdefaulttimeout' or name.endswith('.settimeout')) and not disabled)
        primitive = primitive or name in ('signal.alarm', 'signal.setitimer', 'asyncio.wait_for', 'asyncio.timeout')
        if bounded or primitive:
            item = dict(path=path, line=node.lineno, kind='timeout-call', call=name)
            evidence[(path, node.lineno, item['kind'])] = item
    return sorted(evidence.values(), key=lambda e: (e['path'], e['line'])), None

def audit(root, ignore=()):
    root = Path(root)
    claims, production, testonly, errors = [], [], [], []
    sources = 0
    def walk(directory):
        for path in sorted(directory.iterdir()):
            if path.is_symlink():
                continue
            relative = path.relative_to(root).as_posix()
            if any(fnmatch.fnmatchcase(relative, pattern) for pattern in ignore):
                continue
            if path.is_dir():
                if path.name not in ('.git', '.venv', 'venv', 'build', 'dist', '__pycache__') and not path.name.endswith('.egg-info'):
                    yield from walk(path)
            elif path.is_file():
                yield path, relative
    for path, relative in walk(root):
        parts = Path(relative).parts
        is_doc = relative == 'README.md' or (parts[0] == 'docs' and path.suffix == '.md')
        is_source = path.suffix == '.py' and 'docs' not in parts[:-1] and 'examples' not in parts[:-1]
        if not is_doc and not is_source:
            continue
        try:
            text = path.read_text(encoding='utf-8')
        except (OSError, UnicodeError) as exc:
            errors.append(dict(path=relative, error=str(exc)))
            continue
        if is_doc:
            claims.extend(extract_claims(text, relative))
        if is_source:
            testing = any(part in ('tests', 'fixtures') for part in parts[:-1]) or path.name.startswith('test_') or path.name.endswith('_test.py')
            if not testing:
                sources += 1
            evidence, error = extract_evidence(text, relative)
            (testonly if testing else production).extend(evidence)
            if error:
                errors.append(dict(path=relative, error=error))
    findings, verified = [], []
    for family in ('timeout', 'no-timeout'):
        selected = [c for c in claims if c['family'] == family]
        if not selected:
            continue
        rule = 'documented-' + family + '-claim-false'
        severity, evidence = None, []
        if not sources:
            severity, evidence = 'HIGH', [dict(kind='no-source')]
        elif family == 'timeout' and not production:
            severity, evidence = ('MEDIUM', testonly) if testonly else ('HIGH', [])
        elif family == 'no-timeout' and production:
            severity, evidence = 'HIGH', production
        elif family == 'no-timeout' and testonly:
            severity, evidence = 'MEDIUM', testonly
        if severity:
            if severity == 'MEDIUM':
                rule += '-test-only'
            findings.append(dict(rule=rule, severity=severity, claims=selected, evidence=evidence))
        else:
            verified.append(dict(family=family, claims=selected, evidence=production if family == 'timeout' else []))
    return dict(findings=findings, verified_claims=verified, claims=claims, production_evidence=production, test_only_evidence=testonly, errors=errors)

class Parser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(__import__('sys').stderr)
        self.exit(1, self.prog + ': error: ' + message + '\n')

def main(argv=None):
    parser = Parser(prog='timeout-claim-audit', description=__doc__)
    parser.add_argument('path', nargs='?', default='.')
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--verbose', action='store_true')
    parser.add_argument('--ignore', action='append', default=[], metavar='GLOB')
    parser.add_argument('--fail-on', choices=('MEDIUM', 'HIGH'), default='MEDIUM')
    parser.add_argument('--version', action='version', version='0.1.0')
    args = parser.parse_args(argv)
    if not Path(args.path).is_dir():
        parser.error('path must be a directory')
    try:
        result = audit(args.path, args.ignore)
    except OSError as exc:
        parser.error(str(exc))
    failed = any(f['severity'] == 'HIGH' or args.fail_on == 'MEDIUM' for f in result['findings'])
    result['status'] = 'FAIL' if failed else 'PASS'
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(result['status'])
        for finding in result['findings']:
            print(finding['severity'] + ' ' + finding['rule'])
            if args.verbose:
                print(json.dumps(finding, indent=2))
        if args.verbose:
            print(json.dumps(result, indent=2))
    return 2 if failed else 0
