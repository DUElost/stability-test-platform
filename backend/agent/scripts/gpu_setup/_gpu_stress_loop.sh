#!/system/bin/sh
# Platform GPU stress loop (issue #462 P0c, G15 D1).
# Per-round `am instrument -e loop 1` + structured markers appended to stdout
# (redirected to /sdcard/Auto/test_log.txt by the launcher).
# NOTE: `-e loop 1` x N vs toolkit's `-e loop N` equivalence is to be confirmed
# by real-device smoke (see docs/notes/feature/2026-08-31-toolkit-android-tools-g15-alignment.md).
#
# v1.0.9（#774 run 359 实证）：每轮 am instrument 前清阻塞弹窗。
# 测试每轮自带 force-stop + restart Antutu——restart 后「Security Warning:
# Please enable development settings first!」弹窗重现（确认标记未持久化到
# 测试的启动路径），测试自身不点 → 该轮 JUnit FAILURES（rc=0 假成功）。
# init 的一次性 dismiss 只能清当次——此处下沉到每轮。
rounds=$1
testid=$2
echo "GPU_RUN_START test_id=${testid} rounds=${rounds}"

dismiss_dialogs() {
    # 最多 3 轮：dump UI → 找 OK/确定/知道了/允许 按钮 → tap 中心
    attempt=0
    while [ "$attempt" -lt 3 ]; do
        uiautomator dump /data/local/tmp/stp_ui.xml >/dev/null 2>&1
        line=$(cat /data/local/tmp/stp_ui.xml 2>/dev/null \
            | grep -oE 'text="(OK|确定|知道了|允许)"[^>]*bounds="\[[0-9]+,[0-9]+\]\[[0-9]+,[0-9]+\]"' \
            | head -1)
        [ -z "$line" ] && break
        # sed 提取 4 个数字（tr -d '[]' 会把 ][ 合并——4361246 类错位）
        box=$(echo "$line" | sed -E 's/.*bounds="\[([0-9]+),([0-9]+)\]\[([0-9]+),([0-9]+)\].*/\1 \2 \3 \4/')
        set -- $box
        # 注意：此处 $1..$4 已改为坐标——脚本参数已存入 rounds/testid
        input tap $(( ($1 + $3) / 2 )) $(( ($2 + $4) / 2 ))
        sleep 1
        attempt=$((attempt + 1))
    done
}

i=1
while [ "$i" -le "$rounds" ]; do
    dismiss_dialogs
    am instrument -w -m -e listener com.transsion.common.TestCaseRunListener \
        -e debug false -e loop 1 \
        -e class com.transsion.testcaserepository.stressgpu.TestStressGpuExecute#test_StressSpecial_GPUTest_${testid} \
        com.transsion.testcaserepository.test/androidx.test.runner.AndroidJUnitRunner
    rc=$?
    echo "GPU_ROUND ${i} rc=${rc}"
    i=$((i + 1))
done
echo "GPU_RUN_END rc=${rc}"
