# TvBox 自持源接口

自维护的 TVBox/FongMi 影视源接口，供父母家 TCL 电视使用。GitHub Actions 自动维护，接口地址不变，电视端零操作。

- 客户端：**蜂蜜影视**（FongMi 系，`com.fongmi.android.tv`）
- 站点：纯直连 CMS 源（已彻底移除依赖加密 jar 的爬虫/秒播源）

## 电视端配置地址

蜂蜜影视 → 设置 → 点播 → 配置地址，填入：

```
https://tvbox-202610-1503022424.cos.ap-guangzhou.myqcloud.com/my_interface.json
```

备用地址（主地址失效时用）：

```
https://cdn.jsdelivr.net/gh/cg998/TvBox@main/dist/my_interface.json
```

## 自动维护

| 任务 | 频率 | 说明 |
|---|---|---|
| 每日健康检查 | 每天 05:20（北京） | 挂了的源降级到末尾，连续挂 3 天剔除；保证首页默认源存活 |
| 每周全量筛选 | 每周日 09:20（北京） | 从 tvbox.org 官方合并接口筛新直连源，去重后并入 |
| 部署到 COS | 随上面两步执行 | 上传最终接口到腾讯云 COS |

## 站点

自用 9 个直连 CMS 源，第一位「量子资源」为默认首页。清单见 `my_sites.json`。

## 手动换血

GitHub 仓库 → Actions → 「每周全量筛选影视源」→ Run workflow。

## 免责声明

本仓库仅汇总第三方公开接口地址，不托管、不分发具体影视内容。请遵守当地法律法规，合理使用。
