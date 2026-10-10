# 增量更新 · 随机请求不吃缓存（cache.bypass_keywords）（2026-10-10）

**给用户的话**：把本文件的完整路径发给你正在用的 DSH Agent，然后说一句：

> 按这份补丁更新我的 skill-bridge 配置

**不要重新下载整包、不要覆盖你自己的 `bridge_config.json`。** 本补丁只做字段级合并：
你的 `api_key`、`skills_dir`、上游模型、以及你自己加的路由，一个都不会被动。

> ⚠️ 本次还更新了 bridge/dsh_skill_bridge.py（旁路逻辑在新脚本里）——升级时请一并替换这个脚本，否则只加 config 字段不会生效。

---
---

# 给 DSH Agent 的执行说明

你要做的是**字段级合并**，不是整份覆盖。改前自动备份，改后校验，最后向用户报告。

## 本次变更清单

| 类型 | 路由 / 字段 | 内容 |
|---|---|---|
| 补配置 | `cache` | bypass_keywords |

## 第 0 步 · 只读探测（先别改任何东西）

```powershell
# ① 找 bridge_config.json（问用户，或按常见位置找）
Get-ChildItem "$env:USERPROFILE" -Recurse -Filter "bridge_config.json" -ErrorAction SilentlyContinue |
  Where-Object { $_.FullName -match "bridge" } | Select-Object -ExpandProperty FullName

# ② python（ComfyUI 自带的解释器优先，例如 <ComfyUI 目录>\python_embeded\python.exe）
python -V

# ③ 桥在不在跑，以及它当前有哪些路由
Get-NetTCPConnection -LocalPort 8899 -State Listen -ErrorAction SilentlyContinue
```

把探到的结果先列给用户看，确认要改的是哪一份配置后再继续。

## 第 1 步 · 前置检查

```powershell
& "<python>" -X utf8 -c @'
import json, os, sys
cfg = json.load(open(r'<配置路径>', encoding='utf-8'))
sd = cfg.get('skills_dir') or ''
print('skills_dir =', sd)
for s in []:
    p = os.path.join(sd, s, 'SKILL.md')
    print('  %-16s %s' % (s, 'OK' if os.path.isfile(p) else '缺失（相关路由会读不到规则）'))
'@
```

## 第 2 步 · 合并（会自动备份，不动你的私有字段）

把下面这段脚本存成临时文件后执行（只有两个路径要替换）：

```powershell
@'
#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""合并一份 skill-bridge 增量补丁（字段级，不覆盖整份配置）。随补丁 md 一起生成。

用法：
    python apply_patch.py --md "<补丁 md 路径>" --config "<你的 bridge_config.json 路径>"
    python apply_patch.py --md "..." --config "..." --check   # 只看会改什么，不写盘
"""

import argparse
import json
import os
import shutil
import sys
import time

_BEGIN = "<!--BRIDGE_PATCH_JSON" + "_BEGIN-->"
_END = "<!--BRIDGE_PATCH_JSON" + "_END-->"


def load_patch(md_path):
    """取文末那段数据。md 中段内嵌了本脚本，脚本里也引用了同样的锚点字符串，
    所以只认最后一次出现的位置（数据段永远在文末）。"""
    text = open(md_path, encoding="utf-8").read()
    i, j = text.rfind(_BEGIN), text.rfind(_END)
    if i < 0 or j < 0 or i > j:
        sys.exit("补丁 md 里找不到数据段标记，文件可能被改坏了")
    return json.loads(text[i + len(_BEGIN):j])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--md", required=True, help="补丁 md 路径")
    ap.add_argument("--config", required=True, help="你的 bridge/bridge_config.json 路径")
    ap.add_argument("--check", action="store_true", help="只预演，不写盘")
    args = ap.parse_args()

    patch = load_patch(args.md)

    cfg = json.load(open(args.config, encoding="utf-8"))
    before = json.dumps(cfg, ensure_ascii=False, sort_keys=True)

    set_routes = patch.get("set_routes") or {}
    patch_fields = patch.get("patch_route_fields") or {}
    set_top = patch.get("set_top_level") or {}

    routes = cfg.setdefault("routes", {})
    for key, route in set_routes.items():
        existed = key in routes
        routes[key] = route
        print("  %s路由  %s  (skill=%s, refs=%d)"
              % ("覆盖已有" if existed else "新增", key.split("|")[0],
                 route.get("skill"), len(route.get("refs") or [])))
    for key, delta in patch_fields.items():
        if key not in routes:
            print("  !! 跳过 %s：你的配置里没有这条路由" % key.split("|")[0])
            continue
        for f, v in delta.items():
            routes[key][f] = v
        print("  补字段  %-14s %s" % (key.split("|")[0], "、".join(sorted(delta.keys()))))

    real_top = [k for k in set_top if not k.startswith("_检查")]
    for key in real_top:
        cfg[key] = set_top[key]
    if real_top:
        print("  更新顶层字段：%s" % "、".join(real_top))

    for section, delta in (patch.get("patch_sections") or {}).items():
        cfg.setdefault(section, {}).update(delta)
        print("  补配置  %-12s %s" % (section, "、".join(sorted(delta))))

    missing = patch.get("missing_skills") or []
    if missing:
        print("\n  [提醒] 本补丁里这些 skill 还不在你的 skills 目录，相关路由会读不到规则：")
        for s in missing:
            print("    - %s" % s)

    after = json.dumps(cfg, ensure_ascii=False, sort_keys=True)
    if before == after:
        print("\n没有需要改的内容，你的配置已经是最新的。")
        return

    if args.check:
        print("\n[--check] 未写盘。")
        return

    stamp = time.strftime("%Y%m%d-%H%M%S")
    bak = "%s.bak-patch-%s" % (args.config, stamp)
    shutil.copy2(args.config, bak)
    with open(args.config, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    print("\n  已合并写出：%s" % args.config)
    print("  备份：%s" % os.path.basename(bak))
    print("  桥的配置是热重载的——改完即生效，不用重启桥（除非改了 port）。")


if __name__ == "__main__":
    main()
'@ | Set-Content -Encoding UTF8 "$env:TEMP\apply_bridge_patch.py"
& "<python>" -X utf8 "$env:TEMP\apply_bridge_patch.py" --md "<本补丁 md 的完整路径>" --config "<配置路径>" --check
```

**先跑一次 `--check` 看会改什么**，确认无误再去掉 `--check` 正式合并。

## 第 3 步 · 校验

```powershell
& "<python>" -X utf8 -c "import json; c=json.load(open(r'<配置路径>', encoding='utf-8')); print('JSON OK, routes:', len(c['routes'])); print(' '.join(k.split("|")[0] for k in c['routes']))"
```

可选但推荐：发一个真请求，确认路由与形态都对（桥在跑的话）：

```powershell
& "<python>" -X utf8 -c @'
import json, urllib.request
for sys_txt, usr in [('krea2 巨构', '测试：雪原上的巨型环形加速器'), ('anima 扩写', '银发修女跪在废墟教堂里')]:
    body = {'model': 'skill-bridge', 'max_tokens': 8000, 'messages': [
        {'role': 'system', 'content': sys_txt}, {'role': 'user', 'content': usr}]}
    req = urllib.request.Request('http://127.0.0.1:8899/v1/chat/completions',
        data=json.dumps(body, ensure_ascii=False).encode('utf-8'),
        headers={'Content-Type': 'application/json'})
    d = json.loads(urllib.request.urlopen(req, timeout=600).read().decode('utf-8'))
    c = d['choices'][0]['message']['content']
    print('===', sys_txt, '-> 正文', len(c), '字')
    print(c[:300])
'@
```

**判定**：`anima 扩写` 应输出「tag 流 ＋ 空行 ＋ 自然语言」两段（不是三段式长稿）；
`krea2 巨构` 应输出一整段纯英文长段落。桥日志在 `<配置同目录>/bridge.log`。

## 第 4 步 · 报告给用户

1. 改了哪几处（按上面变更清单说，别贴大段 JSON）
2. 备份文件叫什么
3. 校验结果：JSON OK / 真请求的正文长度与形态
4. 如果用户缺 `krea2-prompt` skill，明确告诉他需要单独补这个目录

## 回滚

合并时自动生成了 `<配置名>.bak-patch-<时间戳>`，出问题就把文件名改回 `bridge_config.json`。

---

## 附：补丁数据（机器可读，勿改）

<!--BRIDGE_PATCH_JSON_BEGIN-->
{
  "schema": "skill-bridge-patch/1",
  "date": "2026-10-10",
  "title": "随机请求不吃缓存（cache.bypass_keywords）",
  "set_routes": {},
  "patch_route_fields": {},
  "patch_sections": {
    "cache": {
      "bypass_keywords": [
        "随机",
        "random"
      ]
    }
  },
  "set_top_level": {},
  "missing_skills": [],
  "skipped_routes": [],
  "_检查": "本文件由 make_patch_md.py 生成，勿手改；合并只影响上面点名的字段。"
}
<!--BRIDGE_PATCH_JSON_END-->
