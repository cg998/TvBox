# TvBox 自持源接口

自维护的 TVBox/FongMi 影视源接口。GitHub Actions 自动维护，接口地址不变，电视端零操作。

- 客户端：**蜂蜜影视**（FongMi 系，`com.fongmi.android.tv`）

## 电视端配置地址

蜂蜜影视 → 设置 → 点播 → 配置地址，填入：

```
https://tvbox-202610-1503022424.cos.ap-guangzhou.myqcloud.com/my_interface.json
```

备用地址（主地址失效时用，仅直连源可用）：

```
https://cdn.jsdelivr.net/gh/cg998/TvBox@main/dist/my_interface.json
```



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

GitHub 仓库 → Actions →「全量筛选影视源(周三/周日)」→ Run workflow。

## 免责声明

本仓库仅汇总第三方公开接口地址，不托管、不分发具体影视内容。请遵守当地法律法规，合理使用。
