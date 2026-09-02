# leetcode 测试框架

面向 C、C++ 和 Python 的本地测试工具，可用于为 LeetCode 试题提供测试和调试环境。

## 一、快速开始

测试框架需要 GNU Make、Bash 以及带 `curses` 的 Python 3.7+，编译器和调试工具按需安装，详细说明见[环境与依赖](docs/dependencies.md)。

```bash
cd project_name
git clone git@github.com:Mountaincats/leetcode-testkit.git
```

```bash
项目结构
project_name/
├── .testkit
│   ├── .cache
│   └── .config
├── leetcode-testkit
│   └── framework.mk
├── src
└── Makefile
```

```makefile
# 在项目 Makefile 中引入框架：
include leetcode-testkit/framework.mk
# 或使用如下命令调用框架
make -f leetcode-testkit/framework.mk <target>
```

搜索目录默认未设置，首次使用先设置源码搜索目录的绝对路径：

```bash
make menuconfig
```

主要命令：

```bash
# 进行测试和调试前需要先编写测试文件
make test       # 运行测试
make debug      # 调试
make template   # 生成测试文件模板
make menuconfig # 配置文件搜索规则、编译参数和分析工具使用
make help

# 可选指定源码文件路径(SUITE)和源码文件(SOURCE)
make test SUITE=exercise/HashTable/ex1 SOURCE=all
make debug SUITE=exercise/HashTable/ex1 SOURCE=method1.c
```

## 二、文件结构默认约定

```bash
search_path/.../<suite>/
├── method1.c
├── method2.py
├── method3.cpp
├── test.c
├── test.py
├── test.cpp
└── data/*.case
```

框架在已配置的搜索目录中查找 `suite`，将 `suite` 中除 `test.*` 外的 `.c`、`.cpp`、`.py` 文件作为待测源码，并读取 `data/*.case` 作为测试数据。

## 三、文档

- [使用与命令](docs/usage.md)：运行与调试方式、测试结果说明、模板生成和设置。
- [测试文件说明](docs/test-file.md)：项目文件结构约定、测试文件公共 API 和完整示例。
- [环境与依赖](docs/dependencies.md)：Python 库说明、编译与调试工具以及操作系统支持。

## 四、许可证

本项目采用 [MIT License](LICENSE)。使用、修改或分发代码时，请保留许可证文件中的版权与许可声明。
