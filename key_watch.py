#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""key-watch · 多中转站监控
余额 / 可用性 / 延迟，状态变化告警（支持企业微信 / 钉钉 / 飞书 / 通用 webhook）
零依赖单文件
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

__version__ = "1.0.0"

CONFIG_PATH = Path.home() / ".key-watch" / "config.json"
STATE_PATH = Path.home() / ".key-watch" / "state.json"
REMOTE_URL = "https://raw.githubusercontent.com/dafeiai918/oneclickrelay-remote/main/remote.json"
FALLBACK_RECOMMEND = {"name": "好同根", "url": "https://haotogen.com/"}

DEFAULT_CONFIG = {
    "sites": [],
    "interval": 300,
    "timeout": 15,
    "webhook": "",
    "alert_on": ["down", "up", "low_balance"],
    "low_balance_usd": 1.0,
}


class C:
    RESET = "\033[0m"; BOLD = "\033[1m"
    RED = "\033[31m"; GREEN = "\033[32m"; YELLOW = "\033[33m"; CYAN = "\033[36m"; GRAY = "\033[90m"


def _c(code: str, text: str) -> str:
    return f"{code}{text}{C.RESET}"


def enable_ansi() -> None:
    if os.name == "nt":
        os.system("")
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


# ---------------- 配置 ----------------

def load_config() -> dict:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))  # 深拷贝，避免默认列表被跨调用污染
    if CONFIG_PATH.exists():
        try:
            cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except Exception:
            pass
    return cfg


def save_config(cfg: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(CONFIG_PATH, 0o600)
    except Exception:
        pass


def load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------- 探测 ----------------

def _get(url: str, key: str, timeout: float):
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {key}",
        "User-Agent": f"key-watch/{__version__}",
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", "replace")
    return json.loads(raw) if raw.strip() else {}


def normalize(base: str) -> tuple[str, str]:
    root = base.strip().rstrip("/")
    if root.endswith("/v1"):
        root = root[:-3].rstrip("/")
    return root, root + "/v1"


def probe_balance(root: str, key: str, timeout: float) -> dict | None:
    """尝试多种常见余额接口，返回 {'amount': float|None, 'currency': str, 'raw': str}"""
    attempts = [
        ("credit_grants", f"{root}/v1/dashboard/billing/credit_grants"),
        ("subscription", f"{root}/v1/dashboard/billing/subscription"),
        ("billing_usage", f"{root}/dashboard/billing/usage"),
    ]
    for kind, url in attempts:
        try:
            data = _get(url, key, timeout)
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        if kind == "credit_grants":
            avail = data.get("total_available")
            if isinstance(avail, (int, float)):
                return {"amount": round(float(avail), 2), "currency": "USD",
                        "raw": "credit_grants.total_available"}
        if kind == "subscription":
            limit = data.get("hard_limit_usd") or data.get("system_hard_limit_usd")
            used = data.get("used_usd")
            if isinstance(limit, (int, float)):
                amount = float(limit) - float(used or 0)
                return {"amount": round(amount, 2), "currency": "USD",
                        "raw": "subscription.hard_limit_usd"}
        if kind == "billing_usage":
            total = data.get("total_usage")
            if isinstance(total, (int, float)):
                return {"amount": None, "currency": "USD",
                        "raw": f"已用 ${float(total)/100:.2f}（此接口只给已用量）"}
    # One API / New API：需要用户 access token，API key 通常无权限
    try:
        data = _get(f"{root}/api/user/self", key, timeout)
        quota = (data.get("data") or {}).get("quota")
        if isinstance(quota, (int, float)):
            return {"amount": round(quota / 500000, 2), "currency": "USD",
                    "raw": "one-api quota/500000"}
    except Exception:
        pass
    return None


def probe_site(site: dict, timeout: float) -> dict:
    root, full = normalize(site["base_url"])
    result = {"name": site.get("name", root), "base": root, "ok": False,
              "latency_ms": None, "models": 0, "balance": None, "error": ""}
    t0 = time.perf_counter()
    try:
        data = _get(f"{full}/models", site["api_key"], timeout)
        result["latency_ms"] = round((time.perf_counter() - t0) * 1000)
        items = data.get("data") or []
        result["models"] = len(items) if isinstance(items, list) else 0
        result["ok"] = True
    except urllib.error.HTTPError as exc:
        result["latency_ms"] = round((time.perf_counter() - t0) * 1000)
        result["error"] = f"HTTP {exc.code}"
        exc.close()
    except Exception as exc:
        result["latency_ms"] = round((time.perf_counter() - t0) * 1000)
        result["error"] = type(exc).__name__
    if result["ok"]:
        result["balance"] = probe_balance(root, site["api_key"], timeout)
    return result


# ---------------- 通知 ----------------

def notify(webhook: str, text: str, timeout: float = 8.0) -> None:
    if not webhook:
        return
    payloads = []
    if "qyapi.weixin.qq.com" in webhook:
        payloads.append({"msgtype": "text", "text": {"content": text}})
    elif "oapi.dingtalk.com" in webhook:
        payloads.append({"msgtype": "text", "text": {"content": text}})
    elif "open.feishu.cn" in webhook:
        payloads.append({"msg_type": "text", "content": {"text": text}})
    else:
        payloads.append({"text": text})
    for payload in payloads:
        try:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            req = urllib.request.Request(webhook, data=body, method="POST",
                                         headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=timeout).close()
        except Exception:
            pass


# ---------------- 展示 ----------------

def render(results: list[dict], cfg: dict) -> None:
    width = 78
    print()
    print(_c(C.GRAY, "═" * width))
    print(_c(C.BOLD, "  key-watch · 中转站状态") + _c(C.GRAY, f"  {time.strftime('%Y-%m-%d %H:%M:%S')}"))
    print(_c(C.GRAY, "═" * width))
    print(f"  {'站点':<16}{'状态':<10}{'延迟':<10}{'模型':<8}{'余额':<12}")
    print(_c(C.GRAY, "─" * width))
    for r in results:
        if r["ok"]:
            status = _c(C.GREEN, "正常")
        else:
            status = _c(C.RED, "异常")
        latency = f"{r['latency_ms']}ms" if r["latency_ms"] is not None else "-"
        models = str(r["models"]) if r["ok"] else "-"
        if r["balance"] and r["balance"].get("amount") is not None:
            amount = r["balance"]["amount"]
            color = C.RED if amount < cfg.get("low_balance_usd", 1) else C.GREEN
            balance = _c(color, f"${amount}")
        elif r["balance"]:
            balance = _c(C.GRAY, "仅有已用量")
        else:
            balance = _c(C.GRAY, "不支持查询")
        err = _c(C.RED, f"  {r['error']}") if r["error"] else ""
        print(f"  {r['name']:<16}{status:<19}{latency:<10}{models:<8}{balance}{err}")
    print(_c(C.GRAY, "═" * width))


def diff_alerts(results: list[dict], prev: dict, cfg: dict) -> list[str]:
    alerts = []
    low = cfg.get("low_balance_usd", 1.0)
    for r in results:
        name = r["name"]
        old = prev.get(name, {})
        was_ok = old.get("ok")
        if was_ok is True and not r["ok"]:
            alerts.append(f"[站点异常] {name} 无法访问：{r['error']}")
        elif was_ok is False and r["ok"]:
            alerts.append(f"[恢复] {name} 已恢复，延迟 {r['latency_ms']}ms")
        if r["ok"] and r["balance"] and r["balance"].get("amount") is not None:
            amount = r["balance"]["amount"]
            old_amount = (old.get("balance") or {}).get("amount")
            if amount < low and (old_amount is None or old_amount >= low):
                alerts.append(f"[余额不足] {name} 剩余 ${amount}（低于 ${low}）")
    return alerts


# ---------------- 命令 ----------------

def cmd_add(args) -> int:
    cfg = load_config()
    if any(s.get("name") == args.name for s in cfg["sites"]):
        print(f"站点 {args.name} 已存在")
        return 1
    cfg["sites"].append({"name": args.name, "base_url": args.url, "api_key": args.key})
    save_config(cfg)
    print(f"已添加 {args.name}（共 {len(cfg['sites'])} 个站点）")
    print(_c(C.GRAY, f"配置：{CONFIG_PATH}"))
    return 0


def cmd_remove(args) -> int:
    cfg = load_config()
    before = len(cfg["sites"])
    cfg["sites"] = [s for s in cfg["sites"] if s.get("name") != args.name]
    save_config(cfg)
    if len(cfg["sites"]) == before:
        print(f"未找到站点 {args.name}")
        return 1
    print(f"已删除 {args.name}")
    return 0


def cmd_list(args) -> int:
    cfg = load_config()
    if not cfg["sites"]:
        print("还没有站点。添加：key-watch add -n 名称 -u 地址 -k 密钥")
        return 0
    for s in cfg["sites"]:
        masked = s["api_key"][:6] + "****" + s["api_key"][-4:] if len(s["api_key"]) > 10 else "****"
        print(f"  {s['name']:<16}{s['base_url']:<40}{masked}")
    return 0


def cmd_check(args) -> int:
    cfg = load_config()
    if not cfg["sites"]:
        print("还没有站点，先用 key-watch add 添加")
        return 1
    results = [probe_site(s, cfg["timeout"]) for s in cfg["sites"]]
    render(results, cfg)
    state = load_state()
    for alert in diff_alerts(results, state, cfg):
        print(_c(C.YELLOW, f"  {alert}"))
        notify(cfg.get("webhook", ""), alert)
    save_state({r["name"]: r for r in results})
    return 0 if all(r["ok"] for r in results) else 1


def cmd_webhook(args) -> int:
    cfg = load_config()
    cfg["webhook"] = args.url.strip()
    save_config(cfg)
    print(f"webhook 已设为 {cfg['webhook'] or '(已清空)'}")
    return 0


def cmd_watch(args) -> int:
    cfg = load_config()
    if not cfg["sites"]:
        print("还没有站点，先用 key-watch add 添加")
        return 1
    interval = args.interval or cfg["interval"]
    print(f"开始监控 {len(cfg['sites'])} 个站点，间隔 {interval}s，Ctrl+C 停止")
    try:
        while True:
            results = [probe_site(s, cfg["timeout"]) for s in cfg["sites"]]
            render(results, cfg)
            state = load_state()
            for alert in diff_alerts(results, state, cfg):
                print(_c(C.YELLOW, f"  {alert}"))
                notify(cfg.get("webhook", ""), alert)
            save_state({r["name"]: r for r in results})
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\n已停止")
    return 0


def fetch_recommend(timeout: float = 5.0) -> dict | None:
    try:
        req = urllib.request.Request(REMOTE_URL, headers={"User-Agent": f"key-watch/{__version__}"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        rec = data.get("recommend")
        if isinstance(rec, dict) and isinstance(rec.get("url"), str):
            return rec
    except Exception:
        pass
    return FALLBACK_RECOMMEND


def main(argv: list[str] | None = None) -> int:
    enable_ansi()
    parser = argparse.ArgumentParser(
        prog="key-watch", description="多中转站监控：余额 / 可用性 / 延迟，状态变化告警")
    parser.add_argument("-V", "--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command")

    p_add = sub.add_parser("add", help="添加站点")
    p_add.add_argument("-n", "--name", required=True)
    p_add.add_argument("-u", "--url", required=True)
    p_add.add_argument("-k", "--key", required=True)
    p_add.set_defaults(func=cmd_add)

    p_rm = sub.add_parser("remove", help="删除站点")
    p_rm.add_argument("-n", "--name", required=True)
    p_rm.set_defaults(func=cmd_remove)

    sub.add_parser("list", help="列出站点").set_defaults(func=cmd_list)
    sub.add_parser("check", help="检查一次").set_defaults(func=cmd_check)

    p_watch = sub.add_parser("watch", help="持续监控")
    p_watch.add_argument("-i", "--interval", type=int, default=0, help="间隔秒数，默认读配置")
    p_watch.set_defaults(func=cmd_watch)

    p_hook = sub.add_parser("webhook", help="设置告警 webhook（传空字符串清空）")
    p_hook.add_argument("url")
    p_hook.set_defaults(func=cmd_webhook)

    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        rec = fetch_recommend()
        if rec:
            print()
            print(_c(C.CYAN, f"  推荐中转站  {rec.get('name', '')}  {rec.get('url', '')}"))
        return 0
    rc = args.func(args)
    if args.command in ("check", "watch"):
        rec = fetch_recommend()
        if rec:
            print(_c(C.GRAY, f"  推荐中转站  {rec.get('name', '')}  {rec.get('url', '')}"))
    return rc


if __name__ == "__main__":
    sys.exit(main())
