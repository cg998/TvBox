# -*- coding: utf-8 -*-
"""按 config.json 的 priority_classes 重排 dist/my_interface.json 的秒播站顺序"""
import json, io

def log(msg):
    with open("reorder_log.txt", "a", encoding="utf-8") as f:
        f.write(msg + "\n")

def main():
    open("reorder_log.txt", "w", encoding="utf-8").close()
    cfg = json.load(open("config.json", encoding="utf-8"))
    hp = cfg.get("homepage", {})
    pref = (hp.get("preferred_class") or "").strip()
    prio = [c for c in (hp.get("priority_classes") or []) if c]
    if pref and pref not in prio:
        prio.insert(0, pref)
    log(f"priority 顺序: {prio}")

    data = json.load(open("dist/my_interface.json", encoding="utf-8"))
    sites = data["sites"]
    by_api = {}
    for s in sites:
        by_api.setdefault(s.get("api"), []).append(s)

    front, fids = [], set()
    for c in prio:
        api = "csp_" + c
        lst = by_api.get(api, [])
        for s in lst:
            if id(s) not in fids:
                front.append(s)
                fids.add(id(s))
    rest = [s for s in sites if id(s) not in fids]
    new_sites = front + rest

    log(f"前置秒播站 {len(front)} 个: {[s.get('name') for s in front]}")
    log(f"总站点 {len(sites)} -> {len(new_sites)}")

    data["sites"] = new_sites
    with io.open("dist/my_interface.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    log("已写回 dist/my_interface.json")
    # 打印新前10个站点
    log("\n新前12站顺序:")
    for s in new_sites[:12]:
        log(f"  {s.get('name')}  api={s.get('api')}")

if __name__ == "__main__":
    main()
