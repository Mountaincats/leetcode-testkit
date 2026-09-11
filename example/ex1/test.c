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
