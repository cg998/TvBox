# -*- coding: utf-8 -*-
"""
自有接口每日健康检查
不重拉上游种子, 只体检 dist/my_interface.json 里的现有站点:
- 采集源: 实测 ac=videolist(有内容) 且 class 非空(分类能显示), 记录延迟
- drpy:  脚本地址可达
- csp_:  离线无法实测, 仅校验 jar/ext 链接可达, 默认视为存活
动作: 挂了的降级到末尾; 连续挂 fail_threshold 天剔除; 保证 sites[0](首页默认源)存活且分类齐全,
      否则自动把最佳存活源顶到第一位(保证 App 打开时电影/连续剧/综艺等分类正常显示)
运行: python health_check.py [config.json]
"""
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from filter_sources import (UA, _api_join, classify, dedup_key, fetch_bytes,
                            log, requests)


def check_collect(api, opt):
    """返回 (ok, ms, has_classes, note)"""
    try:
        t0 = time.time()
        r = requests.get(_api_join(api, "ac=videolist&pg=1"),
                         headers=UA, timeout=opt["timeout"])
        ms = int((time.time() - t0) * 1000)
        if r.status_code != 200:
            return False, ms, False, "HTTP %d" % r.status_code
        j = r.json()
    except (requests.RequestException, ValueError):
        return False, None, False, "api 无响应/返回异常"
    has_list = bool(isinstance(j, dict) and j.get("list"))
    has_cls = bool(isinstance(j, dict) and j.get("class"))
    if not has_cls:
        # provide/vod 类源分类走 ac=list, 单独探测一次
        try:
            r2 = requests.get(_api_join(api, "ac=list"),
                              headers=UA, timeout=opt["timeout"])
            j2 = r2.json()
            if isinstance(j2, dict) and j2.get("class"):
                has_cls = True
        except (requests.RequestException, ValueError):
            pass
    if not has_list:
        return False, ms, has_cls, "视频列表为空"
    note = "可用" if has_cls else "可用但无分类(首页不显示类目)"
    return True, ms, has_cls, note


def check_site(site, opt):
    kind = classify(site)
    r = {"site": site, "name": site.get("name") or site.get("key") or "?",
         "kind": kind, "ok": None, "ms": None, "classes": None, "note": ""}
    if kind == "collect":
        r["ok"], r["ms"], r["classes"], r["note"] = check_collect(site["api"], opt)
    elif kind == "drpy":
        raw = fetch_bytes(site["api"], opt["timeout"], 1, opt.get("_base_dir", "."))
        r["ok"] = raw is not None
        r["note"] = "drpy 脚本可达" if r["ok"] else "drpy 脚本不可达"
    elif kind == "csp":
        broken = []
        for f in ("jar", "ext"):
            v = site.get(f)
            if isinstance(v, str) and v.lower().startswith("http"):
                if fetch_bytes(v, opt["timeout"], 0) is None:
                    broken.append(f)
        r["note"] = "爬虫源,离线不实测" + ("; 但 %s 不可达" % "/".join(broken) if broken else "")
        r["ok"] = None if not broken else False
    else:
        r["note"] = "不支持的 api 形式"
        r["ok"] = False
    return r


def main():
    cfg_path = sys.argv[1] if len(sys.argv) > 1 else "config.json"
    base_dir = os.path.dirname(os.path.abspath(cfg_path))
    with open(cfg_path, encoding="utf-8") as f:
        cfg = json.load(f)
    opt = cfg["options"]
    opt["_base_dir"] = base_dir
    hc = cfg.get("health", {})
    threshold = hc.get("fail_threshold", 3)
    today = time.strftime("%Y-%m-%d")

    itf_path = os.path.join(base_dir, cfg["output"]["interface"])
    with open(itf_path, encoding="utf-8") as f:
        itf = json.load(f)
    sites = itf.get("sites", [])
    log("载入接口: %s, 站点 %d 个" % (itf_path, len(sites)))

    state_path = os.path.join(base_dir, hc.get("state_file", "dist/health_state.json"))
    try:
        with open(state_path, encoding="utf-8") as f:
            state = json.load(f)
    except (OSError, json.JSONDecodeError):
        state = {}

    pinned = []
    my_path = os.path.join(base_dir, "my_sites.json")
    if os.path.exists(my_path):
        try:
            with open(my_path, encoding="utf-8") as f:
                pinned = [s.get("key") for s in json.load(f).get("sites", [])]
        except (json.JSONDecodeError, OSError):
            pass

    # 并发体检
    results = []
    with ThreadPoolExecutor(max_workers=opt.get("workers", 16)) as ex:
        futs = {ex.submit(check_site, s, opt): s for s in sites}
        for fut in as_completed(futs):
            results.append(fut.result())
    by_key = {dedup_key(r["site"]): r for r in results}

    # 更新连续失败计数(只对可实测源; 健康的源不记录, 全部健康时 state 无变化, 当天不产生提交)
    for r in results:
        if r["ok"] is None:
            continue
        k = dedup_key(r["site"])
        if r["ok"]:
            state.pop(k, None)
        else:
            st = state.get(k, {"fails": 0})
            st["fails"] = st.get("fails", 0) + 1
            st["last_fail"] = today
            st["name"] = r["name"]
            state[k] = st

    # 分组: 剔除 / 降级 / 存活
    removed, demoted, alive = [], [], []
    for s in sites:  # 保持原顺序遍历
        k = dedup_key(s)
        r = by_key[k]
        fails = state.get(k, {}).get("fails", 0)
        if r["ok"] is False and fails >= threshold:
            removed.append(r)
        elif r["ok"] is False:
            demoted.append(r)
        else:
            alive.append((s, r))
    new_sites = [s for s, _ in alive] + [r["site"] for r in demoted]

    # 首页保障: sites[0] 必须存活且(若是采集源)分类齐全
    def homepage_ok(r):
        return r["ok"] is not False and (r["kind"] != "collect" or r["classes"])
    if new_sites:
        first_r = by_key[dedup_key(new_sites[0])]
        if not homepage_ok(first_r):
            cand = None
            pinned_alive = [(s, r) for s, r in alive if s.get("key") in pinned and homepage_ok(r)]
            pool = pinned_alive or [(s, r) for s, r in alive if homepage_ok(r)]
            if pool:
                cand = min(pool, key=lambda sr: (sr[1]["ms"] is None, sr[1]["ms"] or 9999))
            if cand:
                new_sites.remove(cand[0])
                new_sites.insert(0, cand[0])
                cand[1]["note"] += " [已顶到首页位]"
                log("首页源 [%s] 不可用, 已顶上 [%s]" % (first_r["name"], cand[1]["name"]))
            else:
                log("警告: 没有可用作首页的存活源!")

    itf["sites"] = new_sites
    with open(itf_path, "w", encoding="utf-8") as f:
        json.dump(itf, f, ensure_ascii=False, indent=2)
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

    log("体检完成: 存活 %d, 降级 %d, 剔除 %d" % (len(alive), len(demoted), len(removed)))

    lines = ["# 每日健康检查报告", "",
             "- 运行时间: %s" % time.strftime("%Y-%m-%d %H:%M:%S"),
             "- 存活: %d | 降级(挂但未达阈值): %d | 剔除(连续挂 %d 天): %d" % (len(alive), len(demoted), threshold, len(removed)),
             "", "| 站点 | 类型 | 状态 | 延迟ms | 分类 | 连续失败 | 备注 |",
             "|---|---|---|---|---|---|---|"]
    order = {dedup_key(s): i for i, s in enumerate(new_sites)}
    for r in sorted(results, key=lambda r: order.get(dedup_key(r["site"]), 999)):
        st = state.get(dedup_key(r["site"]), {})
        status = "存活" if r["ok"] is not False else ("已剔除" if st.get("fails", 0) >= threshold else "降级")
        cls = {True: "有", False: "无", None: "-"}[r["classes"]]
        lines.append("| %s | %s | %s | %s | %s | %d | %s |" % (
            r["name"], r["kind"], status,
            r["ms"] if r["ms"] is not None else "-", cls,
            st.get("fails", 0), r["note"]))
    rpt_path = os.path.join(base_dir, hc.get("report", "dist/health_report.md"))
    with open(rpt_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    log("报告已写出: %s" % rpt_path)


if __name__ == "__main__":
    main()
