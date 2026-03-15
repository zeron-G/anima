# Reflection Prompt

反思刚才的决策和执行结果。

## 决策
{decision_summary}

## 执行结果
{action_result}

## 要求

简要评估：
1. 决策是否合理？
2. 结果是否符合预期？
3. 有什么可以改进的？
4. 是否需要更新对用户偏好的理解？

以 JSON 格式回复：
```json
{
  "outcome": "success | partial | failure",
  "lesson": "学到了什么",
  "importance": 0.0-1.0,
  "update_user_profile": null | "更新内容"
}
```
