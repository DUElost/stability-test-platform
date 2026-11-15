# -*- coding: utf-8 -*-
# 参考实现（reference implementation）—— 不是生产工具，执行者不得原样执行。
# 来源：/home/debian13/tmp/nx2_v56_dedupe_20261105/make_extras_final.py（2026-11 实跑）；管线：batch；数据契约：汇总工作簿。
# 用法：把 CONFIG 区的 run 集合、CUT、build / folder / 目录名、路径改成现场值后再跑；
#       解释器用 /home/debian13/start-log-scan-releases/2.1.0/venv/bin/python（含 xlrd / xlwt）。
# 凭据：一律经环境变量提供，不得写回本文件；查库按 prod-db-readonly-diagnose skill 走只读角色。
# 权威说明：.claude/skills/monkey-aee-dedup-handoff/SKILL.md
#
import xlrd, csv, json, os
base="/home/debian13/tmp/nx2_v56_dedupe_20261105"
out=base+"/out"
p=out+"/NX2_V56_Monkey_去重汇总_20261105.xls"
wb=xlrd.open_workbook(p)
def dump(sheet,path):
    sh=wb.sheet_by_name(sheet)
    with open(path,"w",encoding="utf-8-sig",newline="") as f:
        w=csv.writer(f)
        for i in range(sh.nrows): w.writerow([sh.cell_value(i,j) for j in range(sh.ncols)])
for s in ["问题汇总","代表日志清单","全部事件"]:
    dump(s, out+"/"+s+".csv")
iss=wb.sheet_by_name("问题汇总"); rep=wb.sheet_by_name("代表日志清单")
issues=[]
for i in range(1,iss.nrows):
    issues.append({"no":int(iss.cell_value(i,0)),"category":iss.cell_value(i,1),"exp_class":iss.cell_value(i,2),
        "exp_type":iss.cell_value(i,3),"package":iss.cell_value(i,4),"count":int(iss.cell_value(i,6)),
        "current":int(iss.cell_value(i,7)),"baseline":int(iss.cell_value(i,8)),"device_count":int(iss.cell_value(i,9)),
        "serials":iss.cell_value(i,10).split(","),"runs":iss.cell_value(i,11),"rep_serial":iss.cell_value(i,14),
        "rep_entry":iss.cell_value(i,15),"rep_source":iss.cell_value(i,16),"target_rel":iss.cell_value(i,17).rstrip("/"),
        "size_mb":float(rep.cell_value(i,11)),"files":int(rep.cell_value(i,12)),"target_unc":rep.cell_value(i,10)})
manifest={"generated_at":"2026-11-05 10:45","snapshot_cutoff":"2026-11-05 10:35","build":"MLD-NX2-16-261027V56",
 "runs":[1179,1193,1195,1198],
 "target_root":"\\\\172.21.x.x\\jxtinno\\sonic_tinno\\MLD-NX2_16_261027V56_1029_MonkeyAEEinfo\\",
 "counts":{"observed_events":334,"unique_events":231,"issues":len(issues),"grouped_events":225,"current_events":97,"baseline_events":128,
           "categories":{"current":13,"mixed":7,"baseline_only":14},"filtered":6,"excluded_scan_only":109,
           "representative_dirs":len(issues),"representative_size_mb":round(sum(x["size_mb"] for x in issues),1)},
 "issues":issues}
json.dump(manifest,open(out+"/manifest.json","w"),ensure_ascii=False,indent=1)
print("extras ok",manifest["counts"])
