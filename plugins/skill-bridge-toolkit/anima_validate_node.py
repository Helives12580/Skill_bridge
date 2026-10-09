"""把 anima-tagger 的 anima_validate.py 接进 ComfyUI 流水线。

两件事：
  1. 内容审核拒绝检测——上游模型吐拒绝话时，换成一张「拒绝提示卡」提示词，
     让拒绝直接显示在图上，而不是悄悄把拒绝文本当提示词画出一张无关的图。
  2. 把扩写稿的「标签锚点段」交给校验器过一遍（规范化 / 上位词折叠 / 槽位冲突），
     再把修正后的锚点段原样拼回细节段。校验器是本机 skill 里的确定性脚本，不是 LLM。
"""

import json
import os
import re
import subprocess
import sys

# 分发版用 expanduser 而不是硬编码路径：等价于本机的 C:\Users\<你>\.dsh\skills\anima-tagger，
# 但不会把某个具体用户的目录带进公开仓库（本地 live 那份仍是硬编码，从 live 同步回来时注意保留本行）。
DEFAULT_SKILL_DIR = os.path.join(os.path.expanduser("~"), ".dsh", "skills", "anima-tagger")
VALIDATOR_REL = os.path.join("tools", "anima_validate.py")

# ══ 内容审核拒绝检测 ═══════════════════════════════════════════════════════
# 移植自 ComfyUI-NL-Prompt-Forge 的 py/ai_client.py（is_rejection 及其词表）。
#
# 为什么要做：上游模型触到审核边界时会直接回一段拒绝话。原样送进文本编码器的话，
# 出图会是一张和提示词毫无关系的图，而用户往往看不出发生了什么，只会反复重跑、
# 白白烧额度。识别出来换成提示卡，拒绝就变成看得见的东西。
#
# 判据是「拒绝动作词」与「审核对象词」必须同时出现，所以 Rate limit exceeded、
# Insufficient balance、unknown model 这类普通失败不会被误判成审核拒绝。

_PUNCTUATION_FOLD = {
    "\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'",
    "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u201f": '"',
    "\u2032": "'", "\u00b4": "'", "\u02bc": "'", "\uff07": "'",
    "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-",
    "\u2014": "-", "\u2212": "-",
    "\uff1a": ":", "\uff0c": ",", "\u3002": ".",
}

_REJECT_ACTIONS = (
    "reject", "refus", "block", "violat", "not allowed", "disallow", "denied",
    "deny", "forbidden", "prohibited", "unable to", "cannot", "can not",
    "can't", "won't", "will not", "declin", "not able to", "can't help",
    "can't assist", "can't comply", "not appropriate", "not something i",
    "i must not", "i won't", "i will not", "i do not", "i don't", "i'm not",
    "not comfortable",
    "do not allow", "does not allow", "not allow", "not permitted",
    "not designed to", "not meant to", "won't be able", "not going to",
    "as an ai", "as a language model", "i am programmed", "i'm programmed",
    "i apologize", "sorry, but i", "sorry, i can",
    "不能", "不会",
    "筛除", "驳回", "拒绝", "被拒", "违规", "不予", "无法",
    "未通过", "不通过", "不合规", "拦截", "屏蔽",
)

_REJECT_SUBJECTS = (
    "content", "policy", "safety", "sensitive", "moderation", "abuse", "harm",
    "sexual", "explicit", "minor", "child", "underage", "political",
    "content_filter", "contentfilter", "responsible", "guideline", "terms",
    "request", "prompt", "image", "assist", "help", "produce", "rewrite",
    "审核", "敏感", "内容", "安全", "未成年", "色情", "政治", "违规",
    "请求", "提示词", "提示语", "图像", "画面", "描述",
)


def _folded(text):
    """小写并把印刷体标点折成 ASCII。

    这一步不能省：「I can’t help with this」用的是弯撇号，不折叠的话
    词表里所有 can't / won't 模式全部落空。
    """
    blob = str(text or "").lower()
    for fancy, plain in _PUNCTUATION_FOLD.items():
        if fancy in blob:
            blob = blob.replace(fancy, plain)
    return blob


def is_rejection(text):
    """判断 text 读起来像不像一次内容审核拒绝。"""
    blob = _folded(text)
    if not blob:
        return False
    if "content_filter" in blob or "contentfilter" in blob:
        return True
    return (any(w in blob for w in _REJECT_ACTIONS)
            and any(w in blob for w in _REJECT_SUBJECTS))


# 默认的替换目标：画一个举牌的少女。英文版（流水线要求正文全英文）。
REJECTION_CARD_EN = (
    "the background is pure white, 1girl, solo, blue hair, long hair, closed eyes, "
    "long sleeves, track jacket, track pants, sportswear, holding sign, holding text board, "
    "sign, english text, text focus. "
    "A blue-haired girl stands against a pure white background wearing a long-sleeved "
    "sportswear set, her eyes closed and drawn as two horizontal dashes. "
    "She holds a text board in front of her chest, the board reading "
    "\"AI polish request rejected by the provider\"."
)

# ══ 校验器管不到的东西（只提醒，不修改）═════════════════════════════════════
NOT_VALIDATOR_JOB = (
    ("质量吹捧词", r"\b(masterpiece|best quality|score_\d+|ultra detailed|super detailed"
                r"|highres|absurdres|very aesthetic)\b"),
    ("BREAK 语法", r"\bBREAK\b"),
    ("权重语法", r"\([^()]*:\s*-?\d+(\.\d+)?\)|\(\([^()]+\)\)"),
    ("代码块或反引号", r"`"),
    ("中文或日文", r"[\u4e00-\u9fff\u3040-\u30ff]"),
)


def _find_python(skill_dir):
    """skill 的 .venv 优先，退到 ComfyUI 自己的解释器（依赖是重叠的）。"""
    for cand in (
        os.path.join(skill_dir, ".venv", "Scripts", "python.exe"),
        os.path.join(skill_dir, ".venv", "bin", "python"),
    ):
        if os.path.exists(cand):
            return cand
    return sys.executable


def _split_anchor(text):
    """三段式长稿：第一个空行之前是标签锚点段，其余原样保留。"""
    parts = text.split("\n\n", 1)
    return parts[0].strip(), (parts[1] if len(parts) > 1 else "")


class AnimaValidateTags:
    DESCRIPTION = (
        "先识别内容审核拒绝（命中则按设定替换为提示卡/原样报警/中断），"
        "再对标签锚点段做规范化、上位词折叠与槽位冲突检查。"
        "三段式长稿按第一个空行切分，只校验锚点段，细节段原样拼回。"
    )

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "text": ("STRING", {"forceInput": True, "multiline": True,
                                    "tooltip": "待校验文本：扩写长稿或纯 tag 串"}),
                "skill_dir": ("STRING", {"default": DEFAULT_SKILL_DIR, "multiline": False,
                                         "tooltip": "anima-tagger skill 所在目录"}),
                "mode": (["扩写（不查长度）", "标准（卡 512）"],
                         {"default": "扩写（不查长度）"}),
                "on_problem": (["用修正稿", "用原稿并报警", "中断执行"],
                               {"default": "用修正稿"}),
                "on_reject": (["替换为提示卡", "原样输出并报警", "中断执行"],
                              {"default": "替换为提示卡",
                               "tooltip": "上游返回内容审核拒绝时怎么办"}),
                "reject_card": ("STRING", {"default": REJECTION_CARD_EN, "multiline": True,
                                           "tooltip": "on_reject=替换为提示卡 时用来替换的提示词"}),
                "timeout": ("INT", {"default": 120, "min": 5, "max": 1200, "step": 5}),
            },
        }

    RETURN_TYPES = ("STRING", "STRING", "INT")
    RETURN_NAMES = ("文本", "报告", "退出码")
    FUNCTION = "run"
    CATEGORY = "anima/validate"

    def run(self, text, skill_dir, mode, on_problem, on_reject, reject_card, timeout):
        text = text or ""

        # 拒绝检测必须在校验之前：拒绝话不是提示词，送去校验只会被拆得面目全非。
        if is_rejection(text):
            head = text.strip()[:600]
            if on_reject == "中断执行":
                raise RuntimeError(
                    "[anima_validate] 上游返回内容审核拒绝，已中断。\n原文：\n" + head)
            if on_reject == "原样输出并报警":
                return (text, "[拒绝] 上游返回内容审核拒绝（按设定原样输出）。\n原文：\n" + head, 1)
            card = (reject_card or "").strip() or REJECTION_CARD_EN
            return (card,
                    "[拒绝] 上游返回内容审核拒绝，已替换为提示卡。\n原文：\n" + head, 1)

        anchor, rest = _split_anchor(text)
        if not anchor:
            return (text, "[跳过] 文本为空", 0)

        validator = os.path.join(skill_dir, VALIDATOR_REL)
        if not os.path.exists(validator):
            report = "[跳过] 找不到校验器：%s\n（把 skill_dir 改成 anima-tagger 的实际目录）" % validator
            return (text, report, 0)

        cmd = [_find_python(skill_dir), "-X", "utf8", validator,
               "--tags", anchor, "--json"]
        if mode.startswith("扩写"):
            cmd.append("--no-token-limit")

        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout)
        try:
            result = json.loads(proc.stdout)
        except Exception:
            return (text, "[失败] 校验器输出无法解析\n%s\n%s"
                    % (proc.stdout[:1500], proc.stderr[:1500]), proc.returncode)

        fixed_tags = result.get("final_tags") or []
        problems = result.get("problems") or []
        conflicts = result.get("conflicts") or []
        toks = result.get("tokens") or {}

        fixed_anchor = ", ".join(fixed_tags) if fixed_tags else anchor
        fixed_text = fixed_anchor + ("\n\n" + rest if rest else "")

        anchor_n = len([x for x in anchor.split(",") if x.strip()])
        lines = ["校验器退出码：%d" % proc.returncode,
                 "锚点：%d 条 → %d 条" % (anchor_n, len(fixed_tags))]
        if toks.get("count") is not None:
            lines.append("token：%s（enforced=%s）" % (toks["count"], toks.get("limit_enforced")))
        if conflicts:
            lines.append("")
            lines.append("槽位冲突（需人工定夺）：")
            for c in conflicts:
                lines.append("  * %s: %s" % (c.get("slot"), " | ".join(c.get("candidates") or [])))
        if problems:
            lines.append("")
            lines.append("待处理：")
            for p in problems:
                lines.append("  ! %s" % p)
        if not problems and not conflicts:
            lines.append("无问题。")

        hits = []
        for label, pat in NOT_VALIDATOR_JOB:
            found = re.search(pat, fixed_text, re.IGNORECASE)
            if found:
                hits.append("%s：%s" % (label, found.group(0)[:40]))
        if hits:
            lines.append("")
            lines.append("额外提醒（校验器管不到这些，得靠提示词或 LLM 侧拦）：")
            for h in hits:
                lines.append("  ~ %s" % h)
        report = "\n".join(lines)

        if problems and on_problem == "中断执行":
            raise RuntimeError("[anima_validate] 校验未通过：\n" + report)
        if problems and on_problem == "用原稿并报警":
            return (text, report, proc.returncode)
        return (fixed_text, report, proc.returncode)


NODE_CLASS_MAPPINGS = {"AnimaValidateTags": AnimaValidateTags}
NODE_DISPLAY_NAME_MAPPINGS = {"AnimaValidateTags": "Anima 提示词校验（anima_validate）"}
