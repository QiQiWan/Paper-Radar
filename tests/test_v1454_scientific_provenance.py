"""v1.45.4: prevent scientific source misattribution and stale report reuse.

Synthetic unit cases deliberately test wrong claims rather than grading the
model on examples used for prompt development. They are not human gold.
"""
from copy import deepcopy
from pathlib import Path

import pytest

from paper_radar.research_synthesis_agent import (
    FINGERPRINT_VERSION, _call_model, _source_fingerprint, build_report,
    method_comparison_matrix, method_profiles, source_methods,
)
from paper_radar.research_continuity import confirmed_fingerprint
from paper_radar.evidence_chain import MAX_EVIDENCE_PAGES, EvidenceChain
from paper_radar.fulltext import MAX_PAGES as LOCAL_PAGES
from paper_radar.portal_pdf_worker import MAX_PAGES as PORTAL_PAGES


def _source(pid, method, value):
    return dict(paper_id=pid, anchor_id=pid+'-anchor', title=f'{method} benchmark',
                page=8, pdf_sha256='f'*64,
                quote=f'{method} test error {value} on dataset A', status='ready',
                experiment={'dataset':'dataset A','metric':'error','setting':'case1',
                            'baseline':'reference','review_decision':'approved','value':value})


def _claim(cid, body, sources):
    return {'claim_id': cid, 'body':body, 'sources':sources, 'revision':1, 'kind':'comparison'}


def test_methods_only_attached_to_the_paper_that_mentions_them():
    # One confirmed comparison cites two distinct papers. A cross-source union
    # would falsely say each paper contains BOTH FNO and PINO.
    findings=[_claim('a'*32,'FNO was compared to PINO for a benchmark.',
                     [_source('p-fno','FNO','0.0086'),_source('p-pino','PINO','0.0128')])]
    profiles={entry['method']:entry for entry in method_profiles(findings)}
    assert profiles['FNO']['paper_ids']==['p-fno']
    assert profiles['PINO']['paper_ids']==['p-pino']
    rows={(entry['paper_id'],entry['method']) for entry in method_comparison_matrix(findings)}
    assert rows=={('p-fno','FNO'),('p-pino','PINO')}
    assert all(entry['status'].endswith('role_unverified') for entry in method_comparison_matrix(findings))


def test_summary_method_names_without_source_quote_do_not_tag_papers():
    findings=[_claim('a'*32,'FNO and PINO are compared.',
                     [{'paper_id':'p1','anchor_id':'a1','quote':'Reference experiments show 0.10',
                       'title':'Scientific computations'}])]
    assert source_methods(findings[0]['sources'][0])==set()
    assert method_profiles(findings)==[]


def test_model_cannot_launder_measurements_across_two_valid_claim_ids():
    findings=[_claim('a'*32,'FNO error was 0.0086',[_source('p1','FNO','0.0086')]),
              _claim('b'*32,'PINO error was 0.0128',[_source('p2','PINO','0.0128')])]
    response={'sections':[{'title':'wrong pairing','interpretation':'FNO error 0.0128',
                           'claim_ids':['a'*32,'b'*32]},
                          {'title':'supported','interpretation':'PINO error 0.0128',
                           'claim_ids':['a'*32,'b'*32]}], 'questions':[]}
    model=_call_model('compare numerical errors',findings,{},lambda _: response)
    assert [item['title'] for item in model['sections']]==['supported']
    assert model['validation']['rejected_unverified_method_value_links']==1


def test_numeric_method_pair_must_exist_in_one_source_not_an_aggregated_claim():
    # The human-approved summary may accurately discuss two methods, but
    # rearranging their numbers must not pass automated output validation.
    findings=[_claim('a'*32,'FNO 0.0086; PINO 0.0128',
                     [_source('p-fno','FNO','0.0086'),_source('p-pino','PINO','0.0128')])]
    response={'sections':[{'title':'cross-source false match',
                           'interpretation':'FNO error 0.0128','claim_ids':['a'*32]}],
              'questions':[]}
    result=_call_model('Methods and errors',findings,{},lambda _:response)
    assert result['sections']==[]
    assert result['validation']['rejected_unverified_method_value_links']==1


def test_model_cannot_cite_claims_not_sent_to_provider():
    findings=[_claim(f'{i:032x}',f'FNO method {i}',[_source(f'p{i}','FNO','0.0086')])
              for i in range(13)]
    response={'sections':[{'title':'outside prompt','interpretation':'FNO experiment',
                           'claim_ids':[findings[-1]['claim_id']]}], 'questions':[]}
    result=_call_model('FNO?',findings,{},lambda payload: response)
    assert result['sections']==[]


def _confirmed_claim(exp_value='0.0086'):
    return {'id':'a'*32,'revision':2,'kind':'comparison','body':'FNO experiment was audited.',
            'effective_status':'confirmed',
            'anchors':[dict(_source('p-fno','FNO',exp_value),id='b'*32)]}


def test_reviewed_experiment_changes_invalidate_synthesis_snapshot():
    a={'claims':[_confirmed_claim()]}
    b=deepcopy(a)
    b['claims'][0]['anchors'][0]['experiment']['value']='0.0806'
    assert _source_fingerprint(a)!=_source_fingerprint(b)
    b=deepcopy(a)
    b['claims'][0]['anchors'][0]['experiment']['review_decision']='needs_review'
    assert _source_fingerprint(a)!=_source_fingerprint(b)
    assert build_report(a,'FNO error?')['source_fingerprint_version']==FINGERPRINT_VERSION


def test_memory_fingerprint_includes_experiment_approval_and_pdf_source():
    original={'a'*32:_confirmed_claim()}
    base=confirmed_fingerprint(original,['a'*32])
    newer=deepcopy(original)
    newer['a'*32]['anchors'][0]['experiment']['value']='0.0090'
    assert base!=confirmed_fingerprint(newer,['a'*32])
    newer=deepcopy(original)
    newer['a'*32]['anchors'][0]['pdf_sha256']='e'*64
    assert base!=confirmed_fingerprint(newer,['a'*32])


def test_long_pdf_page_limits_match_parser_backends_and_browser():
    assert MAX_EVIDENCE_PAGES==LOCAL_PAGES==PORTAL_PAGES==240
    root=Path(__file__).resolve().parents[1]
    assert "max:'240'" in (root/'paper_radar/static/trace_workbench.js').read_text(encoding='utf-8')
    code=(root/'paper_radar/static/portal.js').read_text(encoding='utf-8')
    assert 'Math.min(240,res.document.pages||240)' in code
    assert 'Math.min(120,res.document.pages||120)' not in code
    assert 'page<=MAX_EVIDENCE_PAGES' in (root/'paper_radar/public_portal.py').read_text(encoding='utf-8')


def test_evidence_chain_can_display_page_167_without_database_mutation(monkeypatch):
    # page_source is read-only and can be tested independently of SQLite.
    chain=object.__new__(EvidenceChain)
    monkeypatch.setattr(chain,'member',lambda _: 'p1')
    monkeypatch.setattr(chain,'doc',lambda _: {'valid':True,'sha256':'f'*64})
    monkeypatch.setattr(chain,'text_candidates',lambda pid,sha,page: [(17,'Results on page 167')])
    view=chain.page_source('p1',167)
    assert view['page']==167 and view['chunks'][0]['text']=='Results on page 167'
    with pytest.raises(ValueError,match='PDF 页码'):
        chain.page_source('p1',241)


def test_conflict_detection_distinguishes_numeric_equivalence_and_units():
    from paper_radar.research_synthesis_agent import conflict_candidates
    first=_claim('a'*32,'FNO result',[_source('p1','FNO','0.30')])
    second=_claim('b'*32,'FNO result',[_source('p2','FNO','0.3')])
    # String formatting differences must not be called scientific variation.
    assert conflict_candidates([first,second])==[]
    second['sources'][0]['experiment']['value']='0.50'
    r=conflict_candidates([first,second])
    assert len(r)==1 and r[0]['type']=='result_variation' and r[0]['status']=='candidate_only'
    second['sources'][0]['experiment']['value']='0.50%'
    r=conflict_candidates([first,second])
    assert len(r)==1 and r[0]['type']=='unit_mismatch'
    assert r[0]['unit']=='not_aligned'
    second['sources'][0]['experiment']['value']='0.50±0.01'
    assert conflict_candidates([first,second])[0]['type']=='measurement_format_requires_review'


def test_qualitative_negation_does_not_become_positive_evidence():
    from paper_radar.research_synthesis_agent import qualitative_tension_candidates
    negative=_claim('a'*32,'FNO did not significantly improve error in setting A',
                    [_source('p1','FNO','0.1')])
    positive=_claim('b'*32,'FNO significantly improves error in setting A',
                    [_source('p2','FNO','0.2')])
    pairs=qualitative_tension_candidates([negative,positive])
    assert len(pairs)==1 and pairs[0]['status']=='candidate_only'


def test_append_only_anchor_migration_preserves_references_and_audit(tmp_path,monkeypatch):
    from paper_radar.core import connect
    from paper_radar.evidence_chain import local_chain
    from paper_radar.migrations import REGISTRIES,migrate
    from tests.test_v140_research_inquiry import _create_local
    from tests.test_v118_public_pipeline import QUOTE

    all_steps=REGISTRIES['research']
    monkeypatch.setitem(REGISTRIES,'research',all_steps[:-2])
    path,_,pid=_create_local(tmp_path)
    first=local_chain('default','add_anchor',pid,1,QUOTE,db_path=path)
    claim=local_chain('default','save_claim','A confirmed benchmark has Accuracy 91% on DatasetA.',
                      'finding',[first['id']],0,db_path=path)
    local_chain('default','confirm',claim['id'],1,'Checked in the original PDF',db_path=path)
    with connect(path) as db:
        assert migrate(db,'research')==7
        original_links=db.execute('SELECT * FROM research_claim_links').fetchall()
        original_audits=db.execute('SELECT COUNT(*) FROM research_claim_audit').fetchone()[0]
        assert len(original_links)==1 and original_audits==2
    monkeypatch.setitem(REGISTRIES,'research',all_steps)
    with connect(path) as db:
        assert migrate(db,'research')==9
        assert migrate(db,'research')==9
        assert db.execute('PRAGMA foreign_key_check').fetchall()==[]
        assert len(db.execute('SELECT * FROM research_claim_links').fetchall())==1
        assert db.execute('SELECT COUNT(*) FROM research_claim_audit').fetchone()[0]==original_audits
        assert db.execute('SELECT page FROM research_anchors WHERE id=?',
                          (first['id'],)).fetchone()[0]==1
        schema=db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='research_anchors'").fetchone()[0]
        assert 'BETWEEN 1 AND 240' in schema
        assert db.execute('SELECT COUNT(*) FROM research_claims WHERE decision=?',('confirmed',)).fetchone()[0]==1
        with pytest.raises(Exception,match='immutable'):
            db.execute('DELETE FROM research_claim_audit')


def test_user_data_integrity_rejects_anchor_after_page_240(tmp_path):
    from paper_radar.core import connect
    from paper_radar.migrations import migrate
    path=tmp_path/'empty.db'
    with connect(path) as db:
        migrate(db,'research')
        ddl=db.execute("SELECT sql FROM sqlite_master WHERE name='research_anchors'").fetchone()[0]
        assert 'BETWEEN 1 AND 240' in ddl
        assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'


def test_report_becomes_stale_if_pdf_changes_during_model_call(tmp_path):
    from paper_radar.research_synthesis_agent import run_local,read_local
    from tests.test_v144_v145_research_program import _confirmed
    from tests.test_v118_public_pipeline import pdf_bytes

    path,pdf,_,_= _confirmed(tmp_path)
    def modifies_pdf_while_running(_payload):
        pdf.write_bytes(pdf_bytes('Replaced source, no matching original quote'))
        return {'sections':[],'questions':[]}

    result=run_local(path,'default','Neural operator benchmark Accuracy on DatasetA',
        use_model=True,confirm_transfer=True,model_config={'llm_data_consent':True},
        model_callable=modifies_pdf_while_running)
    assert result['report']['coverage']['matched_claims']==1
    assert result['snapshot_status']=='needs_review'
    assert read_local(path,'default',result['id'])['snapshot_status']=='needs_review'
    assert 'needs_review' in result['markdown']
