import importlib.util
import json
import os
import time
from collections import Counter
from pathlib import Path


_timer_started_ns = None
_timer_total_ns = 0
_timer_calls = 0


# Shared node types and typed access to raw case fields.
class ListNode:
    def __init__(self, val=0, next=None):
        self.val = val
        self.next = next


class TreeNode:
    def __init__(self, val=0, left=None, right=None):
        self.val = val
        self.left = left
        self.right = right


class TestCase:
    def __init__(self, name, fields):
        self.name = name
        self._fields = fields

    def get_string(self, key):
        try:
            return self._fields[key]
        except KeyError as error:
            raise KeyError(f"missing field '{key}' in {self.name}") from error

    def get_int(self, key):
        return int(self.get_string(key))

    def get_bool(self, key):
        value = self.get_string(key).lower()
        if value in ("true", "1"):
            return True
        if value in ("false", "0"):
            return False
        raise ValueError(f"field '{key}' is not a boolean in {self.name}")

    def get_float(self, key):
        return float(self.get_string(key))

    def _get_json_array(self, key):
        try:
            value = json.loads(self.get_string(key))
        except json.JSONDecodeError as error:
            raise ValueError(f"field '{key}' is not a valid array in {self.name}") from error
        if not isinstance(value, list):
            raise ValueError(f"field '{key}' is not an array in {self.name}")
        return value

    def get_int_array(self, key):
        value = self._get_json_array(key)
        if any(not isinstance(item, int) or isinstance(item, bool) for item in value):
            raise ValueError(f"field '{key}' is not an integer array in {self.name}")
        return value

    def get_int_matrix(self, key):
        value = self._get_json_array(key)
        if any(not isinstance(row, list) or
               any(not isinstance(item, int) or isinstance(item, bool) for item in row)
               for row in value):
            raise ValueError(f"field '{key}' is not an integer matrix in {self.name}")
        return value

    def get_string_array(self, key):
        value = self._get_json_array(key)
        if any(not isinstance(item, str) for item in value):
            raise ValueError(f"field '{key}' is not a string array in {self.name}")
        return value

    def get_list(self, key):
        dummy = ListNode()
        tail = dummy
        for value in self.get_int_array(key):
            tail.next = ListNode(value)
            tail = tail.next
        return dummy.next

    def get_tree(self, key):
        values = self._get_json_array(key)
        if any(value is not None and
               (not isinstance(value, int) or isinstance(value, bool))
               for value in values):
            raise ValueError(f"field '{key}' is not a level-order tree in {self.name}")
        if not values or values[0] is None:
            return None
        root = TreeNode(values[0])
        parents = [root]
        parent_index = 0
        value_index = 1
        while parent_index < len(parents) and value_index < len(values):
            parent = parents[parent_index]
            parent_index += 1
            if values[value_index] is not None:
                parent.left = TreeNode(values[value_index])
                parents.append(parent.left)
            value_index += 1
            if value_index < len(values) and values[value_index] is not None:
                parent.right = TreeNode(values[value_index])
                parents.append(parent.right)
            value_index += 1
        if value_index < len(values):
            raise ValueError(f"field '{key}' contains unreachable tree nodes in {self.name}")
        return root


# Load the selected source while providing the framework's common node types.
def load_source_module():
    path = Path(os.environ["TESTKIT_SOURCE_FILE"])
    spec = importlib.util.spec_from_file_location(f"testkit_source_{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    module.ListNode = ListNode
    module.TreeNode = TreeNode
    spec.loader.exec_module(module)
    return module


# Explicit timers measure only source calls selected by the test wrapper.
def timer_start():
    global _timer_started_ns
    _timer_started_ns = time.perf_counter_ns()


def timer_stop():
    global _timer_calls, _timer_started_ns, _timer_total_ns
    if _timer_started_ns is None:
        return
    _timer_total_ns += time.perf_counter_ns() - _timer_started_ns
    _timer_calls += 1
    _timer_started_ns = None


def timer_elapsed_ms():
    return _timer_total_ns / 1_000_000


def timer_call_count():
    return _timer_calls


# Assertion and normalization helpers used by Python adapters.
def assert_equal(expected, actual):
    assert expected == actual, f"expected: {expected!r}\nactual:   {actual!r}"


def assert_float_equal(expected, actual, tolerance=1e-9):
    assert tolerance >= 0 and abs(expected - actual) <= tolerance, \
        f"expected: {expected!r}\nactual:   {actual!r}\ntolerance: {tolerance!r}"


def assert_unordered_equal(expected, actual):
    assert Counter(expected) == Counter(actual), \
        f"unordered mismatch\nexpected: {expected!r}\nactual:   {actual!r}"


def assert_unordered_rows_equal(expected, actual):
    assert Counter(map(tuple, expected)) == Counter(map(tuple, actual)), \
        f"unordered row mismatch\nexpected: {expected!r}\nactual:   {actual!r}"


def list_to_values(head):
    values = []
    while head is not None:
        values.append(head.val)
        head = head.next
    return values


def assert_list_equal(expected, actual):
    assert_equal(list_to_values(expected), list_to_values(actual))


def tree_to_level_order(root):
    if root is None:
        return []
    values = []
    nodes = [root]
    for node in nodes:
        if node is None:
            values.append(None)
            continue
        values.append(node.val)
        nodes.extend((node.left, node.right))
    while values and values[-1] is None:
        values.pop()
    return values


def assert_tree_equal(expected, actual):
    assert_equal(tree_to_level_order(expected), tree_to_level_order(actual))


# Case file loading and the public test runner.
def _load_case(path):
    fields = {}
    for number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"invalid case line in {path}:{number}: {line}")
        key, value = line.split("=", 1)
        fields[key.strip()] = value.strip()
    return TestCase(path.name, fields)


def run_cases(test_fn):
    global _timer_calls, _timer_started_ns, _timer_total_ns
    data_dir = Path(os.environ["TESTKIT_DATA_DIR"])
    case_pattern = os.environ.get("TESTKIT_CASE_PATTERN", "*.case")
    quiet = os.environ.get("TESTKIT_QUIET", "0") != "0"
    _timer_started_ns = None
    _timer_total_ns = 0
    _timer_calls = 0
    passed = 0
    case_files = sorted(data_dir.glob(case_pattern))
    for path in case_files:
        try:
            test_fn(_load_case(path))
        except Exception as error:
            if not quiet:
                print(f"\033[33m[FAIL]\033[0m {path.name}")
                print(f"       {type(error).__name__}: {error}")
        else:
            if not quiet:
                print(f"\033[32m[PASS]\033[0m {path.name}")
            passed += 1
    failed = len(case_files) - passed
    if not quiet:
        print(f"Cases: {passed} passed, {failed} failed")
    result_file = os.environ.get("TESTKIT_RESULT_FILE")
    if result_file:
        Path(result_file).write_text(f"{passed} {failed}\n", encoding="utf-8")
    timing_file = os.environ.get("TESTKIT_TIMING_FILE")
    if timing_file:
        Path(timing_file).write_text(
            f"{timer_elapsed_ms():.9f} {_timer_calls}\n", encoding="utf-8")
    return 0 if failed == 0 else 1
