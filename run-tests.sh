#!/bin/sh
# Run the qubes-dashboard test suite, then each tools/ window's own suite.
# Stdlib unittest only, no third-party deps.
#
# Does NOT use "python3 -m unittest discover". Discovery refuses a start
# directory that is not an importable package: with "-s tests -t ." it requires
# tests/__init__.py to exist, and if that file is ever missing (a partial copy,
# a stripped tarball, a filesystem that ate an empty-ish file) you get an opaque
# "ImportError: Start directory is not importable" instead of a test result.
#
# Every test module carries its own sys.path shim and a unittest.main() guard,
# so running them as scripts is layout-independent and version-independent.
set -u
cd "$(dirname "$0")"

failed=0
ran=0
for test_file in tests/test_*.py; do
    [ -e "$test_file" ] || continue
    ran=$((ran + 1))
    printf '\n=== %s\n' "$test_file"
    if ! python3 "$test_file"; then
        failed=$((failed + 1))
    fi
done

if [ "$ran" -eq 0 ]; then
    printf '\nNo test modules found under tests/\n' >&2
    exit 1
fi

# The windows under tools/ each carry their own suite; run them as they are.
tools_failed=0
tools_ran=0
for tool_tests in tools/*/run-tests.sh; do
    [ -e "$tool_tests" ] || continue
    tools_ran=$((tools_ran + 1))
    printf '\n##### %s\n' "${tool_tests%/run-tests.sh}"
    if ! sh "$tool_tests"; then
        tools_failed=$((tools_failed + 1))
    fi
done

if [ "$failed" -ne 0 ] || [ "$tools_failed" -ne 0 ]; then
    printf '\nFAILED: %d of %d test module(s) and %d of %d tools/ suite(s) did not pass\n' \
        "$failed" "$ran" "$tools_failed" "$tools_ran"
    exit 1
fi

printf '\nAll %d test modules and %d tools/ suites passed\n' "$ran" "$tools_ran"
