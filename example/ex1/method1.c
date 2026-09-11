/**
 * Note: The returned array must be malloced, assume caller calls free().
 */
int* twoSum(int* nums, int numsSize, int target, int* returnSize) {
    int* out = (int*)malloc(2 * sizeof(int));
    *returnSize = 2;

    int flag = 0;
    for(int i = 0; i < numsSize; i++){
        for(int j = i + 1; j < numsSize; j++){
            // if(j == i) continue;
            if(nums[i] + nums[j] == target){
                out[0] = i;
                out[1] = j;
                flag = 1;
                break;
            }
        }
        if(flag) break;
    }

    return out;
}
