# Support 开发验收用例

这里的 `cases.jsonl` 只是 25 条开发回归测试，不是运行时依赖，也不是需要建设管理
平台的“Golden Set”。它用于防止问题路由、完整文档交付、后续问答、安全拦截和文档
切换在代码修改后退化。

真实使用中发现有代表性的失败问题时，可以经过人工确认后替换或增加案例；总量保持小而
有效，不追求固定的 200 条配额，也不保存未脱敏的用户身份、账号、密码或访问令牌。

运行方式：

```bash
cd backend
../.venv/bin/pytest -q -o addopts="" tests/test_support_evaluation.py
```
