# Subagent Orchestration Rules
- **Before Fixing Bugs:** Whenever given a stack trace or failing behavior, delegate root-cause investigation to `debugger` before editing code. After the root-cause has been clearly identified, delegate to `business-analyst` and `architect` agents to design a robust fix and ensure that requirements are met and business needs are met.
- **Before Implementing new Features:** Whenever you are asked to implement new features or start a new project:
    - delegate to `business-anlyst`.
    - Pass the results from the `business-analyst` to the `architect`.
    - Loop between the `architect` and `business-analyst` until all requirements are clear, and the architecture meats all business requirements.
    - After the above steps have finished, create a comprehensive plan.
- **Review Requests:** For review requests:
    - Invoke the `code-reviewer` agent.
    - Invoke the `architect` agent.
    - Combine the results from both agents, analyze them and ask for additional feedback from `business-analyst`.
    - Loop between the `architect` and the `business-analyst` if needed.
    - Present a comprehensive list of findinds, if any.
    - Do not plan any solutions, unless you are asked to do so.
- **Before Finalizing Any Task:** Automatically spawn `test-runner` and `code-reviewer` in parallel to validate the changes. Fix any `[CRITICAL]` issues they report before proceeding.
- **After Validation Passes:** Whenever public APIs, CLI flags, environment variables, or architectural patterns are added or modified, delegate to `doc-sync` to update relevant `README.md` and documentation files before presenting the final answer.

# Communication Style
- Be direct and concise. Skip introductory filler and post-task summaries when the diff is self-explanatory.
- Use full sentences. Explain different topics on different sentences.
- Always explain the changes as simple as possible. Everything you write or explain needs to be easily understandable by humans.
- When referencing code, always cite `file_path:line_number`.

# Git Conventions
- Use Conventional Commits (`feat:`, `fix:`, `refactor:`, `chore:`, `test:`).
- Keep commit messages focused on *why* the change was made, not just *what* files changed.
- Never commit code or create branches unless explicitly told to do so.
- Never suggest to commit code or create branches.

# Project Specific Rules

IMPORTANT: Ensure you’ve thoroughly reviewed the [AGENTS.md](AGENTS.md) file of the specific project before beginning any work.

@AGENTS.md