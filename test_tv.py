# -*- coding: utf-8 -*-
"""
tv.json 接口自测工具
用法: python test_tv.py [tv.json路径] [关键词1 关键词2 ...]
作用: 模拟 TVBox 加载接口 -> 对每个站点调搜索接口 -> 报告能否搜出资源
维护时: 每次改完 tv.json，跑一遍即可确认站点是否还活着
"""
import json, sys, io, time, ssl, urllib.request, urllib.parse

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE

CONFIG = sys.argv[1] if len(sys.argv) > 1 else "tv.json"
KWS = sys.argv[2:] if len(sys.argv) > 2 else ["斗罗大陆", "海贼王", "熊出没"]

def fetch(url, timeout=12):
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Linux; Android 11; TV) AppleWebKit/537.36",
        "Accept": "application/json,text/plain,*/*",
    })
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
        raw = r.read()
    return raw.decode("utf-8", errors="replace"), time.time() - t0

def main():
    data = json.load(open(CONFIG, encoding="utf-8"))
    sites = data.get("sites", [])
    print(f"接口文件: {CONFIG}")
    print(f"站点数: {len(sites)} | 测试关键词: {KWS}\n")
    print(f"{'站点':<10} | {'关键词':<8} | 结果")
    print("-" * 55)

    ok_map = {}  # name -> {kw: 命中条数}
    for s in sites:
        name = s.get("name", "?")
        api = s.get("api", "")
        if not api.startswith("http"):
            print(f"{name:<10} | - | 跳过(非http采集, 依赖jar)")
            continue
        ok_map[name] = {}
        for kw in KWS:
            try:
                url = api + "?ac=videolist&wd=" + urllib.parse.quote(kw)
                txt, ms = fetch(url)
                d = json.loads(txt)
                lst = d.get("list") or d.get("data") or []
                cnt = len(lst) if isinstance(lst, list) else 0
                ok_map[name][kw] = cnt
                mark = f"OK {cnt}条 ({ms:.0f}ms)" if cnt > 0 else f"0条 ({ms:.0f}ms)"
            except Exception as e:
                ok_map[name][kw] = -1
                mark = f"FAIL {type(e).__name__}"
            print(f"{name:<10} | {kw:<8} | {mark}")

    print("\n========== 汇总 ==========")
    for name, r in ok_map.items():
        alive = sum(1 for c in r.values() if c > 0)
        total = len(r)
        status = "✅ 稳定" if alive == total else ("⚠️ 部分可用" if alive > 0 else "❌ 已失效")
        detail = "  ".join(f"{kw}:{c}" for kw, c in r.items())
        print(f"  {name:<10} {status}  ({alive}/{total})  {detail}")

if __name__ == "__main__":
    main()
