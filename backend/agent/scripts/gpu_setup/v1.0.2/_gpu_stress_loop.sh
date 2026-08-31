#!/system/bin/sh
# Platform GPU stress loop (issue #462 P0c, G15 D1).
# Per-round `am instrument -e loop 1` + structured markers appended to stdout
# (redirected to /sdcard/Auto/test_log.txt by the launcher).
# NOTE: `-e loop 1` x N vs toolkit's `-e loop N` equivalence is to be confirmed
# by real-device smoke (see docs/notes/feature/2026-08-31-toolkit-android-tools-g15-alignment.md).
rounds=$1
testid=$2
echo "GPU_RUN_START test_id=${testid} rounds=${rounds}"
i=1
while [ "$i" -le "$rounds" ]; do
    am instrument -w -m -e listener com.transsion.common.TestCaseRunListener \
        -e debug false -e loop 1 \
        -e class com.transsion.testcaserepository.stressgpu.TestStressGpuExecute#test_StressSpecial_GPUTest_${testid} \
        com.transsion.testcaserepository.test/androidx.test.runner.AndroidJUnitRunner
    rc=$?
    echo "GPU_ROUND ${i} rc=${rc}"
    i=$((i + 1))
done
echo "GPU_RUN_END rc=${rc}"
