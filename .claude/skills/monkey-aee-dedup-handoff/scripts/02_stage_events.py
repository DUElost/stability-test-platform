# -*- coding: utf-8 -*-
# 参考实现（reference implementation）—— 不是生产工具，执行者不得原样执行。
# 来源：/home/debian13/tmp/p535_monkey_20261111/stage_scan.py（2026-11 实跑）；管线：per-build；数据契约：master_frozen.json。
# 用法：把 CONFIG 区的 run 集合、CUT、build / folder / 目录名、路径改成现场值后再跑；
#       解释器用 /home/debian13/start-log-scan-releases/2.1.0/venv/bin/python（含 xlrd / xlwt）。
# 凭据：一律经环境变量提供，不得写回本文件；查库按 prod-db-readonly-diagnose skill 走只读角色。
# 权威说明：.claude/skills/monkey-aee-dedup-handoff/SKILL.md
#
import json, os, subprocess, collections, sys, glob, shutil
# --- CONFIG（现场调整：下列常量是 2026-11 实跑值）---
BASE="/home/debian13/tmp/p535_monkey_20261111"
build=sys.argv[1]; folder=sys.argv[2]
work=os.path.join(BASE,build.replace("/","_"))
st=os.path.join(work,"events")
os.makedirs(st,exist_ok=True)
master=json.load(open(BASE+"/master_frozen.json"))
items=[m for m in master if m["build"]==build]
print("build",build,"events",len(items))
# center copies
n=0
for m in items:
    if not m["center"]: continue
    src=m["center"][0]; dst=os.path.join(st,m["serial"],"aee_exp",m["entry"])
    os.makedirs(dst,exist_ok=True)
    for name in ["BUILD_INFO","ZZ_INTERNAL"]:
        p=os.path.join(src,name)
        if os.path.isfile(p): shutil.copy2(p,os.path.join(dst,name))
    for p in glob.glob(os.path.join(src,"*.dbg")): shutil.copy2(p,os.path.join(dst,os.path.basename(p)))
    n+=1
print("center staged",n)
# host groups
groups=collections.defaultdict(list)
for m in items:
    if m["center"]: continue
    root=m["local"].split("/"+m["serial"]+"/aee_exp/")[0]
    groups[(m["host"],root)].append(m)
for (host,root),evs in sorted(groups.items()):
    parts=[]
    for m in evs:
        rel=m["serial"]+"/aee_exp/"+m["entry"]
        parts += [rel+"/BUILD_INFO",rel+"/ZZ_INTERNAL",rel+"/*.dbg"]
    cmd="cd %s && tar --ignore-failed-read -cf - %s"%(root," ".join(parts))
    p=subprocess.Popen(["ssh","-o","BatchMode=yes","-o","ConnectTimeout=10","-o","StrictHostKeyChecking=no",f"android@{host.replace('-','.')}",cmd],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    ex=subprocess.run(["tar","-C",st,"-xf","-"],stdin=p.stdout,stderr=subprocess.PIPE)
    p.stdout.close(); err=p.stderr.read(); rc=p.wait()
    print(host,"events",len(evs),"ssh",rc,"tar",ex.returncode,"err",err.decode()[:120])
# verify dbg
missing=[]
for m in items:
    d=os.path.join(st,m["serial"],"aee_exp",m["entry"])
    if not glob.glob(os.path.join(d,"*.dbg")): missing.append((m["serial"],m["entry"],d))
print("missing dbg",len(missing),missing[:5])
print("staging du",subprocess.check_output(["du","-sh",st]).decode().strip())
