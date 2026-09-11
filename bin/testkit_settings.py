"""Persistent settings and validation for the test framework."""

import json
import os
from pathlib import Path


DEFAULTS = {
    "search_roots": [os.path.join(os.environ.get("TESTKIT_DIR"), "example")],
    "wrapper_pattern": "test.*",
    "case_directory": "data",
    "case_pattern": "*.case",
    "sort_order": "time",
    "use_valgrind": False,
    "c_optimization": "0",
    "cpp_optimization": "0",
}
NATIVE_OPTIMIZATION_LEVELS = {"0", "1", "2", "3", "g", "s", "fast"}


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
    temporary.write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(target)
    try:
        cache_file_for_config(target).unlink()
    except FileNotFoundError:
        pass


def cache_file_for_config(config_path):
    configured_dir = os.environ.get("TESTKIT_CACHE_DIR")
    cache_dir = (Path(configured_dir).expanduser() if configured_dir
                 else Path(config_path).parent.parent / ".cache")
    return cache_dir / "discovery.json"


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
            raise ValueError(
                f"{language}_optimization must be one of 0, 1, 2, 3, g, s, fast")
        config[f"{language}_optimization"] = level
