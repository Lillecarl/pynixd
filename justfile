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

# aitest: check
#     #!/usr/bin/env bash
#     logfile=$(mktemp)
#     echo "Logfile: $logfile"
#     pytest tests -v --timeout=60 --timeout-method=thread -m "not slow and not bench" --durations=50 --maxfail=0 2>&1 | tee $logfile
#     echo "Logfile: $logfile"

# Run all checks
precommit: check fmt test

