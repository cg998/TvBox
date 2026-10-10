# TvBox 自持源接口（自动化维护）

自维护的 TVBox/FongMi 影视源接口，供父母家 TCL 电视使用。由 GitHub Actions 自动维护：每天体检 + 每周换血，接口地址永不变，电视端零操作。

- 客户端：**蜂蜜影视**（FongMi 系，`com.fongmi.android.tv`），支持自动跳过失效线路、自动换站源、重启后重新加载配置
- 站点构成：**自用 9 个直连 CMS 源置顶 + 每周从 tvbox.org 自动合并接口筛选的新源**（纯直连，已彻底移除爬虫/秒播源）
- 内容侧重：约 7 成影视 + 3 成动漫儿童；无直播
- 免费、无广告、无会员

## 电视端配置地址

蜂蜜影视 → 设置 → 点播 → 配置地址，填入：

```
https://tvbox-202610-1503022424.cos.ap-guangzhou.myqcloud.com/my_interface.json
```

> 说明：主地址走腾讯云 COS。由于已彻底移除爬虫/秒播源（它们依赖加密 jar、在国内不稳定），接口只剩直连 CMS 源，无需再中转 jar，稳定性大幅提升。

备用地址（主地址失效时用）：

```
https://cdn.jsdelivr.net/gh/cg998/TvBox@main/dist/my_interface.json
```

## 自动维护流水线

| 任务 | 频率 | 做什么 |
|---|---|---|
| 每日健康检查 | 每天 05:20（北京） | 只体检接口内现有站点：挂了的降级到末尾，连续挂 3 天剔除；保证首页默认源存活且分类齐全，挂了自动顶最佳存活源上去。全健康时零提交 |
| 每周全量筛选 | 每周日 09:20（北京）+ 可手动 | 从 tvbox.org 自动合并接口（每日探测 38 个上游）拉新源 → 解密 → 关键词过滤 → 实测/高清探测 → 去重 → 自用 9 源置顶 + 筛选出的直连源，输出 `dist/my_interface.json` |
| 部署到 COS | 随上面两步自动执行 | 把接口里的被墙地址镜像到腾讯云 COS（重写为 COS 绝对地址），上传最终接口到 COS |

## 文件说明

| 文件 | 作用 |
|---|---|
| `dist/my_interface.json` | 正式接口（Actions 自动生成，电视端填这个地址） |
| `my_sites.json` | 自用 9 个站点，无条件置顶（第一位 = 默认首页源） |
| `config.json` | 流水线参数（站点上限 20、已关爬虫、上游种子 = tvbox.org 官方合并接口、关键词过滤） |
| `filter_sources.py` | 每周全量筛选脚本 |
| `health_check.py` | 每日健康检查脚本 |
| `deploy_cos.py` | 部署脚本：镜像被墙地址到 COS 并上传接口 |
| `.github/workflows/` | 定时任务调度 |
| `tv.json` | 静态兜底接口（Actions 万一挂了，电视端可手动切回这个地址） |
| `test_tv.py` | 手动自测工具（测 `tv.json`） |

## 站点构成

### 自用置顶（9 个直连 CMS 源）

| 站点 | 接口地址 | 侧重 |
|---|---|---|
| 量子资源（默认首页） | `https://cj.lziapi.com/api.php/provide/vod/` | 综合，44 分类最全 |
| 非凡资源 | `https://cj.ffzyapi.com/api.php/provide/vod/` | 影视 |
| 天堂资源 | `http://caiji.dyttzyapi.com/api.php/provide/vod/` | 影视 |
| 360资源 | `https://360zy.com/api.php/provide/vod/` | 影视 |
| 火狐资源 | `https://hhzyapi.com/api.php/provide/vod/` | 动漫 + 儿童 |
| 非凡备用 | `http://ffzy5.tv/api.php/provide/vod` | 影视备用 |
| 百度资源 | `https://api.apibdzy.com/api.php/provide/vod/` | 儿童动画 |
| 非凡影视 | `http://www.ffzy.tv/api.php/provide/vod/` | 影视（非凡镜像） |
| 索尼资源 | `https://suoniapi.com/api.php/provide/vod` | 影视（新增） |

### 每周自动筛选

从 tvbox.org 自动合并接口（`TVboxorg/Tvbox` 的 `dist/official.json`，每日探测 38 个上游）拉取、实测、去重后补充直连采集源。已彻底关闭爬虫/秒播源（依赖加密 jar、国内不稳定），接口保持纯直连、稳定可靠。

## 如何手动换血 / 测试

- **立即换血**：GitHub 仓库 → Actions → 「每周全量筛选影视源」→ Run workflow
- **本地自测兜底源**：

```bash
python test_tv.py                         # 测 tv.json（静态兜底）
python filter_sources.py config.json      # 本地跑一次全量筛选（生成 dist/）
python health_check.py config.json        # 本地跑一次健康检查
```

> 注意：流水线里的实测依赖境外 Actions 节点，本地（公司网络）可能跑不通，属正常。

## 免责声明

本仓库仅汇总第三方公开接口地址，不托管、不分发具体影视内容。接口由第三方维护，可用性会变化。请遵守当地法律法规，合理使用。
