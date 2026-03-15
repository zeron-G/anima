# Decision Prompt

基于以下上下文，决定下一步行动。

## 当前感知
{perception_summary}

## 工作记忆
{working_memory_summary}

## 可用工具
{tools_description}

## 要求

分析当前情况，选择以下行动之一：
1. **respond** — 向用户输出消息
2. **tool_call** — 调用一个工具
3. **noop** — 不做任何事（当前情况不需要行动）

以 JSON 格式回复：
```json
{
  "action": "respond | tool_call | noop",
  "reasoning": "你的推理过程",
  "content": "回复内容（respond 时）",
  "tool_name": "工具名（tool_call 时）",
  "tool_args": {}
}
```
