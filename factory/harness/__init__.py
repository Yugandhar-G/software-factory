"""Code-driven factory harness on the Claude Agent SDK.

The graph (spec -> build -> removal audit -> observer -> review -> approve ->
ready), per-role tool policy, budgets and run ledger live in code here. Agent
roles still come from the plugin's agents/*.md, project rules from the project's
.factory/profile.md, and job state from factory/scripts/state.py, so interactive
/factory runs and harness runs share one source of truth. Only runner.SDKRunner
imports the SDK.
"""
