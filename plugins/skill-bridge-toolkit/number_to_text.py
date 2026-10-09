"""把数值转成可直接拼进提示词的文本。

为什么要做这个：本机 3604 个节点里没有任何一个能把 FLOAT 转成 STRING——
ZML_IntegerStringConverter 只收 INT，而且即便转出来也是 "2500" 这种，
生不出 "At 00:02.500" 这类时间戳。于是「把锚定帧的秒数自动写进 system prompt」
这条链断在最后一步，只能靠人在声明里手写一次，还要和 PrimitiveFloat 保持同步。

这个节点补上那一格：
    PrimitiveFloat(2.5) -> 本节点(At 时间戳) -> "At 00:02.500"
                        -> ZML_MultiTextInput5 -> 场景声明

格式一览（value=2.5）：
    小数        "2.5"
    秒          "2.5 秒"
    整数        "2"        （四舍五入）
    时间戳      "00:02.500"
    At 时间戳    "At 00:02.500"
    帧号        "60"       （按 fps 换算，默认 24）
"""

import math

FORMATS = ("小数", "秒", "整数", "时间戳", "At 时间戳", "帧号")


def _trim(s):
    """"2.500" -> "2.5"、"2.000" -> "2"。拼进提示词时尾零只会碍眼。"""
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s


def _format_clock(seconds, decimals):
    """2.5 -> "00:02.500"（分:秒.小数）。负数夹到 0，进位也处理掉。

    自己算而不用 time.strftime：后者没有小数秒，而锚帧要精确到毫秒才有意义
    （24fps 下一帧约 41.7ms，写 00:02 会把 2.5 秒和 2.9 秒混为一谈）。
    """
    total = max(0.0, float(seconds))
    minutes = int(total // 60)
    rest = total - minutes * 60
    whole = int(rest)
    digits = 10 ** decimals
    frac = int(round((rest - whole) * digits))
    if frac >= digits:            # 0.9995 进位成 1.000
        frac = 0
        whole += 1
    if whole >= 60:
        whole -= 60
        minutes += 1
    if decimals:
        return "%02d:%02d.%0*d" % (minutes, whole, decimals, frac)
    return "%02d:%02d" % (minutes, whole)


class AnimaNumberToText:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "value": ("FLOAT", {"default": 0.0, "min": -36000.0, "max": 36000.0,
                                    "step": 0.01,
                                    "tooltip": "要转换的数值。接 PrimitiveFloat 之类的输出。"}),
                "format": (list(FORMATS), {"default": "At 时间戳"}),
                "decimals": ("INT", {"default": 3, "min": 0, "max": 6, "step": 1,
                                     "tooltip": "小数位。时间戳格式下 3 位 = 毫秒。"}),
            },
            "optional": {
                "fps": ("FLOAT", {"default": 24.0, "min": 0.1, "max": 240.0, "step": 0.01,
                                  "tooltip": "只有「帧号」格式会用到。"}),
            },
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("文本",)
    FUNCTION = "convert"
    CATEGORY = "anima/text"

    def convert(self, value, format, decimals, fps=24.0):
        v = float(value)
        if format == "小数":
            text = _trim(("%." + str(decimals) + "f") % v)
        elif format == "秒":
            text = _trim(("%." + str(decimals) + "f") % v) + " 秒"
        elif format == "整数":
            text = "%d" % int(round(v))
        elif format == "时间戳":
            text = _format_clock(v, decimals)
        elif format == "At 时间戳":
            text = "At " + _format_clock(v, decimals)
        elif format == "帧号":
            text = "%d" % int(round(v * float(fps)))
        else:
            raise ValueError("未知格式：%s（可用：%s）" % (format, "、".join(FORMATS)))
        return (text,)


NODE_CLASS_MAPPINGS = {"AnimaNumberToText": AnimaNumberToText}
NODE_DISPLAY_NAME_MAPPINGS = {"AnimaNumberToText": "Anima 数值转文本（含时间戳）"}
