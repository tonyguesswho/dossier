# Eval set: 10 companies, 6 train / 4 holdout. Holdout NEVER tunes prompts or thresholds.
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EvalCompany:
    # gold_filename is relative to backend/eval/golds/; None = ungolded.
    company_name: str
    seed_url: str
    vertical: str
    holdout: bool
    notes: str
    gold_filename: str | None = None


# Order is stable so seed idempotency works (re-running seed.py changes no rows).
EVAL_COMPANIES: tuple[EvalCompany, ...] = (
    # Train (6) — 3 golded, 3 ungolded
    EvalCompany(
        company_name="Abundant",
        seed_url="https://abundant.ai",
        vertical="AI infra",
        holdout=False,
        notes=(
            "API for AI agent teleoperation — human operator intervenes when an agent fails. "
            "Picked for gold: strong press + founder-blog + HN coverage supports verbatim quoting."
        ),
        gold_filename="abundant.json",
    ),
    EvalCompany(
        company_name="Browser Use",
        seed_url="https://browser-use.com",
        vertical="AI tooling",
        holdout=False,
        notes=(
            "Open-source tool enabling AI agents to navigate and interact with web browsers. "
            "Picked for gold: went viral, GitHub + docs + blog posts + press provide rich "
            "source material with clear verbatim spans."
        ),
        gold_filename="browser-use.json",
    ),
    EvalCompany(
        company_name="Pickle",
        seed_url="https://getpickle.ai",
        vertical="Consumer AI",
        holdout=False,
        notes=(
            "Real-time AI video avatar tool that lip-syncs to user voice during video calls. "
            "Picked for gold: demo-video-heavy marketing + TechCrunch coverage gives multiple "
            "verbatim claim sources."
        ),
        gold_filename="pickle.json",
    ),
    EvalCompany(
        company_name="Rebolt",
        seed_url="https://rebolt.ai",
        vertical="Vertical SaaS",
        holdout=False,
        notes=(
            "AI agents for restaurant inventory management + supplier coordination. "
            "Gold headroom: train-set, available for a Phase 4 prep gold-writing session."
        ),
    ),
    EvalCompany(
        company_name="Retrofit",
        seed_url="https://retrofit.shop",
        vertical="Consumer",
        holdout=False,
        notes=(
            "AI-curated vintage clothing marketplace. "
            "Gold headroom: train-set, ungolded."
        ),
    ),
    EvalCompany(
        company_name="Splash",
        seed_url="https://splash9.com",
        vertical="Robotics",
        holdout=False,
        notes=(
            "Autonomous patrol boats for maritime border security. "
            "Gold headroom: train-set, ungolded."
        ),
    ),
    # Holdout (4) — never tuned against
    EvalCompany(
        company_name="GradeWiz",
        seed_url="https://gradewiz.ai",
        vertical="Vertical SaaS",
        holdout=True,
        notes="AI-assisted academic paper grading for teaching assistants. Holdout — no gold.",
    ),
    EvalCompany(
        company_name="Misprint",
        seed_url="https://misprint.com",
        vertical="Consumer",
        holdout=True,
        notes="Bid/ask marketplace for collectible cards (Pokémon etc). Holdout — no gold.",
    ),
    EvalCompany(
        company_name="Nextbyte",
        seed_url="https://trynextbyte.com",
        vertical="Devtools",
        holdout=True,
        notes=(
            "AI-powered assessment platform — interview questions testing 'vibe coding' skill. "
            "Holdout — no gold. Note: Nextbyte is the only Devtools company in the set, which "
            "would let the agent trivially overfit 'Devtools ≈ holdout' IF we trained on other "
            "Devtools companies. Since the train set has NO Devtools, this isn't a leakage risk."
        ),
    ),
    EvalCompany(
        company_name="Red Barn Robotics",
        seed_url="https://redbarnrobotics.com",
        vertical="Robotics",
        holdout=True,
        notes=(
            "Autonomous weeding robot for farms — reportedly 15x faster than human labor. "
            "Holdout — no gold."
        ),
    ),
)


def split_summary() -> dict[str, int]:
    total = len(EVAL_COMPANIES)
    holdout = sum(1 for c in EVAL_COMPANIES if c.holdout)
    golded = sum(1 for c in EVAL_COMPANIES if c.gold_filename is not None)
    verticals = {c.vertical for c in EVAL_COMPANIES}
    return {
        "total": total,
        "holdout": holdout,
        "train": total - holdout,
        "golded": golded,
        "verticals": len(verticals),
    }

