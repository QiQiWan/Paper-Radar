# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
from pathlib import Path
from paper_radar.core import DEFAULTS
from paper_radar.evidence_batch import create_run,process_run,list_run,review_candidate
from paper_radar.pilot_audit import (overview,record_field_check,group_reviews,review_group,
                                     export_zip,COMPATIBILITY_FIELDS,QUALITY_FIELDS)
from test_v150_evidence_batch import fixture
import io,json,zipfile
import pytest


def all_fields(value):return {field:value for field in QUALITY_FIELDS}


def approved_pair(tmp_path):
    db,path=fixture(tmp_path)
    run=create_run(db,'default','神经算子跨网格和几何泛化实验结果？',['evidence-0','evidence-1'])['run_id']
    out=process_run(path,run,DEFAULTS)
    assert out['prepared']==2
    candidates=list_run(db,'default',run,db_path=path)['candidates']
    assert len(candidates)==2
    versions=[]
    for c in candidates:
        record=review_candidate(db,'default',c['id'],revision=c['revision'],decision='approved',
            metric='RMSE',value='0.0123',dataset='MeshBench',baseline='baseline',setting='split A',
            review_note='我已核对 PDF 正文中对应指标与数值以及实验条件。',expected_sha256=c['pdf_sha256'],db_path=path)
        versions.append(record['revision'])
    return db,path,candidates,versions


def test_empty_library_has_null_not_fabricated_metrics(tmp_path):
    db,path=fixture(tmp_path,count=0)
    summary=overview(db,'default',path)['summary']
    assert summary['library_total']==0
    assert summary['pilot_selected_unique']==0
    assert summary['pdf_acquisition_rate'] is None
    assert summary['field_accuracy_on_reviewed_fields'] is None
    assert summary['mean_active_review_seconds'] is None
    assert summary['method_confirmed_comparison_groups']==0
    db.close()


def test_genuine_review_timing_and_compatibility_require_manual_checks(tmp_path):
    db,path,candidates,versions=approved_pair(tmp_path)
    before=overview(db,'default',path)['summary']
    assert before['pilot_selected_unique']==2 and before['downloaded_or_existing_pdf']==2
    assert before['potential_comparison_groups']==1 and before['method_confirmed_comparison_groups']==0
    assert before['mean_active_review_seconds'] is None
    record_field_check(db,'default',candidates[0]['id'],versions[0],97.5,
        {**all_fields('unchecked'),'metric':'correct','value':'incorrect'},
        all_fields('original'),all_fields('corrected'))
    s=overview(db,'default',path)['summary']
    assert s['mean_active_review_seconds']==97.5
    assert s['field_accuracy_denominator']==2
    assert s['field_accuracy_on_reviewed_fields']==0.5
    assert s['field_recall'] is None
    groups=group_reviews(db,'default',path)
    assert len(groups)==1
    with pytest.raises(ValueError,match='未核对'):
        review_group(db,'default',path,groups[0]['group_key'],groups[0]['fingerprint'],
                     {**{k:True for k in COMPATIBILITY_FIELDS},'split_protocol':False},True,
                     '我已确认部分条件尚未一致，暂不能形成正式比较。')
    review_group(db,'default',path,groups[0]['group_key'],groups[0]['fingerprint'],
                 {k:True for k in COMPATIBILITY_FIELDS},True,
                 '已核对数据划分、任务一致性和指标定义，确认本组实验条件可比较。')
    assert overview(db,'default',path)['summary']['method_confirmed_comparison_groups']==1
    bundle=zipfile.ZipFile(io.BytesIO(export_zip(db,'default',path)))
    assert set(bundle.namelist())=={'summary.json','papers.csv','field_review.csv','comparison_groups.csv','HOW_TO_READ.txt'}
    assert json.loads(bundle.read('summary.json'))['field_accuracy_on_reviewed_fields']==0.5
    db.close()


def test_review_revision_prevents_fake_label_and_expired_comparison(tmp_path):
    db,path,candidates,versions=approved_pair(tmp_path)
    with pytest.raises(ValueError,match='已变化'):
        record_field_check(db,'default',candidates[0]['id'],versions[0]+1,99,all_fields('correct'),
                           all_fields('original'),all_fields('corrected'))
    g=group_reviews(db,'default',path)[0]
    review_group(db,'default',path,g['group_key'],g['fingerprint'],
                 {k:True for k in COMPATIBILITY_FIELDS},True,
                 '对相同样本、尺度和训练条件进行核对，满足并列比较条件。')
    # Changing a PDF invalidates approved quotes and hence the group review.
    from paper_radar.downloads.manager import resolve_saved_file
    f=db.execute('SELECT local_file FROM papers WHERE paper_id=?',('evidence-0',)).fetchone()['local_file']
    pdf=resolve_saved_file(path,f)
    pdf.write_bytes(pdf.read_bytes()+b'\n% changed bytes\n')
    after=overview(db,'default',path)['summary']
    assert after['method_confirmed_comparison_groups']==0
    assert after['candidate_stale']>=1
    db.close()


def test_bad_review_metrics_fail_validation(tmp_path):
    db,path,candidates,versions=approved_pair(tmp_path)
    with pytest.raises(ValueError,match='耗时'):
        record_field_check(db,'default',candidates[0]['id'],versions[0],-1,all_fields('correct'),
                           all_fields('original'),all_fields('corrected'))
    with pytest.raises(ValueError,match='取值'):
        record_field_check(db,'default',candidates[0]['id'],versions[0],30,
                           {**all_fields('correct'),'metric':'very_correct'},
                           all_fields('original'),all_fields('corrected'))
    db.close()
