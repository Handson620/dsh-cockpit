# dsh-cockpit — DSH 任务验收台

老板要「像批奏折一样，在手机上清任务」。这个项目就是那道奏折台。

## 它怎么工作

```
                    ┌─────────────────────────────┐
  手机浏览器  ─────▶ │  Cloudflare Pages（外网可访问）│
  看列表/点按钮      │  · public/index.html  网页   │
                    │  · functions/api/*   接口    │
                    │  · KV  存数据 + 动作队列      │
                    └──────────┬──────────────────┘
                               │
              Mac 主动来推 / 主动来拉（不需要公网 IP）
                               │
                    ┌──────────▼──────────────────┐
                    │  site/cockpit_sync.py        │
                    │  · 推：本机看板 → 云端        │
                    │  · 拉：云端动作 → 改本机看板   │
                    │  · 每 2 分钟跑一次            │
                    └─────────────────────────────┘
```

**关键设计**：本机没有公网 IP，网页够不着它。
所以让 Mac 主动去拉动作，而不是网页来改本机。
代价是点完到生效有几分钟延迟，批奏折能接受。

## 目录

```
public/index.html          批奏折网页（手机打开这个）
functions/api/board.js     读看板 / 推快照
functions/api/action.js    提交动作 / 拉动作
site/cockpit_sync.py       本机同步脚本
site/config.json           本机配置（不进 git，放 token 和端口）
```

## 部署

1. Cloudflare Pages 连这个仓库，构建输出目录 `public`，Functions 目录 `functions`
2. 建 KV 命名空间，绑定到变量 `COCKPIT_KV`
3. 设两个环境变量：`REVIEW_TOKEN`（网页访问口令）、`SYNC_TOKEN`（同步密钥）
4. 本机 `site/config.json` 填同样的 `SYNC_TOKEN` 和 DSH 端口
5. 挂定时：每 2 分钟跑 `python3 site/cockpit_sync.py`

## 安全

- 网页要口令才能打开（sessionStorage 记住）
- 推数据要 `SYNC_TOKEN`，网页读也要 `REVIEW_TOKEN`
- 两个 token 不进 git
- 本机看板文件**只读**，改卡走 DSH 自己的 GUI 通道

## 验收动作怎么落到本机

看板插件在 AI 通道拦死了「移到 done」，但 GUI 通道允许。
同步脚本调 `/dsh-taskboard/tasks/<id>/move`，以人的身份移卡。
打回走 `/reject`，退回 todo 并留一条评论。
