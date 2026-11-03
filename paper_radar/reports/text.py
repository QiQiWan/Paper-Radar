# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Markdown and Excel-friendly CSV exports."""
from __future__ import annotations
from datetime import datetime
from pathlib import Path
from .word import _reason
from .synthesis import describe_research
from .evidence import paper_brief
import csv

def write_markdown(path: Path, papers, config: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = ["# 近期文献汇总", "", f"更新时间：{datetime.now():%Y-%m-%d %H:%M}  ",
            f"研究关键词：{', '.join(config.get('keywords', []))}  ",
            f"统计范围：最近 {config['lookback_days']} 天 | 文献 {len(papers)} 篇", "",
            "## 本期研究综述", "", describe_research(papers, config), "", "## 优先阅读", ""]
    if config.get('_source_results'):
        state = '；'.join(f"{r['name']} {'完成 '+str(r['count'])+' 篇' if r['ok'] else '失败'}" for r in config['_source_results'])
        text[5:5] = ['本次来源检索：' + state, '']
    if config.get('zotero_collection_label'):
        text[5:5] = [f"参考收藏夹：Zotero / {config['zotero_collection_label']}（{config.get('zotero_reference_count',0)} 篇）", ""]
    if config.get('_quality_filters'):
        counts=config['_quality_filters']
        text.insert(text.index('## 本期研究综述'),
                    f"本次筛选：日期不符合 {counts.get('date_removed',0)} 篇；主题相关性不足 {counts.get('topic_removed',0)} 篇。")
    if not papers:
        text += ["暂无符合当前检索条件的论文。", ""]
    for i, p in enumerate(papers[:5], 1):
        brief = paper_brief(p, config.get('keywords', []))
        text += [f"{i}. [{p.title}]({p.url}) — {p.published[:10]} | {p.category}",
                 f"   - **研究方向**：{brief['topic']}",
                 f"   - **方法线索**：{brief['method']}",
                 f"   - **摘要证据**：{brief['evidence'] or ('仅题录，需核对原文' if not p.abstract else '当前摘要未包含明确的结果句')}", ""]
    text += ["## 论文清单", ""]
    for i, p in enumerate(papers, 1):
        text += [f"### {i:02d}. {p.title}", "", f"- 作者：{p.authors}",
                 f"- 日期：{p.published[:10]}；来源：{p.source}；期刊或类型：{p.venue or p.category}；匹配：{_reason(p, config.get('keywords', []))}", 
                 f"- 原文：{p.url}", ""]
        text += [f"- DOI：{p.doi or '未提供'}", f"- PDF：{'已下载到 downloads/'+p.local_file if p.local_file and p.download_status=='downloaded' else ('开放获取链接可用' if p.download_url else '暂无直链')}", '']
        brief = paper_brief(p, config.get('keywords', []))
        text += [f"- **研究方向**：{brief['topic']}", f"- **方法线索**：{brief['method']}",
                 f"- **摘要结果依据**：{brief['evidence'] or ('仅题录，无法判断结果' if not p.abstract else '没有可直接提取的结果性原句')}",
                 f"- **待核查**：{brief['review']}", ""]
        if p.summary:
            text += ["**AI 中文速览（需与原摘要核对）**", "", p.summary, ""]
        text += ["**原始摘要**", "", p.abstract or "未提供摘要；请打开原文核对。", ""]
    text += ["---", "", "数据来源：" + "、".join(config.get("sources", ["arxiv"])) + "。PDF 下载仅限可公开获取的直链；中文速览依据标题与摘要生成。"]
    path.write_text("\n".join(text) + "\n", encoding='utf-8')
    return path


def write_csv(path: Path, papers) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['论文ID', '标题', '作者', '日期', '分类', '匹配分', '星标', '阅读状态', '中文速览', '原始摘要', '论文链接', 'PDF链接', '来源', '期刊/会议', 'DOI', '开放获取', 'PDF状态', '本地文件', '首次收录时间'])
        def spreadsheet_safe(value):
            text = str(value or '')
            # Prevent external paper metadata from becoming spreadsheet formulas.
            if text.lstrip().startswith(('=', '+', '-', '@')):
                return "'" + text
            return text
        for p in papers:
            writer.writerow([spreadsheet_safe(v) for v in [
                p.paper_id, p.title, p.authors, p.published[:10], p.category, p.score, int(p.is_starred), p.reading_status, p.summary, p.abstract, p.url, p.pdf_url,
                p.source, p.venue, p.doi, p.is_oa, p.download_status, p.local_file, p.first_seen]])
    return path
