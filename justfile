default:
    @just --list

check:
    pyright .

fmt:
    ruff format .
    ruff check --fix .

upgrade:
    pyupgrade --py313-plus $(find pynixd tests -name '*.py')

cheap: check fmt

# Run tests
test:
    pytest tests/functional tests/unit

# The protocol suite, in its own process.
#
# Not a third path on the line above. `tests/unit` and this suite interfere:
# four of these tests pass alone and fail beside the pynixd suites. One
# process for both is how 57 failures hid behind a green run of 684, and one
# of them was a shipped regression. Issue #33.
protocol-test:
    pytest nix-daemon-protocol/tests

# aitest: check
#     #!/usr/bin/env bash
#     logfile=$(mktemp)
#     echo "Logfile: $logfile"
#     pytest tests -v --timeout=60 --timeout-method=thread -m "not slow and not bench" --durations=50 --maxfail=0 2>&1 | tee $logfile
#     echo "Logfile: $logfile"

# Run all checks
precommit: check fmt test protocol-test

