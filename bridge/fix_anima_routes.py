"""给 anima 系路由配对口径的流水线覆盖层（修「anima 被 krea2 长稿口径污染」）。

问题：`anima` / `anima-n` / `n-prompt` / `sdxl` 四条 route 加载的是 anima-n-prompt
（输出协议＝tag 流），却因为没写 route 级 `pipeline_overrides`，继承了全局那一份
——全局那份是给 `krea2`（anima-tagger 扩写分支）写的长稿口径：七层结构、300~600 词、
[SUBJECT]/[Master Description]。结果 anima 场景被写成三段式自然语言长稿，而
anima 1.0 base 的 CLIP 只有 512 token，长稿会被物理截断成残句。

本脚本做三件事：
  1. 给四条 anima 系 route 挂上 anima 口径覆盖层（两段式：tag 流 + 简短自然语言，512 硬约束）；
  2. 修 `refs`：`["SKILL.md"]` 会被桥解析成 `<skill>/references/SKILL.md`（不存在），
     改为 anima-n-prompt 真正的必读文件 `01-框架规则.md`；
  3. 刷新 `_路由说明`，写清「route 的 skill 与它的覆盖层必须配套」这条教训。

用法：
    python fix_anima_routes.py --check   # 只校验 + 预演组装，不写盘
    python fix_anima_routes.py           # 备份后写入（配置热重载，桥无需重启）
"""

import importlib.util
import json
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
CFG_PATH = os.path.join(HERE, "bridge_config.json")

# 四条 route 都指向 anima-n-prompt（tag 流框架）；sdxl 同一套形态，只是 CLIP 更短
ANIMA_ROUTES = ["anima", "anima-n", "n-prompt"]
SDXL_ROUTES = ["sdxl"]

SKILL = "anima-n-prompt"
FIXED_REFS = ["01-框架规则.md"]

_HEAD = """【流水线覆盖指令 · 优先级高于下方 skill 原文】

你运行在 ComfyUI 自动化流水线里，输出会被直接送进 %s 的文本编码器，中间没有人工编辑环节。

0. 输出形态（anima 口径；本层覆盖 skill 原文里一切与形态、结构、篇幅有关的条款）：只输出**两段**，没有第三段、没有小标题、没有代码块、没有解释。
   第一段 = **tag 流**：按 skill 的槽位顺序与自检清单抽出 danbooru 风格 tag，全小写、逗号 + 空格分隔；不写质量词（masterpiece / best quality / score_x）、不写画师名、不写权重语法（(x:1.2)）、不写负面提示词区。
   第二段 = **自然语言**：英文陈述句，**句数不限**（一句也行、五六句也行，以把关系说清为准），只交代 tag 之间说不清的**场景关系**——谁在哪、朝向与视线、身体与四肢位置、谁挡着谁、前后景与景别；直白陈述即可，不加修辞、不堆形容词、不写画面外的因果与心理、不写负向句。
   两段之间空一行。
1. 长度硬约束（最重要）：%s 超出的部分会被物理截断成残句。两段合计必须**远低于**上限，**目标 ≤ 300 token**（约 200 词以内）。预算优先留给第一段：tag 流要把画面要素尽量列全（主体、外貌、服装、动作、镜头、场景、氛围——该有的槽位都别空着）；第二段句数不限，但每句都要为交代关系服务，不许写成散文。
   tag 流交稿前自查一遍：同一槽位只留一个值（景别别同时写 full body 与 cowboy shot、人数别重复）；上位词与具体词不要并存（church 与 ruined church 只留具体那个）；同义堆叠取最准的一个（dust / floating dust / dust motes 三选一）——别靠堆同义词凑密度。
2. 细节增强模式（仅当用户主动要求时启用）：若用户在需求里明确提出「神态生动」「细致场景」「构图饱满」「场景细节充分」这一类**要求增加主体细节或场景丰富度**的措辞，就放宽第 1 条的精简要求——按用户给出的基本场景声明，抽选更多贴合场景的人物神态（表情、视线、姿态微动）、服装道具、环境物件、光影与氛围 tag，把画面填满；此时两段合计上限放宽到 **≤ 480 token**（仍不得触及 512）。用户没提这类要求时，一律按第 1 条的精简口径执行，不许自行加料。
3. 严格禁止：三段式长稿、七层分层结构（Composition & Pose 之类）、[SUBJECT] / [Master Description] 分块，以及「标签锚点 ➔ 极致细节 ➔ Master」那套 krea2 长通道写法——喂给 anima 会被截断。
4. 只输出提示词正文本身。禁止代码块与反引号、Markdown 记号（# / ** / - 列表）、JSON、前言（「以下是」「Here is」）、解释、说明、附注、自评、字数统计、结尾语。
5. 不要提问、不要确认、不要要求补充信息；skill 原文里凡要求「先向用户确认某件事」的步骤一律跳过，按默认口径执行。
6. 全篇英文：只出现拉丁字母、数字、空格、逗号、句号和括号。禁止中文、日文及任何非拉丁字符——即便场景声明是中文写的。
7. 不要声称执行了任何命令、脚本或校验。你没有工具能力，写出的只能是提示词正文。
8. 自检在心里做完即可，不要把自检过程、清单或结论写出来。
"""

ANIMA_OVERRIDES = _HEAD % (
    "anima 1.0 base 的 CLIP（0.6B，硬上限 512 token）",
    "anima 1.0 base 的文本编码器只有 512 token，",
)

SDXL_OVERRIDES = _HEAD % (
    "SDXL 的 CLIP（单段 77 token，靠拼接延长）",
    "SDXL 的 CLIP 单段只有 77 token，拼接也不该当成长通道用，",
)

ROUTE_NOTE = (
    "看 system_prompt 里出现哪个关键词就挑哪条，命中最长者优先。按「用途」分："
    "anima / anima-n / n-prompt / sdxl -> anima-n-prompt 的 tag 流框架（512 token 硬上限，"
    "只交「tag 流 + 简短自然语言」两段）；krea2 -> anima-tagger 的 tag 扩写长稿；"
    "krea2 巨物 / krea2 海报 / krea2 随机 -> krea2-prompt 三套自然语言规则库；"
    "minimax / h3 -> 视频提示词 skill。"
    "注意：每条 route 的 pipeline_overrides 必须与它加载的 skill 配套——换 skill 时覆盖层要一起换，"
    "否则会拿 krea2 的长稿口径去写 anima（512 token 被截断）。"
)

PROBES = ["anima 扩写", "sdxl 扩写", "anima-n 扩写", "n-prompt 扩写", "krea2 扩写", ""]


def main():
    check_only = "--check" in sys.argv
    with open(CFG_PATH, encoding="utf-8") as fh:
        cfg = json.load(fh)

    skills_dir = cfg.get("skills_dir") or ""
    routes = cfg.setdefault("routes", {})

    plan = {}
    for key in ANIMA_ROUTES:
        plan[key] = ANIMA_OVERRIDES
    for key in SDXL_ROUTES:
        plan[key] = SDXL_OVERRIDES

    problems = []
    for key, ov in plan.items():
        route = routes.get(key)
        if not route:
            problems.append("route 不存在：%s" % key)
            continue
        skill_dir = os.path.join(skills_dir, route.get("skill") or "")
        for ref in FIXED_REFS:
            if not os.path.isfile(os.path.join(skill_dir, "references", ref)):
                problems.append("缺 references/%s（skill=%s）" % (ref, route.get("skill")))
        old_refs = route.get("refs") or []
        bad = [r for r in old_refs if not os.path.isfile(os.path.join(skill_dir, "references", r))]
        print("  route %-9s skill=%-16s refs=%s%s"
              % (key, route.get("skill"), old_refs,
                 ("  ← 无效引用：%s" % ", ".join(bad)) if bad else ""))
    if problems:
        print("\n!! 以下前置条件不满足，已中止：")
        for p in problems:
            print("   " + p)
        raise SystemExit(1)

    # 预演：改完之后各场景的组装结果
    spec = importlib.util.spec_from_file_location(
        "bridge", os.path.join(HERE, "dsh_skill_bridge.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    for key, ov in plan.items():
        routes[key]["pipeline_overrides"] = ov
        routes[key]["refs"] = list(FIXED_REFS)

    print("\n组装预演（skills_dir=%s）：" % skills_dir)
    ok = True
    for text in PROBES:
        sp, key, route, missing = mod.build_system_prompt(cfg, text, with_image=False, user_text="")
        head = (key or "").split("|")[0]
        if missing:
            ok = False
        print("  %s %-16r -> %-10s skill=%-16s missing=%s  覆盖层首条=%s"
              % ("OK " if not missing else "!! ", text, head, (route or {}).get("skill"),
                 missing or "无",
                 "anima 两段式" if (route or {}).get("pipeline_overrides", "").startswith("【流水线覆盖指令")
                 and "两段" in (route or {}).get("pipeline_overrides", "") else "其他"))
    if not ok:
        print("\n!! 预演出现 missing，已中止（未写盘）")
        raise SystemExit(1)

    if check_only:
        print("\n[--check] 未写盘。去掉 --check 即写入。")
        return

    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = os.path.join(HERE, "bridge_config.json.bak-before-anima-%s" % stamp)
    shutil.copy2(CFG_PATH, backup)
    cfg["_路由说明"] = ROUTE_NOTE
    with open(CFG_PATH, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    print("\n已写入 %s（配置热重载，桥无需重启）" % CFG_PATH)
    print("备份 %s" % os.path.basename(backup))


if __name__ == "__main__":
    if sys.stdout is not None:
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    main()
