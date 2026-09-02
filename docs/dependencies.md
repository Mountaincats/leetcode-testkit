# 环境与依赖

---

## 一、基础依赖

| 依赖 | 用途 | 是否必需 |
| --- | --- | --- |
| GNU Make | 提供 `test`、`debug`、`template` 和 `menuconfig` 等任务 | 是 |
| Bash | 执行 runner 和菜单入口脚本 | 是 |
| Python 3.7+ | 运行菜单、配置、发现逻辑以及 Python 测试 | 是 |
| Python `curses` | 提供终端菜单界面 | 是 |

可先检查基础环境：

```bash
make --version
bash --version
python3 --version
python3 -c 'import curses; print("curses available")'
```

`curses` 是 Python 标准库接口，但依赖系统的 ncurses 库。Linux 发行版和 macOS 提供的常规 Python 通常已经包含它。如果最后一条命令失败，应安装发行版提供的 Python/curses 组件，而不是使用 `pip install curses`。

---

## 二、Python 版本与标准库

框架要求 **Python 3.7 或更高版本**，建议使用仍受维护的 Python 3.11 或更新版本。Python 2 不受支持。

最低版本由代码实际使用的标准库功能决定：

| 功能 | 首次内置版本 | 框架中的用途 |
| --- | --- | --- |
| `dataclasses` | Python 3.7 | 定义菜单中的 suite/source 条目 |
| `time.perf_counter_ns()` | Python 3.7 | 统计 Python 待测函数的运行时间 |
| `pathlib` | Python 3.4 | 路径与配置文件处理 |
| f-string | Python 3.6 | 输出和错误信息格式化 |

因此，不能只在 Python 3.6 中安装 `dataclasses` 回移包来满足要求，计时 API 仍然不可用。

待测 Python source 与框架运行在同一个解释器中，因此源码使用的语法和标准库也必须兼容当前 `python3` 版本。可用以下命令同时检查版本和关键模块：

```bash
python3 -c 'import sys, curses, dataclasses; assert sys.version_info >= (3, 7); print(sys.version)'
```

---

## 三、按需求功能安装

| 功能 | 所需工具 |
| --- | --- |
| Python 测试 | Python 3 |
| C 测试 | GCC，命令名为 `gcc` |
| C++ 测试 | G++，命令名为 `g++` |
| Python 调试 | 标准库 PDB，无额外依赖 |
| C/C++ 调试 | GDB |
| C/C++ 内存检查 | Valgrind |

Debian/Ubuntu 可一次安装常用工具：

```bash
sudo apt install make bash python3 gcc g++ gdb valgrind
```

Fedora：

```bash
sudo dnf install make bash python3 gcc gcc-c++ gdb valgrind
```

Arch Linux：

```bash
sudo pacman -S make bash python gcc gdb valgrind
```

不使用 C/C++、GDB 或 Valgrind 时，可以省略对应软件包。不同发行版的包名可能略有差异。

---

## 四、操作系统支持

| 系统 | 支持情况 | 说明 |
| --- | --- | --- |
| Linux | 完整支持 | 推荐环境 |
| Windows + WSL | 支持 | 建议使用 WSL 2，并在 WSL 发行版内按 Linux 方式安装和运行 |
| macOS | 基础功能可用 | Python `curses` 和终端菜单通常可用，GDB 配置及 Valgrind 可用性受系统版本限制 |
| Windows 原生终端 | 暂不支持 | runner 依赖 Bash 和 Unix 命令，标准 Windows Python 通常也不提供 `curses` |
| BSD 等其他 Unix | 未正式验证 | 安装 Bash、Python curses 和 GNU Make 后可能可用，GNU Make 的命令名可能是 `gmake` |

macOS 建议先安装 Command Line Tools：

```bash
xcode-select --install
```

随后用前述检查命令确认 Python `curses`、`gcc` 和 `g++` 是否可用。若需要稳定的 Valgrind 内存检查环境，建议在 Linux 或 WSL 中运行。

---

## 五、终端要求

- 交互菜单需要支持 curses 的真实终端，并要求标准输入和标准输出连接到 TTY。CI、管道或无交互终端中应跳过菜单，显式传入 suite 和 source：
  
    ```bash
    make test SUITE=/absolute/path/to/suite SOURCE=all
    ```

- 终端至少应支持基本 ANSI 颜色和方向键。窗口过小时部分提示可能被截断，但不影响非交互运行。
