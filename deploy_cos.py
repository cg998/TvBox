# -*- coding: utf-8 -*-
"""
部署接口到腾讯云 COS（解决 raw.githubusercontent 被墙导致爬虫源加载不了的问题）

流程:
1. 读取 dist/my_interface.json（由 filter/health 流水线生成）
2. spider jar: 从 raw.githubusercontent 下载 -> 重算 md5 -> 上传 COS jar/ -> 重写 spider 字段
3. 递归扫描接口里所有 raw.githubusercontent 地址与 ./ 相对路径(logo/ext json 等) -> 镜像到 COS -> 重写为 COS 绝对地址
4. 上传最终接口 JSON 到 COS 根目录 my_interface.json

运行: python deploy_cos.py [config.json]
环境变量: TENCENT_SECRET_ID, TENCENT_SECRET_KEY (GitHub Secrets)
依赖: requests, cos-python-sdk-v5 (qcloud_cos)
"""
import hashlib
import json
import os
import sys
import time
from urllib.parse import urlparse

import requests

BUCKET = os.environ.get("COS_BUCKET", "tvbox-202610-1503022424")
REGION = os.environ.get("COS_REGION", "ap-guangzhou")
COS_BASE = "https://%s.cos.%s.myqcloud.com" % (BUCKET, REGION)
INTERFACE_KEY = "my_interface.json"
FALLBACK_SOURCE_BASE = "https://raw.githubusercontent.com/cyao2q/files/master/"

UA = {"User-Agent": "okhttp/3.15"}
RAW_MARKER = "raw.githubusercontent.com/"


def log(msg):
    print("[deploy]", msg, flush=True)


# ---------- 网络 ----------

def http_get(url, retries=2):
    last = None
    for i in range(retries + 1):
        try:
            r = requests.get(url, headers=UA, timeout=40, allow_redirects=True)
            if r.status_code == 200 and r.content:
                return r.content
            last = "HTTP %d" % r.status_code
        except requests.RequestException as e:
            last = repr(e)
        if i < retries:
            time.sleep(2)
    raise RuntimeError("下载失败 %s: %s" % (url, last))


def md5_hex(b):
    return hashlib.md5(b).hexdigest().upper()


# ---------- COS ----------

_cos = None


def cos_client():
    global _cos
    if _cos is None:
        from qcloud_cos import CosConfig, CosS3Client
        sid = os.environ.get("TENCENT_SECRET_ID", "")
        skey = os.environ.get("TENCENT_SECRET_KEY", "")
        if not sid or not skey:
            raise RuntimeError("缺少 TENCENT_SECRET_ID / TENCENT_SECRET_KEY 环境变量")
        _cos = CosS3Client(CosConfig(Region=REGION, SecretId=sid, SecretKey=skey))
    return _cos


def cos_upload(key, data, content_type=None):
    kwargs = {"Bucket": BUCKET, "Key": key, "Body": data}
    if content_type:
        kwargs["ContentType"] = content_type
    cos_client().put_object(**kwargs)
    log("COS 上传 %s (%d 字节) -> %s/%s" % (key, len(data), COS_BASE, key))
    return COS_BASE + "/" + key


def mirror(url, cos_key, content_type=None):
    """下载 url, 上传到 COS cos_key, 返回 COS 访问地址"""
    data = http_get(url)
    log("镜像 %s (%d 字节) -> %s" % (url, len(data), cos_key))
    return cos_upload(cos_key, data, content_type)


# ---------- 路径解析 ----------

def suffix_of_raw(url):
    """把 raw.githubusercontent URL 转成仓库内相对路径; 非 raw 返回 None"""
    i = url.find(RAW_MARKER)
    if i < 0:
        return None
    parts = url[i + len(RAW_MARKER):].split("/")
    if len(parts) < 4:  # owner/repo/branch/...
        return None
    return "/".join(parts[3:])


def derive_source_base(spider_url):
    """从 jar URL 推导仓库根(相对 ./ 路径的解析基准)"""
    marker = RAW_MARKER
    i = spider_url.find(marker)
    if i < 0:
        return FALLBACK_SOURCE_BASE
    parts = spider_url[i + len(marker):].split("/")
    if len(parts) < 4:
        return FALLBACK_SOURCE_BASE
    return "https://" + marker + "/".join(parts[:3]) + "/"


# ---------- 重写 ----------

def deploy_spider(spider, source_base):
    """spider 形如 URL;md5;HASH。下载 jar, 重算 md5, 镜像到 COS jar/, 返回新 spider 字符串"""
    url, sep, old_md5 = spider.partition(";md5;")
    url = url.strip()
    old_md5 = old_md5.strip()
    data = http_get(url)
    new_md5 = md5_hex(data)
    cos_key = suffix_of_raw(url) or ("jar/" + (os.path.basename(urlparse(url).path) or "spider.jar"))
    if not cos_key.lower().startswith("jar/"):
        cos_key = "jar/" + os.path.basename(cos_key)
    cos_url = mirror(url, cos_key, "application/java-archive")
    log("spider 重算 md5: %s -> %s" % (old_md5 or "(空)", new_md5))
    return "%s;md5;%s" % (cos_url, new_md5)


def deploy_ref(v, source_base):
    """把单个字符串里可镜像的源引用重写为 COS 地址; 无关字符串原样返回。

    仅处理: 以 raw.githubusercontent 开头的直连地址, 以及 ./ 或 / 开头的相对路径。
    不处理: 已经用第三方代理(如 gh.927223.xyz)包装过的地址, 它们本就能国内访问。
    """
    if not isinstance(v, str):
        return v
    if v.startswith("./"):
        rel = v[2:]
        return mirror(source_base + rel, rel)
    if v.startswith("/"):
        rel = v[1:]
        return mirror(source_base + rel, rel)
    if v.startswith("https://raw.githubusercontent.com/") or v.startswith("http://raw.githubusercontent.com/"):
        suffix = suffix_of_raw(v)
        if suffix:
            return mirror(v, suffix)
    return v


def walk_rewrite(obj, source_base):
    """递归重写整个 JSON 结构里的源地址"""
    if isinstance(obj, dict):
        return {k: walk_rewrite(v, source_base) for k, v in obj.items()}
    if isinstance(obj, list):
        return [walk_rewrite(x, source_base) for x in obj]
    return deploy_ref(obj, source_base)


# ---------- 主流程 ----------

def main():
    cfg_path = sys.argv[1] if len(sys.argv) > 1 else "config.json"
    base_dir = os.path.dirname(os.path.abspath(cfg_path))
    with open(cfg_path, encoding="utf-8") as f:
        cfg = json.load(f)
    itf_path = os.path.join(base_dir, cfg["output"]["interface"])
    if not os.path.exists(itf_path):
        sys.exit("接口文件不存在: %s (请先运行 filter/health 生成)" % itf_path)
    with open(itf_path, encoding="utf-8") as f:
        itf = json.load(f)

    spider = itf.get("spider", "")
    source_base = derive_source_base(spider) if spider else FALLBACK_SOURCE_BASE
    log("源仓库根: %s" % source_base)

    # 1. spider jar 单独处理(需重算 md5)
    if spider:
        itf["spider"] = deploy_spider(spider, source_base)

    # 2. 其余字段(logo / ext / lives 等)递归重写
    for k in list(itf.keys()):
        if k == "spider":
            continue
        itf[k] = walk_rewrite(itf[k], source_base)

    # 3. 本地存一份部署版, 便于 diff 查看
    deployed_path = os.path.join(base_dir, "dist", "deployed_interface.json")
    out = json.dumps(itf, ensure_ascii=False, indent=2).encode("utf-8")
    with open(deployed_path, "wb") as f:
        f.write(out)

    # 4. 上传最终接口
    cos_upload(INTERFACE_KEY, out, "application/json")
    log("部署完成: %s/%s" % (COS_BASE, INTERFACE_KEY))


if __name__ == "__main__":
    main()
