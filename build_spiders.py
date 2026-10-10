# -*- coding: utf-8 -*-
"""
构建「秒播源包」——A 方案(明文 jar 自托管)的可维护化环节

做什么:
  1. 从上游(默认 qist/tvbox)拉取 *明文* 爬虫 jar 与它的接口
  2. 校验 jar 未加密(无 ftyguard/native .so 解密层) —— 加密则本次跳过
  3. 按 allow_classes 白名单(且该类真实存在于 jar)裁剪出家庭向 csp 站点
  4. 产出 dist/spider_pack.json, 交给 filter_sources.py 合并进主接口
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


# ---------- 主流程 ----------

def _die_soft(base_dir, out_path, reason):
    """软失败: 删除旧包(避免陈旧数据被误用) + 退出码 0"""
    log("本次跳过: %s" % reason)
    if os.path.exists(out_path):
        os.remove(out_path)
        log("已删除旧包 %s" % out_path)
    log("直连流水线不受影响。")
    sys.exit(0)


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
    # 按 allow_classes 的书写顺序排优先级, 再按 max_sites 截断(优先保留白名单里靠前的类别)
    order = {c: i for i, c in enumerate(pack_cfg.get("allow_classes", []))}
    sites.sort(key=lambda s: order.get(s["api"][4:], 999))
    kept_all = len(sites)
    sites = sites[:max_sites]

    if not sites:
        _die_soft(base_dir, out_path, "白名单命中 0 个可用站点(上游 jar/接口可能已变)")

    # 4. 组装输出(spider 用明文 jar 的绝对地址; deploy_cos 会镜像到 COS 并重算 md5)
    md5 = hashlib.md5(jar_bytes).hexdigest()
    out = {
        "spider": "%s;md5;%s" % (jar_url, md5),
        "sites": sites,
        "meta": {
            "interface": itf_url,
            "jar": jar_url,
            "encrypted": False,
            "jar_url_count": url_count,
            "kept": [s["api"][4:] for s in sites],
            "matched_all": kept_all,
            "dropped_absent_in_jar": sorted(set(dropped_absent)),
            "dropped_denied": sorted(set(dropped_denied)),
            "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        },
    }
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    log("源包已写出: %s (%d 个站点: %s)" % (out_path, len(sites), ", ".join(out["meta"]["kept"])))
    if dropped_absent:
        log("注意: 接口引用但 jar 缺失的类(已剔除): %s" % ", ".join(sorted(set(dropped_absent))))


if __name__ == "__main__":
    main()
