# 测试文件说明

---

## 一、文件说明
### (1) 文件结构
一个典型 suite 如下：

```text
exercise/<group>/<suite>/
├── method1.c
├── method2.cpp
├── method3.py
├── test.c
├── test.cpp
├── test.py
└── data/
    ├── 01-basic.case
    └── 02-edge.case
```

- 目录需要同时包含待测源码、同语言测试文件和至少一个 case，才会出现在测试菜单中。
- 源码名称不受限制，但不能和配置的测试文件名相同。

### (2) 测试用例

一个测试用例文件使用一行一个 `key=value`，**忽略空行和 `#` 注释**：

```text
nums=[2,7,11,15]
target=9
expected=[0,1]
```

- 测试用例必须放在 `make menuconfig` 中设置的测试用例文件夹中，默认是 `data/`。测试用例扩展名也必须和设置中的相同，默认是 `.case`。
- 标量字符串直接书写，数组和矩阵使用 JSON 风格，支持整数、布尔值、浮点数、字符串、整数数组、整数二维数组、整数链表和层序二叉树。
- Case 按文件名字典序执行，需要固定顺序时建议使用 `01-`、`02-` 等宽编号。

### (3) 测试文件
测试文件是 suite 中与待测源码同语言的包装文件，默认命名为 `test.c`、`test.cpp` 或 `test.py`。同一测试文件会依次测试该 suite 下所有同语言 source，因此这些 source 应提供相同的调用接口。

可以先运行 `make template` 生成骨架，再补充 case 字段读取、待测函数调用和断言。

---

## 二、C/C++ 测试文件

### (1) 基本结构

`TESTKIT_SOURCE_PATH` 和 `TESTKIT_DATA_DIR` 由框架在构建时注入：

```c
#include "testkit.h"

#ifndef TESTKIT_SOURCE_PATH
#error "TESTKIT_SOURCE_PATH must point to the source under test"
#endif
#include TESTKIT_SOURCE_PATH

static void test_source(const TestCase *test_case) {
    /* 读取字段，在计时区间内调用待测接口，然后断言。 */
}

int main(void) {
    return run_cases(TESTKIT_DATA_DIR, test_source);
}
```

`run_cases()` 对每个 case 调用一次 `test_source()`。C++ 测试文件使用相同 runner 和读取 API，也可以在回调内使用 STL 类型。

### (2) 运行时间 API

只将待测调用放在 `start/stop` 之间，字段读取、数据准备和断言应放在区间外：

```c
timer_start();
int actual = function_under_test(input);
timer_stop();
```

| API | 用途 |
| --- | --- |
| `timer_start()` | 开始一次待测调用计时 |
| `timer_stop()` | 结束并累计本次计时 |
| `timer_elapsed_ms()` | 获取累计毫秒数 |
| `timer_call_count()` | 获取已完成计时次数 |

框架将累计时间写入最终的 `Source summary`。启用 Valgrind 时，C/C++ 会先原生执行以采集时间，再运行 Memcheck，因此时间不包含内存检查开销。计时器不支持嵌套调用。

### (3) 字段读取 API

| API | 返回值 | case 示例 |
| --- | --- | --- |
| `case_name(tc)` | 当前 case 文件名 | — |
| `get_string(tc, key)` | `const char *` | `text=hello` |
| `get_int(tc, key)` | `int` | `count=12` |
| `get_bool(tc, key)` | `bool` | `valid=true` |
| `get_float(tc, key)` | `double` | `ratio=0.25` |
| `get_int_array(tc, key, &size)` | `int *` | `nums=[1,2,3]` |
| `get_int_matrix(tc, key)` | `TestIntMatrix` | `grid=[[1,2],[3,4]]` |
| `get_list(tc, key)` | `struct ListNode *` | `head=[1,2,3]` |
| `get_tree(tc, key)` | `struct TreeNode *` | `root=[1,null,2,3]` |

字符串由 `TestCase` 持有，只在当前回调期间有效，不能释放。数组、矩阵、链表和树由读取 API 分配，测试文件负责释放：

```c
free(array);
free_int_matrix(&matrix);
free_list(head);
free_tree(root);
```

`TestIntMatrix` 的 `row_count` 表示行数，`column_sizes[row]` 表示每行长度，`rows[row]` 指向该行数据。

### (4) 断言与辅助 API

| 宏 | 用途 |
| --- | --- |
| `assert_int_equal(expected, actual)` | 整数相等 |
| `assert_bool_equal(expected, actual)` | 布尔值相等 |
| `assert_float_equal(expected, actual, tolerance)` | 浮点数误差比较 |
| `assert_string_equal(expected, actual)` | 字符串相等 |
| `assert_array_equal(expected, esize, actual, asize)` | 有序整数数组 |
| `assert_unordered_equal(...)` | 无序整数数组，保留重复计数 |
| `assert_matrix_equal(expected, actual)` | 有序整数矩阵 |
| `assert_unordered_rows_equal(expected, actual)` | 行顺序无关的整数矩阵 |
| `assert_list_equal(expected, actual)` | 整数链表相等 |

这些宏会自动记录测试文件的文件名和行号。断言失败会立即终止当前 case，后续清理代码不会执行；待测源码返回的动态内存应按接口约定释放，失败 case 的 Valgrind 输出可能同时包含尚未清理的分配。

### (5) 完整示例

```c
#include "testkit.h"

#ifndef TESTKIT_SOURCE_PATH
#error "TESTKIT_SOURCE_PATH must point to the source under test"
#endif
#include TESTKIT_SOURCE_PATH

static void test_two_sum(const TestCase *test_case) {
    int nums_size;
    int expected_size;
    int actual_size = 0;
    int *nums = get_int_array(test_case, "nums", &nums_size);
    int target = get_int(test_case, "target");
    int *expected = get_int_array(test_case, "expected", &expected_size);

    timer_start();
    int *actual = twoSum(nums, nums_size, target, &actual_size);
    timer_stop();
    assert_array_equal(expected, expected_size, actual, actual_size);

    free(actual);
    free(expected);
    free(nums);
}

int main(void) {
    return run_cases(TESTKIT_DATA_DIR, test_two_sum);
}
```

---

## 三、Python 测试文件

### (1) 基本结构

```python
from testkit import load_source_module, run_cases


source_module = load_source_module()


def test_source(test_case):
    # 读取字段，在计时区间内调用待测接口，然后断言。
    pass


if __name__ == "__main__":
    raise SystemExit(run_cases(test_source))
```

`load_source_module()` 加载当前选中的 source，并向其提供框架的 `ListNode` 和 `TreeNode`。`run_cases()` 捕获测试回调中的异常，任何未处理异常都会令当前 case 失败。

### (2) 运行时间 API

与 C/C++ 相同，只将待测调用放在 `start/stop` 之间：

```python
timer_start()
actual = function_under_test(input_value)
timer_stop()
```

| API | 用途 |
| --- | --- |
| `timer_start()` | 开始一次待测调用计时 |
| `timer_stop()` | 结束并累计本次计时 |
| `timer_elapsed_ms()` | 获取累计毫秒数 |
| `timer_call_count()` | 获取已完成计时次数 |

计时基于 `time.perf_counter_ns()`，累计时间写入最终的 `Source summary`。字段读取、数据准备和断言应放在计时区间外，计时器不支持嵌套调用。

### (3) 字段读取 API

| 方法或属性 | 返回值 | case 示例 |
| --- | --- | --- |
| `test_case.name` | case 文件名 | — |
| `get_string(key)` | 原始字符串 | `text=hello` |
| `get_int(key)` | 整数 | `count=12` |
| `get_bool(key)` | 布尔值 | `valid=true` |
| `get_float(key)` | 浮点数 | `ratio=0.25` |
| `get_int_array(key)` | `list[int]` | `nums=[1,2,3]` |
| `get_int_matrix(key)` | `list[list[int]]` | `grid=[[1,2],[3,4]]` |
| `get_string_array(key)` | `list[str]` | `words=["a","b"]` |
| `get_list(key)` | `ListNode` 链表 | `head=[1,2,3]` |
| `get_tree(key)` | `TreeNode` 二叉树 | `root=[1,null,2,3]` |

### (4) 断言与辅助 API

| API | 用途 |
| --- | --- |
| `assert_equal(expected, actual)` | 普通相等比较 |
| `assert_float_equal(expected, actual, tolerance=1e-9)` | 浮点数比较 |
| `assert_unordered_equal(expected, actual)` | 无序序列比较，保留重复计数 |
| `assert_unordered_rows_equal(expected, actual)` | 二维数组行顺序无关比较 |
| `assert_list_equal(expected, actual)` | 链表比较 |
| `assert_tree_equal(expected, actual)` | 层序二叉树比较 |
| `list_to_values(head)` | 链表转换为 Python 列表 |
| `tree_to_level_order(root)` | 二叉树转换为层序列表 |

### (5) 完整示例

```python
from testkit import assert_equal, load_source_module, run_cases, timer_start, timer_stop


source_module = load_source_module()


def test_happy_number(test_case):
    implementation = source_module.Solution()
    n = test_case.get_int("n")
    expected = test_case.get_bool("expected")
    timer_start()
    actual = implementation.isHappy(n)
    timer_stop()
    assert_equal(expected, actual)


if __name__ == "__main__":
    raise SystemExit(run_cases(test_happy_number))
```

---

## 四、限制与扩展

- C/C++ case 每行最长 4095 个字符，每个 case 最多 32 个字段；
- C/C++ 暂无字符串数组和二叉树专用断言；
- 图、随机链表及自定义结构需要在具体测试文件中解析；
- 如果同一 suite 的不同 source 接口不兼容，应拆分 suite 或提供可兼容各实现的包装逻辑。
