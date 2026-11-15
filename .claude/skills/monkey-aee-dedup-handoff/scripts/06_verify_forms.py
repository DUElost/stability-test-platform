# -*- coding: utf-8 -*-
# 参考实现（reference implementation）—— 不是生产工具，执行者不得原样执行。
# 来源：/home/debian13/tmp/p535_monkey_20261111/verify_final_forms.py（2026-11 实跑）；管线：per-build；数据契约：交付共享根表单 + 目录。
# 用法：把 CONFIG 区的 run 集合、CUT、build / folder / 目录名、路径改成现场值后再跑；
#       解释器用 /home/debian13/start-log-scan-releases/2.1.0/venv/bin/python（含 xlrd / xlwt）。
# 凭据：一律经环境变量提供，不得写回本文件；查库按 prod-db-readonly-diagnose skill 走只读角色。
# 权威说明：.claude/skills/monkey-aee-dedup-handoff/SKILL.md
#
import os, glob, xlrd, json
# --- CONFIG（现场调整：下列常量是 2026-11 实跑值）---
BASE="/home/debian13/tmp/p535_monkey_20261111"
folders=sorted([d for d in os.listdir("/mnt/jxtinno-sonic") if d.endswith("_1106_MonkeyAEEinfo")])
out=[]
for f in folders:
    forms=glob.glob(os.path.join("/mnt/jxtinno-sonic",f,"Result_None_None_MonkeyAEE_SH_*.xls"))
    assert len(forms)==1, (f,forms)
    form=forms[0]; sh=xlrd.open_workbook(form).sheet_by_index(0)
    miss=0; cnt=0
    for i in range(1,sh.nrows):
        cnt+=int(sh.cell_value(i,11))
        p=str(sh.cell_value(i,1)).replace("/home/jxtinno/sonic_tinno","/mnt/jxtinno-sonic")
        if not os.path.isfile(p): miss+=1
    out.append({"folder":f,"form":form,"issues":sh.nrows-1,"count":cnt,"missing":miss,"version":str(sh.cell_value(1,2))})
    print(f,"issues",sh.nrows-1,"count",cnt,"missing",miss,"version",sh.cell_value(1,2))
json.dump(out,open(BASE+"/forms_final.json","w"),ensure_ascii=False,indent=1)
print("total",sum(x["issues"] for x in out),sum(x["count"] for x in out))
