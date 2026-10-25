# timeout-claim-audit

Offline audit of documented timeout promises against Python source.

Stdlib-only, offline static analysis. Audited code is never executed and no
network access is used. Environment-file contents are never read. Symlinks
are skipped; analysis stays inside the selected tree.

## Usage

```sh
python3 -m pip install .
timeout-claim-audit ./project
timeout-claim-audit ./project --json --verbose --ignore 'vendor/*'
python3 -m timeout_claim_audit ./project --fail-on HIGH
PYTHONPATH=src python3 -m unittest discover -s tests -v
python3 -m build
```

The console command and module accept the same flags. `--ignore` is repeatable
and matches relative paths using shell glob syntax. `--version` prints 0.1.0.
Exit status is 0 for PASS, 2 for findings at the selected threshold, and 1 for
usage errors. The default threshold is MEDIUM; HIGH keeps MEDIUM findings in
the report but allows a successful exit. JSON includes `findings`,
`verified_claims`, claim locations, production and test-only evidence, parse
or read errors, and status. `--verbose` includes locations and evidence in
text output. Parse failures are reported as errors, not timeout evidence.

## Claim vocabulary

Only root README.md and docs/**/*.md supply claims. Fenced blocks are excluded;
inline code counts as prose. These are the complete case-insensitive phrases:

```text
timeout:
with a timeout; has a timeout; times out; time out; timeout of;
request timeout; connection timeout; read timeout; socket timeout;
timeout support; configurable timeout; timeout-protected; timeout protection
no-timeout:
no timeout; without a timeout; never times out; never time out; does not time out;
doesn't time out; waits indefinitely; waits forever; no time limit
```

## Rules

| Rule | Severity | Condition |
| --- | --- | --- |
| documented-timeout-claim-false | HIGH | Positive family, zero production evidence |
| documented-timeout-claim-false-test-only | MEDIUM | Positive family, evidence only in tests |
| documented-no-timeout-claim-false | HIGH | Negative family, production evidence |
| documented-no-timeout-claim-false-test-only | MEDIUM | Negative family, evidence only in tests |

Each family yields at most one finding, containing its claims and relevant
 evidence. With no production source each claimed family yields HIGH with
`no-source` evidence. Positive claims with production evidence are verified;
negative claims with neither production nor test evidence are verified.
Families are evaluated independently. No claims means PASS.

## Evidence table

| Primitive | Evidence condition |
| --- | --- |
| Any call | Keyword `timeout` other than literal None |
| socket.setdefaulttimeout / any receiver.settimeout | First argument other than literal None |
| signal.alarm / signal.setitimer | Call present |
| asyncio.wait_for / asyncio.timeout | Call present |
| asyncio.wait | Non-None `timeout` keyword |

Import aliases are recognized. One item per source path, line, and kind is
reported. Production excludes tests/, fixtures/, test_*.py, *_test.py, docs/,
and examples/. Test evidence is collected separately. Symlinked files and
directories, build artifacts, virtual environments and Git metadata are skipped.

## Differentiation

- retry-claim-audit checks attempt counts, never time bounds.
- config-claim-audit checks documented default values, never presence of bounds.
- error-claim-audit checks exit codes, exceptions and messages, never bounds.
- privacy-claim-audit checks network and telemetry presence, never bounds.
- bandit and ruff check code issues, never documentation promises.
- requests, httpx and asyncio documentation describe parameters, never whether
  a project's prose agrees with its source.

## Honest limitations

Exact phrases only: a bare `timeout` noun is not a claim. Parameter naming
and a statement that a parameter is configurable do not imply its application.
Sentence negations (no, not, never, without, doesn't, don't, isn't) suppress
positive-family claims. The negative-family phrases themselves still count:
this is an intentional negation asymmetry. The plural-verb variant in the
specification’s explicit negation example is also recognized. Sentences are split at punctuation
and line boundaries; this is a lexical check, not natural-language inference.

Variable values count even if they could evaluate to None. Dead code counts,
including primitives in uncalled functions. Literal `timeout=None` and
`settimeout(None)` are excluded as explicitly unbounded. Bare imports of
asyncio, socket or signal are not evidence. Bare asyncio.wait is not evidence.
threading.Timer and sched schedule work rather than bounding an operation;
retry loops and plain elapsed/deadline comparisons are outside the table.
Third-party mechanisms beyond the named table are outside scope. The tool
cannot prove runtime reachability, actual durations, cancellation behavior,
or whether every operation is covered. Aliases are resolved lexically, without
scope or reassignment analysis. Invalid or unreadable source is reported in
JSON errors; inspect those errors when interpreting results.

Claim examples belong in fences so this project's self-audit has no claims.
The fixtures ship in the source distribution. License: MIT.
