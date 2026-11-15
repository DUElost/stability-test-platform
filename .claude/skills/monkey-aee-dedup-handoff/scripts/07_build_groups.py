# -*- coding: utf-8 -*-
# 参考实现（reference implementation）—— 不是生产工具，执行者不得原样执行。
# 来源：/home/debian13/tmp/nx2_v56_dedupe_20261105/build_groups_final.py（2026-11 实跑）；管线：batch；数据契约：master_events.json + groups_final.json。
# 用法：把 CONFIG 区的 run 集合、CUT、build / folder / 目录名、路径改成现场值后再跑；
#       解释器用 /home/debian13/start-log-scan-releases/2.1.0/venv/bin/python（含 xlrd / xlwt）。
# 凭据：一律经环境变量提供，不得写回本文件；查库按 prod-db-readonly-diagnose skill 走只读角色。
# 权威说明：.claude/skills/monkey-aee-dedup-handoff/SKILL.md
#
import json, xlrd, re, datetime, collections, os, subprocess
base="/home/debian13/tmp/nx2_v56_dedupe_20261105"
mem=json.load(open(base+"/merge_input/NX2_V56_campaign_20261105_org_dedup_org_20261105_103622_members.json"))
master_list=json.load(open(base+"/master_events.json"))
master={(m["serial"],m["entry"]):m for m in master_list}
sizes=json.load(open(base+"/event_sizes.json"))
# 现场调整：仅当本批需要补扫单个事件时填 (串号, 事件目录名)；否则保持 None
EXTRA_EVENT = None
extra_key = EXTRA_EVENT
extra_src = os.path.join(base, "extra", extra_key[0], "aee_exp", extra_key[1]) if extra_key else ""
if extra_key and extra_key in master:
    master[extra_key]["source"]=extra_src; master[extra_key]["source_kind"]="host_pull"
    if extra_src not in sizes:
        import subprocess
        sizes[extra_src]=int(subprocess.check_output(["du","-sb","--exclude=*.DEC",extra_src]).decode().split("	")[0])
sh=xlrd.open_workbook(base+"/merge_input/NX2_V56_campaign_20261105_org.xls").sheet_by_index(0)
hdr=[str(c.value).strip() for c in sh.row(0)]
org=[dict(zip(hdr,[c.value for c in sh.row(i)])) for i in range(1,sh.nrows)]
def parse_target(p):
    m=re.search(r"MonkeyAEEinfo/([^/]+)/aee_exp/([^/]+)/?$",p); return (m.group(1),m.group(2)) if m else (None,None)
def ptime(s):
    s=str(s or "").strip()
    if not s or s=="TIME_UNKNOWN": return None
    for fmt in ("%a %b %d %H:%M:%S %Z %Y","%a %b %d %H:%M:%S %z %Y","%Y-%m-%d %H:%M:%S"):
        try: return datetime.datetime.strptime(s,fmt).replace(tzinfo=None)
        except Exception: pass
    return None
# --- CONFIG（现场调整：下列常量是 2026-11 实跑值）---
CUT=datetime.datetime(2026,11,2,11,20,19)
def occur_time(o):
    t=ptime(o.get("ExpTime"))
    if t and t.year>=2025: return t
    k=parse_target(o["Path"]); d=master.get(k)
    return ptime(d["detected_at"]) if d else t
groups=[]
for g in mem["groups"]:
    members=[]
    for row in g["member_rows"]:
        o=org[row-1]; k=parse_target(o["Path"]); m=master.get(k,{})
        t=occur_time(o)
        members.append({"key":k,"org":o,"time":t,"phase":"本次" if t and t>=CUT else ("基线" if t else "未知"),"runs":m.get("runs",[]),"source":m.get("source"),"kind":m.get("source_kind")})
    cnt=collections.Counter(x["phase"] for x in members)
    # rep: earliest current if any else earliest
    cur=[x for x in members if x["phase"]=="本次"]
    pool=cur if cur else members
    pool=sorted(pool,key=lambda x:(x["time"] or datetime.datetime(1970,1,1),x["key"]))
    rp=pool[0]
    times=[x["time"] for x in members if x["time"]]
    f=None
    groups.append({"group":g["group"],"final_row":g["final_row"],"count":g["count"],
        "current":cnt.get("本次",0),"baseline":cnt.get("基线",0),"unknown":cnt.get("未知",0),
        "device_count":len(g["device_ids"]),"device_ids":sorted(g["device_ids"]),
        "runs":sorted({r for x in members for r in x["runs"]}),
        "first":min(times).strftime("%Y-%m-%d %H:%M") if times else "",
        "last":max(times).strftime("%Y-%m-%d %H:%M") if times else "",
        "rep_serial":rp["key"][0],"rep_entry":rp["key"][1],"rep_phase":rp["phase"],
        "rep_source":rp["source"],"rep_source_kind":rp["kind"],
        "rep_host":(master.get(rp["key"],{}).get("hosts") or [""])[0],
        "rep_time":rp["time"].strftime("%Y-%m-%d %H:%M") if rp["time"] else "",
        "member_phases":[x["phase"] for x in members]})
# final xls fields
fin=base+"/merge_input/NX2_V56_campaign_20261105_org_dedup_org_20261105_103622.xls"
sh2=xlrd.open_workbook(fin).sheet_by_index(0); h2=[str(c.value).strip() for c in sh2.row(0)]
final=[dict(zip(h2,[c.value for c in sh2.row(i)])) for i in range(1,sh2.nrows)]
for g in groups:
    f=final[g["final_row"]-1]
    g.update({"exp_class":str(f["ExpClass"]),"exp_type":str(f.get("ExpType") or ""),"package":str(f["Package"]),
              "caused":str(f["CausedBy"]),"detail":str(f["Detail"]),"count_xls":int(f["Count"]),"device_xls":int(f["DeviceCount"])})
    g["category"]=("本次" if g["current"]>0 and g["baseline"]==0 else ("基线" if g["current"]==0 else "混合"))
    src=g["rep_source"]
    if src and os.path.isdir(src):
        if g["rep_source_kind"]=="host_pull":
            g["size_mb"]=round(int(subprocess.check_output(["du","-sb","--exclude=*.DEC",src]).decode().split("\t")[0])/1024/1024,1)
        else:
            g["size_mb"]=round(int(subprocess.check_output(["du","-sb",src]).decode().split("\t")[0])/1024/1024,1)
        cnt=int(subprocess.check_output(["find",src]+(["-name","*.DEC","-prune","-o"] if g["rep_source_kind"]=="host_pull" else [])+["-type","f","-print"]).decode().count("\n"))
        g["files"]=cnt
    else:
        g["size_mb"]=0; g["files"]=0; g["size_note"]="missing"
json.dump(groups, open(base+"/groups_final.json","w"), ensure_ascii=False, indent=1)
print("groups",len(groups),"current",sum(g["current"] for g in groups),"baseline",sum(g["baseline"] for g in groups))
print("cats",collections.Counter(g["category"] for g in groups))
for g in sorted(groups,key=lambda x:(-x["current"],-x["count"]))[:8]:
    print(g["group"],g["category"],"total",g["count"],"cur",g["current"],"base",g["baseline"],g["package"])
