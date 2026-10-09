"""给 skill 桥挂上 krea2-prompt 的三条路由（巨物流 / 参考流 / 随机流）。

背景：桥原有 route `krea2` 指向 anima-tagger（danbooru tag 口径），而 krea2 实际吃
自然语言——新固化的 krea2-prompt skill 才是 krea2 的自然语言规则库。三条新路由的别名
都长于裸词 "krea2"，按桥「命中最长者优先」的规则互不干扰：

    System 写 "krea2 巨构"  -> 命中新路由，喂 krea2-prompt 的巨物流规则
    System 只写 "krea2"     -> 仍命中旧路由，走 tag 扩写流程（既有用法不破坏）
    System 只写 "巨构"      -> 命中新路由的裸词别名

用法：
    python add_krea2_routes.py --check   # 只校验 + 模拟路由命中，不写盘
    python add_krea2_routes.py           # 备份后写入（配置热重载，桥无需重启）
"""

import json
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
CFG_PATH = os.path.join(HERE, "bridge_config.json")
SKILL = "krea2-prompt"

GI_COLOSSAL_WITH_TEXT = (
    "# ===== 本次输入：图像 + 文字要求 =====\n"
    "图只当**尺度、形态与材质参考**，不是 ControlNet 控制图，不要走打标/反推分支。\n"
    "按本场景规则（krea2-prompt 巨物流）处理：以图中主体的造型与材质为基准，"
    "自主给定数据体量、画幅占比、负空间与远景参照物；文字要求优先级最高。\n"
    "输出纯英文自然语言长段落（6 维结构），禁用二次元 / 3D / render / CG / UE5 词，"
    "提示词本体不得出现中文与 tag 流。"
)

GI_COLOSSAL_IMAGE_ONLY = (
    "# ===== 本次输入：只有图像 =====\n"
    "按 krea2-prompt 巨物流规则处理：以图中主体的形态与材质为基准，自主补全夸张数据尺度、"
    "画幅占比、10%–40% 负空间与置于同一极远深度平面的参照物。\n"
    "输出纯英文自然语言长段落。不要输出 danbooru tag 流、不要走反推打标分支。"
)

GI_POSTER_WITH_TEXT = (
    "# ===== 本次输入：图像 + 文字要求 =====\n"
    "图是**风格 / 构图 / 配色 / 排版参考**，不是 ControlNet 控制图，不要走打标/反推分支。\n"
    "按 krea2-prompt 参考流规则把图中的风格、色彩关系、构图切割与排版特征转写进提示词；"
    "用户的文字要求优先级最高，未提及的维度由规则自主设计。\n"
    "输出纯英文自然语言长段落（五维法则，可多段），提示词本体不得出现中文、tag 流与权重语法。"
)

GI_POSTER_IMAGE_ONLY = (
    "# ===== 本次输入：只有图像 =====\n"
    "按 krea2-prompt 参考流规则处理：从图中提炼主题、风格、配色与排版特征，自主设计主体、"
    "文字内容与构图层级，输出纯英文自然语言长段落。\n"
    "不要输出 danbooru tag 流、不要走反推打标分支。"
)

GI_RANDOM_WITH_TEXT = (
    "# ===== 本次输入：图像 + 文字要求 =====\n"
    "随机流本不需要参考；既然给了图，就把它当**风格与色彩参考**，主题仍可自主发挥。\n"
    "按 krea2-prompt 随机流规则处理，文字要求优先级最高，输出纯英文自然语言长段落。"
)

GI_RANDOM_IMAGE_ONLY = (
    "# ===== 本次输入：只有图像 =====\n"
    "随机流本不需要参考；既然只有图，就以图的风格、色彩与构成为基准自主派生主题，"
    "按 krea2-prompt 随机流规则输出纯英文自然语言长段落。不要走反推打标分支。"
)

_OV_COMMON = """【流水线覆盖指令 · 优先级高于下方 skill 原文】

你运行在 ComfyUI 自动化流水线里，输出会被直接送进图像模型（Krea2）的文本编码器，中间没有人工编辑环节。

0. 输入钳制（最重要，先做这一步）：用户在本轮消息里给出的人物／角色／环境／场景／动作／事件等要素，是本条提示词的**指定内容项**——一个都不许丢、不许改写语义、不许替换成你自己另选的主题，必须把它们作为主体、背景与事件描述的主要内容写进提示词。只有用户完全没提的维度（构图、景别、光影、色彩、材质细节、画质词等）才由 skill 规则自主设计补全。用户要素与 skill 原文示例冲突时，以用户要素为准。
1. 只输出提示词正文本身。禁止出现：代码块与反引号、Markdown 记号（# / ** / - 列表）、JSON、前言（「以下是」「Here is」）、解释、说明、附注、自评、字数统计、结尾语。
2. 不要提问、不要确认、不要要求补充信息。收到输入直接产出成品；skill 原文里凡要求「先向用户确认某件事」的步骤一律跳过，按默认口径执行。
3. 全篇英文：只出现拉丁字母、数字、空格、标点与括号。禁止中文、日文及任何非拉丁字符——即便场景声明是中文写的，即便 Krea2 的文本编码器（Qwen3-VL）原生支持中文，正文也一律全英文（英文提示词效果更优）。
4. 禁止 tag 堆砌、权重语法 `(x:1.2)`、`BREAK`、负面提示词区——Krea2 吃自然语言，用连贯长句表达。
5. 不要声称执行了任何命令、脚本或校验。你是纯文本生成环节，没有工具能力。
6. 自检在心里做完即可，不要把自检过程、清单或结论写出来。
7. 篇幅上限只受本条约束：不要为了短而砍掉用户给的内容项；skill 原文里与长度、密度有关的硬性数字，与本条冲突时以本条为准。
"""

_OV_COLOSSAL = _OV_COMMON + """
8. 交付形态：单一连贯的纯英文自然语言长段落（不写小标题、不分小节），通常 250–500 词。必须落实巨物流三条铁律——10%–40% 负空间、参照物置于同一极远深度平面且占比极小、夸张的具体数据体量。
"""

_OV_POSTER = _OV_COMMON + """
8. 交付形态：纯英文自然语言，可分 2–4 个自然段（段落之间自然过渡，不写小标题），通常 300–800 词。必须落实五维法则与色彩控制系统；正文里出现的文字内容用双引号给出，并交代它与主体的遮挡／穿插／环绕关系。
"""

_OV_RANDOM = _OV_COMMON + """
8. 交付形态：纯英文自然语言，可分 2–4 个自然段（段落之间自然过渡，不写小标题），通常 300–800 词。本次没有外部参考时由你自主选题；一旦用户在消息里给了要素，那些要素即为指定内容项，按第 0 条钳制执行。
"""

ROUTE_NOTE = (
    "看 system_prompt 里出现哪个关键词就挑哪条，命中最长者优先。按「用途」分："
    "anima=通用扩写流程(anima-tagger)，anima-n=中文场景整套生成框架(anima-n-prompt)，"
    "krea2 巨物 / krea2 海报 / krea2 随机=新增的 krea2-prompt 三套自然语言规则库"
    "(巨物流 / 参考流 / 随机流)。krea2 系列的别名都长于裸词 krea2，"
    "所以写「krea2 巨构」走新路由、只写「krea2」仍走旧的 tag 扩写流程。"
)

def prune_stale(routes, keep_keys, skill=SKILL):
    """删掉同一 skill 的旧键。

    route 的 key 就是别名表本身，改一次别名就等于换了一个键——直接 update 会把旧的留下，
    配置里出现两条同 skill 的路由（只有一条带新别名）。所以写入前先剔除同名 skill 的旧键。
    """
    stale = [k for k, r in routes.items() if (r or {}).get("skill") == skill and k not in keep_keys]
    for k in stale:
        routes.pop(k)
    return stale


ROUTES = {
    # 巨物流：巨怪 / 克苏鲁 / 有机巨物 / 巨构三版
    "krea2 巨物|krea2巨物|krea2 巨怪|krea2巨怪|krea2 巨兽|krea2 克苏鲁|krea2克苏鲁"
    "|krea2 巨构|krea2巨构|krea2 有机巨物|krea2有机巨物|krea2 巨物流"
    "|巨物流|巨构|巨怪|克苏鲁|有机巨物"
    "|krea2 colossal|krea2 megastructure|krea2 kaiju|krea2 cthulhu|colossal megastructure": {
        "skill": SKILL,
        "refs": [
            "巨物流-巨怪.md",
            "巨物流-克苏鲁巨物.md",
            "巨物流-有机巨物.md",
            "巨物流-巨构A-工业尺度.md",
            "巨物流-巨构B-无限延伸.md",
            "巨物流-巨构C-局部完整.md",
        ],
        "model": "cn:deep-model",
        "pipeline_overrides": _OV_COLOSSAL,
        "image_guidance_with_tags": GI_COLOSSAL_WITH_TEXT,
        "image_guidance_image_only": GI_COLOSSAL_IMAGE_ONLY,
    },
    # 参考流：用户给了文字/图片参考的海报式图文设计
    "krea2 海报|krea2海报|krea2 图文海报|krea2图文海报|krea2 参考流|krea2参考流"
    "|krea2 场景海报|krea2 文字海报|krea2 物体海报|krea2 IP海报|krea2 IP角色|krea2 字体排版"
    "|图文海报|文字海报|场景海报|物体海报|IP海报|字体排版|参考流"
    "|krea2 poster|krea2 graphic poster|krea2 typography|poster design": {
        "skill": SKILL,
        "refs": [
            "参考流-00-通用模板.md",
            "参考流-01-IP角色.md",
            "参考流-02-场景A-插画向.md",
            "参考流-03-场景B-海报向.md",
            "参考流-04-文字A-文字主体.md",
            "参考流-05-文字B-图形系统.md",
            "参考流-06-物体A-插画向.md",
            "参考流-07-物体B-海报向.md",
        ],
        "model": "cn:deep-model",
        "pipeline_overrides": _OV_POSTER,
        "image_guidance_with_tags": GI_POSTER_WITH_TEXT,
        "image_guidance_image_only": GI_POSTER_IMAGE_ONLY,
    },
    # 随机流：无参考、模型自主创意
    "krea2 随机|krea2随机|krea2 随机流|krea2随机流|krea2 随机海报|krea2 人文建筑|krea2人文建筑"
    "|随机海报|随机流|人文建筑|krea2 random|random poster": {
        "skill": SKILL,
        "refs": [
            "随机流-00-通用模板.md",
            "随机流-01-IP角色.md",
            "随机流-02-人文建筑.md",
            "随机流-03-场景A-插画向.md",
            "随机流-04-场景B-海报向.md",
            "随机流-05-文字A-文字主体.md",
            "随机流-06-文字B-图形系统.md",
            "随机流-07-物体A-插画向.md",
            "随机流-08-物体B-海报向.md",
        ],
        "model": "cn:deep-model",
        "pipeline_overrides": _OV_RANDOM,
        "image_guidance_with_tags": GI_RANDOM_WITH_TEXT,
        "image_guidance_image_only": GI_RANDOM_IMAGE_ONLY,
    },
}

# 模拟用样例：验证「长别名压过裸词 krea2」的优先级是否如预期
PROBES = [
    ("krea2 巨构，多向出画", "krea2 巨物"),      # 期望命中巨物流
    ("krea2巨怪 摄影写实", "krea2 巨物"),
    ("巨构 扩写", "krea2 巨物"),                  # 裸词别名
    ("krea2 图文海报，主题：深海遗迹", "krea2 海报"),
    ("krea2 字体排版", "krea2 海报"),
    ("krea2 随机流 出一张", "krea2 随机"),
    ("krea2 人文建筑", "krea2 随机"),
    ("krea2 megastructure", "krea2 巨物"),      # 英文别名
    ("krea2 poster", "krea2 海报"),
    ("krea2 random", "krea2 随机"),
    ("krea2 扩写", "krea2"),                      # 旧用法：仍走 anima-tagger
    ("", "(default_route)"),
]


def pick_route(user_system, routes, default_route):
    """与 dsh_skill_bridge.pick_route 同一逻辑（原样复刻，仅用于本地预演）。"""
    lowered = (user_system or "").lower()
    best, best_len = None, 0
    for k in routes:
        for alias in str(k).split("|"):
            alias = alias.strip().lower()
            if alias and alias in lowered and len(alias) > best_len:
                best, best_len = k, len(alias)
    if best is None:
        return default_route
    return best


def main():
    check_only = "--check" in sys.argv
    with open(CFG_PATH, encoding="utf-8") as fh:
        cfg = json.load(fh)

    skills_dir = cfg.get("skills_dir") or ""
    routes = cfg.setdefault("routes", {})

    print("skills_dir = %s" % skills_dir)
    problems = []
    for key, route in ROUTES.items():
        skill_dir = os.path.join(skills_dir, route["skill"])
        if not os.path.isfile(os.path.join(skill_dir, "SKILL.md")):
            problems.append("缺 SKILL.md: %s" % skill_dir)
        for ref in route["refs"]:
            if not os.path.isfile(os.path.join(skill_dir, "references", ref)):
                problems.append("缺 references/%s" % ref)
        first = key.split("|")[0]
        print("  route %-14s -> %-12s refs=%d  别名=%d 个"
              % (first, route["skill"], len(route["refs"]), len(key.split("|"))))

    if problems:
        print("\n!! 以下文件缺失，请先把 krea2-prompt 同步到 skills_dir：")
        for p in problems:
            print("   " + p)
        raise SystemExit(1)

    # 模拟：新路由挂上之后，各样例命中哪条
    merged = dict(routes)
    merged.update(ROUTES)
    print("\n路由预演（default_route=%s）：" % cfg.get("default_route"))
    ok = True
    for text, expect in PROBES:
        got = pick_route(text, merged, cfg.get("default_route"))
        got_head = got.split("|")[0]
        flag = "OK " if (expect == "(default_route)" or got_head == expect) else "!! "
        if flag == "!! ":
            ok = False
        print("  %s %-28r -> %s" % (flag, text, got_head))
    if not ok:
        print("\n!! 预演结果与期望不符，已中止（未写盘）")
        raise SystemExit(1)

    if check_only:
        print("\n[--check] 未写盘。去掉 --check 即写入。")
        return

    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = os.path.join(HERE, "bridge_config.json.bak-before-krea2-%s" % stamp)
    shutil.copy2(CFG_PATH, backup)
    stale = prune_stale(routes, set(ROUTES))
    if stale:
        print("  清理同 skill 的旧键 %d 条：%s"
              % (len(stale), "、".join(k.split("|")[0] for k in stale)))
    routes.update(ROUTES)

    # 顺手把过时的路由说明刷新一遍（原文只提 anima 两个 skill）
    if cfg.get("_路由说明") != ROUTE_NOTE:
        cfg["_路由说明"] = ROUTE_NOTE
    with open(CFG_PATH, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    print("\n已写入 %s（配置热重载，桥无需重启）" % CFG_PATH)
    print("备份 %s" % os.path.basename(backup))
    print("现 routes：%s" % ", ".join(k.split("|")[0] for k in cfg["routes"]))


if __name__ == "__main__":
    if sys.stdout is not None:
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    main()
