# -*- coding: utf-8 -*-
# 参考实现（reference implementation）—— 不是生产工具，执行者不得原样执行。
# 来源：/home/debian13/tmp/p535_monkey_20261111/form_only.py（2026-11 实跑）；管线：per-build；数据契约：merge_input/*.xls + *_members.json。
# 用法：把 CONFIG 区的 run 集合、CUT、build / folder / 目录名、路径改成现场值后再跑；
#       解释器用 /home/debian13/start-log-scan-releases/2.1.0/venv/bin/python（含 xlrd / xlwt）。
# 凭据：一律经环境变量提供，不得写回本文件；查库按 prod-db-readonly-diagnose skill 走只读角色。
# 权威说明：.claude/skills/monkey-aee-dedup-handoff/SKILL.md
#
import json, os, xlrd, xlwt, re, glob, datetime, sys
# --- CONFIG（现场调整：下列常量是 2026-11 实跑值）---
BASE="/home/debian13/tmp/p535_monkey_20261111"
build=sys.argv[1]; folder=sys.argv[2]
work=os.path.join(BASE,build); target=os.path.join("/mnt/jxtinno-sonic",folder)
def read(p):
    wb=xlrd.open_workbook(p); sh=wb.sheet_by_index(0); hdr=[str(c.value).strip() for c in sh.row(0)]
    return [dict(zip(hdr,[c.value for c in sh.row(i)])) for i in range(1,sh.nrows)]
def parse_target(p):
    m=re.search(r"MonkeyAEEinfo/([^/]+)/aee_exp/([^/]+)/?$",p); return (m.group(1),m.group(2)) if m else (None,None)
org=read(glob.glob(work+"/merge_input/*_org.xls")[0])
fin=sorted(glob.glob(work+"/merge_input/*_dedup_org_*.xls"))[-1]
mem=sorted(glob.glob(work+"/merge_input/*_members.json"))[-1]
final=read(fin); m=json.load(open(mem))
groups={}
for g in m["groups"]:
    groups[g["final_row"]]={"count":g["count"],"device_count":len(g["device_ids"]),
        "rep_key":parse_target(org[g["representative_row"]-1]["Path"]),"device_ids":sorted(g["device_ids"])}
ts=datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
form_path=os.path.join(target,f"Result_None_None_MonkeyAEE_SH_{ts}.xls")
wb=xlwt.Workbook(encoding="utf-8"); ws=wb.add_sheet("aeeexp",cell_overwrite_ok=True)
widths=[1000,10000,6000,7000,2000,4000,7000,7000,30000,30000,7000,5000,7000,10000,8000]
for j,w in enumerate(widths): ws.col(j).width=w
st_hdr=xlwt.easyxf("font: height 200, name Times New Roman, color-index blue, bold on"); st_body=xlwt.easyxf("font: name calibri")
cols=["Id","Path","Version","ExpTime","ExpClass","ExpType ","CurProcess","Package","Detail","CausedBy","extraTag","Count","Activity","DeviceCount","Rom_Ram"]
for j,c in enumerate(cols): ws.write(0,j,c,st_hdr)
for i,f in enumerate(final, start=1):
    g=groups[i]; serial,entry=g["rep_key"]
    decs=glob.glob(os.path.join(target,serial,"aee_exp",entry,"*.DEC"))
    if not decs: raise SystemExit(f"no DEC for {serial}/{entry}")
    path="/home/jxtinno/sonic_tinno/"+folder+"/"+serial+"/aee_exp/"+entry+"/"+os.path.basename(decs[0])+"/__exp_main.txt"
    vals=[i-1,path,build,str(f.get("ExpTime") or ""),str(f.get("ExpClass") or ""),str(f.get("ExpType") or ""),
          str(f.get("CurProcess") or ""),str(f.get("Package") or ""),str(f.get("Detail") or ""),str(f.get("CausedBy") or ""),"",
          int(g["count"]),str(f.get("Activity") or ""),int(g["device_count"]),str(f.get("Rom_Ram") or "")]
    for j,v in enumerate(vals): ws.write(i,j,v,st_body)
wb.save(form_path)
res={"build":build,"folder":folder,"issues":len(final),"count_sum":sum(g["count"] for g in groups.values()),
     "form":form_path,"target":target,"groups":[{"final_row":k,**v} for k,v in groups.items()]}
json.dump(res,open(os.path.join(work,"result.json"),"w"),ensure_ascii=False,indent=1)
print("form",form_path,"issues",len(final),"count",res["count_sum"])
