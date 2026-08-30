#!/usr/bin/env python3
import curses
import datetime
import fnmatch
import hashlib
import json
import math
import os
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path


DEFAULTS = {
    "search_roots": [],
    "wrapper_pattern": "test.*",
    "case_directory": "data",
    "case_pattern": "*.case",
    "sort_order": "time",
    "use_valgrind": False,
    "c_optimization": "0",
    "cpp_optimization": "0",
}
SUPPORTED_SUFFIXES = {".c", ".cpp", ".py"}
ESC_DELAY_MS = 25
DISCOVERY_CACHE_TTL = 5.0
FRAMEWORK_DIR = Path(__file__).resolve().parent.parent
_DISCOVERY_MEMORY_CACHE = {}
LANGUAGE_LABELS = {".c": "C", ".cpp": "C++", ".py": "Python"}
NATIVE_OPTIMIZATION_LEVELS = {"0", "1", "2", "3", "g", "s", "fast"}


@dataclass
class Entry:
    label: str
    value: str
    modified: float = 0.0


# Configuration is shared by discovery, menus, and the shell runner.
def normalize_search_roots(search_roots):
    if not isinstance(search_roots, list):
        raise ValueError("search roots must be a list")
    roots = []
    for configured_root in search_roots:
        if not isinstance(configured_root, str) or not configured_root.strip():
            raise ValueError("each search root must be a non-empty path")
        root = Path(configured_root).expanduser()
        if not root.is_absolute():
            raise ValueError("each search root must be an absolute path")
        roots.append(str(root.resolve()))
    return roots


def load_config(path, allow_empty_search=False):
    config = dict(DEFAULTS)
    config["search_roots"] = normalize_search_roots(DEFAULTS["search_roots"])
    try:
        loaded = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        if not allow_empty_search:
            validate_config(config)
        return config
    except (json.JSONDecodeError, OSError) as error:
        raise ValueError(f"cannot read config {path}: {error}") from error
    for key in DEFAULTS:
        if key in loaded:
            config[key] = loaded[key]
    config["search_roots"] = normalize_search_roots(config["search_roots"])
    validate_config(config, allow_empty_search)
    return config


def save_config(path, config):
    validate_config(config)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    temporary.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8")
    temporary.replace(target)
    try:
        discovery_cache_file(config_path=target).unlink()
    except FileNotFoundError:
        pass


def validate_config(config, allow_empty_search=False):
    if not isinstance(config["search_roots"], list):
        raise ValueError("search roots must be a list")
    if not config["search_roots"] and not allow_empty_search:
        raise ValueError("search roots are not configured; run make menuconfig")
    if any(not isinstance(root, str) or not root.strip() for root in config["search_roots"]):
        raise ValueError("each search root must be a non-empty path")
    if any(not Path(root).is_absolute() for root in config["search_roots"]):
        raise ValueError("each search root must be an absolute path")
    for key in ("wrapper_pattern", "case_directory", "case_pattern"):
        if not isinstance(config[key], str) or not config[key].strip():
            raise ValueError(f"{key} cannot be empty")
    wrapper_pattern = config["wrapper_pattern"]
    if (Path(wrapper_pattern).name != wrapper_pattern or
            wrapper_pattern.count("*") != 1 or
            not wrapper_pattern.endswith(".*") or
            any(character in wrapper_pattern for character in "?[]")):
        raise ValueError("test wrapper glob must use the form <name>.*")
    case_dir = Path(config["case_directory"])
    if case_dir.is_absolute() or len(case_dir.parts) != 1 or case_dir.name in ("", ".", ".."):
        raise ValueError("case directory must be one relative directory name")
    if Path(config["case_pattern"]).name != config["case_pattern"]:
        raise ValueError("test case glob must not contain a directory path")
    if config["sort_order"] not in ("time", "name"):
        raise ValueError("sort_order must be 'time' or 'name'")
    if not isinstance(config["use_valgrind"], bool):
        raise ValueError("use_valgrind must be true or false")
    validate_optimization(config)


def validate_optimization(config):
    for language in ("c", "cpp"):
        level = str(config[f"{language}_optimization"])
        if level not in NATIVE_OPTIMIZATION_LEVELS:
            raise ValueError(f"{language}_optimization must be one of 0, 1, 2, 3, g, s, fast")
        config[f"{language}_optimization"] = level


# Small curses widgets used by every interactive workflow.
def safe_addstr(window, row, column, text, attributes=0):
    height, width = window.getmaxyx()
    if row < 0 or row >= height or column >= width:
        return
    try:
        window.addnstr(row, column, text, max(0, width - column - 1), attributes)
    except curses.error:
        pass


def complete_path(text, cursor):
    segment_start = text.rfind(";", 0, cursor) + 1
    segment = text[segment_start:cursor].lstrip()
    leading_space = len(text[segment_start:cursor]) - len(segment)
    if not segment:
        before = text[:segment_start] + " " * leading_space
        updated = before + os.sep + text[cursor:]
        return updated, len(before) + 1, "Tab: list paths under /"
    expanded = Path(segment).expanduser()
    if not expanded.is_absolute():
        return text, cursor, "Use an absolute path"
    search_path = expanded
    if segment.endswith(os.sep):
        directory = search_path
        prefix = ""
    else:
        directory = search_path.parent
        prefix = search_path.name
    try:
        matches = sorted(
            (path for path in directory.iterdir()
             if path.name.startswith(prefix) and (prefix.startswith(".") or not path.name.startswith("."))),
            key=lambda path: path.name.casefold())
    except OSError:
        return text, cursor, "No path matches"
    if not matches:
        return text, cursor, "No path matches"

    common_name = os.path.commonprefix([path.name for path in matches])
    if expanded.is_absolute():
        completed = directory / common_name
        replacement = str(completed)
    else:
        parent = Path(segment).parent
        replacement = common_name if str(parent) == "." else str(parent / common_name)
    if len(matches) == 1 and matches[0].is_dir():
        replacement += os.sep
    before = text[:segment_start] + " " * leading_space
    updated = before + replacement + text[cursor:]
    new_cursor = len(before) + len(replacement)
    hint = "  ".join(path.name + (os.sep if path.is_dir() else "") for path in matches[:5])
    if len(matches) > 5:
        hint += "  ..."
    return updated, new_cursor, hint


def fuzzy_token_score(label, token):
    label = label.casefold()
    token = token.casefold()
    if not token:
        return 0
    if any(character in token for character in "*?["):
        return 30 if fnmatch.fnmatch(label, token) or fnmatch.fnmatch(Path(label).name, token) else None
    if label == token:
        return 0
    components = label.replace("\\", "/").split("/")
    if token in components:
        return 2
    if label.startswith(token):
        return 5
    if any(component.startswith(token) for component in components):
        return 8
    position = label.find(token)
    if position >= 0:
        return 20 + position

    positions = []
    next_position = 0
    for character in token:
        position = label.find(character, next_position)
        if position < 0:
            return None
        positions.append(position)
        next_position = position + 1
    gaps = positions[-1] - positions[0] + 1 - len(token)
    return 100 + positions[0] + gaps * 2


def search_score(label, query):
    try:
        tokens = shlex.split(query)
    except ValueError:
        tokens = query.split()
    score = 0
    for token in tokens:
        excluded = token.startswith("!")
        value = token[1:] if excluded else token
        token_score = fuzzy_token_score(label, value)
        if excluded:
            if value and token_score is not None:
                return None
        elif token_score is None:
            return None
        else:
            score += token_score
    return score


def complete_search(text, choices):
    scored = [(search_score(choice, text), choice) for choice in choices]
    matches = [choice for score, choice in sorted(
        (item for item in scored if item[0] is not None),
        key=lambda item: (item[0], item[1].casefold()))]
    if not matches:
        return text, len(text), "No search matches"
    if len(matches) == 1:
        completed = matches[0]
    else:
        common = os.path.commonprefix(matches)
        query = text.strip().casefold()
        plain_query = query and not any(character in query for character in " !*?[]\"'")
        completed = common if plain_query and query in common.casefold() else text
    hint = "  ".join(matches[:5])
    if len(matches) > 5:
        hint += "  ..."
    return completed, len(completed), hint


def prompt_text(window, label, current, allow_empty=False, edit_current=False,
                complete_paths=False, completions=None):
    text = current if edit_current else ""
    cursor = len(text)
    completion_enabled = complete_paths or bool(completions)
    edit_help = "Enter: apply  Esc: cancel"
    if completion_enabled:
        edit_help += "  Tab: complete"
    hint = edit_help

    def show_with_help(message):
        return f"{message}  |  {edit_help}" if message else edit_help

    cancelled = False
    try:
        curses.curs_set(1)
    except curses.error:
        pass
    while True:
        height, width = window.getmaxyx()
        row = max(0, height - 2)
        hint_row = max(0, row - 1)
        window.move(hint_row, 0)
        window.clrtoeol()
        safe_addstr(window, hint_row, 0, hint, curses.A_DIM)
        window.move(row, 0)
        window.clrtoeol()
        prompt = f"{label}> "
        available = max(1, width - len(prompt) - 1)
        view_start = max(0, cursor - available + 1)
        visible = text[view_start:view_start + available]
        safe_addstr(window, row, 0, prompt + visible, curses.A_BOLD)
        try:
            window.move(row, min(width - 1, len(prompt) + cursor - view_start))
        except curses.error:
            pass
        window.refresh()
        key = window.get_wch()
        if key in ("\n", "\r"):
            break
        if key == "\x1b":
            cancelled = True
            break
        if key in (curses.KEY_BACKSPACE, "\b", "\x7f"):
            if cursor > 0:
                text = text[:cursor - 1] + text[cursor:]
                cursor -= 1
            hint = edit_help
        elif key == curses.KEY_DC:
            text = text[:cursor] + text[cursor + 1:]
            hint = edit_help
        elif key in (curses.KEY_LEFT, "\x02"):
            cursor = max(0, cursor - 1)
        elif key in (curses.KEY_RIGHT, "\x06"):
            cursor = min(len(text), cursor + 1)
        elif key in (curses.KEY_HOME, "\x01"):
            cursor = 0
        elif key in (curses.KEY_END, "\x05"):
            cursor = len(text)
        elif key == "\x15":
            text = text[cursor:]
            cursor = 0
            hint = edit_help
        elif key == "\t" and complete_paths:
            text, cursor, completion_hint = complete_path(text, cursor)
            hint = show_with_help(completion_hint)
        elif key == "\t" and completions:
            text, cursor, completion_hint = complete_search(text, completions)
            hint = show_with_help(completion_hint)
        elif isinstance(key, str) and key.isprintable():
            text = text[:cursor] + key + text[cursor:]
            cursor += len(key)
            hint = edit_help
    try:
        curses.curs_set(0)
    except curses.error:
        pass
    if cancelled:
        return current
    value = text.strip()
    return value if value or allow_empty else current


def pick(window, title, entries, order, allow_search=True):
    query = ""
    selected = 0
    offset = 0
    while True:
        scored = [(search_score(entry.label, query), entry) for entry in entries]
        scored = [item for item in scored if item[0] is not None]
        if query and order == "time":
            scored.sort(key=lambda item: (item[0], -item[1].modified, item[1].label.casefold()))
        elif query:
            scored.sort(key=lambda item: (item[0], item[1].label.casefold()))
        elif order == "time":
            scored.sort(key=lambda item: (-item[1].modified, item[1].label.casefold()))
        else:
            scored.sort(key=lambda item: item[1].label.casefold())
        filtered = [entry for _, entry in scored]
        selected = min(selected, max(0, len(filtered) - 1))

        window.erase()
        height, width = window.getmaxyx()
        safe_addstr(window, 0, 0, title, curses.A_BOLD)
        sort_label = "recently modified" if order == "time" else "name"
        help_text = "Up/Down or j/k: move  Enter: select  Esc: back"
        if allow_search:
            help_text += "  /: search"
        safe_addstr(window, 1, 0, f"Sort: {sort_label}  {help_text}", curses.A_DIM)
        if query:
            safe_addstr(window, 2, 0, f"Search: {query}", curses.A_BOLD)
        list_top = 4
        visible = max(1, height - list_top - 1)
        if selected < offset:
            offset = selected
        elif selected >= offset + visible:
            offset = selected - visible + 1
        if not filtered:
            safe_addstr(window, list_top, 2, "No matches. Press / to search again or Esc to go back.", curses.A_DIM)
        for row, entry in enumerate(filtered[offset:offset + visible], list_top):
            absolute = offset + row - list_top
            marker = "> " if absolute == selected else "  "
            attributes = curses.A_REVERSE if absolute == selected else 0
            timestamp = ""
            if entry.modified > 0 and math.isfinite(entry.modified):
                timestamp = datetime.datetime.fromtimestamp(entry.modified).strftime("  %Y-%m-%d %H:%M")
            safe_addstr(window, row, 0, marker + entry.label + timestamp, attributes)
        window.refresh()
        key = window.getch()
        if key in (curses.KEY_UP, ord("k")) and filtered:
            selected = (selected - 1) % len(filtered)
        elif key in (curses.KEY_DOWN, ord("j")) and filtered:
            selected = (selected + 1) % len(filtered)
        elif key == curses.KEY_PPAGE and filtered:
            selected = max(0, selected - visible)
        elif key == curses.KEY_NPAGE and filtered:
            selected = min(len(filtered) - 1, selected + visible)
        elif key in (curses.KEY_HOME, ord("g")):
            selected = 0
        elif key in (curses.KEY_END, ord("G")) and filtered:
            selected = len(filtered) - 1
        elif key in (10, 13, curses.KEY_ENTER) and filtered:
            return filtered[selected].value
        elif key == 27:
            return None
        elif key == ord("/") and allow_search:
            query = prompt_text(
                window, "Search (leave empty to clear)", query, allow_empty=True,
                completions=[entry.label for entry in entries])
            selected = 0
            offset = 0


def confirm(window, title, message, question="Replace existing template files?"):
    selected = False
    while True:
        window.erase()
        safe_addstr(window, 0, 0, title, curses.A_BOLD)
        safe_addstr(window, 2, 0, message)
        safe_addstr(window, 4, 0, question, curses.A_BOLD)
        no_attributes = curses.A_REVERSE if not selected else 0
        yes_attributes = curses.A_REVERSE if selected else 0
        safe_addstr(window, 6, 2, "No", no_attributes)
        safe_addstr(window, 6, 10, "Yes", yes_attributes)
        safe_addstr(window, 8, 0, "Left/Right or h/l: move  Enter: confirm  Esc: cancel", curses.A_DIM)
        window.refresh()
        key = window.getch()
        if key in (curses.KEY_LEFT, ord("h")):
            selected = False
        elif key in (curses.KEY_RIGHT, ord("l")):
            selected = True
        elif key in (ord("y"), ord("Y")):
            return True
        elif key in (ord("n"), ord("N")):
            return False
        elif key in (10, 13, curses.KEY_ENTER):
            return selected
        elif key == 27:
            return None


def choose_value(window, title, choices, current):
    """Select one configuration value without opening a text editor."""
    selected = next(
        (index for index, (_, value) in enumerate(choices) if value == current), 0)
    while True:
        window.erase()
        safe_addstr(window, 0, 0, title, curses.A_BOLD)
        safe_addstr(
            window, 1, 0,
            "Up/Down or j/k: move  Enter: apply  Esc: cancel",
            curses.A_DIM)
        for row, (label, _) in enumerate(choices, 3):
            attributes = curses.A_REVERSE if row - 3 == selected else 0
            safe_addstr(window, row, 2, label, attributes)
        window.refresh()
        key = window.getch()
        if key in (curses.KEY_UP, ord("k")):
            selected = (selected - 1) % len(choices)
        elif key in (curses.KEY_DOWN, ord("j")):
            selected = (selected + 1) % len(choices)
        elif key in (10, 13, curses.KEY_ENTER):
            return choices[selected][1]
        elif key == 27:
            return current


# Source and wrapper matching rules define which suites are runnable.
def matching_wrapper(suite, source, config):
    return any(path.is_file() and path.suffix == source.suffix
               for path in suite.glob(config["wrapper_pattern"]))


def suite_sources(suite, config):
    return [path for path in suite.iterdir()
            if path.is_file() and path.suffix in SUPPORTED_SUFFIXES
            and not fnmatch.fnmatch(path.name, config["wrapper_pattern"])]


def runnable_sources(suite, config):
    return [path for path in suite_sources(suite, config)
            if matching_wrapper(suite, path, config)]


def newest_timestamp(paths):
    timestamps = []
    for path in paths:
        try:
            timestamps.append(path.stat().st_mtime)
        except OSError:
            pass
    return max(timestamps, default=0.0)


# Cache regular test discovery because recursive project scans are frequent.
def discovery_cache_file(project_root=None, config_path=None):
    configured_dir = os.environ.get("TESTKIT_CACHE_DIR")
    if configured_dir:
        cache_dir = Path(configured_dir).expanduser()
    elif project_root is not None:
        cache_dir = Path(project_root) / ".testkit" / ".cache"
    elif config_path is not None:
        cache_dir = Path(config_path).parent.parent / ".cache"
    else:
        raise ValueError("project root or config path is required for cache discovery")
    return cache_dir / "discovery.json"


def discovery_cache_key(project_root, config):
    content = json.dumps({"project_root": str(project_root.resolve()), "config": config},
                         ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def load_discovery_cache(cache_key, cache_file):
    if cache_key in _DISCOVERY_MEMORY_CACHE:
        return _DISCOVERY_MEMORY_CACHE[cache_key]
    try:
        payload = json.loads(cache_file.read_text(encoding="utf-8"))
        if payload.get("key") != cache_key:
            return None
        if time.time() - float(payload["created_at"]) > DISCOVERY_CACHE_TTL:
            return None
        entries = [Entry(item["label"], item["value"], float(item["modified"]))
                   for item in payload["entries"]]
    except (FileNotFoundError, KeyError, TypeError, ValueError, json.JSONDecodeError, OSError):
        return None
    _DISCOVERY_MEMORY_CACHE[cache_key] = entries
    return entries


def save_discovery_cache(cache_key, entries, cache_file):
    _DISCOVERY_MEMORY_CACHE[cache_key] = entries
    payload = {
        "key": cache_key,
        "created_at": time.time(),
        "entries": [
            {"label": entry.label, "value": entry.value, "modified": entry.modified}
            for entry in entries
        ],
    }
    try:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        temporary = cache_file.with_name(f"{cache_file.name}.{os.getpid()}.tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        temporary.replace(cache_file)
    except OSError:
        pass


def discover_suites(project_root, config):
    cache_key = discovery_cache_key(project_root, config)
    cache_file = discovery_cache_file(project_root=project_root)
    cached = load_discovery_cache(cache_key, cache_file)
    if cached is not None:
        return cached
    found = {}
    for configured_root in config["search_roots"]:
        root = Path(configured_root).expanduser()
        if not root.is_absolute():
            root = project_root / root
        if not root.is_dir():
            continue
        for case_dir in root.rglob(config["case_directory"]):
            if not case_dir.is_dir():
                continue
            suite = case_dir.parent
            sources = runnable_sources(suite, config)
            cases = [path for path in case_dir.glob(config["case_pattern"]) if path.is_file()]
            if not sources or not cases:
                continue
            wrappers = [path for path in suite.glob(config["wrapper_pattern"]) if path.is_file()]
            modified = newest_timestamp(sources + wrappers + cases)
            try:
                label = str(suite.relative_to(project_root))
            except ValueError:
                label = str(suite)
            value = label if not Path(label).is_absolute() else str(suite.resolve())
            found[str(suite.resolve())] = Entry(label, value, modified)
    entries = list(found.values())
    save_discovery_cache(cache_key, entries, cache_file)
    return entries


def discover_source_suites(project_root, config):
    found = {}
    for configured_root in config["search_roots"]:
        root = Path(configured_root).expanduser()
        if not root.is_absolute():
            root = project_root / root
        if not root.is_dir():
            continue
        for source in root.rglob("*"):
            if (not source.is_file() or source.suffix not in SUPPORTED_SUFFIXES or
                    fnmatch.fnmatch(source.name, config["wrapper_pattern"])):
                continue
            suite = source.parent
            try:
                label = str(suite.relative_to(project_root))
            except ValueError:
                label = str(suite)
            value = label if not Path(label).is_absolute() else str(suite.resolve())
            key = str(suite.resolve())
            entry = found.get(key)
            modified = newest_timestamp([source])
            if entry is None or modified > entry.modified:
                found[key] = Entry(label, value, modified)
    return list(found.values())


# Template generation maps configured globs to safe language templates.
def template_target_name(pattern, suffix):
    if (Path(pattern).name != pattern or pattern.count("*") != 1 or
            not pattern.endswith(".*") or any(character in pattern for character in "?[]")):
        raise ValueError(
            "template generation requires a simple '<name>.*' wrapper glob, such as 'test.*'")
    return pattern[:-1] + suffix.lstrip(".")


def template_targets(suite, config):
    suffixes = sorted({source.suffix for source in suite_sources(suite, config)})
    return [(suffix, suite / template_target_name(config["wrapper_pattern"], suffix))
            for suffix in suffixes]


def template_content(suffix, source_names):
    listed_sources = "\n".join(f" *   {name}" for name in sorted(source_names))
    if suffix in (".c", ".cpp"):
        return f'''#include "testkit.h"

#ifndef TESTKIT_SOURCE_PATH
#error "TESTKIT_SOURCE_PATH must point to the source under test"
#endif
#include TESTKIT_SOURCE_PATH

/* Detected {LANGUAGE_LABELS[suffix]} sources:
{listed_sources}
 */
static void test_source(const TestCase *test_case) {{
    (void)test_case;
    timer_start();
#error "TODO: read case fields and call the source interface"
    timer_stop();
}}

int main(void) {{
    return run_cases(TESTKIT_DATA_DIR, test_source);
}}
'''
    listed_sources = "\n".join(f"#   {name}" for name in sorted(source_names))
    return f'''from testkit import load_source_module, run_cases, timer_start, timer_stop


source_module = load_source_module()

# Detected Python sources:
{listed_sources}
def test_source(test_case):
    timer_start()
    try:
        raise NotImplementedError("TODO: read case fields and call the source interface")
    finally:
        timer_stop()


if __name__ == "__main__":
    raise SystemExit(run_cases(test_source))
'''


def generate_templates(suite, config, replace_existing):
    sources = suite_sources(suite, config)
    source_names = {}
    for source in sources:
        source_names.setdefault(source.suffix, []).append(source.name)
    generated = []
    skipped = []
    for suffix, target in template_targets(suite, config):
        if target.exists() and not replace_existing:
            skipped.append(target)
            continue
        target.write_text(template_content(suffix, source_names[suffix]), encoding="utf-8")
        generated.append(target)
    return generated, skipped


def source_entries(project_root, suite_value, config, mode):
    suite = Path(suite_value)
    if not suite.is_absolute():
        suite = project_root / suite
    entries = []
    if mode != "debug":
        entries.append(Entry("<all runnable sources>", "", float("inf")))
    for path in runnable_sources(suite, config):
        entries.append(Entry(path.name, path.name, newest_timestamp([path])))
    return entries


# Configuration and test selection screens share the same navigation widgets.
def configure_screen(window, config, config_path):
    optimization_choices = [
        ("-O0 (no optimization)", "0"),
        ("-O1", "1"),
        ("-O2", "2"),
        ("-O3", "3"),
        ("-Og (debug-friendly)", "g"),
        ("-Os (optimize for size)", "s"),
        ("-Ofast", "fast"),
    ]
    fields = [
        ("Search roots (separate multiple paths with ;)", "search_roots"),
        ("Test wrapper glob", "wrapper_pattern"),
        ("Test case directory", "case_directory"),
        ("Test case glob", "case_pattern"),
        ("Use Valgrind for C/C++ tests", "use_valgrind"),
        ("C optimization", "c_optimization"),
        ("C++ optimization", "cpp_optimization"),
        ("List sort order", "sort_order"),
    ]
    index = 0
    error_message = ""
    saved_message = ""
    saved_once = False
    dirty = False
    while True:
        window.erase()
        safe_addstr(window, 0, 0, "Test Framework Configuration", curses.A_BOLD)
        safe_addstr(window, 1, 0, "Up/Down or j/k: move  Enter: edit  s: save  q: quit", curses.A_DIM)
        if error_message:
            safe_addstr(window, 2, 0, f"Invalid: {error_message}", curses.A_BOLD)
        elif saved_message:
            safe_addstr(window, 2, 0, saved_message, curses.A_BOLD)
        elif dirty:
            safe_addstr(window, 2, 0, "Unsaved changes", curses.A_BOLD)
        for row, (label, key) in enumerate(fields, 4):
            if key == "search_roots":
                value = ";".join(config[key]) or "<not configured>"
            elif key == "sort_order":
                value = "recently modified" if config[key] == "time" else "name"
            elif key == "use_valgrind":
                value = "enabled" if config[key] else "disabled"
            else:
                value = config[key]
            attributes = curses.A_REVERSE if row - 4 == index else 0
            safe_addstr(window, row, 0, f"{label}: {value}", attributes)
        window.refresh()
        key_code = window.getch()
        if key_code in (curses.KEY_UP, ord("k")):
            index = (index - 1) % len(fields)
        elif key_code in (curses.KEY_DOWN, ord("j")):
            index = (index + 1) % len(fields)
        elif key_code in (ord("s"), ord("S")):
            try:
                save_config(config_path, config)
            except (ValueError, OSError) as error:
                error_message = str(error)
                saved_message = ""
            else:
                error_message = ""
                saved_message = f"Saved to {config_path}"
                saved_once = True
                dirty = False
        elif key_code == 27:
            error_message = ""
            saved_message = "Press q to quit"
        elif key_code in (ord("q"), ord("Q")):
            if dirty:
                discard = confirm(
                    window, "[configuration] Unsaved changes",
                    "The current changes have not been saved.",
                    "Discard unsaved changes and quit?")
                if not discard:
                    error_message = ""
                    saved_message = "Quit cancelled"
                    continue
            return saved_once, False
        elif key_code in (10, 13, curses.KEY_ENTER):
            label, key = fields[index]
            if key in ("c_optimization", "cpp_optimization"):
                value = choose_value(window, label, optimization_choices, config[key])
                if value != config[key]:
                    config[key] = value
                    dirty = True
                error_message = ""
                saved_message = ""
                continue
            if key in ("sort_order", "use_valgrind"):
                if key == "sort_order":
                    config[key] = "name" if config[key] == "time" else "time"
                else:
                    config[key] = not config[key]
                error_message = ""
                saved_message = ""
                dirty = True
                continue
            current = ";".join(config[key]) if key == "search_roots" else config[key]
            value = prompt_text(
                window, label, current, edit_current=True,
                complete_paths=key == "search_roots")
            candidate = dict(config)
            candidate["search_roots"] = list(config["search_roots"])
            if key == "search_roots":
                roots = [item.strip() for item in value.split(";") if item.strip()]
                candidate[key] = normalize_search_roots(roots)
            else:
                candidate[key] = value
            try:
                validate_config(candidate)
            except ValueError as error:
                error_message = str(error)
                saved_message = ""
            else:
                if candidate != config:
                    config.update(candidate)
                    dirty = True
                error_message = ""
                saved_message = ""


def run_configure(project_root, config_path):
    config = load_config(config_path, allow_empty_search=True)
    saved, dirty = curses.wrapper(configure_screen, config, config_path)
    if dirty:
        print("Configuration changes not saved")
    elif saved:
        print(f"Configuration saved to {config_path}")
    else:
        print("Configuration unchanged")
    return 0


def select_screen(window, project_root, config, mode, fixed_suite, fixed_source):
    try:
        curses.curs_set(0)
    except curses.error:
        pass
    suite = fixed_suite or None
    while True:
        if suite is None:
            suites = discover_suites(project_root, config)
            if not suites:
                raise ValueError("no suite contains a wrapper, runnable source, and matching test cases")
            suite = pick(window, f"[{mode}] Select suite", suites, config["sort_order"])
            if suite is None:
                return None
        if fixed_source:
            source = "" if fixed_source in ("all", "*") else fixed_source
        else:
            sources = source_entries(project_root, suite, config, mode)
            if not sources:
                raise ValueError(f"no runnable source found in {suite}")
            source = pick(window, f"[{mode}] {suite}: Select source", sources, config["sort_order"])
            if source is None:
                if fixed_suite:
                    return None
                suite = None
                continue
        return mode, suite, source


def run_select(mode, project_root, config_path, suite, source):
    config = load_config(config_path)
    if mode and suite and source:
        selected_source = "" if source in ("all", "*") else source
        selected = (mode, suite, selected_source)
    else:
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            raise ValueError("an interactive terminal is required; pass both SUITE and SOURCE for automation")
        selected = curses.wrapper(select_screen, project_root, config, mode, suite, source)
    if selected is None:
        return 0
    selected_mode, selected_suite, selected_source = selected
    source_label = selected_source or "all sources"
    print(f"\n[{selected_mode}] {selected_suite} ({source_label})")
    environment = os.environ.copy()
    environment["TESTKIT_PROJECT_ROOT"] = str(project_root)
    runner = FRAMEWORK_DIR / "bin" / "run.sh"
    return subprocess.run([str(runner), f"--tool={selected_mode}", selected_suite, selected_source],
                          cwd=project_root, env=environment, check=False).returncode


# Template generation performs its writes after suite selection.
def template_screen(window, project_root, config, fixed_suite, force):
    try:
        curses.curs_set(0)
    except curses.error:
        pass
    suite_value = fixed_suite
    if not suite_value:
        suites = discover_source_suites(project_root, config)
        if not suites:
            raise ValueError("no test suite contains a matching C, C++ or Python source")
        suite_value = pick(window, "[template] Select test suite", suites, config["sort_order"])
        if suite_value is None:
            return None
    suite = Path(suite_value)
    if not suite.is_absolute():
        suite = project_root / suite
    if not suite.is_dir() or not suite_sources(suite, config):
        raise ValueError(f"no matching source found in {suite_value}")
    existing = [target.name for _, target in template_targets(suite, config) if target.exists()]
    replace_existing = force
    if existing and not force:
        names = ", ".join(existing)
        replace_existing = confirm(window, "[template] Existing files", names)
        if replace_existing is None:
            return None
    return suite_value, suite, replace_existing


def run_template(project_root, config_path, fixed_suite, force):
    config = load_config(config_path)
    template_target_name(config["wrapper_pattern"], ".c")
    interactive = sys.stdin.isatty() and sys.stdout.isatty()
    if not fixed_suite and not interactive:
        raise ValueError("an interactive terminal is required; pass SUITE for automation")
    if interactive:
        selected = curses.wrapper(
            template_screen, project_root, config, fixed_suite, force)
    else:
        suite = Path(fixed_suite)
        if not suite.is_absolute():
            suite = project_root / suite
        if not suite.is_dir() or not suite_sources(suite, config):
            raise ValueError(f"no matching source found in {fixed_suite}")
        selected = (fixed_suite, suite, force)
    if selected is None:
        return 0
    suite_value, suite, replace_existing = selected
    generated, skipped = generate_templates(suite, config, replace_existing)
    print(f"\n[template] {suite_value}")
    for target in generated:
        print(f"  created: {target.name}")
    for target in skipped:
        print(f"  kept:    {target.name}")
    if not generated:
        print("  no files created")
    return 0


# Export configuration to Bash and route command-line subcommands.
def print_shell_config(config_path):
    config = load_config(config_path)
    values = {
        "TESTKIT_WRAPPER_PATTERN": config["wrapper_pattern"],
        "TESTKIT_CASE_DIRECTORY": config["case_directory"],
        "TESTKIT_CASE_PATTERN": config["case_pattern"],
        "TESTKIT_USE_VALGRIND": "1" if config["use_valgrind"] else "0",
        "TESTKIT_C_OPTIMIZATION": config["c_optimization"],
        "TESTKIT_CPP_OPTIMIZATION": config["cpp_optimization"],
    }
    for key, value in values.items():
        print(f"{key}={shlex.quote(value)}")


def main(argv):
    os.environ["ESCDELAY"] = str(ESC_DELAY_MS)
    if hasattr(curses, "set_escdelay"):
        curses.set_escdelay(ESC_DELAY_MS)
    try:
        command = argv[1]
        if command == "configure" and len(argv) == 4:
            return run_configure(Path(argv[2]).resolve(), Path(argv[3]).resolve())
        if command == "select" and len(argv) == 7:
            mode = argv[2]
            if mode not in ("test", "debug"):
                raise ValueError(f"unknown mode: {mode}")
            return run_select(mode, Path(argv[3]).resolve(), Path(argv[4]).resolve(), argv[5], argv[6])
        if command == "template" and len(argv) == 6:
            force = argv[5].casefold() in ("1", "true", "yes", "on")
            return run_template(Path(argv[2]).resolve(), Path(argv[3]).resolve(), argv[4], force)
        if command == "config-shell" and len(argv) == 3:
            print_shell_config(Path(argv[2]).resolve())
            return 0
    except (IndexError, ValueError, OSError) as error:
        print(f"testkit: {error}", file=sys.stderr)
        return 2
    print("usage: ui.py configure PROJECT CONFIG | select MODE PROJECT CONFIG SUITE SOURCE | "
          "template PROJECT CONFIG SUITE FORCE | "
          "config-shell CONFIG",
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
