# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Compatibility exports for the Word, Markdown and CSV report engines."""
from __future__ import annotations
from collections import Counter
from .synthesis import describe_research
from .evidence import paper_brief
from ..quality import matched_keywords, is_broad
from datetime import datetime
from pathlib import Path
import csv
from docx import Document
from docx.shared import Inches, Pt, RGBColor
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_CELL_VERTICAL_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

NAVY = RGBColor(25, 44, 69)
BLUE = RGBColor(34, 98, 158)
MUTED = RGBColor(94, 108, 125)


def _font(style, size=10.5, chinese="宋体", latin="Times New Roman", bold=False, color=None):
    style.font.name = latin
    style.font.size = Pt(size)
    style.font.bold = bold
    if color:
        style.font.color.rgb = color
    rpr = style.element.get_or_add_rPr()
    rf = rpr.rFonts
    if rf is None:
        rf = OxmlElement("w:rFonts")
        rpr.insert(0, rf)
    for tag, value in (("eastAsia", chinese), ("ascii", latin), ("hAnsi", latin)):
        rf.set(qn("w:" + tag), value)


def _link(paragraph, text: str, url: str):
    if not url or not url.startswith(("http://", "https://")):
        return
    rel = paragraph.part.relate_to(url,
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink", True)
    element = OxmlElement("w:hyperlink")
    element.set(qn("r:id"), rel)
    r = OxmlElement("w:r")
    props = OxmlElement("w:rPr")
    c = OxmlElement("w:color")
    c.set(qn("w:val"), "27649A")
    props.append(c)
    r.append(props)
    t = OxmlElement("w:t")
    t.text = text
    r.append(t)
    element.append(r)
    paragraph._p.append(element)


def _category_counts(papers) -> str:
    pairs = Counter(p.category or "其他" for p in papers).most_common(5)
    return "    ".join(f"{name} {n}篇" for name, n in pairs) if pairs else "无"


def _reason(p, keywords: list[str]) -> str:
    hits = matched_keywords(p.title, p.abstract, keywords)
    return "、".join(hits[:3]) if hits else "需人工核对主题"


def write_report(path: Path, title: str, papers, config: dict, count: int, *, demo=False) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = Document()
    doc.core_properties.title = title
    doc.core_properties.subject = "Paper-Radar research digest"
    doc.core_properties.keywords = "Paper-Radar; research literature; digest"
    sec = doc.sections[0]
    sec.page_width = Inches(8.5)
    sec.page_height = Inches(11)
    sec.top_margin = Inches(.72)
    sec.bottom_margin = Inches(.70)
    sec.left_margin = Inches(.87)
    sec.right_margin = Inches(.87)

    st = doc.styles
    _font(st['Normal'], 10.2)
    st['Normal'].paragraph_format.line_spacing = 1.18
    st['Normal'].paragraph_format.space_after = Pt(5)
    _font(st['Title'], 20, '黑体', bold=True, color=NAVY)
    _font(st['Heading 1'], 13.5, '黑体', bold=True, color=NAVY)
    st['Heading 1'].paragraph_format.space_before = Pt(13)
    st['Heading 1'].paragraph_format.space_after = Pt(5)
    _font(st['Heading 2'], 11, '黑体', bold=True, color=NAVY)
    st['Heading 2'].paragraph_format.space_before = Pt(10)
    st['Heading 2'].paragraph_format.space_after = Pt(4)
    for name, size in [('MetaLocal', 9), ('AbstractLocal', 9.6)]:
        if name not in st:
            st.add_style(name, 1)
        _font(st[name], size, color=MUTED if name == 'MetaLocal' else None)
        st[name].paragraph_format.space_after = Pt(4)
        st[name].paragraph_format.line_spacing = 1.15

    header = doc.add_paragraph(style='Title')
    header.add_run(title)
    doc.add_paragraph('研究主题：' + (', '.join(config.get('keywords', [])) or '全部分类'), style='MetaLocal')
    doc.add_paragraph('更新时间：' + datetime.now().strftime('%Y-%m-%d %H:%M') +
                      f"    范围：最近{config['lookback_days']}天    来源：{', '.join(config.get('sources', ['arxiv']))}", style='MetaLocal')
    if config.get('_source_results'):
        source_info = '  ·  '.join(f"{r['name']}：{'成功 '+str(r['count'])+' 篇' if r['ok'] else '失败'}"
                                   for r in config['_source_results'])
        doc.add_paragraph('来源检索结果：' + source_info, style='MetaLocal')
    if config.get('zotero_collection_label'):
        doc.add_paragraph('参考文献：Zotero / ' + config['zotero_collection_label'] +
                          f"（{config.get('zotero_reference_count',0)} 篇）", style='MetaLocal')
    if demo:
        doc.add_paragraph('排版示例：以下论文为虚构记录。')

    doc.add_heading('本期概览', 1)
    overview = doc.add_table(rows=2, cols=3)
    overview.alignment = WD_TABLE_ALIGNMENT.CENTER
    overview.style = 'Light Shading Accent 1'
    for i, (label, val) in enumerate([
        ('论文数量', str(len(papers))),
        ('中文速览', str(sum(bool(p.summary) for p in papers))),
        ('本地 PDF', str(sum(bool(p.local_file) and p.download_status=='downloaded' for p in papers))),
    ]):
        overview.cell(0, i).text = label
        overview.cell(1, i).text = val
        for row in overview.rows:
            row.cells[i].vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            for paragraph in row.cells[i].paragraphs:
                paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    doc.add_paragraph('论文来源：' + '  ·  '.join(f'{k} {v}篇' for k,v in Counter(p.source or '未知' for p in papers).most_common()), style='MetaLocal')
    doc.add_paragraph('分类分布：' + _category_counts(papers), style='MetaLocal')
    if config.get('_quality_filters'):
        report_filters=config['_quality_filters']
        doc.add_paragraph(f'本次筛选：日期不符合 {report_filters.get("date_removed",0)} 篇；研究主题相关性不足 {report_filters.get("topic_removed",0)} 篇。',style='MetaLocal')
    if papers and config.get('keywords'):
        doc.add_heading('主题分布', 1)
        for keyword in [k for k in config.get('keywords', []) if not is_broad(k)][:8]:
            hits = [p for p in papers if matched_keywords(p.title, p.abstract, [keyword])]
            if hits:
                para = doc.add_paragraph()
                para.add_run(f'{keyword}  ').bold = True
                para.add_run(f'{len(hits)} 篇；例如 {hits[0].title[:95]}')
    saved = [p for p in papers if p.is_starred]
    if saved:
        doc.add_heading('已收藏的论文', 1)
        for paper in saved[:8]:
            para = doc.add_paragraph(style='MetaLocal')
            para.add_run('★  ' + paper.title[:140] + '  ')
            _link(para, '查看', paper.url)

    doc.add_heading('本期研究综述', 1)
    introduction = doc.add_paragraph(describe_research(papers, config))
    introduction.paragraph_format.line_spacing = 1.28
    introduction.paragraph_format.space_after = Pt(9)
    doc.add_heading('优先阅读', 1)
    if not papers:
        doc.add_paragraph('暂无符合条件的论文。可调整关键词或检索时间后重新生成。')
    else:
        doc.add_paragraph('当前研究方向中优先阅读的5篇论文。', style='MetaLocal')
        for idx, p in enumerate(papers[:5], 1):
            block = doc.add_paragraph()
            block.paragraph_format.space_after = Pt(4)
            r = block.add_run(f'{idx:02d}  {p.title}')
            r.bold = True
            r.font.color.rgb = BLUE
            block.paragraph_format.keep_with_next = True
            brief = paper_brief(p, config.get('keywords', []))
            doc.add_paragraph('研究方向：' + brief['topic'] + '  ·  方法：' + brief['method'], style='AbstractLocal')
            if brief['evidence']:
                doc.add_paragraph('摘要证据（原文节选）：' + brief['evidence'], style='AbstractLocal')
            elif not p.abstract:
                doc.add_paragraph('仅题录：原始来源没有提供摘要，研究结果尚不能判断。', style='AbstractLocal')
            elif p.summary:
                doc.add_paragraph('AI 中文速览（需与原摘要核对）：' + p.summary[:240], style='AbstractLocal')
            meta = doc.add_paragraph(style='MetaLocal')
            meta.add_run(f'{p.published[:10] or "日期未提供"}  ·  {p.source}  ·  {p.venue or p.category or "未分类"}  ·  检索词：{_reason(p, config.get("keywords", []))}    ')
            _link(meta, '查看原文', p.url)

        doc.add_heading('论文详目', 1)
        for idx, p in enumerate(papers, 1):
            label = '★  ' if p.is_starred else ''
            heading = doc.add_paragraph(style='Heading 2')
            heading.add_run(f'{idx:02d}  {label}{p.title}')
            heading.paragraph_format.keep_with_next = True
            authors = (p.authors or '作者未提供')[:160]
            author_para = doc.add_paragraph(authors, style='MetaLocal')
            author_para.paragraph_format.keep_with_next = True
            detail_meta = doc.add_paragraph(f'{p.published[:10] or "日期未提供"}  |  {p.source}  |  {p.venue or p.category or "未分类"}  |  '
                              f'检索词：{_reason(p, config.get("keywords", []))}', style='MetaLocal')
            detail_meta.paragraph_format.keep_with_next = True
            brief = paper_brief(p, config.get('keywords', []))
            q = doc.add_paragraph(style='AbstractLocal')
            q.add_run('研究对象  ').bold = True
            q.add_run(brief['topic'])
            q = doc.add_paragraph(style='AbstractLocal')
            q.add_run('方法线索  ').bold = True
            q.add_run(brief['method'])
            if brief['evidence']:
                q = doc.add_paragraph(style='AbstractLocal')
                q.add_run('摘要结果依据（英文原句节选）  ').bold = True
                q.add_run(brief['evidence'])
            elif not p.abstract:
                doc.add_paragraph('仅题录：原始来源未提供摘要，需打开原文核对研究方法和结论。', style='AbstractLocal')
            else:
                doc.add_paragraph('摘要未出现可直接提取的结果句；暂不推断方法有效性。', style='AbstractLocal')
            if p.summary:
                q = doc.add_paragraph()
                q.add_run('AI 中文速览（需核对）  ').bold = True
                q.add_run(p.summary)
            doc.add_paragraph('进一步核查：' + brief['review'], style='MetaLocal')
            q = doc.add_paragraph(style='AbstractLocal')
            q.add_run('原始摘要（Abstract）  ').bold = True
            q.add_run((p.abstract or '无摘要')[:2800])
            links = doc.add_paragraph(style='MetaLocal')
            _link(links, '论文页面', p.url)
            if p.doi:
                links.add_run('    ·    ')
                _link(links, 'DOI', 'https://doi.org/'+p.doi)
            if p.pdf_url:
                links.add_run('    ·    ')
                _link(links, '在线 PDF', p.pdf_url)
            if p.local_file and p.download_status=='downloaded':
                doc.add_paragraph('本地 PDF：downloads/' + p.local_file, style='MetaLocal')
            elif not p.download_url:
                doc.add_paragraph('PDF：暂无可直接下载的开放获取文件', style='MetaLocal')

    footer = sec.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    footer.text = ('Paper-Radar © 2026 @EatRice  ·  数据来源：' + '、'.join(config.get('sources', ['arxiv'])) + '；中文速览基于题名和摘要生成。\n'
                   + ('Thank you to arXiv for use of its open access interoperability.' if 'arxiv' in config.get('sources', []) else '全文与图片版权由原作者及出版方持有。'))
    for run in footer.runs:
        run.font.size = Pt(8)
        run.font.color.rgb = MUTED
    doc.save(str(path))
    return path


