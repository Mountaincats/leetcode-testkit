"""Source matching, suite discovery, and discovery-cache handling."""

import fnmatch
import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path


SUPPORTED_SUFFIXES = {".c", ".cpp", ".py"}
DISCOVERY_CACHE_TTL = 5.0
_DISCOVERY_MEMORY_CACHE = {}


@dataclass
class Entry:
    label: str
    value: str
    modified: float = 0.0


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


def discovery_cache_file(project_root):
    configured_dir = os.environ.get("TESTKIT_CACHE_DIR")
    cache_dir = (Path(configured_dir).expanduser() if configured_dir
                 else Path(project_root) / ".testkit" / ".cache")
    return cache_dir / "discovery.json"


def discovery_cache_key(project_root, config):
    content = json.dumps(
        {"project_root": str(project_root.resolve()), "config": config},
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


def _display_entry(project_root, suite, modified):
    try:
        label = str(suite.relative_to(project_root))
    except ValueError:
        label = str(suite)
    value = label if not Path(label).is_absolute() else str(suite.resolve())
    return Entry(label, value, modified)


def discover_suites(project_root, config):
    cache_key = discovery_cache_key(project_root, config)
    cache_file = discovery_cache_file(project_root)
    cached = load_discovery_cache(cache_key, cache_file)
    if cached is not None:
        return cached
    found = {}
    for configured_root in config["search_roots"]:
        root = Path(configured_root).expanduser()
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
            found[str(suite.resolve())] = _display_entry(project_root, suite, modified)
    entries = list(found.values())
    save_discovery_cache(cache_key, entries, cache_file)
    return entries


def discover_source_suites(project_root, config):
    found = {}
    for configured_root in config["search_roots"]:
        root = Path(configured_root).expanduser()
        if not root.is_dir():
            continue
        for source in root.rglob("*"):
            if (not source.is_file() or source.suffix not in SUPPORTED_SUFFIXES or
                    fnmatch.fnmatch(source.name, config["wrapper_pattern"])):
                continue
            suite = source.parent
            key = str(suite.resolve())
            entry = _display_entry(project_root, suite, newest_timestamp([source]))
            if key not in found or entry.modified > found[key].modified:
                found[key] = entry
    return list(found.values())
