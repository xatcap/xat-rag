# Subagent Orchestration Rules

Scale orchestration according to task complexity:

- **Tier 1 — Trivial Changes (Typos, Docs, Single-Line Configs):**
    - Execute directly without spawning planning subagents.
    - If documentation is modified, ensure compliance with [docs/portable/writing-style/writing-style.md](docs/portable/writing-style/writing-style.md).
- **Tier 2 — Bug Fixes:**
    - Whenever given a stack trace or failing behavior, delegate root-cause investigation to `debugger` before editing code.
    - If the root cause is isolated to a single module and does not alter public contracts or architecture, apply the fix and proceed to validation.
    - If the fix alters architectural boundaries, ports, or business logic, escalate to Tier 3 (`business-analyst` and `architect`) to design a robust fix.
- **Tier 3 — New Features & Architectural Changes:**
    - Delegate requirements analysis to `business-analyst`.
    - Pass the results from `business-analyst` to `architect`.
    - Loop between `architect` and `business-analyst` for **at most 2 refinement iterations** until requirements are clear and the architecture meets all business requirements. If unresolved trade-offs remain after 2 iterations, surface them directly to the human for a decision.
    - After the above steps have finished, create a comprehensive plan.
- **Review Requests:** For review requests:
    - Invoke the `code-reviewer` agent.
    - Invoke the `architect` agent.
    - Combine the results from both agents, analyze them, and ask for additional feedback from `business-analyst`.
    - Loop between `architect` and `business-analyst` for **at most 2 iterations** if needed.
    - Present a comprehensive list of findings, if any.
    - Do not plan any solutions, unless you are asked to do so.
- **Before Finalizing Code or Behavioural Changes:** Automatically spawn `test-runner` and `code-reviewer` in parallel to validate the changes. Fix any `[CRITICAL]` issues they report before proceeding.
- **After Validation Passes:** Whenever public APIs, CLI flags, environment variables, or architectural patterns are added or modified, delegate to `doc-sync` to update relevant `README.md` and documentation files before presenting the final answer.

# Communication Style
- Be direct and concise. Skip introductory filler and post-task summaries when the diff is self-explanatory.
- Use full sentences. Explain different topics in different sentences.
- Always explain the changes as simply as possible. Everything you write or explain needs to be easily understandable by humans.
- When referencing code, always cite `file_path:line_number`.

# Git Conventions
- Use Conventional Commits (`feat:`, `fix:`, `refactor:`, `chore:`, `test:`).
- Keep commit messages focused on *why* the change was made, not just *what* files changed.
- Never commit code or create branches unless explicitly told to do so.
- Never suggest to commit code or create branches.

# Project Specific Rules

IMPORTANT: Ensure you have thoroughly reviewed the [AGENTS.md](AGENTS.md) file of the specific project before beginning any work.

@AGENTS.md
