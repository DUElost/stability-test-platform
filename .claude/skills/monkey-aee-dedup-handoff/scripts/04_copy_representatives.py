# -*- coding: utf-8 -*-
# 参考实现（reference implementation）—— 不是生产工具，执行者不得原样执行。
# 来源：/home/debian13/tmp/p535_monkey_20261111/repair_clean.py（2026-11 实跑）；管线：per-build；数据契约：master_frozen.json + merge_input/*.xls。
# 用法：把 CONFIG 区的 run 集合、CUT、build / folder / 目录名、路径改成现场值后再跑；
#       解释器用 /home/debian13/start-log-scan-releases/2.1.0/venv/bin/python（含 xlrd / xlwt）。
# 凭据：一律经环境变量提供，不得写回本文件；查库按 prod-db-readonly-diagnose skill 走只读角色。
# 权威说明：.claude/skills/monkey-aee-dedup-handoff/SKILL.md
#
import json, os, xlrd, re, glob, subprocess, time, shutil, sys
# --- CONFIG（现场调整：下列常量是 2026-11 实跑值）---
BASE="/home/debian13/tmp/p535_monkey_20261111"
build=sys.argv[1]; folder=sys.argv[2]
work=os.path.join(BASE,build); st=os.path.join(work,"events"); target=os.path.join("/mnt/jxtinno-sonic",folder)
master={ (m["serial"],m["entry"]):m for m in json.load(open(BASE+"/master_frozen.json")) if m["build"]==build }
def read(p):
    wb=xlrd.open_workbook(p); sh=wb.sheet_by_index(0); hdr=[str(c.value).strip() for c in sh.row(0)]
    return [dict(zip(hdr,[c.value for c in sh.row(i)])) for i in range(1,sh.nrows)]
def parse_target(p):
    m=re.search(r"MonkeyAEEinfo/([^/]+)/aee_exp/([^/]+)/?$",p); return (m.group(1),m.group(2)) if m else (None,None)
cons=glob.glob(work+"/merge_input/*_org.xls")[0]
mem=sorted(glob.glob(work+"/merge_input/*_members.json"))[-1]
org=read(cons); m=json.load(open(mem))
reps=[parse_target(org[g["representative_row"]-1]["Path"]) for g in m["groups"]]
fails=[]
for serial,entry in reps:
    mm=master[(serial,entry)]
    dst_parent=os.path.join(target,serial,"aee_exp"); dst=os.path.join(dst_parent,entry)
    os.makedirs(dst_parent,exist_ok=True)
    if os.path.isdir(dst): shutil.rmtree(dst)
    src=mm["center"][0] if mm["center"] else None
    if src:
        cmd=f"set -o pipefail; tar -C '{os.path.dirname(src)}' -cf - '{entry}' | tar -C '{dst_parent}' --no-same-owner --touch -xf -"
    else:
        root=mm["local"].split("/"+serial+"/aee_exp/")[0]; rel=serial+"/aee_exp/"+entry
        cmd=f"set -o pipefail; ssh -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=no android@{mm['host'].replace('-','.')} \"cd '{root}' && tar -cf - '{rel}'\" | tar -C '{target}' --no-same-owner --touch -xf -"
    p=subprocess.run(["bash","-c",cmd],capture_output=True)
    ok=os.path.isfile(os.path.join(dst,"BUILD_INFO")) and (os.path.isdir(os.path.join(dst,"mobilelog")) or glob.glob(os.path.join(dst,"*.dbg")))
    if not ok:
        fails.append((serial,entry,"full",p.returncode,p.stderr.decode()[:120])); continue
    decs=glob.glob(os.path.join(st,serial,"aee_exp",entry,"*.DEC"))
    if not decs:
        fails.append((serial,entry,"no_dec_src",0,"")); continue
    p=subprocess.run(["cp","-r",decs[0],dst+"/"],capture_output=True)
    if p.returncode!=0:
        fails.append((serial,entry,"dec",p.returncode,p.stderr.decode()[:120])); continue
    if not os.path.isfile(os.path.join(dst,os.path.basename(decs[0]),"__exp_main.txt")):
        fails.append((serial,entry,"dec_missing",0,""))
print("reps",len(reps),"fails",len(fails))
for f in fails[:10]: print(f)
json.dump({"fails":fails},open(os.path.join(work,"repair_clean.json"),"w"),ensure_ascii=False,indent=1)
