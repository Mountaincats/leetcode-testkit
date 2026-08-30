#define _POSIX_C_SOURCE 200809L
#include "testkit.h"

#include <ctype.h>
#include <dirent.h>
#include <errno.h>
#include <fnmatch.h>
#include <setjmp.h>
#include <time.h>

#define TESTKIT_MAX_FIELDS 32
#define TESTKIT_MAX_LINE 4096

#define TESTKIT_DEFAULT_CASE_PATTERN "*.case"

/* Internal case storage and the jump target used to abort one failed case. */
typedef struct {
    char *key;
    char *value;
} TestField;

struct TestCase {
    const char *name;
    TestField fields[TESTKIT_MAX_FIELDS];
    int field_count;
};

static jmp_buf testkit_failure_jump;
static struct timespec testkit_timer_started_at;
static double testkit_timer_total_ms;
static size_t testkit_timer_calls;
static bool testkit_timer_running;

/* Explicit timers measure only source calls selected by the test wrapper. */
void timer_start(void) {
    testkit_timer_running = clock_gettime(CLOCK_MONOTONIC, &testkit_timer_started_at) == 0;
}

void timer_stop(void) {
    struct timespec stopped_at;
    double seconds;
    if (!testkit_timer_running || clock_gettime(CLOCK_MONOTONIC, &stopped_at) != 0) return;
    seconds = (double)(stopped_at.tv_sec - testkit_timer_started_at.tv_sec);
    seconds += (double)(stopped_at.tv_nsec - testkit_timer_started_at.tv_nsec) / 1000000000.0;
    testkit_timer_total_ms += seconds * 1000.0;
    testkit_timer_calls++;
    testkit_timer_running = false;
}

double timer_elapsed_ms(void) {
    return testkit_timer_total_ms;
}

size_t timer_call_count(void) {
    return testkit_timer_calls;
}

/* Case text parsing, file loading, and result bookkeeping. */
static char *testkit_trim(char *text) {
    char *end;
    while (isspace((unsigned char)*text)) text++;
    end = text + strlen(text);
    while (end > text && isspace((unsigned char)end[-1])) end--;
    *end = '\0';
    return text;
}

static char *testkit_strdup(const char *text) {
    size_t size = strlen(text) + 1;
    char *copy = malloc(size);
    if (copy != NULL) memcpy(copy, text, size);
    return copy;
}

static const char *testkit_case_get(const TestCase *test_case, const char *key) {
    for (int i = 0; i < test_case->field_count; i++) {
        if (strcmp(test_case->fields[i].key, key) == 0) {
            return test_case->fields[i].value;
        }
    }
    fprintf(stderr, "missing field '%s' in %s\n", key, test_case->name);
    longjmp(testkit_failure_jump, 1);
}

static int testkit_load_case(const char *path, const char *name, TestCase *test_case) {
    FILE *file = fopen(path, "r");
    char line[TESTKIT_MAX_LINE];
    if (file == NULL) {
        fprintf(stderr, "cannot open %s: %s\n", path, strerror(errno));
        return 0;
    }

    memset(test_case, 0, sizeof(*test_case));
    test_case->name = name;
    while (fgets(line, sizeof(line), file) != NULL) {
        char *text = testkit_trim(line);
        char *equal;
        if (*text == '\0' || *text == '#') continue;
        equal = strchr(text, '=');
        if (equal == NULL || test_case->field_count == TESTKIT_MAX_FIELDS) {
            fprintf(stderr, "invalid case line in %s: %s\n", path, text);
            fclose(file);
            return 0;
        }
        *equal = '\0';
        test_case->fields[test_case->field_count].key = testkit_strdup(testkit_trim(text));
        test_case->fields[test_case->field_count].value = testkit_strdup(testkit_trim(equal + 1));
        if (test_case->fields[test_case->field_count].key == NULL ||
            test_case->fields[test_case->field_count].value == NULL) {
            fclose(file);
            return 0;
        }
        test_case->field_count++;
    }
    fclose(file);
    return 1;
}

static void testkit_release_case(TestCase *test_case) {
    for (int i = 0; i < test_case->field_count; i++) {
        free(test_case->fields[i].key);
        free(test_case->fields[i].value);
    }
}

static int testkit_case_name_compare(const void *left, const void *right) {
    const char *const *a = left;
    const char *const *b = right;
    return strcmp(*a, *b);
}

static void testkit_write_result(int passed, int failed) {
    const char *path = getenv("TESTKIT_RESULT_FILE");
    FILE *file;
    if (path == NULL || *path == '\0') return;
    file = fopen(path, "w");
    if (file == NULL) return;
    fprintf(file, "%d %d\n", passed, failed);
    fclose(file);
}

static void testkit_write_timing(void) {
    const char *path = getenv("TESTKIT_TIMING_FILE");
    FILE *file;
    if (path == NULL || *path == '\0') return;
    file = fopen(path, "w");
    if (file == NULL) return;
    fprintf(file, "%.9f %zu\n", testkit_timer_total_ms, testkit_timer_calls);
    fclose(file);
}

int run_cases(const char *data_dir, TestFn test_fn) {
    DIR *dir = opendir(data_dir);
    struct dirent *entry;
    char **names = NULL;
    int name_count = 0;
    int passed = 0;
    const char *case_pattern = getenv("TESTKIT_CASE_PATTERN");
    const char *quiet_value = getenv("TESTKIT_QUIET");
    bool quiet = quiet_value != NULL && strcmp(quiet_value, "0") != 0;

    testkit_timer_total_ms = 0.0;
    testkit_timer_calls = 0;
    testkit_timer_running = false;

    if (case_pattern == NULL || *case_pattern == '\0') case_pattern = TESTKIT_DEFAULT_CASE_PATTERN;

    if (dir == NULL) {
        fprintf(stderr, "cannot open data directory %s: %s\n", data_dir, strerror(errno));
        return 1;
    }
    while ((entry = readdir(dir)) != NULL) {
        if (fnmatch(case_pattern, entry->d_name, 0) != 0) continue;
        char **next = realloc(names, sizeof(*names) * (name_count + 1));
        if (next == NULL) {
            closedir(dir);
            return 1;
        }
        names = next;
        names[name_count++] = testkit_strdup(entry->d_name);
    }
    closedir(dir);
    qsort(names, name_count, sizeof(*names), testkit_case_name_compare);

    for (int i = 0; i < name_count; i++) {
        char path[TESTKIT_MAX_LINE];
        TestCase test_case;
        snprintf(path, sizeof(path), "%s/%s", data_dir, names[i]);
        if (!testkit_load_case(path, names[i], &test_case)) {
            if (!quiet) printf("[FAIL] %s\n", names[i]);
        } else if (setjmp(testkit_failure_jump) == 0) {
            test_fn(&test_case);
            if (!quiet) printf("\033[32m[PASS]\033[0m %s\n", names[i]);
            passed++;
            testkit_release_case(&test_case);
        } else {
            if (!quiet) printf("\033[31m[FAIL]\033[0m %s\n", names[i]);
            testkit_release_case(&test_case);
        }
        free(names[i]);
    }
    free(names);
    if (!quiet) printf("Cases: %d passed, %d failed\n", passed, name_count - passed);
    testkit_write_result(passed, name_count - passed);
    testkit_write_timing();
    return passed == name_count ? 0 : 1;
}

/* Scalar field accessors validate and convert raw key/value text. */
const char *case_name(const TestCase *test_case) {
    return test_case->name;
}

const char *get_string(const TestCase *test_case, const char *key) {
    return testkit_case_get(test_case, key);
}

int get_int(const TestCase *test_case, const char *key) {
    const char *value = testkit_case_get(test_case, key);
    char *end;
    long result = strtol(value, &end, 10);
    if (*testkit_trim(end) != '\0') {
        fprintf(stderr, "field '%s' is not an integer in %s\n", key, test_case->name);
        longjmp(testkit_failure_jump, 1);
    }
    return (int)result;
}

bool get_bool(const TestCase *test_case, const char *key) {
    const char *value = testkit_case_get(test_case, key);
    if (strcmp(value, "true") == 0 || strcmp(value, "1") == 0) return true;
    if (strcmp(value, "false") == 0 || strcmp(value, "0") == 0) return false;
    fprintf(stderr, "field '%s' is not a boolean in %s\n", key, test_case->name);
    longjmp(testkit_failure_jump, 1);
}

double get_float(const TestCase *test_case, const char *key) {
    const char *value = testkit_case_get(test_case, key);
    char *end;
    double result = strtod(value, &end);
    if (end == value || *testkit_trim(end) != '\0') {
        fprintf(stderr, "field '%s' is not a number in %s\n", key, test_case->name);
        longjmp(testkit_failure_jump, 1);
    }
    return result;
}

/* Composite field parsers build arrays, matrices, lists, and trees. */
static int *testkit_parse_int_array(const char **source, int *size) {
    const char *cursor = *source;
    int *values = NULL;
    *size = 0;
    while (isspace((unsigned char)*cursor)) cursor++;
    if (*cursor++ != '[') return NULL;
    for (;;) {
        char *end;
        long value;
        int *next;
        while (isspace((unsigned char)*cursor)) cursor++;
        if (*cursor == ']') {
            *source = cursor + 1;
            return values;
        }
        value = strtol(cursor, &end, 10);
        if (end == cursor) break;
        next = realloc(values, sizeof(*values) * (*size + 1));
        if (next == NULL) break;
        values = next;
        values[(*size)++] = (int)value;
        cursor = end;
        while (isspace((unsigned char)*cursor)) cursor++;
        if (*cursor == ',') cursor++;
        else if (*cursor != ']') break;
    }
    free(values);
    *size = -1;
    return NULL;
}

int *get_int_array(const TestCase *test_case, const char *key, int *size) {
    const char *source = testkit_case_get(test_case, key);
    const char *cursor = source;
    int *values = testkit_parse_int_array(&cursor, size);
    while (isspace((unsigned char)*cursor)) cursor++;
    if (*size >= 0 && *cursor == '\0') return values;
    free(values);
    fprintf(stderr, "field '%s' is not an integer array in %s\n", key, test_case->name);
    longjmp(testkit_failure_jump, 1);
}

TestIntMatrix get_int_matrix(const TestCase *test_case, const char *key) {
    const char *cursor = testkit_case_get(test_case, key);
    TestIntMatrix matrix = {0};
    while (isspace((unsigned char)*cursor)) cursor++;
    if (*cursor++ != '[') goto invalid;
    for (;;) {
        int size;
        int *row;
        int **next_rows;
        int *next_sizes;
        while (isspace((unsigned char)*cursor)) cursor++;
        if (*cursor == ']') {
            cursor++;
            while (isspace((unsigned char)*cursor)) cursor++;
            if (*cursor == '\0') return matrix;
            goto invalid;
        }
        row = testkit_parse_int_array(&cursor, &size);
        if (size < 0) goto invalid;
        next_rows = realloc(matrix.rows, sizeof(*matrix.rows) * (matrix.row_count + 1));
        if (next_rows == NULL) { free(row); goto invalid; }
        matrix.rows = next_rows;
        next_sizes = realloc(matrix.column_sizes,
                             sizeof(*matrix.column_sizes) * (matrix.row_count + 1));
        if (next_sizes == NULL) { free(row); goto invalid; }
        matrix.column_sizes = next_sizes;
        matrix.rows[matrix.row_count] = row;
        matrix.column_sizes[matrix.row_count++] = size;
        while (isspace((unsigned char)*cursor)) cursor++;
        if (*cursor == ',') cursor++;
        else if (*cursor != ']') goto invalid;
    }

invalid:
    free_int_matrix(&matrix);
    fprintf(stderr, "field '%s' is not an integer matrix in %s\n", key, test_case->name);
    longjmp(testkit_failure_jump, 1);
}

struct ListNode *get_list(const TestCase *test_case, const char *key) {
    int size;
    int *values = get_int_array(test_case, key, &size);
    struct ListNode *head = NULL;
    struct ListNode **tail = &head;
    for (int i = 0; i < size; i++) {
        *tail = malloc(sizeof(**tail));
        if (*tail == NULL) {
            free(values);
            free_list(head);
            longjmp(testkit_failure_jump, 1);
        }
        **tail = (struct ListNode){values[i], NULL};
        tail = &(*tail)->next;
    }
    free(values);
    return head;
}

struct TreeNode *get_tree(const TestCase *test_case, const char *key) {
    const char *source = testkit_case_get(test_case, key);
    char *copy = testkit_strdup(source);
    char *cursor;
    struct TreeNode **nodes = NULL;
    int count = 0;
    struct TreeNode *root = NULL;
    if (copy == NULL) longjmp(testkit_failure_jump, 1);
    cursor = testkit_trim(copy);
    if (*cursor != '[') goto done;
    cursor++;
    while (*cursor != '\0' && *cursor != ']') {
        char *end = strchr(cursor, ',');
        char *token;
        if (end == NULL) end = strchr(cursor, ']');
        if (end == NULL) goto done;
        *end = '\0';
        token = testkit_trim(cursor);
        struct TreeNode **next = realloc(nodes, sizeof(*nodes) * (count + 1));
        if (next == NULL) goto done;
        nodes = next;
        if (strcmp(token, "null") == 0) {
            nodes[count++] = NULL;
        } else {
            char *number_end;
            long value = strtol(token, &number_end, 10);
            if (*testkit_trim(number_end) != '\0') goto done;
            nodes[count] = malloc(sizeof(*nodes[count]));
            if (nodes[count] == NULL) goto done;
            *nodes[count] = (struct TreeNode){(int)value, NULL, NULL};
            count++;
        }
        cursor = end + 1;
    }
    if (count == 0 || nodes[0] == NULL) goto done;
    root = nodes[0];
    int child = 1;
    for (int parent = 0; parent < count && child < count; parent++) {
        if (nodes[parent] == NULL) continue;
        nodes[parent]->left = nodes[child++];
        if (child < count) nodes[parent]->right = nodes[child++];
    }

done:
    free(nodes);
    free(copy);
    return root;
}

/* Release helpers own only structures allocated by the field parsers. */
void free_tree(struct TreeNode *root) {
    if (root == NULL) return;
    free_tree(root->left);
    free_tree(root->right);
    free(root);
}

void free_int_matrix(TestIntMatrix *matrix) {
    if (matrix == NULL) return;
    for (int i = 0; i < matrix->row_count; i++) free(matrix->rows[i]);
    free(matrix->rows);
    free(matrix->column_sizes);
    *matrix = (TestIntMatrix){0};
}

void free_list(struct ListNode *head) {
    while (head != NULL) {
        struct ListNode *next = head->next;
        free(head);
        head = next;
    }
}

/* Assertion implementations report context, then abort only the current case. */
void assert_int_equal_impl(int expected, int actual, const char *file, int line) {
    if (expected == actual) return;
    fprintf(stderr, "%s:%d: expected %d, actual %d\n", file, line, expected, actual);
    longjmp(testkit_failure_jump, 1);
}

void assert_bool_equal_impl(bool expected, bool actual, const char *file, int line) {
    if (expected == actual) return;
    fprintf(stderr, "%s:%d: expected %s, actual %s\n", file, line,
            expected ? "true" : "false", actual ? "true" : "false");
    longjmp(testkit_failure_jump, 1);
}

void assert_float_equal_impl(double expected, double actual, double tolerance,
                              const char *file, int line) {
    double difference = expected - actual;
    if (difference < 0.0) difference = -difference;
    if (tolerance >= 0.0 && difference <= tolerance) return;
    fprintf(stderr, "%s:%d: expected %.17g, actual %.17g (tolerance %.17g)\n",
            file, line, expected, actual, tolerance);
    longjmp(testkit_failure_jump, 1);
}

void assert_string_equal_impl(const char *expected, const char *actual,
                              const char *file, int line) {
    if (expected == actual || (expected != NULL && actual != NULL && strcmp(expected, actual) == 0)) return;
    fprintf(stderr, "%s:%d: expected \"%s\", actual \"%s\"\n", file, line,
            expected == NULL ? "(null)" : expected, actual == NULL ? "(null)" : actual);
    longjmp(testkit_failure_jump, 1);
}

static void testkit_print_array(const int *values, int size) {
    fputc('[', stderr);
    for (int i = 0; i < size; i++) {
        if (i != 0) fputs(", ", stderr);
        fprintf(stderr, "%d", values[i]);
    }
    fputc(']', stderr);
}

void assert_array_equal_impl(const int *expected, int expected_size,
                                 const int *actual, int actual_size,
                                 const char *file, int line) {
    bool equal = expected_size == actual_size;
    for (int i = 0; equal && i < expected_size; i++) equal = expected[i] == actual[i];
    if (equal) return;
    fprintf(stderr, "%s:%d: array mismatch\nexpected: ", file, line);
    testkit_print_array(expected, expected_size);
    fputs("\nactual:   ", stderr);
    testkit_print_array(actual, actual_size);
    fputc('\n', stderr);
    longjmp(testkit_failure_jump, 1);
}

void assert_unordered_equal_impl(const int *expected, int expected_size,
                                           const int *actual, int actual_size,
                                           const char *file, int line) {
    bool equal = expected_size == actual_size;
    bool *matched = equal ? calloc((size_t)actual_size, sizeof(*matched)) : NULL;
    if (equal && actual_size > 0 && matched == NULL) longjmp(testkit_failure_jump, 1);
    for (int i = 0; equal && i < expected_size; i++) {
        int found = -1;
        for (int j = 0; j < actual_size; j++) {
            if (!matched[j] && expected[i] == actual[j]) { found = j; break; }
        }
        if (found < 0) equal = false;
        else matched[found] = true;
    }
    free(matched);
    if (equal) return;
    fprintf(stderr, "%s:%d: unordered array mismatch\nexpected: ", file, line);
    testkit_print_array(expected, expected_size);
    fputs("\nactual:   ", stderr);
    testkit_print_array(actual, actual_size);
    fputc('\n', stderr);
    longjmp(testkit_failure_jump, 1);
}

void assert_matrix_equal_impl(const TestIntMatrix *expected,
                                  const TestIntMatrix *actual,
                                  const char *file, int line) {
    bool equal = expected->row_count == actual->row_count;
    for (int row = 0; equal && row < expected->row_count; row++) {
        equal = expected->column_sizes[row] == actual->column_sizes[row];
        for (int column = 0; equal && column < expected->column_sizes[row]; column++)
            equal = expected->rows[row][column] == actual->rows[row][column];
    }
    if (equal) return;
    fprintf(stderr, "%s:%d: integer matrix mismatch\n", file, line);
    longjmp(testkit_failure_jump, 1);
}

static bool testkit_int_row_eq(const int *left, int left_size,
                          const int *right, int right_size) {
    if (left_size != right_size) return false;
    for (int i = 0; i < left_size; i++) {
        if (left[i] != right[i]) return false;
    }
    return true;
}

void assert_unordered_rows_equal_impl(const TestIntMatrix *expected,
                                            const TestIntMatrix *actual,
                                            const char *file, int line) {
    bool equal = expected->row_count == actual->row_count;
    bool *matched = equal ? calloc((size_t)actual->row_count, sizeof(*matched)) : NULL;
    if (equal && actual->row_count > 0 && matched == NULL) longjmp(testkit_failure_jump, 1);
    for (int i = 0; equal && i < expected->row_count; i++) {
        int found = -1;
        for (int j = 0; j < actual->row_count; j++) {
            if (!matched[j] && testkit_int_row_eq(expected->rows[i], expected->column_sizes[i],
                                             actual->rows[j], actual->column_sizes[j])) {
                found = j;
                break;
            }
        }
        if (found < 0) equal = false;
        else matched[found] = true;
    }
    free(matched);
    if (equal) return;
    fprintf(stderr, "%s:%d: unordered integer matrix mismatch\n", file, line);
    longjmp(testkit_failure_jump, 1);
}

void assert_list_equal_impl(const struct ListNode *expected,
                            const struct ListNode *actual,
                            const char *file, int line) {
    int index = 0;
    while (expected != NULL && actual != NULL && expected->val == actual->val) {
        expected = expected->next;
        actual = actual->next;
        index++;
    }
    if (expected == NULL && actual == NULL) return;
    fprintf(stderr, "%s:%d: linked list mismatch at index %d\n", file, line, index);
    longjmp(testkit_failure_jump, 1);
}
