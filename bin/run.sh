#!/usr/bin/env bash
set -u

# Resolve framework paths and export the configured discovery patterns.
script_dir="$(cd "$(dirname "$0")" && pwd)"
framework_dir="$(cd "$script_dir/.." && pwd)"
repo_dir="$(cd "${TESTKIT_PROJECT_ROOT:-$framework_dir/..}" && pwd)"
config_file="$repo_dir/.testkit/.config/settings.conf"
export PYTHONDONTWRITEBYTECODE=1
export LC_NUMERIC=C
unset PYTHONPYCACHEPREFIX
config_values="$(python3 "$script_dir/ui.py" config-shell "$config_file")" || exit 2
eval "$config_values"
tool="test"

# Locate same-language wrappers for the selected source or suite.
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

has_wrapper() {
    local candidate
    while IFS= read -r candidate; do
        [[ -f "$candidate" ]] && return 0
    done < <(compgen -G "$1/$TESTKIT_WRAPPER_PATTERN" || true)
    return 1
}

# Resolve the requested mode, suite, and optional source selection.
case "${1:-}" in
    --tool=*) tool="${1#--tool=}"; shift ;;
    test|debug) tool="$1"; shift ;;
esac

case "$tool" in
    test|debug) ;;
    *)
        echo "unknown tool: $tool (expected test or debug)" >&2
        exit 2
        ;;
esac

suite="${1:-}"
source="${2:-}"

if [[ -n "$suite" && -z "$source" && ! -d "$repo_dir/$suite" && ! -d "$suite" ]]; then
    source="$suite"
    suite=""
fi

if [[ -z "$suite" ]]; then
    current_dir="$PWD"
    while [[ "$current_dir" != "$repo_dir" && "$current_dir" != "/" ]]; do
        if has_wrapper "$current_dir" && [[ -d "$current_dir/$TESTKIT_CASE_DIRECTORY" ]]; then
            suite="${current_dir#"$repo_dir/"}"
            break
        fi
        current_dir="$(dirname "$current_dir")"
    done
fi

suite="${suite%/}"
suite="${suite#./}"
if [[ "$suite" == /* ]]; then
    suite_dir="$suite"
else
    suite_dir="$repo_dir/$suite"
fi
if [[ -z "$suite" || ! -d "$suite_dir/$TESTKIT_CASE_DIRECTORY" ]] || ! has_wrapper "$suite_dir"; then
    echo "usage: ./test/bin/run.sh [test|debug] TEST_SUITE [SOURCE]" >&2
    echo "or run test/bin/run.sh [tool] [source] from a test suite directory" >&2
    exit 2
fi

# Collect runnable sources and reject missing language wrappers.
sources=()
source_was_explicit=0
if [[ -n "$source" ]]; then
    source_was_explicit=1
    if [[ "$source" == *.c || "$source" == *.cpp || "$source" == *.py ]]; then
        [[ -f "$suite_dir/$source" ]] && sources+=("$suite_dir/$source")
    else
        [[ -f "$suite_dir/$source.c" ]] && sources+=("$suite_dir/$source.c")
        [[ -f "$suite_dir/$source.cpp" ]] && sources+=("$suite_dir/$source.cpp")
        [[ -f "$suite_dir/$source.py" ]] && sources+=("$suite_dir/$source.py")
    fi
else
    while IFS= read -r source; do
        [[ -f "$source" ]] || continue
        source_name="${source##*/}"
        [[ "$source_name" == $TESTKIT_WRAPPER_PATTERN ]] && continue
        case "$source" in *.c|*.cpp|*.py) sources+=("$source") ;; esac
    done < <(compgen -G "$suite_dir/*" || true)
fi

if [[ ${#sources[@]} -eq 0 ]]; then
    echo "no C, C++ or Python source found in $suite" >&2
    exit 2
fi

filtered_sources=()
for source in "${sources[@]}"; do
    source_name="${source##*/}"
    [[ "$source_name" == $TESTKIT_WRAPPER_PATTERN ]] || filtered_sources+=("$source")
done
sources=("${filtered_sources[@]}")
if [[ ${#sources[@]} -eq 0 ]]; then
    echo "selected source matches the test wrapper glob '$TESTKIT_WRAPPER_PATTERN'" >&2
    exit 2
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

# Keep execution and summary order deterministic across filesystems.
sorted_sources=()
while IFS= read -r source; do
    sorted_sources+=("$source")
done < <(printf '%s\n' "${sources[@]}" | LC_ALL=C sort)
sources=("${sorted_sources[@]}")

if [[ "$tool" == "debug" && ${#sources[@]} -ne 1 ]]; then
    echo "debug requires exactly one source; specify SOURCE (include its extension if the name is ambiguous)" >&2
    exit 2
fi

# Check mode-specific tools before creating any build workspace.
required_commands=()
case "$tool" in
    test|debug)
        needs_native=0
        needs_python=0
        for source in "${sources[@]}"; do
            [[ "$source" == *.c || "$source" == *.cpp ]] && needs_native=1
            [[ "$source" == *.py ]] && needs_python=1
        done
        if [[ "$needs_native" -eq 1 ]]; then
            if [[ "$tool" == "debug" ]]; then
                required_commands+=("gdb")
            elif [[ "$TESTKIT_USE_VALGRIND" == "1" ]]; then
                required_commands+=("valgrind")
            fi
        fi
        [[ "$needs_python" -eq 1 ]] && required_commands+=("python3")
        ;;
esac
for required_command in "${required_commands[@]}"; do
    if ! command -v "$required_command" >/dev/null 2>&1; then
        echo "required command not found: $required_command" >&2
        exit 127
    fi
done

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

# Compile or load each source, then dispatch to its test or debug tool.
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
        case "$tool" in
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
    case "$tool" in
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
