#!/usr/bin/env bash
set -u

# Resolve framework and project paths.
script_dir="$(cd "$(dirname "$0")" && pwd)"
framework_dir="$(cd "$script_dir/.." && pwd)"
if [[ -z "${TESTKIT_PROJECT_ROOT:-}" ]]; then
    echo "missing required environment variable: TESTKIT_PROJECT_ROOT" >&2
    exit 2
fi
repo_dir="$(cd "$TESTKIT_PROJECT_ROOT" && pwd)"
config_file="$repo_dir/.testkit/.config/settings.conf"
export PYTHONDONTWRITEBYTECODE=1
export LC_NUMERIC=C
unset PYTHONPYCACHEPREFIX
config_values="$(python3 "$script_dir/config_loader.py" "$config_file")" || exit 2
eval "$config_values"

# Parse the selected mode, suite and source.
if [[ "$#" -ne 3 ]]; then
    echo "usage: run.sh MODE SUITE SOURCE" >&2
    exit 2
fi
mode="$1"
suite="$2"
source="$3"

case "$mode" in
    test|debug) ;;
    *)
        echo "unknown mode: $mode (expected test or debug)" >&2
        exit 2
        ;;
esac

suite="${suite%/}"
suite="${suite#./}"
if [[ "$suite" == /* ]]; then
    suite_dir="$suite"
else
    suite_dir="$repo_dir/$suite"
fi
if [[ -z "$suite" || ! -d "$suite_dir/$TESTKIT_CASE_DIRECTORY" ]]; then
    echo "invalid test suite: $suite" >&2
    exit 2
fi

# Collect runnable sources and reject missing language wrappers.
find_wrapper() {
    local directory="$1"
    local extension="$2"
    local candidate
    wrapper=""
    while IFS= read -r candidate; do
        if [[ -f "$candidate" && "${candidate##*.}" == "$extension" ]]; then
            wrapper="$candidate"
            return 0
        fi
    done < <(compgen -G "$directory/$TESTKIT_WRAPPER_PATTERN" || true)
    return 1
}

sources=()
source_was_explicit=0
if [[ -n "$source" ]]; then
    source_was_explicit=1
    if [[ -f "$suite_dir/$source" ]]; then
        case "$source" in
            $TESTKIT_WRAPPER_PATTERN)
                echo "selected source should not be a test file" >&2
                exit 2
                ;;
            *.c|*.cpp|*.py)
                sources+=("$suite_dir/$source")
                ;;
            *)
                echo "error: unsupported file extension in source: $source (supported: .c, .cpp, .py)" >&2
                exit 2
                ;;
        esac
    else
        echo "error: source file not found: $suite_dir/$source" >&2
        exit 2
    fi
else
    while IFS= read -r source; do
        [[ -f "$source" ]] || continue
        source_name="${source##*/}"
        [[ "$source_name" == $TESTKIT_WRAPPER_PATTERN ]] && continue
        case "$source" in *.c|*.cpp|*.py) sources+=("$source") ;; esac
    done < <(compgen -G "$suite_dir/*" || true)

    if [[ ${#sources[@]} -eq 0 ]]; then
        echo "no C, C++ or Python source found in $suite" >&2
        exit 2
    fi
fi

runnable_sources=()
for source in "${sources[@]}"; do
    extension="${source##*.}"
    if find_wrapper "$suite_dir" "$extension"; then
        runnable_sources+=("$source")
    elif [[ "$source_was_explicit" -eq 1 ]]; then
        echo "$(basename "$source") requires a same-language wrapper matching '$TESTKIT_WRAPPER_PATTERN'" >&2
        exit 2
    else
        echo "skip $(basename "$source"): $(basename "$wrapper") not found" >&2
    fi
done
sources=("${runnable_sources[@]}")
if [[ ${#sources[@]} -eq 0 ]]; then
    echo "no source with a matching language wrapper found in $suite" >&2
    exit 2
fi

if [[ "$mode" == "debug" && ${#sources[@]} -ne 1 ]]; then
    echo "debug requires exactly one source; specify SOURCE (include its extension if the name is ambiguous)" >&2
    exit 2
fi

# Keep execution and summary order deterministic across filesystems.
sorted_sources=()
while IFS= read -r source; do
    sorted_sources+=("$source")
done < <(printf '%s\n' "${sources[@]}" | LC_ALL=C sort)
sources=("${sorted_sources[@]}")

# Check mode-specific tools.
required_commands=()
needs_native=0
needs_python=0
for source in "${sources[@]}"; do
    [[ "$source" == *.c || "$source" == *.cpp ]] && needs_native=1
    [[ "$source" == *.py ]] && needs_python=1
done
if [[ "$needs_native" -eq 1 ]]; then
    if [[ "$mode" == "debug" ]]; then
        required_commands+=("gdb")
    elif [[ "$TESTKIT_USE_VALGRIND" == "1" ]]; then
        required_commands+=("valgrind")
    fi
fi
[[ "$needs_python" -eq 1 ]] && required_commands+=("python3")

for required_command in "${required_commands[@]}"; do
    if ! command -v "$required_command" >/dev/null 2>&1; then
        echo "required command not found: $required_command" >&2
        exit 127
    fi
done

# Compile or load each source, then dispatch to its test or debug tool.
work_dir="$(mktemp -d "${TMPDIR:-/tmp}/testkit.XXXXXX")" || exit 1
cleanup() {
    rm -rf -- "$work_dir"
}
trap cleanup EXIT
framework_object="$work_dir/testkit.o"
framework_ready=0

status=0
result_names=()
result_statuses=()
valgrind_statuses=()
result_times=()

record_result() {
    local result="$1"
    local valgrind_status="${2:--1}"
    local elapsed_time="${3:--}"
    result_names+=("$source_name")
    result_statuses+=("$result")
    valgrind_statuses+=("$valgrind_status")
    result_times+=("$elapsed_time")
    [[ "$result" -ne 0 || "$valgrind_status" -gt 0 ]] && status=1
}

read_timing() {
    local elapsed_ms
    local timed_calls
    elapsed_time="-"
    if read -r elapsed_ms timed_calls <"$timing_file" 2>/dev/null &&
       [[ "$elapsed_ms" =~ ^[0-9]+([.][0-9]+)?$ && "$timed_calls" =~ ^[0-9]+$ ]] &&
       [[ "$timed_calls" -gt 0 ]]; then
        printf -v elapsed_time '%.6f ms' "$elapsed_ms"
    fi
}

read_case_status() {
    local fallback="$1"
    local case_passed
    local case_failed
    case_status="$fallback"
    if read -r case_passed case_failed <"$result_file" 2>/dev/null &&
       [[ "$case_passed" =~ ^[0-9]+$ && "$case_failed" =~ ^[0-9]+$ ]]; then
        [[ "$case_failed" -eq 0 ]] && case_status=0 || case_status=1
    fi
}

print_source_header() {
    printf '\n-- %s --\n' "$source_name"
}

for source_file in "${sources[@]}"; do
    source_name="$(basename "$source_file")"
    selected="${source_name%.*}"
    extension="${source_name##*.}"
    binary="$work_dir/$selected"
    result_file="$work_dir/$selected.result"
    timing_file="$work_dir/$selected.timing"
    printf '' >"$result_file"
    printf '' >"$timing_file"
    source_status=0
    valgrind_status=-1

    if [[ "$extension" == "py" ]]; then
        find_wrapper "$suite_dir" "$extension" || exit 2
        print_source_header
        case "$mode" in
            test)
                TESTKIT_SOURCE_FILE="$source_file" TESTKIT_DATA_DIR="$suite_dir/$TESTKIT_CASE_DIRECTORY" \
                    TESTKIT_CASE_PATTERN="$TESTKIT_CASE_PATTERN" TESTKIT_RESULT_FILE="$result_file" \
                    TESTKIT_TIMING_FILE="$timing_file" \
                    PYTHONPATH="$framework_dir/src${PYTHONPATH:+:$PYTHONPATH}" \
                    python3 "$wrapper"
                source_status=$?
                ;;
            debug)
                TESTKIT_SOURCE_FILE="$source_file" TESTKIT_DATA_DIR="$suite_dir/$TESTKIT_CASE_DIRECTORY" \
                    TESTKIT_CASE_PATTERN="$TESTKIT_CASE_PATTERN" TESTKIT_RESULT_FILE="$result_file" \
                    PYTHONPATH="$framework_dir/src${PYTHONPATH:+:$PYTHONPATH}" \
                    python3 -m pdb "$wrapper"
                source_status=$?
                ;;
        esac
        read_case_status "$source_status"
        read_timing
        record_result "$case_status" -1 "$elapsed_time"
        continue
    elif [[ "$extension" == "cpp" ]]; then
        compiler="g++"
        standard="-std=c++17"
        optimization="-O$TESTKIT_CPP_OPTIMIZATION"
        find_wrapper "$suite_dir" "$extension" || exit 2
    else
        compiler="gcc"
        standard="-std=c11"
        optimization="-O$TESTKIT_C_OPTIMIZATION"
        find_wrapper "$suite_dir" "$extension" || exit 2
    fi

    if [[ "$framework_ready" -eq 0 ]]; then
        gcc -std=c11 -g "-O$TESTKIT_C_OPTIMIZATION" -Wall -Wextra -I"$framework_dir/include" \
            -c "$framework_dir/src/testkit.c" -o "$framework_object" || exit 1
        framework_ready=1
    fi
    print_source_header
    "$compiler" "$standard" -g "$optimization" -Wall -Wextra \
        -I"$framework_dir/include" \
        -DTESTKIT_SOURCE_PATH="\"$source_file\"" \
        -DTESTKIT_DATA_DIR="\"$suite_dir/$TESTKIT_CASE_DIRECTORY\"" \
        "$wrapper" "$framework_object" \
        -o "$binary"
    source_status=$?
    if [[ "$source_status" -ne 0 ]]; then
        record_result "$source_status"
        continue
    fi
    case "$mode" in
        test)
            TESTKIT_CASE_PATTERN="$TESTKIT_CASE_PATTERN" TESTKIT_RESULT_FILE="$result_file" \
                TESTKIT_TIMING_FILE="$timing_file" "$binary"
            source_status=$?
            if [[ "$TESTKIT_USE_VALGRIND" == "1" ]]; then
                valgrind_log="$work_dir/$selected.valgrind.log"
                TESTKIT_CASE_PATTERN="$TESTKIT_CASE_PATTERN" TESTKIT_QUIET=1 valgrind \
                    --leak-check=full \
                    --show-leak-kinds=all \
                    --track-origins=yes \
                    --error-exitcode=1 \
                    --log-file="$valgrind_log" \
                    "$binary"
                cat "$valgrind_log"
                if grep -q 'ERROR SUMMARY: 0 errors' "$valgrind_log" &&
                   grep -q 'in use at exit: 0 bytes in 0 blocks' "$valgrind_log"; then
                    valgrind_status=0
                else
                    valgrind_status=1
                fi
            fi
            ;;
        debug)
            TESTKIT_CASE_PATTERN="$TESTKIT_CASE_PATTERN" TESTKIT_RESULT_FILE="$result_file" gdb --args "$binary"
            source_status=$?
            ;;
    esac
    read_case_status "$source_status"
    read_timing
    record_result "$case_status" "$valgrind_status" "$elapsed_time"
done

# Present test and Valgrind results in separate columns.
passed=0
failed=0
printf '\nSource summary\n'
printf '%s\n' '--------------'
source_width=20
result_width=8
valgrind_width=10
show_valgrind=0
for source_name in "${result_names[@]}"; do
    (( ${#source_name} + 4 > source_width )) && source_width=$((${#source_name} + 4))
done
for valgrind_status in "${valgrind_statuses[@]}"; do
    [[ "$valgrind_status" -ge 0 ]] && show_valgrind=1
done
if [[ "$show_valgrind" -eq 1 ]]; then
    printf '  %-*s  %-*s  %-*s  %s\n' \
        "$source_width" "SOURCE" "$result_width" "RESULT" \
        "$valgrind_width" "VALGRIND" "TIME (total)"
else
    printf '  %-*s  %-*s  %s\n' \
        "$source_width" "SOURCE" "$result_width" "RESULT" "TIME (total)"
fi
for index in "${!result_names[@]}"; do
    display_time="${result_times[index]}"
    printf '  %-*s  ' "$source_width" "${result_names[index]}"
    if [[ "${result_statuses[index]}" -eq 0 ]]; then
        result_label="[PASS]"
        printf '\033[32m%s\033[0m' "$result_label"
        ((passed++)) || true
    else
        result_label="[FAIL]"
        printf '\033[31m%s\033[0m' "$result_label"
        display_time="N/A"
        ((failed++)) || true
    fi
    printf '%*s' "$((result_width + 2 - ${#result_label}))" ''
    if [[ "$show_valgrind" -eq 1 ]]; then
        if [[ "${valgrind_statuses[index]}" -ge 0 ]]; then
            valgrind_label="[PASS]"
            if [[ "${valgrind_statuses[index]}" -eq 0 ]]; then
                printf '\033[32m%s\033[0m' "$valgrind_label"
            else
                valgrind_label="[FAIL]"
                printf '\033[31m%s\033[0m' "$valgrind_label"
            fi
        else
            valgrind_label="-"
            printf '%s' "$valgrind_label"
        fi
        valgrind_padding=$((valgrind_width + 2 - ${#valgrind_label}))
        (( valgrind_padding < 2 )) && valgrind_padding=2
        printf '%*s' "$valgrind_padding" ''
    fi
    printf '%s\n' "$display_time"
done
printf 'Total: %d passed, %d failed\n' "$passed" "$failed"
exit "$status"
