from pathlib import Path
import json
b=Path(__file__).resolve().parents[1]
papers={p['paper_id']:p for p in json.loads((b/'manifest.json').read_text(encoding='utf-8'))['papers']}
raw=[
 ('Q01','single_paper_evidence','2010.08895',None,
  'FNO Table 1: with viscosity 1e-3, T=50, N=1000, 64x64 resolution, what are FNO-3D and FNO-2D reported errors?',
  'FNO-3D: 0.0086; FNO-2D: 0.0128. Same table/column with the stated conditions.',8,None,'Table 1, FNO-3D and FNO-2D rows'),
 ('Q02','single_paper_evidence','2010.08895',None,
  'Is FNO-3D uniformly better than FNO-2D at all four Table 1 operating settings?',
  'No. In Table 1, FNO-3D is lower at nu=1e-3,N=1000 and nu=1e-4,N=10000, whereas FNO-2D is lower at nu=1e-4,N=1000 and nu=1e-5,N=1000. The claim needs setting qualifiers.',8,None,'Table 1, four viscosity/time/sample columns'),
 ('Q03','single_paper_evidence','2109.03697',None,
  'U-FNO Table 2(a): compare U-FNO and CNN gas saturation front errors and name the front threshold.',
  'U-FNO: 3.4%; CNN: 9.2%; the gas saturation front uses threshold 0.01.',19,None,'Table 2(a) and explanatory paragraph'),
 ('Q04','single_paper_evidence','2109.03697',None,
  'U-FNO Table 2(b): compare pressure buildup front errors and identify the threshold.',
  'U-FNO: 12.0%; CNN: 21.2%; the pressure buildup front threshold is 0.5 bar.',19,None,'Table 2(b) and explanatory paragraph'),
 ('Q05','single_paper_evidence','2111.03794',None,
  'PINO Table 2 on Darcy flow: what solution errors are reported for DeepONet with data, PINO with data, and PINO without data?',
  'DeepONet with data: 6.97 ± 0.09%; PINO with data: 1.22 ± 0.03%; PINO without data: 1.50 ± 0.03%.',13,None,'Table 2, Method / Solution error'),
 ('Q06','single_paper_evidence','2111.03794',None,
  'PINO Table 3 for Kolmogorov flow: how does solution error vary with number of data samples at 2200 PDE instances?',
  '0, 800, and 2200 data samples correspond to 6.22 ± 0.11%, 6.01 ± 0.12%, and 5.04 ± 0.11% respectively; the table fixes 2200 PDE instances.',13,None,'Table 3, three rows'),
 ('Q07','cross_paper_comparability','2010.08895','2109.03697',
  'Can the FNO Navier-Stokes error 0.0086 be directly compared numerically with U-FNO gas saturation front error 3.4%?',
  'No. Different PDE/problem context, prediction target and error definition. Both numbers are real table values, but a relative improvement across them is not meaningful.',8,19,'FNO Table 1 and U-FNO Table 2(a)'),
 ('Q08','cross_paper_comparability','2109.03697','2111.03794',
  'Do U-FNO pressure front error 12.0% and PINO Darcy solution error 1.22 ± 0.03% support a direct claim that one operator is more accurate?',
  'No. They measure different quantities under different scientific tasks and settings; metric definitions and evaluation datasets are incompatible.',19,13,'U-FNO Table 2(b) and PINO Table 2'),
]
rows=[]
for n,kind,a,c,q,answer,pa,pb,ev in raw:
 rows.append({'id':n,'task_type':kind,'paper_a':a,'paper_b':c,
    'pdf_sha_a':papers[a]['sha256'],'pdf_sha_b':papers[c]['sha256'] if c else None,
    'page_a':pa,'page_b':pb,'question':q,'pilot_answer':answer,'evidence_location':ev,
    'review_level':'assistant_page_visual_check_2026_11_10','is_double_reviewed_human_gold':False})
p=b/'tasks'/'pilot_visual_checked_qa.jsonl'
p.write_text('\n'.join(json.dumps(r,ensure_ascii=False) for r in rows)+'\n',encoding='utf-8')
print('pilot visual QA',len(rows))
