# key-watch · 多中转站监控

**多个中转站，余额还剩多少？哪个挂了？** 一条命令看全部，异常自动告警到微信/钉钉/飞书。

零依赖 · 单文件 · 中文

---

## 功能

- **余额查询**：自动尝试多种常见余额接口（`credit_grants` / `subscription` / One API quota）
- **可用性监控**：延迟、模型数量、连通性
- **状态变化告警**：站点掉线 / 恢复 / 余额不足，只在**变化时**通知（不刷屏）
- **Webhook**：企业微信机器人 / 钉钉机器人 / 飞书机器人 / 任意 POST 接口
- **本地存储**：配置和状态都在 `~/.key-watch/`，密钥权限 600

---

## 快速开始

```bash
# 1. 添加站点
python key_watch.py add -n 好同根 -u https://api.example.com/v1 -k sk-xxxx
python key_watch.py add -n 备用站 -u https://api2.example.com/v1 -k sk-yyyy

# 2. 检查一次
python key_watch.py check

# 3. 持续监控（每5分钟）
python key_watch.py watch -i 300

# 4. 可选：配置告警机器人
python key_watch.py webhook "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxxx"
```

---

## 输出示例

```
══════════════════════════════════════════════════════════════════════════════
  key-watch · 中转站状态  2026-10-10 14:22:31
══════════════════════════════════════════════════════════════════════════════
  站点            状态      延迟      模型    余额
──────────────────────────────────────────────────────────────────────────────
  好同根          正常      312ms     87      $24.30
  备用站          异常      5001ms    -       不支持查询  HTTP 503
══════════════════════════════════════════════════════════════════════════════
  [站点异常] 备用站 无法访问：HTTP 503
```

告警只在状态**变化**时触发：

| 变化 | 告警 |
|---|---|
| 正常 → 异常 | `[站点异常] 备用站 无法访问：HTTP 503` |
| 异常 → 正常 | `[恢复] 备用站 已恢复，延迟 280ms` |
| 余额跌破阈值 | `[余额不足] 好同根 剩余 $0.80（低于 $1.0）` |

---

## 命令一览

| 命令 | 说明 |
|---|---|
| `add -n 名称 -u 地址 -k 密钥` | 添加站点 |
| `remove -n 名称` | 删除站点 |
| `list` | 列出已配置站点（密钥打码） |
| `check` | 检查一次并输出表格 |
| `watch -i 秒数` | 持续监控 |
| `webhook URL` | 设置告警机器人（传 `""` 清空） |

---

## 余额查询说明

不同中转站的余额接口不一样，本工具会**依次尝试**以下几种，谁先成功用谁：

| 接口 | 常见于 |
|---|---|
| `GET /v1/dashboard/billing/credit_grants` | 仿官方 OpenAI 计费接口 |
| `GET /v1/dashboard/billing/subscription` | 同上 |
| `GET /dashboard/billing/usage` | 只返回已用量 |
| `GET /api/user/self` | One API / New API（需用户 token） |

都查不到时显示「不支持查询」，不影响可用性监控。

---

## 自动化

### Windows 任务计划

```
程序：python
参数：C:\path\to\key_watch.py check
触发器：每 30 分钟
```

### Linux / macOS crontab

```cron
*/30 * * * * /usr/bin/python3 /path/to/key_watch.py check >> /var/log/key-watch.log 2>&1
```

### Docker

```bash
docker run -d --name key-watch \
  -v ~/.key-watch:/root/.key-watch \
  python:3.12-alpine python /app/key_watch.py watch
```

---

## 安全

- 密钥以明文存在 `~/.key-watch/config.json`（权限 600，仅当前用户可读）
- 告警内容**不含密钥**
- 本工具只读取状态，不发起对话请求，不消耗你的余额

---

## 相关项目

| 项目 | 用途 |
|---|---|
| [relay-bench](https://github.com/dafeiai918/relay-bench) | 深度体检：延迟/并发/参数/降智 |
| [relay-proxy](https://github.com/dafeiai918/relay-proxy) | 本地代理：藏密钥/局域网共享 |
| [relay-converter](https://github.com/dafeiai918/relay-converter) | 一份配置转 19 种客户端格式 |

---

## License

MIT
