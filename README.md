# TvBox 自持源接口

自维护的 TVBox 影视源接口，供父母家 TCL 电视（takagen99 黑盒版）使用。全部站点为**直连 CMS 采集站**（type=1），不依赖爬虫 jar，白天晚上稳定可用。

- 内容侧重：**约 7 成影视（剧集/电影）+ 3 成动漫与儿童动画**
- 无直播源
- 免费、无广告、无会员

## 文件说明

| 文件 | 作用 |
|---|---|
| `tv.json` | 接口文件，电视端「配置地址」填它 |
| `test_tv.py` | 接口自测工具，改完接口后跑一遍验证站点是否存活 |

## 使用

电视端（黑盒）→ 设置 → 配置地址，填入：

```
https://cdn.jsdelivr.net/gh/cg998/TvBox@main/tv.json
```

备用地址（接口失效时自动切换）：

```
https://fastly.jsdelivr.net/gh/cg998/TvBox@main/tv.json
```

> 说明：不使用 `raw.githubusercontent.com` 直连，因其在国内网络不稳定。

## 当前站点清单（8 个）

### 影视（综合，覆盖剧集/电影/动漫）

| 站点 | 接口地址 |
|---|---|
| 量子资源 | `https://cj.lziapi.com/api.php/provide/vod/` |
| 非凡资源 | `https://cj.ffzyapi.com/api.php/provide/vod/` |
| 天堂资源 | `http://caiji.dyttzyapi.com/api.php/provide/vod/` |
| 360资源 | `https://360zy.com/api.php/provide/vod/` |
| 淘片资源 | `https://taopianapi.com/cjapi/mc/vod/json.html` |
| 非凡备用 | `http://ffzy5.tv/api.php/provide/vod` |

### 动漫 / 儿童

| 站点 | 接口地址 | 侧重 |
|---|---|---|
| 火狐资源 | `https://hhzyapi.com/api.php/provide/vod/` | 动漫 + 儿童 |
| 百度资源 | `https://api.apibdzy.com/api.php/provide/vod/` | 儿童动画 |

## 如何测试

```bash
cd tvbox-repo
python test_tv.py                       # 默认测 tv.json，关键词：斗罗大陆/海贼王/熊出没
python test_tv.py tv.json 狂飙 流浪地球 海贼王 小猪佩奇   # 自定义关键词
```

输出中某站标 `❌ 已失效` 即需要替换。

## 如何更新 / 维护

1. 编辑 `tv.json`，增删 `sites` 数组里的站点。
2. 跑 `python test_tv.py` 验证新站可用。
3. 提交推送：

```bash
git add tv.json
git commit -m "update sites"
git push
```

4. 电视端重启 App 或手动刷新，新源自动生效（jsDelivr 缓存约 5 分钟）。

## 免责声明

本仓库仅汇总第三方公开接口地址，不托管、不分发具体影视内容。接口由第三方维护，可用性会变化。请遵守当地法律法规，合理使用。
