import json

from pydantic import BaseModel

from config import PLAN_MODEL
from steps.llm import parse
from tracing import span


class PlannedProgram(BaseModel):
    program_id: str
    subject_ids: list[str]
    why: str
    criteria_to_check: list[str]


class Plan(BaseModel):
    programs: list[PlannedProgram]
    skipped: list[str]


SYSTEM = """You plan which incentive programs to investigate for a company.
For each candidate program that could plausibly apply, list the subjects it applies to (subject ids from the company profile, or the company id when the program is company-level) and the specific eligibility criteria that must be checked against the official source.
Criteria must be checkable facts from the program's own text (dates, thresholds, rates, caps), not general advice.
Include programs that look expired or superseded: the fetch step verifies status from the source, so do not drop a program on your own belief about its status. Put a program in `skipped` only when it cannot apply to this company at all, with a short reason."""


def plan(company, programs):
    user = f"Company profile:\n{json.dumps(company, indent=2)}\n\nCandidate programs:\n{json.dumps(programs, indent=2)}"
    with span("plan", kind="CHAIN", model=PLAN_MODEL):
        return parse(PLAN_MODEL, SYSTEM, user, Plan, effort="medium")
