# -*- coding: utf-8 -*-
"""
TVBox/FongMi 接口源自动筛选脚本
从多个上游公开接口拉取站点 -> 可用性/速度/1080p 探测 -> 去重合并 -> 输出自有接口 + 报告
运行: python filter_sources.py [config.json]
依赖: requests (必需), pycryptodome (可选, 用于解密加密接口)
"""
import base64
import hashlib
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin, urlparse

try:
    import requests
except ImportError:
    sys.exit("缺少依赖: pip install requests")

try:
    from Crypto.Cipher import AES
    HAS_AES = True
except ImportError:
    HAS_AES = False

UA = {"User-Agent": "okhttp/3.15"}
HEX_RE = re.compile(r"^[0-9a-fA-F\s]+$")
RES_RE = re.compile(r"RESOLUTION=(\d+)x(\d+)")


def log(msg):
    print(time.strftime("[%H:%M:%S]"), msg, flush=True)


# ---------- 拉取与解密 ----------

def fetch_bytes(url, timeout, retries, base_dir="."):
    """支持 http(s) 与本地文件(便于离线测试)。返回 bytes 或 None"""
    if not url.lower().startswith(("http://", "https://")):
        path = url if os.path.isabs(url) else os.path.join(base_dir, url)
        try:
            with open(path, "rb") as f:
                return f.read()
        except OSError:
            return None
    for i in range(retries + 1):
        try:
            r = requests.get(url, headers=UA, timeout=timeout, allow_redirects=True)
            if r.status_code == 200 and r.content:
                return r.content
        except requests.RequestException:
            pass
        if i < retries:
            time.sleep(1)
    return None


def _unpad(b):
    if not b:
        return b
    pad = b[-1]
    if 0 < pad <= 16 and b.endswith(bytes([pad]) * pad):
        return b[:-pad]
    return b


def _aes_ecb(data, key):
    k = key.encode()[:16].ljust(16, b"\0")
    return _unpad(AES.new(k, AES.MODE_ECB).decrypt(data))


def _aes_cbc(data, key):
    k = hashlib.md5(key.encode()).hexdigest()[:16].encode()
    return _unpad(AES.new(k, AES.MODE_CBC, iv=k).decrypt(data))


def decode_interface(raw, key_hint=None):
    """把接口原始字节尽力还原成 JSON 文本。支持: 明文 / hex+$#key#$ ECB / 2423 CBC(需;key) / ** base64"""
    try:
        text = raw.decode("utf-8")
        if text.lstrip().startswith(("{", "[")):
            return text
    except UnicodeDecodeError:
        text = raw.decode("utf-8", "ignore")
    st = text.strip()
    data = None
    if HEX_RE.match(st) and len(st) > 32:
        try:
            data = bytes.fromhex(re.sub(r"\s", "", st))
        except ValueError:
            data = None
    blob = data if data is not None else raw
    if HAS_AES and blob.startswith(b"$#"):
        end = blob.find(b"#$", 2)
        if end > 2:
            key = blob[2:end].decode("utf-8", "ignore")
            try:
                return _aes_ecb(blob[end + 2:], key).decode("utf-8")
            except Exception:
                return None
    if HAS_AES and blob.startswith(b"2423") and key_hint:
        try:
            return _aes_cbc(blob[4:], key_hint).decode("utf-8")
        except Exception:
            return None
    if "**" in st:
        try:
            return base64.b64decode(st.split("**")[-1]).decode("utf-8")
        except Exception:
            return None
    return None


def load_interface(url, opt, base_dir="."):
    """返回 (dict|list|None, 说明)。list 表示这是一个 collection 索引"""
    key_hint = None
    if ";key=" in url:
        url, key_hint = url.split(";key=", 1)
    raw = fetch_bytes(url, opt["timeout"], opt["retries"], base_dir)
    if raw is None:
        return None, "拉取失败"
    text = decode_interface(raw, key_hint)
    if text is None:
        return None, "解密/解码失败(可能缺 pycryptodome 或格式不支持)"
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        return None, "JSON 解析失败"
    if isinstance(obj, list):
        return obj, "collection 索引"
    if isinstance(obj, dict) and obj.get("sites"):
        return obj, "OK"
    return None, "结构不识别(无 sites)"


def absolutize(u, base):
    if not u or not isinstance(u, str):
        return u
    if u.startswith(("http://", "https://", "csp_")):
        return u
    return urljoin(base, u)


# ---------- 站点测试 ----------

def _api_join(api, query):
    return api + ("&" if "?" in api else "?") + query


def _probe_collect(api, opt):
    """采集类接口: ac=videolist 实测。返回 (ok, avg_ms, first_vod_id)"""
    lat, vid = [], None
    for _ in range(opt["retries"]):
        t0 = time.time()
        try:
            r = requests.get(_api_join(api, "ac=videolist&pg=1"),
                             headers=UA, timeout=opt["timeout"])
            ms = int((time.time() - t0) * 1000)
            if r.status_code == 200:
                j = r.json()
                if isinstance(j, dict) and j.get("list"):
                    lat.append(ms)
                    if vid is None:
                        vid = j["list"][0].get("vod_id")
        except (requests.RequestException, ValueError):
            pass
    ok = len(lat) > 0
    return ok, (sum(lat) // len(lat) if lat else None), vid


def _probe_hd(api, vid, opt):
    """抽查播放地址 m3u8 分辨率。返回 True/False/None(无法判定)"""
    try:
        r = requests.get(_api_join(api, "ac=videolist&ids=%s" % vid),
                         headers=UA, timeout=opt["timeout"])
        j = r.json()
        play = j["list"][0].get("vod_play_url", "")
    except (requests.RequestException, ValueError, IndexError, KeyError):
        return None
    m3u8 = None
    for part in play.split("#"):
        for seg in part.split("$$$"):
            for piece in seg.split("$"):
                if ".m3u8" in piece:
                    m3u8 = piece.strip()
                    break
    if not m3u8:
        return None
    try:
        r = requests.get(m3u8, headers=UA, timeout=opt["timeout"], stream=True)
        chunk = next(r.iter_content(32768), b"")
        r.close()
    except requests.RequestException:
        return None
    heights = [int(h) for _, h in RES_RE.findall(chunk.decode("utf-8", "ignore"))]
    if not heights:
        return None
    return max(heights) >= 1080


def classify(site):
    api = (site.get("api") or "").strip()
    if api.startswith("csp_"):
        return "csp"
    if api.lower().endswith(".js") or ".js" in api.lower():
        return "drpy"
    if api.lower().startswith("http"):
        return "collect"
    return "other"


def test_site(site, base_url, opt, hd_budget):
    """返回结果 dict。hd_budget 是 [剩余次数] 列表(跨线程共享计数)"""
    name = site.get("name") or site.get("key") or "?"
    kind = classify(site)
    res = {"site": site, "name": name, "kind": kind, "ok": False,
           "ms": None, "hd": None, "note": ""}
    if kind == "csp":
        res["ok"] = bool(opt.get("keep_spiders"))
        res["note"] = "爬虫源,无法离线实测,保留" if res["ok"] else "爬虫源,按配置剔除"
        return res
    if kind == "drpy":
        api = absolutize(site.get("api"), base_url)
        raw = fetch_bytes(api, opt["timeout"], 0, opt.get("_base_dir", "."))
        res["ok"] = raw is not None
        res["note"] = "drpy 脚本可达" if res["ok"] else "drpy 脚本不可达"
        return res
    if kind != "collect":
        res["note"] = "不支持的 api 形式"
        return res
    ok, ms, vid = _probe_collect(site["api"], opt)
    res["ok"] = ok
    res["ms"] = ms
    if not ok:
        res["note"] = "api 无响应/返回异常"
        return res
    if ms is not None and ms > opt.get("max_latency_ms", 99999):
        res["ok"] = False
        res["note"] = "响应过慢(%dms)" % ms
        return res
    res["note"] = "可用"
    if opt.get("hd_probe") and vid and hd_budget[0] > 0:
        hd_budget[0] -= 1
        res["hd"] = _probe_hd(site["api"], vid, opt)
    return res


def dedup_key(site):
    api = (site.get("api") or "").strip()
    if api.startswith("csp_"):
        ext = site.get("ext")
        return api + "|" + (ext if isinstance(ext, str) else json.dumps(ext, sort_keys=True, ensure_ascii=False) if ext else "")
    p = urlparse(api)
    return (p.netloc + p.path).rstrip("/").lower()


def domain_of(site):
    """取直连源的主机名(用于同域去重)。爬虫/脚本等非 http 源返回 ""。"""
    api = (site.get("api") or "").strip()
    if api.startswith("csp_") or not api.lower().startswith("http"):
        return ""
    return urlparse(api).netloc.lower()


# ---------- 主流程 ----------

def main():
    cfg_path = sys.argv[1] if len(sys.argv) > 1 else "config.json"
    base_dir = os.path.dirname(os.path.abspath(cfg_path))
    with open(cfg_path, encoding="utf-8") as f:
        cfg = json.load(f)
    opt = cfg["options"]
    opt["_base_dir"] = base_dir

    # 1. 拉取全部种子接口(含 collection 展开)
    interfaces = []  # (来源名, url, obj)
    for seed in cfg["seeds"]:
        name, url = seed["name"], seed["url"]
        obj, msg = load_interface(url, opt, base_dir)
        if obj is None:
            log("种子[%s] 跳过: %s" % (name, msg))
            continue
        if isinstance(obj, list):
            log("种子[%s] 是合集索引, 展开前 %d 个" % (name, opt.get("collection_limit", 6)))
            for entry in obj[: opt.get("collection_limit", 6)]:
                sub_url = urljoin(url, entry.get("url", ""))
                sub, smsg = load_interface(sub_url, opt, base_dir)
                if sub is None or isinstance(sub, list):
                    log("  子接口[%s] 跳过: %s" % (entry.get("name", "?"), smsg))
                    continue
                interfaces.append(("%s/%s" % (name, entry.get("name", "?")), sub_url, sub))
        else:
            log("种子[%s] 加载成功, 站点 %d 个" % (name, len(obj["sites"])))
            interfaces.append((name, url, obj))

    if not interfaces:
        sys.exit("没有可用的种子接口, 终止")

    # 2. 汇总站点 + 关键词过滤 + 绝对化相对路径
    pool = []
    for src_name, src_url, obj in interfaces:
        for site in obj["sites"]:
            nm = site.get("name", "")
            if any(k in nm for k in opt.get("drop_keywords", [])):
                continue
            s = dict(site)
            if not (s.get("api") or "").startswith("csp_"):
                s["api"] = absolutize(s.get("api"), src_url)
                # 归一化: 剥离上游自带的 ?ac=list 后缀(标准接口由 App 自行拼 ac 参数)
                if "ac=list" in (s["api"] or ""):
                    s["api"] = s["api"].split("?")[0]
            if isinstance(s.get("jar"), str):
                s["jar"] = absolutize(s["jar"], src_url)
            if isinstance(s.get("ext"), str) and s["ext"].startswith(("./", "/")):
                s["ext"] = absolutize(s["ext"], src_url)
            pool.append((src_name, s))
    log("站点池共 %d 个(已过滤关键词)" % len(pool))

    # 3. 并发测试
    hd_budget = [opt.get("hd_probe_limit", 40)]
    results = []
    src_url_of = {n: u for n, u, _ in interfaces}
    with ThreadPoolExecutor(max_workers=opt.get("workers", 16)) as ex:
        futs = {ex.submit(test_site, s, src_url_of[n], opt, hd_budget): (n, s)
                for n, s in pool}
        for fut in as_completed(futs):
            r = fut.result()
            r["src"] = futs[fut][0]
            results.append(r)
    ok_cnt = sum(1 for r in results if r["ok"])
    log("测试完成: 通过 %d / %d" % (ok_cnt, len(results)))

    # 4. 去重(同 api 保留: 优先可用 -> 高清 -> 低延迟)
    def rank(r):
        return (not r["ok"], r["hd"] is not True, r["ms"] or 99999)
    best = {}
    for r in sorted(results, key=rank):
        best.setdefault(dedup_key(r["site"]), r)
    kept = [r for r in best.values() if r["ok"]]
    kept.sort(key=lambda r: (r["kind"] != "collect", r["hd"] is not True, r["ms"] or 9999))

    # 4b. 同域去重: 同一域名只留排名最高的一个直连源(去掉量子/360/虎牙等同一站点的不同线路)
    if opt.get("dedup_domain", True):
        seen_dom, dd = set(), []
        for r in kept:
            d = domain_of(r["site"])
            if d:
                if d in seen_dom:
                    log("同域去重: 丢弃 [%s] (%s)" % (r["name"], d))
                    continue
                seen_dom.add(d)
            dd.append(r)
        kept = dd

    # 直连采集源优先; 秒播/爬虫类(drpy/csp/other)最多保留 max_spiders 个兜底, 其余名额给直连
    max_spiders = opt.get("max_spiders", 8)
    collects = [r for r in kept if r["kind"] == "collect"]
    others = [r for r in kept if r["kind"] != "collect"]
    kept = collects + others[:max_spiders]
    kept = kept[: opt.get("max_sites", 60)]
    n_collect = sum(1 for r in kept if r["kind"] == "collect")
    log("去重后保留 %d 个站点(直连 %d + 秒播/爬虫 %d)" % (len(kept), n_collect, len(kept) - n_collect))

    # 5. 组装输出接口: 基础设施取第一个含 parses 的上游, 站点 = 自用置顶 + 筛选结果
    own = []
    my_path = os.path.join(base_dir, "my_sites.json")
    if os.path.exists(my_path):
        try:
            with open(my_path, encoding="utf-8") as f:
                own = json.load(f).get("sites", [])
        except (json.JSONDecodeError, OSError):
            log("my_sites.json 读取失败, 忽略")
    base = next((o for _, _, o in interfaces if o.get("parses")), interfaces[0][2])
    base_url = next(u for u, o in [(u, o) for _, u, o in interfaces] if o is base)
    out = {}
    for k in ("spider", "wallpaper", "logo"):
        if k == "spider" and not any(r["kind"] in ("csp", "drpy") for r in kept):
            continue  # 无爬虫/脚本源时, 不携带 spider 字段(上游 spider 可能只是占位图)
        if base.get(k):
            out[k] = absolutize(base[k].split(";")[0], base_url) + (";" + base[k].split(";", 1)[1] if ";" in base[k] else "")
    own_keys = {dedup_key(s) for s in own}
    own_doms = {domain_of(s) for s in own if domain_of(s)}
    picked = []
    for r in kept:
        s = r["site"]
        if dedup_key(s) in own_keys:
            continue
        d = domain_of(s)
        if d and d in own_doms:  # 与自用源同域(如量子/360), 上游副本丢弃
            log("同域去重: 丢弃 [%s] (%s, 与自用源同域)" % (r["name"], d))
            continue
        picked.append(s)
    out["sites"] = own + picked

    # 合并「秒播源包」(由 build_spiders.py 从明文 jar 上游裁剪产出; 见 config.spider_pack)
    # 放在直连源之后。spider 字段指向明文 jar 的绝对地址, 随后由 deploy_cos.py 镜像到 COS。
    pack_cfg = cfg.get("spider_pack", {})
    pack_path = os.path.join(base_dir, pack_cfg.get("output", "dist/spider_pack.json"))
    n_pack = 0
    if pack_cfg.get("enabled") and os.path.exists(pack_path):
        try:
            with open(pack_path, encoding="utf-8") as f:
                pack = json.load(f)
            if pack.get("spider") and pack.get("sites"):
                out["spider"] = pack["spider"]
                have = {(s.get("api"), s.get("key")) for s in out["sites"]}
                for s in pack["sites"]:
                    sig = (s.get("api"), s.get("key"))
                    if sig not in have:
                        out["sites"].append(s); have.add(sig)
                n_pack = len(pack["sites"])
                log("已合并秒播源包: %d 个站点 (spider -> %s)" % (n_pack, pack["spider"].split(";")[0]))
            else:
                log("秒播源包为空, 跳过合并")
        except (OSError, json.JSONDecodeError) as e:
            log("秒播源包读取失败, 忽略: %s" % e)
    elif pack_cfg.get("enabled"):
        log("未找到秒播源包(%s), 本次仅直连源" % pack_path)

    # 首页可配置(第②项): 若指定首选类且本次命中, 顶到 sites[0]; 否则维持 my_sites 第一个(=量子)
    # 挂了自动回退的逻辑在 health_check.py(fallback_key)。
    hp = cfg.get("homepage", {})
    pref = (hp.get("preferred_class") or "").strip()
    if pref:
        idx = next((i for i, s in enumerate(out["sites"]) if s.get("api") == "csp_" + pref), None)
        if idx is None:
            log("首页首选类 csp_%s 未在本次站点中, 维持默认首页(%s)" % (pref, out["sites"][0].get("name")))
        elif idx > 0:
            s = out["sites"].pop(idx)
            out["sites"].insert(0, s)
            log("首页已切换为 [%s] (csp_%s)" % (s.get("name"), pref))

    for k in ("parses", "flags", "ijk", "lives", "ads", "rules", "doh"):
        if base.get(k):
            out[k] = base[k]

    os.makedirs(os.path.join(base_dir, "dist"), exist_ok=True)
    itf_path = os.path.join(base_dir, cfg["output"]["interface"])
    with open(itf_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    log("接口已写出: %s (站点 %d 个 = 自用 %d + 直连筛选 %d + 秒播源包 %d)" % (
        itf_path, len(out["sites"]), len(own), len(out["sites"]) - len(own) - n_pack, n_pack))

    # 6. 报告
    lines = ["# 源筛选报告", "",
             "- 运行时间: %s" % time.strftime("%Y-%m-%d %H:%M:%S"),
             "- 站点池: %d | 通过: %d | 去重后保留: %d | 自用置顶: %d | 秒播源包: %d" % (
                 len(pool), ok_cnt, len(kept), len(own), n_pack),
             "", "| 站点 | 来源 | 类型 | 结果 | 延迟ms | 1080p | 备注 |",
             "|---|---|---|---|---|---|---|"]
    for r in sorted(results, key=lambda r: (not r["ok"], r["ms"] or 99999)):
        hd = {True: "是", False: "否", None: "-"}[r["hd"]]
        lines.append("| %s | %s | %s | %s | %s | %s | %s |" % (
            r["name"], r["src"], r["kind"], "通过" if r["ok"] else "剔除",
            r["ms"] if r["ms"] is not None else "-", hd, r["note"]))
    rpt_path = os.path.join(base_dir, cfg["output"]["report"])
    with open(rpt_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    log("报告已写出: %s" % rpt_path)


if __name__ == "__main__":
    main()
