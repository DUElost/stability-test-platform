# -*- coding: utf-8 -*-
# 参考实现（reference implementation）—— 不是生产工具，执行者不得原样执行。
# 来源：/home/debian13/tmp/nx2_v56_dedupe_20261105/make_workbook_final.py（2026-11 实跑）；管线：batch；数据契约：groups_final.json。
# 用法：把 CONFIG 区的 run 集合、CUT、build / folder / 目录名、路径改成现场值后再跑；
#       解释器用 /home/debian13/start-log-scan-releases/2.1.0/venv/bin/python（含 xlrd / xlwt）。
# 凭据：一律经环境变量提供，不得写回本文件；查库按 prod-db-readonly-diagnose skill 走只读角色。
# 权威说明：.claude/skills/monkey-aee-dedup-handoff/SKILL.md
#
import json, xlrd, xlwt, os, datetime, re, collections
base="/home/debian13/tmp/nx2_v56_dedupe_20261105"
outdir=base+"/out"
# --- CONFIG（现场调整：下列常量是 2026-11 实跑值）---
BUILD="MLD-NX2-16-261027V56"
FOLDER="MLD-NX2_16_261027V56_1029_MonkeyAEEinfo"
UNC="\\\\172.21.x.x\\jxtinno\\sonic_tinno\\"+FOLDER
LOCAL="/mnt/jxtinno-sonic/"+FOLDER
groups=json.load(open(base+"/groups_final.json"))
master={ (m["serial"],m["entry"]):m for m in json.load(open(base+"/master_events.json")) }
sizes=json.load(open(base+"/event_sizes.json"))
mem=json.load(open(base+"/merge_input/NX2_V56_campaign_20261105_org_dedup_org_20261105_103622_members.json"))
def read_xls(path):
    wb=xlrd.open_workbook(path); sh=wb.sheet_by_index(0)
    hdr=[str(c.value).strip() for c in sh.row(0)]
    return [dict(zip(hdr,[c.value for c in sh.row(i)])) for i in range(1,sh.nrows)]
org=read_xls(base+"/merge_input/NX2_V56_campaign_20261105_org.xls")
def parse_target(p):
    m=re.search(r"MonkeyAEEinfo/([^/]+)/aee_exp/([^/]+)/?$",p); return (m.group(1),m.group(2)) if m else (None,None)
def ptime(s):
    s=str(s or "").strip()
    if not s or s=="TIME_UNKNOWN": return None
    for fmt in ("%a %b %d %H:%M:%S %Z %Y","%a %b %d %H:%M:%S %z %Y","%Y-%m-%d %H:%M:%S"):
        try: return datetime.datetime.strptime(s,fmt).replace(tzinfo=None)
        except Exception: pass
    return None
CUT=datetime.datetime(2026,11,2,11,20,19)
# 现场调整：仅当本批需要补扫单个事件时填 (串号, 事件目录名)；否则保持 None
EXTRA_EVENT = None
extra_key = EXTRA_EVENT
extra_src = os.path.join(base, "extra", extra_key[0], "aee_exp", extra_key[1]) if extra_key else ""
if extra_key and extra_key in master:
    master[extra_key]["source"]=extra_src; master[extra_key]["source_kind"]="host_pull"
    if extra_src not in sizes:
        import subprocess
        sizes[extra_src]=int(subprocess.check_output(["du","-sb","--exclude=*.DEC",extra_src]).decode().split("\t")[0])
# sort/number
gs=sorted(groups,key=lambda g:(-g["current"],-g["count"],g["package"],g["exp_class"]))
num={g["group"]:i+1 for i,g in enumerate(gs)}
f2g={g["final_row"]:g for g in groups}
assign={}
for r in mem["rows"]:
    key=parse_target(org[r["org_row"]-1]["Path"])
    assign[key]=("过滤: "+str(r["filter_reason"])) if r["branch"]=="filtered" else ("问题 %d"%num[r["group"]])
def style(s): return xlwt.easyxf(s)
st_title=style("font: bold on, height 320; align: vert center")
st_sub=style("font: color-index gray50")
st_hdr=style("font: bold on, color white; pattern: pattern solid, fore_colour dark_blue; borders: left thin, right thin, top thin, bottom thin; align: horiz center, vert center, wrap on")
st_body=style("borders: left thin, right thin, top thin, bottom thin; align: vert top, wrap on")
st_ctr=style("borders: left thin, right thin, top thin, bottom thin; align: horiz center, vert top")
st_num=style("borders: left thin, right thin, top thin, bottom thin; align: horiz right, vert top")
st_key=style("font: bold on; borders: left thin, right thin, top thin, bottom thin; align: vert top, wrap on")
st_note=style("align: vert top, wrap on")
wb=xlwt.Workbook(encoding="utf-8")
def sheet(name,widths):
    ws=wb.add_sheet(name,cell_overwrite_ok=True)
    for i,w in enumerate(widths): ws.col(i).width=int(256*w)
    return ws
def header(ws,cols,row=0):
    for j,c in enumerate(cols): ws.write(row,j,c,st_hdr)
    ws.row(row).height_mismatch=True; ws.row(row).height=620

# 概览
ws=sheet("概览",[18,12,17,17,10,14,12,10,12,36])
ws.write_merge(0,0,0,9,"NX2 Monkey 专项日志去重汇总（%s）"%BUILD,st_title)
ws.write_merge(1,1,0,9,"数据快照 2026-11-05 10:35；PlanRun 1179/1193/1195/1198；1195/1198 仍在运行",st_sub)
rows=[("观测事件（去重前）",334,"按 run 分别登记的事件观测数合计"),
      ("唯一事件（串号+事件目录）",231,"跨 run 物理目录去重后的数量"),
      ("去重后问题",34,"扫描工具 v2.1.0 离线去重（ExpClass+Package+CausedBy 相似度≥90）"),
      ("本次事件 / 基线事件",97,"本次=ExpTime≥2026-11-02 11:20（1179 开始）；基线=更早遗留事件"),
      ("问题构成",34,"本次 13 / 混合 7 / 仅基线 14"),
      ("扫描过滤",6,"discard_dbg_package 标记，非真实问题"),
      ("代表证据",34,"每个问题 1 个代表事件目录，约 3.9 GB"),
      ("交付状态","已复制 34/34","2026-11-05 11:18 已复制到 15.4；34 个目录文件数与字节数逐一一致；账本 copy_manifest_20261105.tsv"),
      ("Jira 表单","已回填","Result_None_None_MonkeyAEE_SH_20261105_113930.xls：34 行，Count 合计 225，DeviceCount 已按去重表回填（含基线事件）；备份 .xls.bak-20261105_1145"),
      ("未纳入扫描条目",109,"扫描报表中出现但不在本次 run 事件登记内的 baseline/temp 目录"),
      ("目标共享目录",UNC,"15.4 新建目录；同一 build 仅一个 MonkeyAEEinfo 目录")]
r=3
for k,v,d in rows:
    ws.write(r,0,k,st_key); ws.write_merge(r,r,1,3,str(v),st_body); ws.write_merge(r,r,4,9,d,st_body); r+=1
r+=1
header(ws,["Run ID","状态","开始","结束","作业数","完成/失败/中止","观测事件","设备数","唯一事件","日志来源"],r); r+=1
runrows=[(1179,"FAILED","2026-11-02 11:20","2026-11-03 01:07",79,"0/0/79",31,23,31,"旧中心 /mnt/stp-aee（已上传 31 条）"),
         (1193,"FAILED","2026-11-03 17:31","2026-11-04 00:29",79,"0/0/79",72,38,72,"15.4 /mnt/stp-log（已上传/归档 72 条）"),
         (1195,"RUNNING","2026-11-04 01:17","—",79,"0/1/0",229,45,228,"宿主 LOCAL，已从 6 台 host 拉取暂存"),
         (1198,"RUNNING","2026-11-04 01:17","—",9,"0/0/0",2,1,2,"宿主 LOCAL，已拉取暂存"),
         ("合计","—","—","—",246,"0/1/158",334,"—",231,"—")]
for row in runrows:
    for j,v in enumerate(row): ws.write(r,j,v,st_num if j in (0,4,6,7,8) else st_body)
    r+=1
r+=1
ws.write_merge(r,r,0,9,"说明：1195/1198 仍在运行，本表为 2026-11-05 10:35 快照；结束后需补扫新增事件并更新 Count/DeviceCount。",st_note)

# 问题汇总
ws=sheet("问题汇总",[6,10,14,20,34,54,8,8,8,8,40,14,16,16,16,28,52,46,26])
header(ws,["序号","类别","异常类型","子类型","应用包名","问题摘要","发生次数","本次次数","基线次数","设备数","涉及串号","来源 Run","首见时间","末见时间","代表串号","代表事件目录","代表源路径","目标相对路径","备注"])
for i,g in enumerate(gs,start=1):
    lines=[l.strip() for l in g["caused"].splitlines() if l.strip()]
    summary=""
    for l in lines:
        if l.startswith("Subject: "): summary=l[len("Subject: "):]; break
    if not summary: summary=lines[0] if lines else ""
    if summary.startswith("Activity: "): summary=summary[len("Activity: "):]
    note=[]
    if g["category"]=="基线": note.append("仅基线（往期遗留），建议不提交 Jira")
    elif g["category"]=="混合": note.append("含本次 %d 次 / 基线 %d 次"%(g["current"],g["baseline"]))
    else: note.append("本次新增")
    note.append("代表证据在中心存储" if g["rep_source_kind"]=="center" else "代表证据在宿主，已暂存")
    vals=[i,{"本次":"本次","基线":"基线","混合":"混合"}[g["category"]],g["exp_class"],g["exp_type"],g["package"],summary[:300],
          g["count"],g["current"],g["baseline"],g["device_count"],",".join(g["device_ids"]),",".join(map(str,g["runs"])),
          g["first"],g["last"],g["rep_serial"],g["rep_entry"],g["rep_source"] or "",
          FOLDER+"/"+g["rep_serial"]+"/aee_exp/"+g["rep_entry"]+"/","；".join(note)]
    for j,v in enumerate(vals): ws.write(i,j,v,st_num if j in (6,7,8,9) else (st_ctr if j in (0,1) else st_body))

# 代表日志清单
ws=sheet("代表日志清单",[8,10,14,30,16,34,16,14,52,50,58,10,8,40])
header(ws,["问题序号","类别","异常类型","应用包名","代表串号","事件目录","代表阶段/时间","源位置","源路径","目标相对路径","目标 UNC","大小 MB","文件数","备注"])
for i,g in enumerate(gs,start=1):
    rel=FOLDER+"/"+g["rep_serial"]+"/aee_exp/"+g["rep_entry"]
    loc="中心存储" if g["rep_source_kind"]=="center" else "宿主（已暂存）"
    hp=master.get((g["rep_serial"],g["rep_entry"]),{}).get("local_path","")
    if g["rep_source_kind"]=="center": note2="中心存储已有；复制原始事件目录；共享上可重扫生成 .DEC"
    else: note2="宿主原路径: %s；已暂存"%hp
    rep_time=(g["rep_phase"]+" "+(g.get("rep_time") or "")).strip()
    # use actual rep time from member list not stored; show phase + first/last instead
    vals=[i,g["category"],g["exp_class"],g["package"],g["rep_serial"],g["rep_entry"],rep_time,loc,g["rep_source"] or "",
          rel+"/",UNC+"\\"+g["rep_serial"]+"\\aee_exp\\"+g["rep_entry"]+"\\",g["size_mb"],g["files"],note2]
    for j,v in enumerate(vals): ws.write(i,j,v,st_num if j in (11,12) else (st_ctr if j in (0,1,6) else st_body))

# 全部事件
ws=sheet("全部事件",[6,18,34,8,18,16,34,14,10,16,52,48,10])
header(ws,["序号","串号","事件目录","类型","ExpTime","ExpClass","应用包名","来源 Run","阶段","归属","源路径","目标相对路径","大小 MB"])
events=[]
for o in org:
    key=parse_target(o["Path"]); m=master.get(key)
    if not m: continue
    t=ptime(o.get("ExpTime"))
    if not t or t.year<2025:
        t=ptime(m.get("detected_at"))
    phase="本次" if t and t>=CUT else ("基线" if t else "未知")
    src=m.get("source") or ""
    rel=FOLDER+"/"+key[0]+"/aee_exp/"+key[1]
    events.append((key[0],key[1],o,src,rel,assign.get(key,""),phase))
events.sort(key=lambda x:(x[0],x[1]))
for i,(serial,entry,o,src,rel,a,phase) in enumerate(events,start=1):
    mb=round(sizes.get(src,0)/1024/1024,1) if src else ""
    vals=[i,serial,entry,entry.split(".")[-1],str(o["ExpTime"]),str(o["ExpClass"]),str(o["Package"]),
          ",".join(map(str,master[serial,entry]["runs"])),phase,a,src,rel+"/",mb]
    for j,v in enumerate(vals): ws.write(i,j,v,st_num if j in (0,12) else (st_ctr if j in (3,8) else st_body))

# 口径说明
ws=sheet("口径说明",[24,110])
header(ws,["项目","说明"])
notes=[("数据来源","1179 与 1193 的事件与扫描报表来自中心存储；1195/1198 的事件仍在宿主 LOCAL，已按 device_log_event.local_path 从 6 台宿主拉取 158 个事件目录并补扫；10:32 新增的 1 条 ANR 也已补拉。"),
 ("事件去重键","串号 + aee_exp 下事件目录名。同一物理目录在多个 run 中被重复观测时只保留一条；本批 334 条观测去重为 231 条唯一事件。"),
 ("问题归并规则","扫描工具 v2.1.0 离线去重：ExpClass 相同、Package 相同、CausedBy 规范化后相似度 ≥ 90 归为同一问题；工具同时执行 pkglist 白名单过滤。"),
 ("本次 / 基线","以 run 1179 开始时间 2026-11-02 11:20:19 为界：ExpTime 不早于该时刻为“本次”，早于该时刻为“基线”。RebootException 等 ExpTime 为 2010 的事件按 device_log_event.detected_at 回退判定。"),
 ("Count 口径","同一问题下按 (DeviceId, ExpTime, CausedBy) 去重后的事件数；本次每个事件目录对应一次发生，Count 与成员目录数一致。"),
 ("DeviceCount 口径","同一问题涉及的不同 DeviceId 数；跨 run 取并集。"),
 ("代表选择","混合问题优先选“本次”最早发生的事件作代表；仅基线问题只能选基线事件。交付时只复制代表事件目录。"),
 ("扫描过滤","6 条记录的 Package 为 discard_dbg_package（预分析丢弃标记），工具按 not_in_pkglist 过滤，不计入问题表；明细见“全部事件”的“归属”列。"),
 ("未纳入条目","扫描报表中还出现 109 个不在 device_log_event 登记内的目录（1793..._temp、1793..._db.XX.TYPE 等预分析/baseline 目录），未纳入本次汇总，避免把往期遗留问题重复计入。"),
 ("目标目录","同一 build 只建一个目录：%s。目录名规则：<product>_<build>_<首轮日期>_MonkeyAEEinfo；日期按用户指定 1029。"%FOLDER),
 ("运行状态","1179/1193 已终态 FAILED（79 台全部 ABORTED）；1195/1198 仍为 RUNNING（截至 2026-11-05 10:35）。本表是快照，结束后应补扫并重算。"),
 ("待确认","1) 仅基线的 14 个问题日志已复制，是否提交 Jira 待定；2) 109 条 baseline/temp 目录是否纳入；3) 1195/1198 结束后是否重跑本流程并补交付。")]
for i,(k,v) in enumerate(notes,start=1):
    ws.write(i,0,k,st_key); ws.write(i,1,v,st_body)
path=outdir+"/NX2_V56_Monkey_去重汇总_20261105.xls"
wb.save(path)
print("saved",path)
print("issues",len(gs),"cats",collections.Counter(g["category"] for g in gs),"cur",sum(g["current"] for g in gs),"base",sum(g["baseline"] for g in gs))
