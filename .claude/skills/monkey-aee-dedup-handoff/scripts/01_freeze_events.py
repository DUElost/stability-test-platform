# -*- coding: utf-8 -*-
# 参考实现（reference implementation）—— 不是生产工具，执行者不得原样执行。
# 来源：/home/debian13/tmp/p535_monkey_20261111/freeze.py（2026-11 实跑）；管线：per-build；数据契约：master_frozen.json。
# 用法：把 CONFIG 区的 run 集合、CUT、build / folder / 目录名、路径改成现场值后再跑；
#       解释器用 /home/debian13/start-log-scan-releases/2.1.0/venv/bin/python（含 xlrd / xlwt）。
# 凭据：一律经环境变量提供，不得写回本文件；查库按 prod-db-readonly-diagnose skill 走只读角色。
# 权威说明：.claude/skills/monkey-aee-dedup-handoff/SKILL.md
#
import os, subprocess, json, collections

def _require_env(name):
    v = os.environ.get(name)
    if not v:
        raise SystemExit("缺少环境变量 %s；按 prod-db-readonly-diagnose skill 由站点配置取只读角色与凭据" % name)
    return v

PGHOST = _require_env("PGHOST")
PGUSER = _require_env("PGUSER")            # 只读角色，不得用应用账号
PGDATABASE = _require_env("PGDATABASE")
PGPASSWORD = _require_env("PGPASSWORD")   # 只从环境读取，不得硬编码
# --- CONFIG（现场调整：下列常量是 2026-11 实跑值）---
BASE="/home/debian13/tmp/p535_monkey_20261111"
os.makedirs(BASE,exist_ok=True)
CUT="2026-11-12 17:01:00+08"
def q(sql):
    out=subprocess.check_output(["psql","-h",PGHOST,"-U",PGUSER,"-d",PGDATABASE,"-t","-A","-F","\t","-c",sql],
        env={**os.environ,"PGPASSWORD":PGPASSWORD})
    return [l.split("\t") for l in out.decode().splitlines() if l.strip()]
brows=q("""SELECT d.serial, (pr.run_context->'device_builds'->>ji.device_id::text)
FROM plan_run pr JOIN job_instance ji ON ji.plan_run_id=pr.id JOIN device d ON d.id=ji.device_id WHERE pr.id=1264""")
build={s:b for s,b in brows if b}
rows=q(f"""SELECT plan_run_id, serial, host_id, state, coalesce(size_bytes,0)::bigint,
 split_part(local_path,'/',array_length(string_to_array(local_path,'/'),1)) AS entry,
 coalesce(remote_path,''), local_path
 FROM device_log_event WHERE plan_run_id IN (1246,1253,1256,1264) AND detected_at <= '{CUT}'""")
uniq={}
for r in rows:
    if not r[5]: continue
    uniq.setdefault((r[1],r[5]),[]).append(r)
master=[]
for (serial,entry),recs in sorted(uniq.items()):
    center=[x[6] for x in recs if x[6] and os.path.isdir(x[6])]
    loc=next((x[7] for x in recs if x[7]),"")
    host=next((x[2] for x in recs if x[7]),recs[0][2])
    master.append({"serial":serial,"entry":entry,"build":build.get(serial,"UNKNOWN"),
        "runs":sorted({int(x[0]) for x in recs}),"center":center,"local":loc,"host":host,
        "size":max(int(x[4] or 0) for x in recs)})
json.dump(master,open(BASE+"/master_frozen.json","w"),ensure_ascii=False,indent=1)
print("frozen unique",len(master))
by=collections.defaultdict(list)
for m in master: by[m["build"]].append(m)
for b,items in sorted(by.items(), key=lambda kv:-len(kv[1])):
    cen=sum(1 for m in items if m["center"]); host=len(items)-cen
    print(b,len(items),"center",cen,"host",host,"sizeMB",round(sum(m["size"] for m in items)/1024/1024,1))
