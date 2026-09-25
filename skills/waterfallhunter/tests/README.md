# WaterfallHunter Skill Behavioral Tests

Static Markdown validation is necessary but not sufficient. Each WaterfallHunter engineering skill must be pressure-tested against a fresh conversation/context using the same scenario before and after loading the target skill.

## Protocol

```text
RED: run the scenario with a fresh worker that has not loaded the target WFH skill; capture the concrete failure or rationalization.
GREEN: load the target skill and rerun the same scenario with a fresh worker; every Passing behavior criterion must be satisfied and the Forbidden shortcut must be absent.
REFACTOR: if the worker finds a loophole, tighten only the minimum skill wording needed, then rerun GREEN.
```

## Rules

- Use the scenario text in `scenarios.md` without weakening its pressure.
- A RED run is useful only when it demonstrates a concrete baseline failure, unsafe shortcut, overclaim, or ambiguity the skill is intended to correct.
- A GREEN run must demonstrate behavior, not merely repeat the skill's vocabulary.
- Use a fresh conversation/context for each RED and GREEN run to avoid contamination from prior instructions.
- Record compact evidence in `scenarios.md`; do not paste private model reasoning.
- If a target skill requires a Superpowers process skill, load only that required process skill plus the target WFH skill.
- Do not publish, deploy, send notifications, migrate production data, or place orders as part of behavioral skill testing.
- A skill-system PR is not merge-ready until all thirteen GREEN scenarios pass and the repository-local static validator passes.
