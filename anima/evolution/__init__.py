"""ANIMA Evolution Engine — six-layer autonomous evolution pipeline.

Layers:
  1. Proposal   — generate structured evolution proposals
  2. Consensus   — distributed voting (≥50% alive nodes)
  3. Implementor — isolated development in git worktree
  4. Tester      — three-level test suite (static, unit, sandbox)
  5. Reviewer    — code review + doc check + env audit
  6. Deployer    — merge, push, hot-reload, rollback

Cross-cutting:
  - Memory      — experience database (successes, failures, anti-patterns)
  - Goals       — directed evolution with target tracking
  - AgentPool   — depth-limited SubAgent spawn control
"""
