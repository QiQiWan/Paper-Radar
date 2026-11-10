"""Acquire version-pinned arXiv PDFs for an experimental benchmark.

PDFs remain private research copies; check licenses before redistribution.
"""
import hashlib
import json
import re
import time
from datetime import datetime,timezone
from pathlib import Path
from urllib.request import Request,urlopen
import fitz

ROOT=Path("real_pdf_acquisition")
ROOT.mkdir(parents=True,exist_ok=True)
meta=json.loads(Path("benchmarks/real_papers_v2/manifest.json").read_text(encoding="utf-8"))
pattern=re.compile(r"\b(?:experiments?|results?|empirical|evaluation|benchmarks?|simulations?|comparisons?|ablations?|performance|numerical\s+stud(?:y|ies)|case\s+stud(?:y|ies))\b",re.I)
rows=[]
for i,p in enumerate(meta["papers"]):
    key=p["paper_id"]+p["version"]
    folder=ROOT/"pdfs"/f"{i//11+1:02d}"
    folder.mkdir(parents=True,exist_ok=True)
    target=folder/(key+".pdf")
    item={"paper_id":p["paper_id"],"version":p["version"],"title":p["title"],
          "category":p["category"],"pdf_url":p["pdf_url"],"filename":str(target.relative_to(ROOT)),
          "status":"failed","selected":False,"license_status":"not_checked"}
    for url in (p["pdf_url"],"https://export.arxiv.org/pdf/"+key):
        try:
            with urlopen(Request(url,headers={"User-Agent":"PaperRadarBenchmark/1.0 research corpus; contact via repository","Accept":"application/pdf"}),timeout=45) as resp:
                blob=resp.read(38*1024*1024+1)
            if len(blob)>38*1024*1024 or len(blob)<10000 or not blob.startswith(b"%PDF"):
                raise ValueError("Invalid/truncated/oversized PDF")
            pdf=fitz.open(stream=blob,filetype="pdf")
            if pdf.is_encrypted or pdf.page_count<4:
                raise ValueError("Encrypted PDF or too few pages")
            text_len=0
            signals=0
            pages=[]
            for k,page in enumerate(pdf):
                text=page.get_text("text",sort=True)
                text_len+=len(text)
                hits=len(pattern.findall(text[:14000]))
                if hits:
                    pages.append({"page":k+1,"matches":hits})
                    signals+=hits
            if text_len<3000:
                raise ValueError("PDF has too little extractable text")
            target.write_bytes(blob)
            item.update(status="downloaded",sha256=hashlib.sha256(blob).hexdigest(),bytes=len(blob),
                        pages=pdf.page_count,extracted_chars=text_len,experiment_mentions=signals,
                        candidate_experiment_pages=sorted(pages,key=lambda x:-x["matches"])[:12],
                        experiment_signal=signals>=3 and len(pages)>=2)
            pdf.close()
            print("PDF_OK",key,item["pages"],item["bytes"],signals,flush=True)
            break
        except Exception as exc:
            item["error"]=type(exc).__name__+": "+str(exc)[:200]
            if url==p["pdf_url"]:time.sleep(5)
    if item["status"]!="downloaded":
        print("PDF_FAIL",key,item["error"],flush=True)
    rows.append(item)
    time.sleep(3.2)
valid=[r for r in rows if r["status"]=="downloaded" and r.get("experiment_signal")]
valid.sort(key=lambda r:(r["category"] not in ("operator","pinn","weather","benchmark"),
                        -r["experiment_mentions"],r["paper_id"]))
for r in valid[:30]:r["selected"]=True
report={"schema":"paper-radar-real-pdf-30-acquisition-v1",
        "downloaded_at":datetime.now(timezone.utc).isoformat(),
        "source":"existing real_papers_v2 manifest; downloaded from official arxiv.org",
        "papers_requested":len(rows),"pdf_downloaded":sum(r["status"]=="downloaded" for r in rows),
        "experimental_signal":len(valid),"selected":sum(r["selected"] for r in rows),
        "note":"Experiment signals are lexical screening, not independent human review. Check version and PDF rights before redistribution.",
        "items":rows}
(ROOT/"download_manifest.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
print("SUMMARY",report["papers_requested"],report["pdf_downloaded"],report["experimental_signal"],report["selected"],flush=True)
