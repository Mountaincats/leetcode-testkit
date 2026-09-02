# 使用与命令

---

## 一、测试与调试

```bash
make test       # 运行测试；可在 menuconfig 中为 C/C++ 开启 Valgrind
make debug      # C/C++ 使用 GDB 调试，Python 使用 PDB 调试
```

- 运行 `test/debug` 后依次选择 suite 和 source。`debug` 必须选择单个 source 文件，`test` 可以选择全部文件。也可通过变量跳过选择菜单：
  ```bash
  make test SUITE=exercise/HashTable/ex1 SOURCE=all
  make debug SUITE=exercise/HashTable/ex1 SOURCE=method1.c
  # `SOURCE=all` 或 `SOURCE='*'` 表示全部
  ```
- 菜单支持方向键或 `j/k` 移动、`Enter` 确认、`q` 返回、`s` 保存及 `/` 搜索。
  - 编辑文本选项时，按 `Enter` 应用编辑结果并返回菜单，按 `Esc` 放弃本次编辑并返回菜单。
  - 搜索输入时可按 `Tab` 补全匹配项。搜索不区分大小写、支持空格分隔的多关键词、`!关键词` 排除、通配符 `*`/`?` 和不连续字符模糊匹配，结果按相关度优先排列。

---

## 二、测试结果说明

```text
-- method1.c --
[PASS] 01-basic.case
[PASS] 02-edge.case
Cases: 2 passed, 0 failed

Source summary
--------------
  SOURCE                RESULT    VALGRIND      TIME (total)
  method1.c             [PASS]    [PASS]        0.012000 ms
  method2.py            [PASS]    -             0.008000 ms
Total: 2 passed, 0 failed
```

- `RESULT` 只表示测试用例运行结果。
- 启用 Valgrind 时内存检查结果显示在 `VALGRIND` 列，未启用时不显示该列。
- `TIME` 是该 source 所有已计时调用的累计时间；没有调用计时 API 时显示 `-`。时间不包含编译、case 解析和断言，也不统计 Memcheck 的额外开销。测试结果为 `FAIL` 时，`TIME` 显示 `N/A`。

---

## 三、生成测试文件模板

`make template` 列出所有包含源码的 suite，不要求已有测试模板或测试用例。框架按已有语言生成 `test.c`、`test.cpp` 或 `test.py`，并为待测接口保留明确的 `TODO`。

已有文件默认保留；确认覆盖或使用 `FORCE=1` 才会替换：

```bash
make template   # 生成测试模板
make template SUITE=exercise/HashTable/ex1
make template SUITE=exercise/HashTable/ex1 FORCE=1
```

---

## 四、设置

| 配置 | 默认值 | 说明 |
| --- | --- | --- |
| 搜索根目录 | 未设置 | 必须配置；保存为绝对路径，支持多个目录 |
| 测试文件名 | `test.*` | 源码与测试文件扩展名必须一致 |
| 测试用例目录名 | `data` | suite 下的单个目录名 |
| 测试用例扩展名 | `*.case` | - |
| 目录、文件显示顺序 | 最近修改 | 也可按名称排序 |
| C/C++ 测试使用 Valgrind | 关闭 | 开启后执行内存检查，不保留日志 |
| C 优化等级 | `0` | `0/1/2/3/g/s/fast` |
| C++ 优化等级 | `0` | `0/1/2/3/g/s/fast` |

- 首次使用需要通过 `make menuconfig` 设置至少一个搜索根目录。搜索目录只接受绝对路径，路径字段支持 Tab 补全。
- Valgrind 使用、文件目录显示方式以及 C/C++ 优化等级均通过菜单选择。
- 每个 suite 目录中的 `.c`、`.cpp` 和 `.py` 文件都会被识别为待测源码，但测试文件除外，因此源码名称不受限制。
- `make menuconfig` 将项目配置固定保存到执行 Make 的目录下：`.testkit/.config/settings.conf`。搜索根目录缓存保存在 `.testkit/.cache/discovery.json`，配置改变时自动失效。
  ```text
  project/
  ├── .testkit/
  │   ├── .config/settings.conf
  │   └── .cache/discovery.json
  └── Makefile
  ```
