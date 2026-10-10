# -*- coding: utf-8 -*-
"""
从明文 jar 反编译生成「爬虫类 -> 真实上游域名」表(供 build_spiders 上游探活用)。

用法:
    python gen_spider_hosts.py <jar文件> <输出json>

原理: 解压 jar 的 *.dex, 用 androguard 读 com.github.catvod.spider.* 各类,
提取方法里的 const-string 常量, 正则捞出域名(过滤框架/CDN/图床噪音)。

jar 大改后(上游换版)可重新生成; 通常无需频繁跑。
依赖: androguard, loguru
"""
import io
import json
import os
import re
import sys
import zipfile

from loguru import logger
logger.remove()

from androguard.core.dex import DEX

SPIDER_PREFIX = "Lcom/github/catvod/spider/"

URL_RE = re.compile(r'https?://[A-Za-z0-9._\-]{3,}\.[A-Za-z]{2,}(?::\d+)?[A-Za-z0-9._\-/:%?=&]*')
HOST_RE = re.compile(
    r'\b((?:[a-z0-9](?:[a-z0-9\-]{0,61}[a-z0-9])?\.)+'
    r'(?:com|cn|net|org|cc|tv|xyz|top|vip|io|me|site|club|online|app|fun|live|pro|shop|'
    r'work|space|store|art|win|link|one|red|asia|biz|info|life|world|today|run|wang|xin|'
    r'ltd|group|tech|dev|cloud|host|website|press|ch|in|us|la|pm|su|pw|icu|monster|cyber|uno))'
    r'(?::\d+)?\b'
)
# 框架/公共基础设施/图床/网盘通用域名 —— 不是「站点本身」, 探活无意义
NOISE = re.compile(
    r'(java\.|javax\.|android|kotlin|androidx|google|oracle|apache|json|okhttp|okio|slf4j|'
    r'jackson|gson|snakeyaml|bouncycastle|commons|jetbrains|intellij|squareup|junit|mockito|'
    r'hamcrest|kotlinx|dalvik|w3\.org|schemas\.android|xml\.org|apache\.org|github\.com|'
    r'githubusercontent|gitee\.com|gitcode|jihulab|jsdelivr|unpkg|npmjs|sourceforge|maven|'
    r'gradle|example\.com|localhost|127\.0\.0\.1|0\.0\.0\.0|googleapis|gstatic|gvt1|'
    r'alicdn|aliyuncs|myqcloud|tencentcs|bdstatic|hdslb|meitudata|moji\.com|icve\.com\.cn|'
    r'gelonghui|kstore|kstore\.space|'
    r'images\.cnblogs\.com|img\.51shazhu\.com|inews\.gtimg\.com|img1\.baidu\.com|'
    r'image\.uc\.cn|img\.youxiguancha\.com|www\.douban\.com|www\.bing\.com|dns\.alidns\.com|'
    r'api-pan\.xunlei\.com|openapi\.alipan\.com|api\.guangyapan\.com|'
    r'auth\.aliyundrive\.com|passport\.aliyundrive\.com|api\.aliyundrive\.com|'
    r'pan\.baidu\.com|passport\.baidu\.com|wappass\.baidu\.com|pan\.quark\.cn|'
    r'cloud\.189\.cn|drive\.uc\.cn|open-api-drive\.uc\.cn)', re.I)


def hosts_from_literals(lits):
    hs = set()
    for t in lits:
        for m in URL_RE.finditer(t):
            h = re.sub(r'^https?://', '', m.group(0)).split('/')[0].split(':')[0]
            if h and not NOISE.search(h):
                hs.add(h)
        for m in HOST_RE.finditer(t.lower()):
            h = m.group(1)
            if not NOISE.search(h):
                hs.add(h)
    return hs


def extract(jar_path):
    data = open(jar_path, 'rb').read()
    z = zipfile.ZipFile(io.BytesIO(data))
    dexes = [n for n in z.namelist() if n.lower().endswith('.dex')]
    classes = {}
    for dn in dexes:
        try:
            d = DEX(bytearray(z.read(dn)))
        except Exception as e:
            print('  dex %s 解析失败: %s' % (dn, e))
            continue
        for c in d.get_classes():
            cn = c.get_name()
            if not cn or not cn.startswith(SPIDER_PREFIX):
                continue
            base = cn[len(SPIDER_PREFIX):].rstrip(';').split('$')[0]
            lits = []
            try:
                for m in c.get_methods():
                    code = m.get_code()
                    if not code:
                        continue
                    for ins in code.get_bc().get_instructions():
                        if 'const-string' in (ins.get_name() or ''):
                            lits.append(ins.get_output())
            except Exception:
                continue
            hs = hosts_from_literals(lits)
            if hs:
                classes.setdefault(base, set()).update(hs)
    return {k: sorted(v) for k, v in sorted(classes.items())}


def main():
    jar_path, out_path = sys.argv[1], sys.argv[2]
    classes = extract(jar_path)
    out = {
        "_说明": "%s 反编译所得「爬虫类 -> 真实上游域名」, 供 build_spiders 做上游探活。" % os.path.basename(jar_path),
        "classes": classes,
    }
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print('%s -> %s (%d 个类有域名)' % (jar_path, out_path, len(classes)))


if __name__ == '__main__':
    main()
