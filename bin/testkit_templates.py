"""Language-specific test-template generation."""

from pathlib import Path

from testkit_discovery import suite_sources


LANGUAGE_LABELS = {".c": "C", ".cpp": "C++", ".py": "Python"}


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
