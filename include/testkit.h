#ifndef TESTKIT_H
#define TESTKIT_H

#include <stdbool.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>
#include <math.h>
#include <stdint.h>
#include <limits.h>
#include <float.h>
#include <time.h>

#include "uthash.h"

/* Common data structures exposed to wrappers and tested sources. */
struct ListNode {
    int val;
    struct ListNode *next;
};

struct TreeNode {
    int val;
    struct TreeNode *left;
    struct TreeNode *right;
};

typedef struct {
    int **rows;
    int *column_sizes;
    int row_count;
} TestIntMatrix;

typedef struct TestCase TestCase;
typedef void (*TestFn)(const TestCase *test_case);

#ifdef __cplusplus
extern "C" {
#endif

/* Case runner and typed field accessors. */
int run_cases(const char *data_dir, TestFn test_fn);

/* Explicit timers exclude case parsing and assertions from source timings. */
void timer_start(void);
void timer_stop(void);
double timer_elapsed_ms(void);
size_t timer_call_count(void);

const char *case_name(const TestCase *test_case);
const char *get_string(const TestCase *test_case, const char *key);
int get_int(const TestCase *test_case, const char *key);
bool get_bool(const TestCase *test_case, const char *key);
double get_float(const TestCase *test_case, const char *key);
int *get_int_array(const TestCase *test_case, const char *key, int *size);
TestIntMatrix get_int_matrix(const TestCase *test_case, const char *key);
struct ListNode *get_list(const TestCase *test_case, const char *key);
struct TreeNode *get_tree(const TestCase *test_case, const char *key);

/* Release values allocated by composite field accessors. */
void free_int_matrix(TestIntMatrix *matrix);
void free_list(struct ListNode *head);
void free_tree(struct TreeNode *root);

/* Assertion backends are wrapped by source-location macros below. */
void assert_int_equal_impl(int expected, int actual, const char *file, int line);
void assert_bool_equal_impl(bool expected, bool actual, const char *file, int line);
void assert_float_equal_impl(double expected, double actual, double tolerance,
                              const char *file, int line);
void assert_string_equal_impl(const char *expected, const char *actual,
                              const char *file, int line);
void assert_array_equal_impl(const int *expected, int expected_size,
                                 const int *actual, int actual_size,
                                 const char *file, int line);
void assert_unordered_equal_impl(const int *expected, int expected_size,
                                           const int *actual, int actual_size,
                                           const char *file, int line);
void assert_matrix_equal_impl(const TestIntMatrix *expected,
                                  const TestIntMatrix *actual,
                                  const char *file, int line);
void assert_unordered_rows_equal_impl(const TestIntMatrix *expected,
                                            const TestIntMatrix *actual,
                                            const char *file, int line);
void assert_list_equal_impl(const struct ListNode *expected,
                            const struct ListNode *actual,
                            const char *file, int line);

#ifdef __cplusplus
}
#endif

/* Public assertion macros preserve the wrapper's source location. */
#define assert_int_equal(expected, actual) \
    assert_int_equal_impl((expected), (actual), __FILE__, __LINE__)

#define assert_bool_equal(expected, actual) \
    assert_bool_equal_impl((expected), (actual), __FILE__, __LINE__)

#define assert_float_equal(expected, actual, tolerance) \
    assert_float_equal_impl((expected), (actual), (tolerance), __FILE__, __LINE__)

#define assert_string_equal(expected, actual) \
    assert_string_equal_impl((expected), (actual), __FILE__, __LINE__)

#define assert_array_equal(expected, expected_size, actual, actual_size) \
    assert_array_equal_impl((expected), (expected_size), (actual), \
                                (actual_size), __FILE__, __LINE__)

#define assert_unordered_equal(expected, expected_size, actual, actual_size) \
    assert_unordered_equal_impl((expected), (expected_size), (actual), \
                                          (actual_size), __FILE__, __LINE__)

#define assert_matrix_equal(expected, actual) \
    assert_matrix_equal_impl((expected), (actual), __FILE__, __LINE__)

#define assert_unordered_rows_equal(expected, actual) \
    assert_unordered_rows_equal_impl((expected), (actual), __FILE__, __LINE__)

#define assert_list_equal(expected, actual) \
    assert_list_equal_impl((expected), (actual), __FILE__, __LINE__)

#endif
