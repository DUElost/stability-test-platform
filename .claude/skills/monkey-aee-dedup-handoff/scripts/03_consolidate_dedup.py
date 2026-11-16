# -*- coding: utf-8 -*-
# 参考实现（reference implementation）—— 不是生产工具，执行者不得原样执行。
# 来源：/home/debian13/tmp/p535_monkey_20261111/cons_dedup.py（2026-11 实跑）；管线：per-build；数据契约：master_frozen.json + merge_input/*.xls。
# 用法：把 CONFIG 区的 run 集合、CUT、build / folder / 目录名、路径改成现场值后再跑；
#       解释器用 /home/debian13/start-log-scan-releases/2.1.0/venv/bin/python（含 xlrd / xlwt）。
# 凭据：一律经环境变量提供，不得写回本文件；查库按 prod-db-readonly-diagnose skill 走只读角色。
# 权威说明：.claude/skills/monkey-aee-dedup-handoff/SKILL.md
#
import json, os, xlrd, xlwt, re, glob, subprocess, datetime, sys
# --- CONFIG（现场调整：下列常量是 2026-11 实跑值）---
BASE="/home/debian13/tmp/p535_monkey_20261111"
build=sys.argv[1]; folder=sys.argv[2]
work=os.path.join(BASE,build); st=os.path.join(work,"events")
master={ (m["serial"],m["entry"]):m for m in json.load(open(BASE+"/master_frozen.json")) if m["build"]==build }
scan_org=sorted(glob.glob(st+"/Result_None_None_MonkeyAEE_SH_*_org.xls"))[-1]
def read(p):
    wb=xlrd.open_workbook(p); sh=wb.sheet_by_index(0); hdr=[str(c.value).strip() for c in sh.row(0)]
    return [dict(zip(hdr,[c.value for c in sh.row(i)])) for i in range(1,sh.nrows)]
def entry_of(p):
    m=re.search(r"/aee_exp/([^/]+)/",p); return m.group(1) if m else None
def ptime(s):
    s=str(s or "").strip()
    if not s or s=="TIME_UNKNOWN": return None
    for fmt in ("%a %b %d %H:%M:%S %Z %Y","%a %b %d %H:%M:%S %z %Y","%Y-%m-%d %H:%M:%S"):
        try: return datetime.datetime.strptime(s,fmt).replace(tzinfo=None)
        except Exception: pass
    return None
rows=read(scan_org); bykey={}
for r in rows:
    e=entry_of(r["Path"]); s=str(r.get("DeviceId","")).strip()
    if not s or s.lower() in ("none","undefined","unknown_device_id","discard_device_id"):
        m=re.search(r"/([A-Z0-9]{10,})/aee_exp/",r["Path"]); s=m.group(1) if m else ""
    bykey[(s,e)]=r
print("rows",len(rows),"master",len(master),"missing",len(set(master)-set(bykey)))
outdir=os.path.join(work,"merge_input"); os.makedirs(outdir,exist_ok=True)
cons=os.path.join(outdir,build.replace("/","_")+"_org.xls")
wb=xlwt.Workbook(encoding="utf-8"); ws=wb.add_sheet("aeeexp",cell_overwrite_ok=True)
cols=["Id","Path","Version","ExpTime","ExpClass","ExpType ","CurProcess","Package","Detail","CausedBy","extraTag","Count","Activity","DeviceId","Rom_Ram"]
for j,c in enumerate(cols): ws.write(0,j,c)
items=sorted(master.items(), key=lambda kv:(ptime(bykey[kv[0]].get("ExpTime")) or datetime.datetime(1970,1,1), kv[0]))
i=1
for k,_ in items:
    r=bykey.get(k)
    if r is None: continue
    target_dir=os.path.join("/mnt/jxtinno-sonic",build.replace("/","_")) # unused
    vals=[i-1,"/tmp/"+k[1]+"/",build,str(r.get("ExpTime") or ""),str(r.get("ExpClass") or ""),str(r.get("ExpType ") or r.get("ExpType") or ""),
          str(r.get("CurProcess") or ""),str(r.get("Package") or ""),str(r.get("Detail") or ""),str(r.get("CausedBy") or ""),"",1,
          str(r.get("Activity") or ""),k[0],str(r.get("Rom_Ram") or "")]
    # placeholder Path must be unique; replace with target folder path
    vals[1]=os.path.join("/mnt/jxtinno-sonic",folder,k[0],"aee_exp",k[1])+"/"
    for j,v in enumerate(vals): ws.write(i,j,v if isinstance(v,int) else (v[:10000] if len(v)>10000 else v))
    i+=1
wb.save(cons)
print("cons",cons,"rows",i-1)
ded=subprocess.run(["bash","-c",f"cd /home/debian13/start-log-scan-releases/2.1.0 && ./venv/bin/python start_log_scan.py -dedup_org '{cons}' -side shanghai"],capture_output=True)
print("dedup rc",ded.returncode)
fin=sorted(glob.glob(outdir+"/*_dedup_org_*.xls"))[-1]; mem=sorted(glob.glob(outdir+"/*_members.json"))[-1]
final=read(fin); m=json.load(open(mem))
print("issues",len(final),"stats",m["stats"])
