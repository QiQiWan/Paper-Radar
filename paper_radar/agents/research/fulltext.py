"""FulltextAgent: prepare a version-checked local PDF and evidence snippets."""
from ..contracts import AgentResult


def execute(ctx):
    from ... import fulltext, experiments
    from ...core import open_database
    ctx.require('database_read')
    ctx.checkpoint()
    pid=ctx.config['_analysis_paper_id']
    with open_database(ctx.db_path) as db:
        status=fulltext.paper_status(db,pid,db_path=ctx.db_path)
        if not status['indexed']:
            ctx.require('database_write')
            fulltext.index_paper(db,pid,db_path=ctx.db_path)
            status=fulltext.paper_status(db,pid,db_path=ctx.db_path)
        if not status['indexed']:
            raise ValueError('PDF 没有可读取的正文，无法提取实验信息。')
        chunks=experiments.selected_experiment_chunks(db,pid)
        materials,by_id=experiments.safe_model_material(chunks)
        if not materials:
            raise ValueError('没有可用于分析的全文片段。')
        old=experiments.read_findings(db,pid,db_path=ctx.db_path)
    ctx.checkpoint()
    return AgentResult({'fulltext_materials':materials,'evidence_map':by_id,
                        'pdf_document':status['document'],'cached_findings':old},
                       metrics={'chunks':len(materials),'pages':status['document']['indexed_pages']})
