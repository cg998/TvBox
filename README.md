# TvBox 自持源接口

自维护的 TVBox/FongMi 影视源接口。

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


## 手动换血

GitHub 仓库 → Actions →「全量筛选影视源(周三/周日)」→ Run workflow。

## 免责声明

本仓库仅汇总第三方公开接口地址，不托管、不分发具体影视内容。请遵守当地法律法规，合理使用。
