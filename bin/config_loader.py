#!/usr/bin/env python3
import sys
import shlex
from pathlib import Path
from testkit_settings import load_config


if len(sys.argv) != 2:
    print("Usage: config_loader.py CONFIG_FILE", file=sys.stderr)
    sys.exit(2)

config_path = Path(sys.argv[1])
if not config_path.exists():
    print(f"config_loader: file not found: {config_path}", file=sys.stderr)
    sys.exit(2)

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
