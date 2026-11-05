"""EvidenceVerifierAgent: deterministic, non-LLM evidence gate."""
from ..contracts import AgentResult


def execute(ctx):
    from ...experiments import validate_experiments
    ctx.checkpoint()
    if ctx.state['experiment_cached']:
        findings=ctx.state['cached_findings']['findings']
    else:
        findings=validate_experiments(ctx.state['experiment_raw'],ctx.state['evidence_map'])
    ctx.checkpoint()
    return AgentResult({'verified_experiments':findings}, metrics={'verified':len(findings)})
