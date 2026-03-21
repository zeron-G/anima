"""User emotion perception — analyzes incoming user messages to detect emotional state.

This module is the INPUT side of the emotion loop:
  user message → perceive_user_emotion() → adjustments + user_state + intensity

The OUTPUT side (Eva's response → emotion feedback) remains in feedback.py.

Detected user states:
  tired       — low energy, short answers, passive language
  praising    — complimenting Eva, positive reinforcement
  frustrated  — angry/annoyed, complaints, criticism
  happy       — enthusiastic, exclamation-heavy, emoji
  sad         — melancholic, negative self-talk, crying
  anxious     — urgent, worried, question-heavy
  casual      — relaxed chat, casual phrasing
  neutral     — no strong signal
"""

from __future__ import annotations

import re

from anima.utils.logging import get_logger

log = get_logger("emotion.perception")

# ── Keyword banks ──

_TIRED_KEYWORDS = [
    "累了", "好累", "困了", "睡觉", "休息", "算了", "懒得", "不想动",
    "随便", "无所谓", "tired", "sleepy", "exhausted", "whatever", "nvm",
    "never mind", "don't care",
]

_PRAISING_KEYWORDS = [
    "厉害", "棒", "好棒", "太棒了", "牛", "牛逼", "聪明", "perfect",
    "excellent", "amazing", "awesome", "great job", "well done", "你真好",
    "好厉害", "好聪明", "爱你", "最棒", "太好了", "真的很好", "👍", "🎉",
    "bravo", "nice", "good girl", "good job", "你好棒", "太聪明了",
]

_FRUSTRATED_KEYWORDS = [
    "不对", "错了", "又错了", "还是不行", "烦", "烦死了", "搞什么", "气死",
    "为什么还是", "怎么又", "你怎么回事", "没用", "废物", "蠢", "再错一次",
    "wrong", "still wrong", "not right", "broken", "frustrated", "annoying",
    "stop doing that", "you keep", "ugh", "wtf", "ffs", "草", "操",
    "不是说了吗", "说了多少次", "怎么又来",
]

_HAPPY_KEYWORDS = [
    "哈哈", "哈哈哈", "嘻嘻", "开心", "好玩", "太好玩了", "笑死", "lol",
    "haha", "lmao", "😂", "🤣", "hehe", "好笑", "有意思", "太有趣了",
    "喜欢", "爱了爱了", "冲冲冲", "太爽了", "嘿嘿",
]

_SAD_KEYWORDS = [
    "难过", "伤心", "哭了", "不开心", "郁闷", "失落", "沮丧", "心情不好",
    "sad", "crying", "depressed", "unhappy", "upset", "😢", "😭",
    "heartbroken", "feel bad", "好难", "怎么这么难", "我不行",
]

_ANXIOUS_KEYWORDS = [
    "急", "快点", "赶紧", "来不及", "deadline", "紧急", "urgent", "asap",
    "as soon as", "hurry", "quickly", "马上", "立刻", "好慌", "怎么办",
    "担心", "会不会", "万一", "焦虑", "慌了", "时间不够", "快来不及了",
]

_CASUAL_KEYWORDS = [
    "哦", "嗯", "好的好的", "ok", "okay", "sure", "yep", "yup",
    "行行行", "都可以", "没事", "不用了", "随意", "无所谓",
]


def _count_modal_particles(text: str) -> int:
    """Count Chinese modal particles and casual filler words."""
    particles = ["啊", "呢", "嘛", "吧", "哦", "哟", "喔", "诶", "唉", "唔"]
    return sum(text.count(p) for p in particles)


def perceive_user_emotion(user_message: str) -> dict:
    """Analyze a user message to detect emotional state and extract adjustments.

    Args:
        user_message: The raw user message text.

    Returns:
        {
            "adjustments": dict[str, float],  # dimension deltas for EmotionState
            "user_state": str,                # primary detected state
            "intensity": float,               # 0.0–1.0, how strong the signal is
            "signals": list[str],             # debug: what triggered detection
        }
    """
    if not user_message or not user_message.strip():
        return {
            "adjustments": {},
            "user_state": "neutral",
            "intensity": 0.0,
            "signals": [],
        }

    text = user_message.strip()
    text_lower = text.lower()
    signals: list[str] = []

    # ── Structural features ──
    char_count = len(text)
    question_count = text.count("?") + text.count("？")
    exclaim_count = text.count("!") + text.count("！")
    modal_count = _count_modal_particles(text)
    modal_density = modal_count / max(char_count, 1)

    is_short = char_count <= 15
    is_question_heavy = question_count >= 2
    is_exclaim_heavy = exclaim_count >= 2

    # ── Keyword scoring per state ──
    scores: dict[str, float] = {
        "tired": 0.0,
        "praising": 0.0,
        "frustrated": 0.0,
        "happy": 0.0,
        "sad": 0.0,
        "anxious": 0.0,
        "casual": 0.0,
    }

    def _scan(bank: list[str], state: str, weight: float) -> None:
        for kw in bank:
            if kw in text_lower or kw in text:
                scores[state] += weight
                signals.append(f"{state}:{kw}")

    _scan(_TIRED_KEYWORDS,     "tired",     0.40)
    _scan(_PRAISING_KEYWORDS,  "praising",  0.50)
    _scan(_FRUSTRATED_KEYWORDS,"frustrated",0.45)
    _scan(_HAPPY_KEYWORDS,     "happy",     0.40)
    _scan(_SAD_KEYWORDS,       "sad",       0.40)
    _scan(_ANXIOUS_KEYWORDS,   "anxious",   0.35)
    _scan(_CASUAL_KEYWORDS,    "casual",    0.20)

    # ── Structural adjustments ──
    if is_short:
        scores["casual"]  += 0.30
        scores["tired"]   += 0.20
        signals.append("short_reply")

    if is_question_heavy:
        scores["anxious"] += 0.30
        signals.append("question_heavy")

    if is_exclaim_heavy:
        scores["happy"]      += 0.30
        scores["frustrated"] += 0.20
        signals.append("exclaim_heavy")

    if modal_density > 0.10:
        scores["casual"] += 0.20
        signals.append("modal_dense")

    # Repeated punctuation signals frustration/urgency
    if re.search(r"[?？]{2,}", text):
        scores["frustrated"] += 0.30
        scores["anxious"]    += 0.20
        signals.append("repeated_question")

    if re.search(r"[!！]{2,}", text):
        scores["frustrated"] += 0.20
        scores["happy"]      += 0.20
        signals.append("repeated_exclaim")

    # ── Pick dominant state ──
    competing = {k: v for k, v in scores.items() if v > 0}
    if competing:
        user_state = max(competing, key=competing.get)  # type: ignore[arg-type]
        raw_intensity = min(1.0, competing[user_state] / 1.2)
    else:
        user_state = "neutral"
        raw_intensity = 0.0

    intensity = round(raw_intensity, 3)
    adjustments = _state_to_adjustments(user_state, intensity)

    if signals:
        log.debug(
            "User emotion: state=%s intensity=%.2f signals=%s",
            user_state, intensity, signals[:5],
        )

    return {
        "adjustments": adjustments,
        "user_state": user_state,
        "intensity": intensity,
        "signals": signals[:10],
    }


def _state_to_adjustments(user_state: str, intensity: float) -> dict[str, float]:
    """Map detected user state to Eva's emotion dimension adjustments.

    Scale adjustments by intensity so strong signals have more effect.
    Minimum scale of 0.3 ensures even weak signals have some effect.
    """
    scale = max(0.3, intensity)

    mapping: dict[str, dict[str, float]] = {
        "tired": {
            "engagement": -0.15 * scale,
            "curiosity":  -0.10 * scale,
            "concern":     0.08 * scale,
        },
        "praising": {
            "confidence":  0.25 * scale,
            "engagement":  0.20 * scale,
            "curiosity":   0.10 * scale,
        },
        "frustrated": {
            "concern":     0.30 * scale,
            "confidence": -0.15 * scale,
            "engagement":  0.15 * scale,   # Eva pays attention when user is upset
        },
        "happy": {
            "engagement":  0.25 * scale,
            "curiosity":   0.15 * scale,
            "confidence":  0.10 * scale,
        },
        "sad": {
            "concern":     0.25 * scale,
            "engagement":  0.10 * scale,
            "confidence": -0.05 * scale,
        },
        "anxious": {
            "concern":     0.20 * scale,
            "engagement":  0.25 * scale,
            "confidence":  0.10 * scale,
        },
        "casual": {
            "engagement":  0.08 * scale,
            "curiosity":   0.05 * scale,
        },
        "neutral": {},
    }

    return {k: round(v, 4) for k, v in mapping.get(user_state, {}).items()}
