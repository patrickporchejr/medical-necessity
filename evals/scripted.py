"""A scripted model: no call, no cost. The tests use it, and `experiment.py --models scripted:faithful` runs
the harness end to end with it.

`faithful_from_prompt` does the job right from what the assemble prompt says, so it follows the extract
node's summary (and so the rule) exactly: it is a wiring check, not a stand-in for a real model's reading.
"""

import re

from app.graph.nodes.assemble import PacketDraft
from app.graph.state import Assertion, ResourceRef
from app.llm.client import LLMResult


def faithful_from_prompt(prompt: str) -> PacketDraft:
    """A model that does the job right, working only from what the prompt says."""
    assertions = []
    for block in re.split(r"\n(?=Criterion )", prompt)[1:]:
        cid = re.match(r"Criterion (\w+):", block).group(1)
        lines = [ln for ln in block.splitlines() if ln.strip().startswith("- ")]
        required = re.search(r"Required status: (\w+)", block)
        if required:  # only records that carry the required status count
            lines = [ln for ln in lines if f"status {required.group(1)}" in ln]
        refs = re.findall(r"- (\w+) (<[A-Z]+_\d+>)", "\n".join(lines))
        status = re.search(r"Duration status: (\w+)", block)
        if ("No matching records" in block or (status and status.group(1) != "met")
                or (required and not refs)):
            assertions.append(Assertion(criterion_id=cid, kind="gap", text="not established by the chart"))
        else:
            cites = [ResourceRef(resource_type=t, id=i) for t, i in refs]
            assertions.append(Assertion(criterion_id=cid, text="established by the chart", citations=cites))
    return PacketDraft(assertions=assertions)


class Scripted:
    provider = "fake"

    def __init__(self, respond):
        self.respond = respond

    async def generate(self, system, user, schema):
        return LLMResult(parsed=self.respond(user), provider="fake", model="scripted",
                         input_tokens=100, output_tokens=50)
