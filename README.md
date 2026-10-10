# TvBox 自持源接口

自维护的 TVBox/FongMi 影视源接口，供父母家 TCL 电视使用。GitHub Actions 自动维护，接口地址不变，电视端零操作。

- 客户端：**蜂蜜影视**（FongMi 系，`com.fongmi.android.tv`）
- 站点：9 个自用直连 CMS 源 + 每周自动补充的直连源 + 家庭向秒播源（**多个明文 jar 上游**，自托管到 COS，非加密、国内可加载）

## 电视端配置地址

蜂蜜影视 → 设置 → 点播 → 配置地址，填入：

```
https://tvbox-202610-1503022424.cos.ap-guangzhou.myqcloud.com/my_interface.json
```

备用地址（主地址失效时用，仅直连源可用）：

```
https://cdn.jsdelivr.net/gh/cg998/TvBox@main/dist/my_interface.json
```

## 站点构成

| 类别 | 数量 | 说明 |
|---|---|---|
| 自用直连源 | 9 | 量子 / 非凡 / 天堂 / 360 / 火狐 / 百度 / 索尼… 见 `my_sites.json`（**首页兜底源**） |
| 自动补充直连源 | 若干 | 每周从公开合并接口筛出，去重后并入 |
| 秒播源 | 约 40 | 儿童 / 影视 / 短剧 / 体育 / 戏曲·养生·预告；来自 **2 个明文 jar 上游**（qist + L佬）；每周自动探活，**死站剔除、慢站降级** |

秒播源取自**明文 jar** 上游（主上游 `qist/tvbox`；额外上游 `L佬线路`），由 `build_spiders.py` 每周按各自白名单裁剪家庭向站点并**上游探活淘汰死站/慢站**；各 jar 镜像到自有 COS。**不加密、国内可直连**，因此不再受制于别人的加密 jar。

**多上游 = 多 jar，无需缝合 jar**：FongMi 的站点对象有 `jar` 字段（站点自带 jar 优先，为空才回退全局 `spider`）。因此主上游的 jar 作全局 `spider`，每个额外上游的 jar 写到它自己站点的 `site.jar`。新增一个明文上游只需在 `config.json → spider_pack.extra_upstreams` 加一项（`interface` + `jar` + `allow_classes` + `hosts_file`），**无需改代码**。

## 自动维护

| 任务 | 频率 | 说明 |
|---|---|---|
| 构建秒播源包 | 每周日 09:20 | 重拉上游 jar + 接口 → 校验未加密 → 按白名单裁剪 → **上游探活：死站剔除、慢站降级** → 产出源包 |
| 每周全量筛选 | 每周日 09:20 | 从公开合并接口筛新直连源，按 `homepage` 配置排定首页与搜索优先级，去重后并入 |
| 每日健康检查 | 每天 05:20、18:00 | 挂了的源降级到末尾，连续挂 3 天剔除；**首页源不可用时按 `homepage.fallback_key` 回退**；**首页秒播源用上游域名探活近似判活**（探针不可靠时不回退）。**自用源与首页精选源只体检、不降级、不剔除**——体检跑在境外节点，避免误删国内精选源。一天两次按「北京时间日期」计数，不会因多跑一次而提前剔除 |
| 部署到 COS | 随以上执行 | 镜像 jar 到 COS、重写接口地址、上传接口。**jar 每日刷新，站点清单每周刷新** |

## 首页与站点排序

App 的**首页 = `sites[0]`**（显示该站的分类）；**搜索 = 并发搜全部站、按站点顺序分组展示结果**。因此「换首页」与「搜索优先」都由站点排序决定，全部在 `config.json → homepage` 配置：

```json
"homepage": {
  "preferred_class": "Duboku",
  "priority_classes": ["Duboku", "Gz360", "Hxq", "Web1905", "FengYe", "ShuangXing", "RenRen", "Jike", "Yidong4K"],
  "fallback_key": "lzzy"
}
```

- **`preferred_class`**：填某个秒播类名（如 `Duboku`），该站顶到 `sites[0]` 作首页；**留空则用量子**。首页应选**分类齐全**的站——父母不搜索、靠首页分类浏览（当前 = **独播 Duboku**：电影/电视剧/综艺/动漫/港剧/美剧/韩剧/短剧…分类最全）。
- **`priority_classes`**：这些秒播类整体排到**直连源之前**，使**搜索结果优先展示**这些影视综合秒播站（顺序即优先级）。不在此列表的秒播类（儿童/短剧/体育…）仍排在最后。
- **`fallback_key`**：首页源不可用时，每日体检自动回退到它（`lzzy` = 量子，自用直连，稳定兜底）。

**增删秒播站点**：改 `config.json → spider_pack.allow_classes`（白名单，**书写顺序 = 优先级**，超出 `max_sites` 时靠后的被截断），`deny_classes` 强制排除。改完触发一次「每周全量筛选」。

**加一个明文 jar 上游**：在 `spider_pack.extra_upstreams` 追加一项：

```json
{ "name": "某上游", "enabled": true, "interface": "https://.../index.json",
  "jar": "", "hosts_file": "spider_hosts_X.json", "max_sites": 16,
  "allow_classes": ["Xxx", "Yyy"] }
```

其 jar 会写到该上游站点的 `site.jar`。`hosts_file`（类→域名表）用 `python gen_spider_hosts.py <jar> spider_hosts_X.json` 生成，供探活使用。

- **上游探活**（`spider_pack.upstream_probe`）每周按 `spider_hosts.json` 的「类→上游域名」探测：**全部域名不可达 → 剔除；可达但很慢 → 降级到末尾**。若探针网络整体不通，自动忽略本轮淘汰（不会误删）。
- 探活跑在**境外 CI**，对国内站偶有误判（把可达的判死、把已死的判活）。个别站点想强制保留/剔除，写进 `spider_pack.force_keep` / `force_drop`（覆盖探活结果）。当前：`force_keep: ["ShuangXing"]`（双星，国内实测可达却被境外判死）、`force_drop: ["SportsKafei"]`（咖啡体育，国内实测已死却判活）。
- **同域去重**（`options.dedup_domain`）：同一域名的直连源只保留最快的一个，并丢弃与自用源同域的上游副本，避免「量子/360/虎牙」等同站点不同线路重复出现。
- 上游 jar 一旦被改成加密版，本环节会**自动停用**并退回纯直连接口，不影响电视使用。
- 想看实际收录与探活结果，运行 `python build_spiders.py config.json`，看产出文件的 `meta.kept` / `meta.probe`。

## 手动换血

GitHub 仓库 → Actions →「每周全量筛选影视源」→ Run workflow。

## 免责声明

本仓库仅汇总第三方公开接口地址，不托管、不分发具体影视内容。请遵守当地法律法规，合理使用。
