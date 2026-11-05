"""ResearchStorageAgent: transactional persistence, only for verified evidence."""
from ..contracts import AgentResult


def execute(ctx):
    from ...core import open_database
    from ... import experiments
    ctx.require('database_write')
    ctx.checkpoint()
    # Recheck bytes after the cloud request: a PDF could be replaced mid-run.
    from ...fulltext import paper_status
    with open_database(ctx.db_path) as db:
        fresh=paper_status(db,ctx.config['_analysis_paper_id'],db_path=ctx.db_path)
        if (not fresh['indexed'] or
                fresh['document']['pdf_sha256'] != ctx.state['pdf_document']['pdf_sha256']):
            raise ValueError('PDF 在分析期间被修改，拒绝保存旧证据，请重新解析。')
    if ctx.state['experiment_cached']:
        result={'paper_id':ctx.config['_analysis_paper_id'],'status':'cached',
                'findings':len(ctx.state['verified_experiments'])}
    else:
        with open_database(ctx.db_path) as db:
            result=experiments.persist_findings(db,ctx.config['_analysis_paper_id'],
                ctx.state['pdf_document'],ctx.state['experiment_cache_key'],
                ctx.state['verified_experiments'])
    ctx.checkpoint()
    return AgentResult({'experiment_result':result}, metrics={'stored':result['findings']})
