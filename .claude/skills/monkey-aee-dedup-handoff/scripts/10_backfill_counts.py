# -*- coding: utf-8 -*-
# 参考实现（reference implementation）—— 不是生产工具，执行者不得原样执行。
# 来源：/home/debian13/tmp/nx3_v78_run1200_dedup_20261105/sync_counts_1103.py（2026-11 实跑）；管线：batch；数据契约：groups_final.json + *_members.json。
# 用法：把 CONFIG 区的 run 集合、CUT、build / folder / 目录名、路径改成现场值后再跑；
#       解释器用 /home/debian13/start-log-scan-releases/2.1.0/venv/bin/python（含 xlrd / xlwt）。
# 凭据：一律经环境变量提供，不得写回本文件；查库按 prod-db-readonly-diagnose skill 走只读角色。
# 权威说明：.claude/skills/monkey-aee-dedup-handoff/SKILL.md
#
import xlrd, xlwt, json, re, glob, os, hashlib, subprocess
# --- CONFIG（现场调整：下列常量是 2026-11 实跑值）---
DIR="/mnt/jxtinno-sonic/MLD-NX3_16_261103V78_1103_MonkeyAEEinfo"
src=sorted(glob.glob(DIR+"/Result_None_None_MonkeyAEE_SH_*.xls"),key=os.path.getmtime)[-1]
BASE="/home/debian13/tmp/nx3_v78_run1200_dedup_20261105"
OUT="/home/debian13/nx3_v78_run1200_dedup_20261105"
local=OUT+"/"+os.path.basename(src).replace(".xls","_counted.xls")
ledger=OUT+"/count_sync_20261105_143300.tsv"
sh=xlrd.open_workbook(src).sheet_by_index(0); hdr=[str(c.value).strip() for c in sh.row(0)]
groups=json.load(open(BASE+"/groups_final.json"))
mem=json.load(open(BASE+"/merge_input/NX3_V78_run1200_20261105_org_dedup_org_20261105_140430_members.json"))
owb=xlrd.open_workbook(BASE+"/merge_input/NX3_V78_run1200_20261105_org.xls"); osh=owb.sheet_by_index(0)
ohdr=[str(c.value).strip() for c in osh.row(0)]
org=[dict(zip(ohdr,[c.value for c in osh.row(i)])) for i in range(1,osh.nrows)]
def parse(p):
    m=re.search(r"MonkeyAEEinfo/([^/]+)/aee_exp/([^/]+)/",p); return (m.group(1),m.group(2)) if m else (None,None)
def prefix(e):
    p=e.split("_"); return "_".join(p[:3]) if len(p)>=3 else e
row2g={r["org_row"]:r["group"] for r in mem["rows"] if r["branch"]!="filtered"}
rep={}; member={}
for g in groups:
    rep[(g["rep_serial"],g["rep_entry"])]=g
    for i,o in enumerate(org,start=1):
        if row2g.get(i)==g["group"]: member[parse(o["Path"])]=g
pref={}
for k,g in member.items():
    pref.setdefault((k[0],prefix(k[1])),[]).append(g)
def match(k):
    g=rep.get(k) or member.get(k)
    if g: return g,"exact"
    c=pref.get((k[0],prefix(k[1])),[])
    return (c[0],"prefix") if len(c)==1 else (None,"none")
wb=xlwt.Workbook(encoding="utf-8"); ws=wb.add_sheet("aeeexp",cell_overwrite_ok=True)
widths=[1000,10000,6000,7000,2000,4000,7000,7000,30000,30000,7000,5000,7000,10000,8000]
for j,w in enumerate(widths): ws.col(j).width=w
st_hdr=xlwt.easyxf("font: height 200, name Times New Roman, color-index blue, bold on")
st_body=xlwt.easyxf("font: name calibri")
for j,h in enumerate(hdr): ws.write(0,j,h,st_hdr)
lines=["row\tserial\tentry\tpackage\told_count\tnew_count\told_devices\tnew_devices\tmatch"]
for i in range(1,sh.nrows):
    vals=[sh.cell_value(i,j) for j in range(sh.ncols)]
    d=dict(zip(hdr,vals)); k=parse(d["Path"]); g,how=match(k)
    if not g: raise SystemExit("no match row %d %s"%(i,k))
    oldc=int(vals[11]); oldd=int(vals[13])
    vals[11]=int(g["count"]); vals[13]=int(g["device_count"])
    for j,v in enumerate(vals):
        if isinstance(v,float) and j in (0,11,13): v=int(v)
        ws.write(i,j,v,st_body)
    lines.append(f"{i}\t{k[0]}\t{k[1]}\t{g['package']}\t{oldc}\t{g['count']}\t{oldd}\t{g['device_count']}\t{how}")
wb.save(local)
open(ledger,"w",encoding="utf-8").write("\n".join(lines)+"\n")
def md5(p):
    h=hashlib.md5()
    with open(p,"rb") as f:
        for b in iter(lambda:f.read(1<<20),b""): h.update(b)
    return h.hexdigest()
print("src",src,"md5",md5(src))
print("local",local,"md5",md5(local),"size",os.path.getsize(local))
