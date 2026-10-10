# -*- coding: utf-8 -*-
"""
构建「秒播源包」——A 方案(明文 jar 自托管)的可维护化环节

做什么:
  1. 从上游(默认 qist/tvbox)拉取 *明文* 爬虫 jar 与它的接口
  2. 校验 jar 未加密(无 ftyguard/native .so 解密层) —— 加密则本次跳过
  3. 按 allow_classes 白名单(且该类真实存在于 jar)裁剪出家庭向 csp 站点
  4. 上游探活(可选): 依 spider_hosts.json 的「类 -> 真实上游域名」剔除已挂站、降级超慢站
  5. 产出 dist/spider_pack.json, 交给 filter_sources.py 合并进主接口
  之后由 deploy_cos.py 把 jar 镜像到自有 COS, 并把接口里的 spider 指向 COS。

为什么这样设计(可维护性):
  - 每次运行都重新拉上游 => 自动跟随上游「新增站点 / 修复解析」
  - jar 一旦被上游改成加密版 => 自动停用本环节, 直连流水线不受影响
  - 白名单/黑名单全在 config.json, 改站点只是改一行配置, 不用动代码
  - 任一环节失败都「退出码 0 + 删除旧包」, 绝不阻断主流水线

运行: python build_spiders.py [config.json]
依赖: requests
"""
import hashlib
import io
import json
import os
import re
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin

import requests

try:
    from Crypto.Cipher import AES
    HAS_AES = True
except ImportError:
    HAS_AES = False

UA = {"User-Agent": "okhttp/3.15"}
RAW_HOST = "raw.githubusercontent.com/"


def log(msg):
    print("[spider]", msg, flush=True)


# ---------- 网络(带国内可用镜像回退) ----------

def _mirror_candidates(url):
    """raw.githubusercontent 直连在部分网络被墙, 给出镜像候选列表"""
    if RAW_HOST not in url:
        return [url]
    tail = url.split(RAW_HOST, 1)[1]          # owner/repo/branch/path
    owner_repo_branch, _, path = tail.split("/", 2) if tail.count("/") >= 2 else (tail, "", "")
    parts = tail.split("/")
    direct = "https://" + RAW_HOST + tail
    cands = []
    if len(parts) >= 3:
        raw_repo = "/".join(parts[:3])
        cands.append("https://gh-proxy.com/" + direct)
        cands.append("https://ghproxy.net/" + direct)
        if path:
            cands.append("https://raw.gitmirror.com/%s/%s" % (raw_repo, path))
    cands.append(direct)
    # 去重保序
    seen, out = set(), []
    for c in cands:
        if c not in seen:
            seen.add(c); out.append(c)
    return out


def fetch_bytes(url, timeout=60):
    last = None
    for i, u in enumerate(_mirror_candidates(url)):
        for attempt in range(2):
            try:
                s = requests.Session()
                s.trust_env = False  # 忽略本机代理, 避免代理抖动
                r = s.get(u, headers=UA, timeout=(10, timeout), allow_redirects=True)
                if r.status_code == 200 and r.content:
                    if i > 0:
                        log("经镜像取回: %s" % u)
                    return r.content
                last = "HTTP %d" % r.status_code
            except requests.RequestException as e:
                last = repr(e)[:80]
            time.sleep(1)
    raise RuntimeError("下载失败 %s: %s" % (url, last))


# ---------- 接口解密(复用 filter 的能力) ----------

def decode_interface(raw, key_hint=None):
    try:
        text = raw.decode("utf-8")
        if text.lstrip().startswith(("{", "[")):
            return text
    except UnicodeDecodeError:
        text = raw.decode("utf-8", "ignore")
    st = text.strip()
    blob = raw
    if re.match(r"^[0-9a-fA-F\s]+$", st) and len(st) > 32:
        try:
            blob = bytes.fromhex(re.sub(r"\s", "", st))
        except ValueError:
            blob = raw
    if HAS_AES and blob.startswith(b"$#"):
        end = blob.find(b"#$", 2)
        if end > 2:
            key = blob[2:end].decode("utf-8", "ignore")
            try:
                k = key.encode()[:16].ljust(16, b"\0")
                pt = AES.new(k, AES.MODE_ECB).decrypt(blob[end + 2:])
                pad = pt[-1] if pt else 0
                if 0 < pad <= 16 and pt.endswith(bytes([pad]) * pad):
                    pt = pt[:-pad]
                return pt.decode("utf-8")
            except Exception:
                return None
    if HAS_AES and blob.startswith(b"2423") and key_hint:
        try:
            k = hashlib.md5(key_hint.encode()).hexdigest()[:16].encode()
            pt = AES.new(k, AES.MODE_CBC, iv=k).decrypt(blob[4:])
            pad = pt[-1] if pt else 0
            if 0 < pad <= 16 and pt.endswith(bytes([pad]) * pad):
                pt = pt[:-pad]
            return pt.decode("utf-8")
        except Exception:
            return None
    if "**" in st:
        try:
            import base64
            return base64.b64decode(st.split("**")[-1]).decode("utf-8")
        except Exception:
            return None
    return None


def load_interface(url, timeout=30):
    key_hint = None
    if ";key=" in url:
        url, key_hint = url.split(";key=", 1)
    raw = fetch_bytes(url, timeout)
    text = decode_interface(raw, key_hint)
    if text is None:
        raise RuntimeError("接口解密失败")
    obj = json.loads(text)
    if isinstance(obj, dict) and obj.get("sites"):
        return obj
    raise RuntimeError("接口结构异常(无 sites)")


# ---------- jar 校验 ----------

def _fetch_jar(url, timeout=120):
    """下载 jar; 支持 SPIDER_LOCAL_JAR 指定的本地缓存(离线/慢网络下调试用)"""
    local = os.environ.get("SPIDER_LOCAL_JAR", "")
    if local and os.path.exists(local):
        log("使用本地缓存 jar: %s" % local)
        with open(local, "rb") as f:
            return f.read()
    return fetch_bytes(url, timeout)


ENC_ENTRY = ("ftyguard", ".guard", ".so")


def inspect_jar(jar_bytes):
    """返回 (encrypted:bool, guard_entries:list, dex_class_blob:bytes, url_count:int)"""
    z = zipfile.ZipFile(io.BytesIO(jar_bytes))
    names = z.namelist()
    guard = [n for n in names if any(t in n.lower() for t in ENC_ENTRY)]
    blob = b""
    for n in names:
        if n.lower().endswith(".dex"):
            blob += z.read(n)
    url_count = len(re.findall(rb"https?://", blob))
    return (len(guard) > 0, guard, blob, url_count)


# ---------- 上游探活(第①项: 秒播源体检淘汰) ----------
# 白名单是按「类别」选的, 没按「质量/存活」选, 所以会混进已挂/超慢的站。
# 这里用 spider_hosts.json(反编译 jar 得到的「类 -> 真实上游域名」)对各站点做探活:
#   - 全部域名不可达 -> dead   -> 剔除(除非在 force_keep)
#   - 可达但最快延迟 > slow_ms -> slow -> 降级(排到站点末尾, 不影响其它类别顺序)
#   - spider_hosts.json 无该类的域名 -> unknown -> 保留
# 安全阀: 若 dead 占比过高(说明探针所在网络整体不通, 如同 CI 在境外), 本轮不做淘汰, 仅记录。

def load_host_map(base_dir, pack_cfg, hosts_file=None):
    rel = hosts_file or pack_cfg.get("hosts_file", "spider_hosts.json")
    p = rel if os.path.isabs(rel) else os.path.join(base_dir, rel)
    if not os.path.exists(p):
        log("未找到上游域名表 %s, 跳过探活" % p)
        return {}
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f).get("classes") or {}
    except (OSError, json.JSONDecodeError) as e:
        log("上游域名表读取失败(%s), 跳过探活" % e)
        return {}


def _probe_one_host(host, timeout):
    """探测单个域名根地址。返回 (alive:bool, ms|None)"""
    for scheme in ("https", "http"):
        t0 = time.time()
        try:
            s = requests.Session()
            s.trust_env = False
            r = s.get("%s://%s" % (scheme, host), headers=UA, timeout=(5, timeout), allow_redirects=True)
            ms = int((time.time() - t0) * 1000)
            if r.status_code < 500:          # 2xx/3xx/4xx 均视为「站点在线」
                return True, ms
        except requests.RequestException:
            continue
    return False, None


def probe_classes(classes, host_map, timeout, workers=10):
    """并发探测每个类别的上游域名。返回 {cls: {"alive","ms","hosts","verdict"}}"""
    out, jobs = {}, {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for cls in classes:
            hosts = [h for h in (host_map.get(cls) or []) if h][:4]
            if not hosts:
                out[cls] = {"alive": None, "ms": None, "hosts": [], "verdict": "unknown"}
                continue
            for h in hosts:
                jobs[ex.submit(_probe_one_host, h, timeout)] = cls
        acc = {}
        for fut in as_completed(jobs):
            cls = jobs[fut]
            try:
                alive, ms = fut.result()
            except Exception:
                alive, ms = False, None
            a = acc.setdefault(cls, {"alive": False, "ms": None, "hosts": []})
            if alive:
                a["alive"] = True
                if ms is not None and (a["ms"] is None or ms < a["ms"]):
                    a["ms"] = ms
        for cls in classes:
            if cls in out:
                continue
            a = acc.get(cls, {"alive": False, "ms": None, "hosts": []})
            a["hosts"] = [h for h in (host_map.get(cls) or []) if h][:4]
            a["verdict"] = "alive" if a["alive"] else "dead"
            out[cls] = a
    return out


def apply_probe(sites, pack_cfg, base_dir, out_path, hosts_file=None):
    """对候选站点做上游探活并淘汰/降级。返回 (sites, slow_set, probe_meta)"""
    probe_cfg = pack_cfg.get("upstream_probe", {})
    if not probe_cfg.get("enabled", False):
        return sites, set(), {}
    host_map = load_host_map(base_dir, pack_cfg, hosts_file)
    if not host_map:
        return sites, set(), {}

    classes = sorted({s["api"][4:] for s in sites})
    log("上游探活: %d 个类别 ..." % len(classes))
    res = probe_classes(classes, host_map, probe_cfg.get("timeout", 8))
    dead = [c for c in classes if res.get(c, {}).get("verdict") == "dead"]
    alive = [c for c in classes if res.get(c, {}).get("verdict") == "alive"]
    unknown = [c for c in classes if res.get(c, {}).get("verdict") == "unknown"]
    meta = {"alive": alive, "dead": dead, "unknown": unknown,
            "detail": {c: {"v": res[c]["verdict"], "ms": res[c]["ms"]} for c in classes},
            "checked_at": time.strftime("%Y-%m-%d %H:%M:%S")}

    ratio = (len(dead) / float(len(classes))) if classes else 0.0
    if ratio > probe_cfg.get("unreliable_dead_ratio", 0.6):
        log("探针不可靠(dead %d/%d 超过阈值 %.0f%%), 本轮不做淘汰/降级, 仅记录" % (
            len(dead), len(classes), probe_cfg.get("unreliable_dead_ratio", 0.6) * 100))
        meta["unreliable"] = True
        return sites, set(), meta

    force_keep = set(pack_cfg.get("force_keep", []))
    force_drop = set(pack_cfg.get("force_drop", []))
    if not probe_cfg.get("drop_dead", True):
        drop = set(force_drop)
    else:
        drop = (set(dead) - force_keep) | force_drop
    slow_ms = probe_cfg.get("slow_ms", 4000)
    slow_cls = {c for c in classes if res.get(c, {}).get("verdict") == "alive"
                and res[c].get("ms") is not None and res[c]["ms"] > slow_ms}

    kept = [s for s in sites if s["api"][4:] not in drop]
    meta["dropped"] = sorted({s["api"][4:] for s in sites if s["api"][4:] in drop})
    meta["slow"] = sorted(slow_cls)
    log("探活淘汰 %d 个: %s" % (len(meta["dropped"]), ", ".join(meta["dropped"]) or "无"))
    log("探活降级(慢>%dms) %d 个: %s" % (slow_ms, len(slow_cls), ", ".join(sorted(slow_cls)) or "无"))
    if unknown:
        log("无上游域名映射(保留) %d 个: %s" % (len(unknown), ", ".join(unknown)))
    return kept, slow_cls, meta


# ---------- 主流程 ----------

def _die_soft(base_dir, out_path, reason):
    """软失败: 删除旧包(避免陈旧数据被误用) + 退出码 0"""
    log("本次跳过: %s" % reason)
    if os.path.exists(out_path):
        os.remove(out_path)
        log("已删除旧包 %s" % out_path)
    log("直连流水线不受影响。")
    sys.exit(0)


# ---------- 额外上游(每站独立 jar) ----------

def _upstream_spec(name, d, defaults):
    """把 extra_upstreams 里的一项补全默认值; 未给 allow_classes 则退回主配置的白名单会过宽, 故强制其自带。"""
    return {
        "name": name,
        "interface": d.get("interface"),
        "jar": d.get("jar", ""),
        "hosts_file": d.get("hosts_file") or defaults.get("hosts_file", "spider_hosts.json"),
        "allow_classes": d.get("allow_classes") or defaults.get("allow_classes", []),
        "max_sites": d.get("max_sites", defaults.get("max_sites", 20)),
    }


def _build_one(spec, pack_cfg, base_dir, out_path):
    """处理一个额外上游: 拉接口 -> 拉 jar -> 校验明文 -> 白名单裁剪 -> 探活 -> 截断。

    与主上游同一套规则(deny_classes / drop_name_keywords / 探活), 但用各自的
    hosts_file / allow_classes / max_sites。失败返回 None(调用方跳过, 不影响其它上游)。
    """
    itf_url = spec.get("interface")
    if not itf_url:
        log("[%s] 缺少 interface, 跳过" % spec["name"])
        return None
    log("[%s] 上游接口: %s" % (spec["name"], itf_url))
    try:
        itf = load_interface(itf_url)
    except Exception as e:
        log("[%s] 接口拉取失败: %s" % (spec["name"], e))
        return None
    itf_base = itf_url.split(";key=")[0]
    spider_raw = spec.get("jar") or itf.get("spider") or ""
    if not spider_raw:
        log("[%s] 接口未提供 spider 且未配置 jar, 跳过" % spec["name"])
        return None
    jar_url = urljoin(itf_base, spider_raw.partition(";md5;")[0].strip())
    if jar_url == itf_base.rstrip("/") or jar_url.endswith(".json"):
        log("[%s] jar 地址异常(疑似占位图): %s" % (spec["name"], jar_url))
        return None
    log("[%s] 爬虫 jar: %s" % (spec["name"], jar_url))
    try:
        jar_bytes = _fetch_jar(jar_url)
        encrypted, guard, dex_blob, url_count = inspect_jar(jar_bytes)
    except Exception as e:
        log("[%s] jar 下载/解析失败: %s" % (spec["name"], e))
        return None
    if encrypted:
        log("[%s] jar 已加密(%s), 跳过该上游" % (spec["name"], ",".join(guard[:3])))
        return None
    if url_count == 0:
        log("[%s] jar 无明文 URL, 跳过该上游" % spec["name"])
        return None
    log("[%s] jar 明文校验通过(明文URL %d 条)" % (spec["name"], url_count))

    allow = set(spec.get("allow_classes") or [])
    deny = set(pack_cfg.get("deny_classes", []))
    drop_kw = pack_cfg.get("drop_name_keywords", [])
    sites, absent = [], []
    for s in itf.get("sites", []):
        api = str(s.get("api", ""))
        if not api.startswith("csp_"):
            continue
        cls = api[4:]
        if allow and cls not in allow:
            continue
        if cls in deny:
            continue
        if any(k in s.get("name", "") for k in drop_kw):
            continue
        if cls.encode() not in dex_blob:
            absent.append(cls)
            continue
        sites.append(dict(s))
    sites, slow_cls, probe_meta = apply_probe(sites, pack_cfg, base_dir, out_path, spec.get("hosts_file"))
    order = {c: i for i, c in enumerate(spec.get("allow_classes") or [])}
    sites.sort(key=lambda s: (1 if s["api"][4:] in slow_cls else 0, order.get(s["api"][4:], 999)))
    matched_all = len(sites)
    sites = sites[:spec.get("max_sites", 20)]
    log("[%s] 命中 %d 个(截断后 %d)" % (spec["name"], matched_all, len(sites)))
    return {"name": spec["name"], "interface": itf_url, "jar_url": jar_url, "jar_bytes": jar_bytes,
            "sites": sites, "matched_all": matched_all, "slow": sorted(slow_cls),
            "dropped_absent": sorted(set(absent)), "probe": probe_meta}


def main():
    cfg_path = sys.argv[1] if len(sys.argv) > 1 else "config.json"
    base_dir = os.path.dirname(os.path.abspath(cfg_path))
    with open(cfg_path, encoding="utf-8") as f:
        cfg = json.load(f)

    pack_cfg = cfg.get("spider_pack", {})
    out_rel = pack_cfg.get("output", "dist/spider_pack.json")
    out_path = os.path.join(base_dir, out_rel)

    if not pack_cfg.get("enabled", False):
        log("spider_pack 未启用, 跳过(如有旧包一并删除)")
        if os.path.exists(out_path):
            os.remove(out_path)
        sys.exit(0)

    itf_url = pack_cfg.get("interface")
    if not itf_url:
        _die_soft(base_dir, out_path, "配置缺少 spider_pack.interface")

    log("上游接口: %s" % itf_url)
    try:
        itf = load_interface(itf_url)
    except Exception as e:
        _die_soft(base_dir, out_path, "接口拉取失败: %s" % e)
    itf_base = itf_url.split(";key=")[0]
    log("接口站点 %d 个" % len(itf.get("sites", [])))

    # 1. 解析 jar 地址(优先配置, 否则取接口 spider 字段)
    spider_raw = pack_cfg.get("jar") or itf.get("spider") or ""
    if not spider_raw:
        _die_soft(base_dir, out_path, "接口未提供 spider 字段且未配置 jar")
    jar_ref, _, _up_md5 = spider_raw.partition(";md5;")
    jar_url = urljoin(itf_base, jar_ref.strip())
    log("爬虫 jar: %s" % jar_url)

    # 2. 下载 + 校验
    try:
        if jar_url == itf_base.rstrip("/") or jar_url.endswith(".json"):
            _die_soft(base_dir, out_path, "jar 地址异常(疑似占位图): %s" % jar_url)
        jar_bytes = _fetch_jar(jar_url)
    except Exception as e:
        _die_soft(base_dir, out_path, "jar 下载失败: %s" % e)
    log("jar 下载成功 %d 字节" % len(jar_bytes))
    try:
        encrypted, guard, dex_blob, url_count = inspect_jar(jar_bytes)
    except Exception as e:
        _die_soft(base_dir, out_path, "jar 解析失败: %s" % e)
    if encrypted:
        _die_soft(base_dir, out_path, "jar 已加密(%s), 无法在国内稳定加载" % ",".join(guard[:3]))
    log("jar 明文校验通过(明文URL %d 条)" % url_count)
    if url_count == 0:
        _die_soft(base_dir, out_path, "jar 无明文 URL, 疑似混淆/加密")

    # 3. 白名单裁剪 csp 站点
    allow = set(pack_cfg.get("allow_classes", []))
    deny = set(pack_cfg.get("deny_classes", []))
    drop_kw = pack_cfg.get("drop_name_keywords", [])
    max_sites = pack_cfg.get("max_sites", 20)

    sites, dropped_absent, dropped_denied, dropped_kw = [], [], [], []
    for s in itf.get("sites", []):
        api = str(s.get("api", ""))
        if not api.startswith("csp_"):
            continue
        cls = api[4:]
        if allow and cls not in allow:
            continue
        if cls in deny:
            dropped_denied.append(cls); continue
        name = s.get("name", "")
        if any(k in name for k in drop_kw):
            dropped_kw.append(name); continue
        if cls.encode() not in dex_blob:
            dropped_absent.append(cls); continue
        sites.append(dict(s))
    # 3.5 上游探活: 剔除已挂站、降级超慢站(第①项)
    sites, slow_cls, probe_meta = apply_probe(sites, pack_cfg, base_dir, out_path)

    # 按 allow_classes 的书写顺序排优先级(慢站降到末尾), 再按 max_sites 截断(优先保留白名单里靠前的类别)
    order = {c: i for i, c in enumerate(pack_cfg.get("allow_classes", []))}
    sites.sort(key=lambda s: (1 if s["api"][4:] in slow_cls else 0, order.get(s["api"][4:], 999)))
    kept_all = len(sites)
    sites = sites[:max_sites]

    if not sites:
        _die_soft(base_dir, out_path, "白名单命中 0 个可用站点(上游 jar/接口可能已变)")

    # 4. 额外上游(每个上游一个独立 jar -> 写到各自站点的 site.jar)
    #    FongMi 的 Site 有 jar 字段: 站点自带 jar 优先, 为空才回退全局 spider —— 故多 jar 无需缝合。
    md5 = hashlib.md5(jar_bytes).hexdigest()
    primary_final = len(sites)
    jars = [{"name": "主上游", "url": jar_url, "md5": md5, "primary": True}]
    extra_meta = []
    for up in pack_cfg.get("extra_upstreams", []):
        if not up.get("enabled", True):
            log("额外上游 [%s] 已禁用, 跳过" % up.get("name", "?"))
            continue
        spec = _upstream_spec(up.get("name") or "额外上游", up, pack_cfg)
        r = _build_one(spec, pack_cfg, base_dir, out_path)
        if not r or not r["sites"]:
            log("额外上游 [%s] 无可用站点, 跳过" % spec["name"])
            continue
        jars.append({"name": r["name"], "url": r["jar_url"],
                     "md5": hashlib.md5(r["jar_bytes"]).hexdigest(), "primary": False})
        for s in r["sites"]:
            s["jar"] = r["jar_url"]          # per-site jar(明文 URL; deploy_cos 会镜像到 COS)
        sites += r["sites"]
        extra_meta.append({"name": r["name"], "interface": r["interface"], "jar": r["jar_url"],
                           "kept": [s["api"][4:] for s in r["sites"]], "matched_all": r["matched_all"],
                           "slow": r["slow"], "dropped_absent_in_jar": r["dropped_absent"],
                           "probe": r["probe"]})
        log("额外上游 [%s] 并入 %d 个站点" % (r["name"], len(r["sites"])))

    # 5. 组装输出(spider 用主上游明文 jar 的绝对地址; deploy_cos 会镜像到 COS 并重算 md5)
    out = {
        "spider": "%s;md5;%s" % (jar_url, md5),
        "sites": sites,
        "jars": jars,
        "meta": {
            "interface": itf_url,
            "jar": jar_url,
            "encrypted": False,
            "jar_url_count": url_count,
            "kept": [s["api"][4:] for s in sites],
            "matched_all": kept_all,
            "dropped_absent_in_jar": sorted(set(dropped_absent)),
            "dropped_denied": sorted(set(dropped_denied)),
            "probe": probe_meta,
            "extra_upstreams": extra_meta,
            "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        },
    }
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    log("源包已写出: %s (主 %d + 额外 %d = %d 个站点)" % (
        out_path, primary_final, len(sites) - primary_final, len(sites)))
    if dropped_absent:
        log("注意: 接口引用但 jar 缺失的类(已剔除): %s" % ", ".join(sorted(set(dropped_absent))))


if __name__ == "__main__":
    main()
