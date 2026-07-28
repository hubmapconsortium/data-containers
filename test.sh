#!/usr/bin/env bash
# Example usage:
# Run specific test in interactive debug mode (will not proceed if linting/formatting errors):
#   ./test.sh -t tests/test_fastq_validator_logic.py::TestFASTQValidatorLogic::test_fastq_groups_good -d
# Same as above, skipping linting/formatting:
#   ./test.sh -n -t tests/test_fastq_validator_logic.py::TestFASTQValidatorLogic::test_fastq_groups_good -d
# Run all tests, skip linting/formatting, no interactive debugger:
#   ./test.sh -n
# Run all tests, pass arbitrary argument directly to pytest:
#   ./test.sh -- hello_pytest

usage() { echo "Usage: $0 [-n] [-t <test_string>]
    -n : skip linting/formatting
    -t : run specific test; use pytest format
        example: tests/test_fastq_validator_logic.py::TestFASTQValidatorLogic::test_fastq_groups_bad
    -d : pass --pdb to pytest for debugging
    -- pass arbitrary other args following ' -- '" 1>&2; exit 1; }

# Define expected options
while getopts "nt:d" opt; do
	case "$opt" in
		n)
			echo "Skipping linting/formatting"
            SKIP="$1"
		;;
		t)
            if [[ "$OPTARG" = "--" ]]; then
                echo "-t must be followed by reference to a test"
                usage
            else
                TEST="$OPTARG"
            fi
        ;;
		d)
            DEBUG="--pdb"
		;;
        ?)
            usage
        ;;
	esac
done
shift $((OPTIND - 1))

# Define -- as delimiter between options and arbitrary args to pass to pytest
[[ $1 = "--" ]] && shift
PARAMS=("$PARAMS$@")

# Run linting/formatting if not skipped with -n
# Respects configs in pyproject.toml / .flake8
# Note: black/isort will auto-format
if [ -z "$SKIP" ]; then
    echo "Running linting/formatting checks..."
    echo "--------"
    echo "black"
    black src
    if [ $? -ne 0 ]; then
        ERROR=1
    fi
    echo "--------"
    echo "isort"
    isort src
    if [ $? -ne 0 ]; then
        ERROR=1
    fi
    echo "--------"
    echo "flake8"
    flake8 src
    if [ $? -ne 0 ]; then
        ERROR=1
    fi
fi

# Exit if linting/formatting errors found
if [[ $ERROR == 1 ]]; then
    echo "Fix linting/formatting errors or pass -n/-no_lint to run tests."
    exit 1
fi

# Run test suite with appropriate args
echo "--------"
echo "running python tests/pytest_runner.py $TEST $DEBUG ${PARAMS[@]}"
python tests/pytest_runner.py "$TEST" "$DEBUG" "${PARAMS[@]}" || exit 1
