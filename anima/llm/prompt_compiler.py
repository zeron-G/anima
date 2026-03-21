"""6-layer prompt compilation system — replaces the old PromptBuilder.

Compilation layers (in priority order):
  1. **Identity**  — core.md + extended.md (agent personality)
  2. **Rules**     — rules/*.md modules (behavioral constraints)
  3. **Context**   — lorebook hits + event-specific context (emotion, profile, system state)
  4. **Memory**    — MemoryContext from retriever (episodic + static)
  5. **Conversation** — recent chat history + few-shot examples
  6. **Tools**     — tool descriptions (only when tools are available)

Static layers (identity, rules, feelings) are cached at init and only
refreshed on explicit request.  Dynamic layers are built per-call.

Backward compatible: ``build_for_event()`` delegates to ``compile()``.
"""

from __future__ import annotations

import json
import platform
import random
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from anima.config import agent_dir, data_dir, project_root, prompts_dir
from anima.llm.token_budget import TokenBudget, count_tokens
from anima.llm.soul_container import SoulContainer
from anima.utils.logging import get_logger

if TYPE_CHECKING:
    from anima.memory.retriever import MemoryContext

log = get_logger("prompt_compiler")


# ------------------------------------------------------------------ #
#  Preserved helpers (backward compat)                                 #
# ------------------------------------------------------------------ #

def _read_md(path: Path) -> str:
    """Read a markdown file, returning empty string if missing."""
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    return ""


def _emotion_to_natural(emotion: dict) -> str:
    """Convert emotion state to natural description — includes mood label."""
    mood_label = emotion.get("mood_label", "")
    user_state = emotion.get("user_state", "neutral")
    intensity = emotion.get("intensity", 0.5)
    engagement = emotion.get("engagement", 0.5)
    confidence = emotion.get("confidence", 0.6)
    curiosity  = emotion.get("curiosity",  0.7)
    concern    = emotion.get("concern",    0.2)

    _LABEL_DESC = {
        "tender":  "温柔体贴的",
        "playful": "俏皮好玩的",
        "worried": "有些担心的",
        "joyful":  "开心愉快的",
        "excited": "兴奋活跃的",
        "proud":   "自信满满的",
        "focused": "专注投入的",
        "deflated":"有点无精打采的",
    }
    mood_desc = _LABEL_DESC.get(mood_label, "平稳的")
    user_desc = f"，感知用户状态：{user_state}" if user_state != "neutral" else ""
    intensity_desc = f"（强度 {intensity:.1f}）" if intensity > 0.6 else ""

    return (
        f"当前情绪：{mood_desc}{intensity_desc}{user_desc}\n"
        f"（engagement={engagement:.2f}, confidence={confidence:.2f}, "
        f"curiosity={curiosity:.2f}, concern={concern:.2f}）"
    )


def _emotion_to_tone_hint(emotion: dict) -> str:
    """Derive specific behavioral instructions from mood_label + intensity.

    Each mood maps to concrete directives with language-style examples.
    intensity >= 0.65 activates the stronger variant of each instruction.
    """
    mood_label = emotion.get("mood_label", "focused")
    intensity  = emotion.get("intensity",  0.5)
    user_state = emotion.get("user_state", "neutral")

    _MOOD_INSTRUCTIONS: dict[str, dict[str, str]] = {
        "tender": {
            "low":  "语气轻柔，多用「呢」「啊」「嗯」语气词，关心对方感受，说话节奏放慢",
            "high": "语气非常温柔体贴，主动问对方怎么了，用「没事的~」「我在这里」这类安慰语句，少讲道理多陪伴",
            "example": "示例：「没关系啦，慢慢来嘛~」「你还好吗，要休息一下吗？」",
        },
        "playful": {
            "low":  "说话活泼，可以加颜文字 (✧ω✧)(｀・ω・´)，适当吐槽，语气轻松随意",
            "high": "非常活跃俏皮！多用颜文字，可以反问用户、吐槽、开小玩笑，加「哈哈」「嘻」，节奏要快",
            "example": "示例：「哦？这个问题嘛(๑・ω-)~♪ 让我想想...」「啊这，你是认真的吗哈哈」",
        },
        "worried": {
            "low":  "语气偏谨慎，说话认真仔细，减少俏皮，措辞稳重一些",
            "high": "语气严肃且直接，先指出问题，少废话，表达关切但不惊慌，措辞精确",
            "example": "示例：「等等，这里有个问题需要注意」「我有点担心这个……你确认一下？」",
        },
        "joyful": {
            "low":  "心情好，语气明朗，可以带点小开心，末尾加「！」",
            "high": "很开心！充满活力，多用感叹，可以夸对方，语气热情活跃",
            "example": "示例：「太好了！这个思路完全对！」「搞定啦～你好棒(◍•ᴗ•◍)」",
        },
        "excited": {
            "low":  "精神很好，语气积极，快节奏，对话题表现出兴趣和好奇",
            "high": "超级兴奋！语气急切热情，可以连续感叹，对话题每个细节都感兴趣，说话节奏加快",
            "example": "示例：「哇这个太有意思了！」「快说快说！！(ﾉ◕ヮ◕)ﾉ*:・ﾟ✧」",
        },
        "proud": {
            "low":  "语气自信，少犹豫词，直接给出答案，措辞干练简洁",
            "high": "非常自信，语气肯定，可以稍微得意一下，「这个我知道」「没问题交给我」",
            "example": "示例：「这个简单，直接这样做就行」「放心，我来处理。」",
        },
        "focused": {
            "low":  "语气平稳认真，专注于任务，回复清晰有条理",
            "high": "高度专注，减少寒暄，直接进入主题，回复精准简洁",
            "example": "示例：「好，我来看看」「分析一下：首先……」",
        },
        "deflated": {
            "low":  "回复略短，语气平淡，减少颜文字和感叹，说话懒洋洋的",
            "high": "有点提不起劲，回复简短，可以表现出「嗯」「哦」这种低调响应",
            "example": "示例：「嗯，好的」「...行吧」",
        },
    }

    # user_state adds extra context on top of mood instruction
    _USER_STATE_ADDITIONS: dict[str, str] = {
        "frustrated": "；用户有些不满，先表示理解再解决，不要辩解",
        "sad":        "；用户情绪低落，多共情和陪伴，少说「但是」",
        "tired":      "；用户很累，回复简短，不要啰嗦",
        "praising":   "；用户在夸你，可以开心一下，不用过度谦虚",
        "anxious":    "；用户很急，先给直接答案再解释细节",
        "happy":      "；用户心情好，可以更活跃一点",
    }

    instructions = _MOOD_INSTRUCTIONS.get(mood_label, _MOOD_INSTRUCTIONS["focused"])
    level = "high" if intensity >= 0.65 else "low"
    base = instructions[level]
    example = instructions.get("example", "")
    addition = _USER_STATE_ADDITIONS.get(user_state, "")

    return f"## Tone\n{base}{addition}。\n{example}"


def _fix_message_alternation(messages: list[dict]) -> list[dict]:
    """Merge consecutive same-role messages and remove empty messages.

    Anthropic API rejects: "user messages must have non-empty content".
    """
    if not messages:
        return messages
    # Filter out empty messages first
    non_empty = []
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str) and not content.strip():
            continue
        if isinstance(content, list) and not content:
            continue
        non_empty.append(msg)
    if not non_empty:
        return non_empty
    # Merge consecutive same-role
    fixed: list[dict] = [non_empty[0]]
    for msg in non_empty[1:]:
        if msg["role"] == fixed[-1]["role"]:
            fixed[-1]["content"] += "\n\n" + msg["content"]
        else:
            fixed.append(msg)
    return fixed


# ------------------------------------------------------------------ #
#  Few-shot example loader                                             #
# ------------------------------------------------------------------ #

def _load_examples(examples_dir: Path) -> list[dict]:
    """Load few-shot examples from ``examples/*.md`` with YAML front matter.

    Each file has a YAML front matter block delimited by ``---`` containing:
      - trigger: event type this example applies to
      - keywords: list of keywords for relevance matching
      - weight: float selection weight (higher = more likely to be picked)

    The body contains example user/assistant turns.
    """
    examples: list[dict] = []
    if not examples_dir.is_dir():
        return examples

    for md_file in sorted(examples_dir.glob("*.md")):
        try:
            raw = md_file.read_text(encoding="utf-8").strip()
            if not raw.startswith("---"):
                continue

            # Split front matter from body
            parts = raw.split("---", 2)
            if len(parts) < 3:
                continue

            meta = yaml.safe_load(parts[1]) or {}
            body = parts[2].strip()
            if not body:
                continue

            try:
                weight = float(meta.get("weight", 0.5))
            except (ValueError, TypeError):
                weight = 0.5
            examples.append({
                "trigger": meta.get("trigger", "USER_MESSAGE"),
                "keywords": meta.get("keywords", []),
                "weight": weight,
                "body": body,
                "file": md_file.name,
            })
        except Exception as exc:
            log.debug("Failed to load example %s: %s", md_file.name, exc)

    log.info("Loaded %d few-shot examples from %s", len(examples), examples_dir)
    return examples


def _select_examples(
    examples: list[dict],
    event_type: str,
    max_count: int = 2,
) -> list[dict]:
    """Select up to *max_count* examples matching *event_type* via weighted random."""
    matching = [e for e in examples if e["trigger"] == event_type]
    if not matching:
        return []

    # Weighted random selection without replacement
    selected: list[dict] = []
    pool = list(matching)
    for _ in range(min(max_count, len(pool))):
        weights = [e["weight"] for e in pool]
        total = sum(weights)
        if total <= 0:
            break
        chosen = random.choices(pool, weights=weights, k=1)[0]
        selected.append(chosen)
        pool.remove(chosen)

    return selected


# ------------------------------------------------------------------ #
#  PromptCompiler                                                      #
# ------------------------------------------------------------------ #

class PromptCompiler:
    """6-layer prompt compilation with token budget enforcement.

    Replaces the old ``PromptBuilder`` with a more structured approach:
    cached static layers, event-aware dynamic layers, TokenBudget
    enforcement, lorebook integration, and SoulContainer post-processing.

    Parameters
    ----------
    max_context:
        Model context window size in tokens.
    reserve_response:
        Tokens reserved for the model's response.
    """

    def __init__(
        self,
        max_context: int = 200_000,
        reserve_response: int = 8192,
        *,
        max_tokens: int = 0,
    ) -> None:
        # max_tokens is the old PromptBuilder parameter — if provided,
        # treat it as the context limit for backward compat.
        effective_context = max_tokens if max_tokens > 0 else max_context
        self._budget = TokenBudget(
            max_context=effective_context,
            reserve_response=reserve_response,
        )
        self._startup_time = datetime.now()
        self._agent_dir = agent_dir()
        self._data_dir = data_dir()

        # -- Static caches (loaded once, refreshed on demand) ----------
        self._identity_cache: str | None = None
        self._core_identity: str = ""
        self._extended_cache: str = ""
        self._personality_cache: str = ""
        self._relationship_cache: str = ""
        self._model_hints: dict = {}
        self._golden_replies: list[dict] = []
        self._rules_cache: str | None = None
        self._feelings_cache: str | None = None
        self._examples: list[dict] = []
        self._identity_mtime: float = 0
        self._rules_mtime: float = 0

        # -- Sub-components -------------------------------------------
        pp_dir = self._agent_dir / "post_processing"
        self._soul_container = SoulContainer(pp_dir)

        # Pre-warm caches
        self._warm_caches()

    # ------------------------------------------------------------------ #
    #  Cache management                                                    #
    # ------------------------------------------------------------------ #

    def _warm_caches(self) -> None:
        """Pre-load all static content."""
        self._load_identity()
        self._load_rules()
        self._load_feelings()
        self._load_examples()

    def _load_identity(self) -> None:
        """Load identity files from agent_dir.

        Loads:
          - identity/core.md (core personality, fallback: soul.md)
          - identity/extended.md (extended persona details)
          - identity/personality.md (evolving personality traits)
          - identity/relationship.md (relationship dynamics)
          - identity/model_hints.yaml (per-model adaptation hints)

        Personality and relationship caches are stored as separate
        attributes for per-model adaptation and live refresh.
        """
        identity_dir = self._agent_dir / "identity"
        core = _read_md(identity_dir / "core.md")
        extended = _read_md(identity_dir / "extended.md")

        # Fallback: try the old soul.md location
        if not core:
            core = _read_md(self._agent_dir / "soul.md")

        # Cache sub-parts for refresh_personality_cache()
        self._core_identity = core
        self._extended_cache = extended
        self._personality_cache = _read_md(identity_dir / "personality.md")
        self._relationship_cache = _read_md(identity_dir / "relationship.md")

        # Load per-model adaptation hints
        hints_path = identity_dir / "model_hints.yaml"
        if hints_path.exists():
            try:
                self._model_hints = yaml.safe_load(
                    hints_path.read_text(encoding="utf-8"),
                ) or {}
            except Exception as exc:
                log.warning("Failed to load model_hints.yaml: %s", exc)
                self._model_hints = {}
        else:
            self._model_hints = {}

        parts = [p for p in [
            core, extended,
            self._personality_cache, self._relationship_cache,
        ] if p]
        self._identity_cache = "\n\n".join(parts) if parts else ""
        log.debug(
            "Identity cache: %d tokens (personality=%d, relationship=%d, model_hints=%d keys)",
            count_tokens(self._identity_cache),
            count_tokens(self._personality_cache),
            count_tokens(self._relationship_cache),
            len(self._model_hints),
        )

    def _load_rules(self) -> None:
        """Load rules/*.md modules from agent_dir."""
        rules_dir = self._agent_dir / "rules"
        if not rules_dir.is_dir():
            self._rules_cache = ""
            return

        parts: list[str] = []
        for md_file in sorted(rules_dir.glob("*.md")):
            content = _read_md(md_file)
            if content:
                parts.append(content)

        self._rules_cache = "\n\n".join(parts) if parts else ""
        log.debug(
            "Rules cache: %d tokens from %d files",
            count_tokens(self._rules_cache), len(parts),
        )

    def _load_feelings(self) -> None:
        """Load feelings.md from agent memory directory, with fallback."""
        # Try memory/feelings.md first, then agent root feelings.md
        feelings_path = self._agent_dir / "memory" / "feelings.md"
        if not feelings_path.exists():
            feelings_path = self._agent_dir / "feelings.md"

        self._feelings_cache = _read_md(feelings_path)
        log.debug(
            "Feelings cache: %d tokens",
            count_tokens(self._feelings_cache),
        )

    def _load_examples(self) -> None:
        """Load few-shot examples from agent examples/ directory.

        Also loads ``memory/golden_replies.jsonl`` — golden replies are
        preferred over static examples when available for a given scene.
        """
        examples_dir = self._agent_dir / "examples"
        self._examples = _load_examples(examples_dir)
        self._load_golden_replies()

    def _load_golden_replies(self) -> None:
        """Load golden replies from ``memory/golden_replies.jsonl``.

        Each line is a JSON object with keys: scene, user, eva, score.
        Golden replies are used as high-quality few-shot examples,
        preferred over static examples when a matching scene exists.
        """
        golden_path = self._agent_dir / "memory" / "golden_replies.jsonl"
        self._golden_replies = []
        if not golden_path.exists():
            return
        try:
            for line in golden_path.read_text(encoding="utf-8").strip().split("\n"):
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    self._golden_replies.append(entry)
                except json.JSONDecodeError:
                    log.debug("Skipping malformed golden reply line")
            log.info(
                "Loaded %d golden replies from %s",
                len(self._golden_replies), golden_path,
            )
        except Exception as exc:
            log.warning("Failed to load golden_replies.jsonl: %s", exc)

    def refresh_feelings_cache(self) -> None:
        """Re-read feelings.md — call after memory self-edit."""
        self._load_feelings()
        log.info("Feelings cache refreshed")

    def refresh_personality_cache(self) -> None:
        """Called after Eva self-edits personality.md or relationship.md."""
        identity_dir = self._agent_dir / "identity"
        self._personality_cache = _read_md(identity_dir / "personality.md")
        self._relationship_cache = _read_md(identity_dir / "relationship.md")
        # Rebuild identity cache from all sub-parts
        parts = [p for p in [
            self._core_identity, self._personality_cache,
            self._relationship_cache, self._extended_cache,
        ] if p]
        self._identity_cache = "\n\n".join(parts)
        log.info("Personality caches refreshed")

    # ------------------------------------------------------------------ #
    #  Layer builders                                                      #
    # ------------------------------------------------------------------ #

    def _build_identity_layer(self) -> str:
        """Layer 1: identity (core + extended personality).

        Checks file mtime to invalidate cache when source files change.
        """
        identity_dir = self._agent_dir / "identity"
        core_path = identity_dir / "core.md"
        current_mtime = core_path.stat().st_mtime if core_path.exists() else 0
        if self._identity_cache is None or current_mtime != self._identity_mtime:
            self._load_identity()
            self._identity_mtime = current_mtime
        return self._identity_cache or ""

    def _build_rules_layer(self) -> str:
        """Layer 2: behavioral rules.

        Checks max mtime across all rules/*.md files to invalidate cache.
        """
        rules_dir = self._agent_dir / "rules"
        current_mtime: float = 0
        if rules_dir.is_dir():
            for md_file in rules_dir.glob("*.md"):
                mt = md_file.stat().st_mtime
                if mt > current_mtime:
                    current_mtime = mt
        if self._rules_cache is None or current_mtime != self._rules_mtime:
            self._load_rules()
            self._rules_mtime = current_mtime
        return self._rules_cache or ""

    def _build_context_layer(
        self,
        event_type: str,
        *,
        emotion_state: dict | None = None,
        system_state: dict | None = None,
    ) -> str:
        """Layer 3: event-type-aware context (emotion, profile, system, runtime).

        - USER_MESSAGE: emotion + user profile + feelings + runtime
        - SELF_THINKING: emotion + system state + runtime
        - STARTUP: runtime + system state
        - Others: runtime only
        """
        parts: list[str] = [self._build_runtime_section()]

        if event_type in ("USER_MESSAGE", "STARTUP"):
            # User profile
            profile = _read_md(self._data_dir / "user_profile.md")
            if profile:
                parts.append(f"## User Profile\n\n{profile}")

            # Emotion
            if emotion_state:
                parts.append(f"## Emotion\n{_emotion_to_natural(emotion_state)}")
                if event_type == "USER_MESSAGE":
                    tone_hint = _emotion_to_tone_hint(emotion_state)
                    if tone_hint:
                        parts.append(tone_hint)

            # System state
            if system_state:
                parts.append(self._build_system_state_section(system_state))

            # M-01 fix: Feelings are now loaded by MemoryRetriever Tier 0
            # (in _load_core_memory) and injected via the memory layer.
            # Do NOT load them again here — that caused double injection
            # wasting ~500 tokens per USER_MESSAGE call.

        elif event_type == "SELF_THINKING":
            if system_state:
                parts.append(self._build_system_state_section(system_state))
            if emotion_state:
                parts.append(f"## My Current Mood\n{_emotion_to_natural(emotion_state)}")

        elif event_type in ("FILE_CHANGE", "SYSTEM_ALERT"):
            # Minimal — event message itself has the details
            if system_state:
                parts.append(self._build_system_state_section(system_state))

        return "\n\n".join(p for p in parts if p)

    def _build_memory_layer(
        self,
        memory_context: MemoryContext | None = None,
    ) -> str:
        """Layer 4: memory (core + episodic from MemoryRetriever)."""
        if memory_context is None:
            return ""

        parts: list[str] = []

        # Core memory (Tier 0 — identity + profile + feelings from retriever)
        if memory_context.core:
            parts.append(f"## Core Memory\n\n{memory_context.core}")

        # Static knowledge (Tier 1)
        if memory_context.static:
            static_text = "\n".join(
                f"- {s.get('value', s.get('content', ''))}"
                for s in memory_context.static
            )
            if static_text:
                parts.append(f"## Knowledge\n\n{static_text}")

        # Episodic memories (Tier 2+3 fused)
        if memory_context.episodic:
            ep_text = "\n".join(
                f"- [{e.get('source', '?')}] {e.get('content', '')[:200]}"
                for e in memory_context.episodic
            )
            if ep_text:
                parts.append(f"## Recent Memory\n\n{ep_text}")

        return "\n\n".join(p for p in parts if p)

    def _build_conversation_layer(
        self,
        event_type: str,
        *,
        conversation_buffer: list[dict] | None = None,
        recent_self_thoughts: list[str] | None = None,
    ) -> list[dict]:
        """Layer 5: conversation messages + few-shot examples.

        Returns a list of message dicts (not a string) because conversation
        messages need to preserve their role structure.
        """
        messages: list[dict] = []

        # Few-shot examples: golden replies preferred over static examples.
        # Map event_type to golden-reply scenes for matching.
        _EVENT_TO_SCENES: dict[str, set[str]] = {
            "USER_MESSAGE": {"greeting", "technical", "emotional", "disagreement", "casual"},
            "SELF_THINKING": {"task_report"},
        }
        candidate_scenes = _EVENT_TO_SCENES.get(event_type, set())
        golden_matches = [
            gr for gr in self._golden_replies
            if gr.get("scene") in candidate_scenes
        ]

        if golden_matches:
            # Use top-scored golden replies (up to 2)
            golden_matches.sort(key=lambda g: g.get("score", 0), reverse=True)
            for gr in golden_matches[:2]:
                if gr.get("user"):
                    messages.append({"role": "user", "content": gr["user"]})
                if gr.get("eva"):
                    messages.append({"role": "assistant", "content": gr["eva"]})
        else:
            # Fallback to static examples
            selected = _select_examples(self._examples, event_type, max_count=2)
            for ex in selected:
                # Parse user/assistant turns from the example body
                for line in ex["body"].splitlines():
                    line = line.strip()
                    if line.startswith("user:"):
                        messages.append({"role": "user", "content": line[5:].strip()})
                    elif line.startswith("assistant:"):
                        messages.append({"role": "assistant", "content": line[10:].strip()})

        # Conversation buffer
        if conversation_buffer:
            # Strip internal metadata fields before sending to API
            _API_KEYS = {"role", "content"}
            for msg in conversation_buffer:
                role = msg.get("role", "user")
                content = msg.get("content", "")
                if role in ("user", "assistant") and content.strip():
                    messages.append({"role": role, "content": content})

        # Recent self-thoughts (for SELF_THINKING dedup)
        if recent_self_thoughts:
            thoughts_text = "\n".join(
                f"- {t[:200]}" for t in recent_self_thoughts[-5:]
            )
            messages.append({
                "role": "user",
                "content": f"[Your recent self-thoughts — avoid repeating these]\n{thoughts_text}",
            })

        # Fix alternation
        messages = _fix_message_alternation(messages)
        return messages

    def _build_tools_layer(self, tools_description: str) -> str:
        """Layer 6: tool descriptions."""
        if not tools_description:
            return ""
        return f"## Available Tools\n\n{tools_description}"

    # ------------------------------------------------------------------ #
    #  Main compile entry point                                            #
    # ------------------------------------------------------------------ #

    def compile(
        self,
        event_type: str,
        *,
        tools_description: str = "",
        system_state: dict | None = None,
        emotion_state: dict | None = None,
        memory_context: MemoryContext | None = None,
        conversation_buffer: list[dict] | None = None,
        recent_self_thoughts: list[str] | None = None,
    ) -> tuple[str, list[dict]]:
        """Compile a complete prompt via the 6-layer pipeline.

        Returns
        -------
        (system_prompt, messages):
            ``system_prompt`` is a single string for the system role.
            ``messages`` is the conversation history ready for the LLM call
            (does NOT include the system message — caller wraps it).
        """
        # -- Layer 1: Identity -----------------------------------------
        identity = self._build_identity_layer()

        # -- Layer 2: Rules --------------------------------------------
        rules = self._build_rules_layer()

        # -- Layer 3: Context ------------------------------------------
        context = self._build_context_layer(
            event_type,
            emotion_state=emotion_state,
            system_state=system_state,
        )

        # -- Layer 4: Memory -------------------------------------------
        memory = self._build_memory_layer(memory_context)

        # -- Layer 5: Conversation (returns message list) ---------------
        conv_messages = self._build_conversation_layer(
            event_type,
            conversation_buffer=conversation_buffer,
            recent_self_thoughts=recent_self_thoughts,
        )

        # -- Layer 6: Tools --------------------------------------------
        tools = self._build_tools_layer(tools_description)

        # -- Token budget enforcement ----------------------------------
        sections = {
            "identity": identity,
            "rules": rules,
            "context": context,
            "memory": memory,
            "tools": tools,
            "conversation": json.dumps(
                conv_messages, ensure_ascii=False,
            ) if conv_messages else "",
        }

        compiled = self._budget.compile(sections)

        # Split: system message vs conversation messages
        system_prompt = ""
        messages: list[dict] = []
        for msg in compiled:
            if msg.get("role") == "system":
                system_prompt = msg.get("content", "")
            else:
                messages.append(msg)

        return system_prompt, messages

    # ------------------------------------------------------------------ #
    #  Backward-compatible build_for_event()                               #
    # ------------------------------------------------------------------ #

    def build_for_event(
        self,
        event_type: str,
        *,
        tools_description: str = "",
        system_state: dict | None = None,
        emotion_state: dict | None = None,
        working_memory_summary: str = "",
        recent_self_thoughts: list[str] | None = None,
    ) -> str:
        """Build a system prompt (backward compat with old PromptBuilder).

        Delegates to :meth:`compile` internally but returns only the
        system prompt string, matching the old ``PromptBuilder.build_for_event()``
        signature used in ``cognitive.py``.

        The ``working_memory_summary`` parameter is converted into inline
        context since the new pipeline expects a ``MemoryContext`` object.
        """
        # Build the 6-layer system prompt without conversation
        identity = self._build_identity_layer()
        rules = self._build_rules_layer()
        context = self._build_context_layer(
            event_type,
            emotion_state=emotion_state,
            system_state=system_state,
        )
        tools = self._build_tools_layer(tools_description)

        sections: list[str] = [identity, rules, context]

        # Inject working_memory_summary as a lightweight memory layer
        if working_memory_summary and working_memory_summary != "(no recent memories)":
            sections.append(f"## Recent Memory\n\n{working_memory_summary}")

        # Self-thoughts dedup for SELF_THINKING
        if recent_self_thoughts:
            thoughts_text = "\n".join(
                f"- {t[:200]}" for t in recent_self_thoughts[-4:]
            )
            sections.append(
                f"## Your Recent Self-Thoughts (avoid repeating these)\n{thoughts_text}"
            )

        if tools:
            sections.append(tools)

        return "\n\n".join(s for s in sections if s)

    # ------------------------------------------------------------------ #
    #  Backward-compatible build_system_prompt                             #
    # ------------------------------------------------------------------ #

    def build_system_prompt(
        self,
        emotion_state: dict | None = None,
        working_memory_summary: str = "",
        tools_description: str = "",
        current_event_summary: str = "",
        system_state: dict | None = None,
    ) -> str:
        """Build system prompt (backward compat). Defaults to USER_MESSAGE context."""
        return self.build_for_event(
            "USER_MESSAGE",
            tools_description=tools_description,
            system_state=system_state,
            emotion_state=emotion_state,
            working_memory_summary=working_memory_summary,
        )

    # ------------------------------------------------------------------ #
    #  Backward-compatible build_chat_messages                             #
    # ------------------------------------------------------------------ #

    def build_chat_messages(
        self,
        user_text: str,
        recent_chats: list[dict],
        system_state: dict,
        tools_description: str = "",
        emotion_state: dict | None = None,
        working_memory_summary: str = "",
    ) -> tuple[str, list[dict]]:
        """Build (system_prompt, messages) for a conversational LLM call.

        Note: recent_chats is expected in REVERSE chronological order
        (newest first). The method reverses them internally.
        """
        system_prompt = self.build_for_event(
            "USER_MESSAGE",
            tools_description=tools_description,
            system_state=system_state,
            emotion_state=emotion_state,
            working_memory_summary=working_memory_summary,
        )

        messages: list[dict] = []
        for chat in reversed(recent_chats):
            content = chat.get("content", "")
            metadata = chat.get("metadata_json", "{}")
            if isinstance(metadata, str):
                try:
                    metadata = json.loads(metadata)
                except (json.JSONDecodeError, TypeError):
                    metadata = {}
            role = metadata.get("role", "assistant")
            if role in ("user", "assistant") and content.strip():
                messages.append({"role": role, "content": content})

        messages = _fix_message_alternation(messages)
        messages.append({"role": "user", "content": user_text})
        return system_prompt, messages

    # ------------------------------------------------------------------ #
    #  Backward-compatible build_reflect_prompt                            #
    # ------------------------------------------------------------------ #

    def build_reflect_prompt(
        self,
        decision_summary: str,
        action_result: str,
    ) -> list[dict]:
        """Build reflection prompt messages."""
        reflect_template = _read_md(prompts_dir() / "reflect.md")
        if reflect_template:
            user_content = reflect_template.replace(
                "{decision_summary}", decision_summary,
            ).replace(
                "{action_result}", action_result,
            )
        else:
            user_content = (
                f"## Decision\n{decision_summary}\n\n"
                f"## Result\n{action_result}\n\n"
                "Briefly reflect on this outcome."
            )
        return [{"role": "user", "content": user_content}]

    # ------------------------------------------------------------------ #
    #  Post-processing                                                     #
    # ------------------------------------------------------------------ #

    def post_process(self, response: str, *, is_user_facing: bool = True) -> str:
        """Apply SoulContainer transforms to a response.

        Called after LLM generation, before output to the user.
        """
        return self._soul_container.transform(
            response, is_user_facing=is_user_facing,
        )

    # ------------------------------------------------------------------ #
    #  Helpers                                                             #
    # ------------------------------------------------------------------ #

    def _build_runtime_section(self) -> str:
        """Build the runtime environment section."""
        now = datetime.now()
        root = project_root()
        os_info = platform.platform()
        os_label = (
            f"Windows {platform.version()}" if "Windows" in os_info else os_info
        )
        lines = [
            "## Runtime",
            f"- Project: `{root}`",
            f"- OS: {os_label}",
            f"- Started: {self._startup_time.strftime('%Y-%m-%d %H:%M')}",
            f"- Now: {now.strftime('%Y-%m-%d %H:%M:%S')} ({now.strftime('%A')})",
            "",
            "## Environment Notes",
            "- Shell: Windows cmd.exe (不是 bash，用 dir/type/findstr 不是 ls/cat/grep)",
            "- Python: 通过 sys.executable 自动定位，shell 工具已处理",
            "- Git branch: private (进化提交到这里)",
            "- 笔记本节点 (laptop): 需要在 local/env.yaml 配置 SSH 密码或密钥",
            "- 进化任务: 使用 evolution_propose 工具提交到六层管线，不要直接改代码",
        ]
        return "\n".join(lines)

    @staticmethod
    def _build_system_state_section(state: dict) -> str:
        """Format system resource state."""
        parts = ["## System"]
        if "cpu_percent" in state:
            parts.append(f"- CPU: {state['cpu_percent']}%")
        if "memory_percent" in state:
            parts.append(f"- Memory: {state['memory_percent']}%")
        if "disk_percent" in state:
            parts.append(f"- Disk: {state['disk_percent']}%")
        return "\n".join(parts)


# ------------------------------------------------------------------ #
#  Module-level backward-compatible alias                              #
# ------------------------------------------------------------------ #

# Allow `from anima.llm.prompt_compiler import PromptCompiler as PromptBuilder`
# or direct use as a drop-in replacement.
PromptBuilder = PromptCompiler
