# Planning Directory

This directory uses the **Planning-with-Files (PWF)** pattern for organizing complex multi-phase work.

## Active Planning Files

| File | Purpose | Update Frequency |
|------|---------|------------------|
| [active/task_plan.md](active/task_plan.md) | Phase breakdown, current focus, success criteria | After each phase completion |
| [active/findings.md](active/findings.md) | Technical discoveries, trade-offs, benchmarks | After any significant discovery |
| [active/progress.md](active/progress.md) | Chronological session log | Throughout each session |

## Quick Reference

**Where am I?** → Check `task_plan.md` for current phase
**What did I learn?** → Check `findings.md` for technical context
**What's been done?** → Check `progress.md` for chronological history

## Archive

Completed plans and superseded documents are stored in `archive/` with date stamps.

## PWF Pattern

This planning format follows the Planning-with-Files pattern:
- Separates plan (what to do) from findings (what we learned) from progress (what we did)
- Better for long-running multi-phase projects
- Creates clear SRED audit trail
- Handles plan evolution gracefully

For more on PWF: See https://github.com/topics/planning-with-files
