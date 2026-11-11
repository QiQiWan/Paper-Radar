# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Explicit, version-guarded Zotero Web API note export.

Environment-only credentials; never save them in settings.json or send them
back to the browser. Operates only on an existing DOI-matched Zotero item and
creates a child note. Does NOT modify Zotero citation metadata, PDF or tags.
"""
from __future__ import annotations
import hashlib
import html
from html.parser import HTMLParser
import json
import os
import re
from urllib import parse, request, error

API = 'https://api.zotero.org'


class ZoteroSyncError(ValueError):
    pass


class ZoteroConflict(ZoteroSyncError):
    pass


def _doi(s: str) -> str:
    return re.sub(r'^\s*(?:https?://(?:dx\.)?doi\.org/|doi:)', '',str(s or '').strip(),flags=re.I).lower()


def _safe_html(text) -> str:
    return html.escape(str(text or '').replace('\x00',''),quote=True).replace('\n','<br/>')


def fingerprint(paper: dict, note: str):
    payload=[str(paper.get(k) or '') for k in ('paper_id','title','doi','url')]+[str(note or '')]
    return hashlib.sha256(json.dumps(payload,ensure_ascii=False,sort_keys=True).encode('utf-8')).hexdigest()


class ZoteroWebSync:
    def __init__(self, *, user_id: str | None = None, api_key: str | None = None, opener=None):
        self.user_id = (user_id if user_id is not None else os.getenv('ZOTERO_USER_ID','')).strip()
        self.api_key = (api_key if api_key is not None else os.getenv('ZOTERO_API_KEY','')).strip()
        if not re.fullmatch(r'[1-9][0-9]{0,14}',self.user_id):
            raise ZoteroSyncError('请设置 ZotERO_USER_ID（Zotero 账号数字 ID）')
        if not 12 <= len(self.api_key) <= 256 or any(x in self.api_key for x in ('\r','\n')):
            raise ZoteroSyncError('请设置有写入权限的 ZOTERO_API_KEY 环境变量')
        self.opener=opener or request.urlopen
        self.prefix=f'/users/{self.user_id}'

    def _send(self, path, *, method='GET', payload=None, headers=None):
        if not path.startswith(self.prefix+'/') or '..' in path or '://' in path:
            raise ZoteroSyncError('Zotero 请求路径无效')
        data=json.dumps(payload,ensure_ascii=False).encode('utf-8') if payload is not None else None
        req=request.Request(API+path,data=data,method=method,headers={
            'Zotero-API-Key':self.api_key,'Zotero-API-Version':'3',
            'Accept':'application/json', 'Content-Type':'application/json',
            'User-Agent':'Paper-Radar/1.39 Zotero note transfer',**(headers or {})})
        try:
            with self.opener(req,timeout=12) as resp:
                raw=resp.read(500_000)
                meta=resp.headers
                code=getattr(resp,'status',200)
        except error.HTTPError as exc:
            if exc.code in (409,412,428):
                raise ZoteroConflict('Zotero 文献库已更新；未写入，请刷新预览后重试') from exc
            if exc.code in (401,403):
                raise ZoteroSyncError('Zotero 授权已失效或没有写入权限') from exc
            if exc.code == 429:
                raise ZoteroSyncError('Zotero 触发请求限流，请稍后重试') from exc
            raise ZoteroSyncError('Zotero 服务返回错误 HTTP '+str(exc.code)) from exc
        except (OSError,TimeoutError) as exc:
            raise ZoteroSyncError('Zotero 连接失败，未写入') from exc
        try:result=json.loads(raw) if raw else None
        except (ValueError,UnicodeError) as exc:raise ZoteroSyncError('Zotero 响应格式无效') from exc
        return code,result,meta

    def preview(self, paper: dict, note: str) -> dict:
        doi=_doi(paper.get('doi',''))
        if not doi or len(doi)>200:
            raise ZoteroSyncError('这篇论文尚无 DOI，请先在 Zotero 内手工关联')
        if type(note) is not str or not 1 <= len(note.strip()) <= 1600:
            raise ZoteroSyncError('请先保存不超过 1600 字的个人阅读笔记')
        params=parse.urlencode({'q':doi,'qmode':'everything','limit':100})
        _,items,headers=self._send(self.prefix+'/items?'+params)
        if not isinstance(items,list):raise ZoteroSyncError('Zotero 文献列表响应无效')
        matches=[]
        for item in items:
            if not isinstance(item,dict):continue
            data=item.get('data') or {}
            if _doi(data.get('DOI',''))==doi and data.get('itemType') not in ('attachment','note'):
                matches.append(item)
        if len(matches)!=1:
            raise ZoteroSyncError('Zotero 中没有唯一 DOI 对应条目；请检查该 DOI 是否重复或不存在')
        chosen=matches[0]
        parent=str(chosen.get('key') or chosen.get('data',{}).get('key') or '')
        if not re.fullmatch(r'[A-Za-z0-9]{8}',parent):
            raise ZoteroSyncError('Zotero 条目编号无效')
        # Get current library version independently; search may be cached.
        _,_,library_headers=self._send(self.prefix+'/items?limit=1')
        version=str(library_headers.get('Last-Modified-Version',''))
        if not version.isdigit():
            raise ZoteroSyncError('Zotero 未提供可用于冲突保护的文献库版本')
        return {'paper_id':paper.get('paper_id'),'title':paper.get('title'),
                'zotero_parent':parent,'zotero_title':str(chosen.get('data',{}).get('title',''))[:300],
                'library_version':int(version),'fingerprint':fingerprint(paper,note),
                'note_length':len(note),'action':'create_child_note',
                'warning':'仅为匹配的 Zotero 论文新建一条阅读笔记；不修改已有题录或 PDF。'}

    def preview_import(self, paper: dict) -> dict:
        return _preview_import(self,paper)

    def confirm_import(self, paper: dict, *, parent: str, note_key: str,
                       expected_fingerprint: str) -> str:
        return _confirm_import(self,paper,parent=parent,note_key=note_key,
                               expected_fingerprint=expected_fingerprint)

    def confirm(self, paper: dict, note: str, *, parent: str, version: int,
                expected_fingerprint: str) -> dict:
        if type(version) is not int or version<0 or not re.fullmatch(r'[A-Za-z0-9]{8}',str(parent)):
            raise ZoteroSyncError('Zotero 写入凭据无效')
        if fingerprint(paper,note)!=expected_fingerprint:
            raise ZoteroConflict('预览后本地论文或笔记已变化；请重新预览')
        # A versioned request makes Zotero reject any intervening library writes.
        content=(f'<h2>Paper-Radar 阅读笔记</h2><p><strong>{_safe_html(paper.get("title"))}</strong></p>'
                 f'<p>DOI: {_safe_html(paper.get("doi"))}</p>'
                 f'<p>{_safe_html(note)}</p>')
        code,result,_=self._send(self.prefix+'/items',method='POST',headers={
             'If-Unmodified-Since-Version':str(version)},payload=[{
             'itemType':'note','parentItem':parent,'note':content,'tags':[]}])
        if code!=200 or not isinstance(result,dict) or result.get('failed'):
            raise ZoteroSyncError('Zotero 未确认写入，或返回部分失败；请检查 Zotero 后再尝试')
        successful=result.get('successful') or result.get('success') or {}
        if '0' not in successful:
            raise ZoteroSyncError('Zotero 未确认新建笔记，请先检查原文献库')
        saved=successful['0']
        key=saved if isinstance(saved,str) else str((saved or {}).get('key',''))
        return {'ok':True,'zotero_parent':parent,'zotero_note_key':key,
                'warning':'已创建独立 Zotero 子笔记。原有条目、附件及标签未修改。'}


class _PlainNote(HTMLParser):
    """Extract only the human note paragraph from our own exported format."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.in_p=False
        self.parts=[]
        self.paragraphs=[]
    def handle_starttag(self,tag,attrs):
        if tag=='p':self.in_p=True;self.parts=[]
        elif tag=='br' and self.in_p:self.parts.append('\n')
    def handle_data(self,data):
        if self.in_p:self.parts.append(data)
    def handle_endtag(self,tag):
        if tag=='p' and self.in_p:
            self.paragraphs.append(''.join(self.parts).strip())
            self.in_p=False


def _import_text(markup: str) -> str:
    if type(markup) is not str or not 20<=len(markup)<=15000:
        raise ZoteroSyncError('Zotero 笔记结构不符合预期')
    if not markup.strip().startswith('<h2>Paper-Radar 阅读笔记</h2>'):
        raise ZoteroSyncError('仅支持导入由 Paper-Radar 导出的子笔记')
    collector=_PlainNote();collector.feed(markup)
    if len(collector.paragraphs)<3:
        raise ZoteroSyncError('Zotero 笔记缺少必要内容')
    note=collector.paragraphs[-1]
    if not note or len(note)>1200 or '\x00' in note:
        raise ZoteroSyncError('Zotero 阅读笔记长度超出可安全导入范围')
    return note


def _remote_fingerprint(data: dict) -> str:
    return hashlib.sha256(json.dumps({'key':data.get('key',''),'version':data.get('version',0),
        'parentItem':data.get('parentItem',''),'note':data.get('note','')},
        ensure_ascii=False,sort_keys=True).encode('utf-8')).hexdigest()


def _preview_import(self, paper: dict) -> dict:
    matched=self.preview(paper,'check remote note')
    parent=matched['zotero_parent']
    _,children,_=self._send(self.prefix+'/items/'+parent+'/children?'+parse.urlencode({'itemType':'note','limit':100}))
    if not isinstance(children,list):raise ZoteroSyncError('Zotero 子笔记列表无效')
    candidates=[]
    for item in children:
        if not isinstance(item,dict):continue
        data=item.get('data') or {}
        if data.get('itemType')!='note' or data.get('parentItem')!=parent:continue
        try:plain=_import_text(data.get('note',''))
        except ZoteroSyncError:continue
        key=str(item.get('key') or data.get('key') or '')
        if not re.fullmatch(r'[A-Za-z0-9]{8}',key):continue
        candidates.append((key,data,plain))
    if len(candidates)!=1:
        raise ZoteroSyncError('Zotero 中找不到唯一的 Paper-Radar 子笔记；请在 Zotero 内核对重复笔记')
    key,data,plain=candidates[0]
    return {'paper_id':paper.get('paper_id'),'zotero_parent':parent,'zotero_note_key':key,
        'remote_fingerprint':_remote_fingerprint({**data,'key':key}),
        'preview':plain[:180], 'length':len(plain),
        'warning':'只有明确确认后才将这条 Zotero 子笔记添加到本地个人笔记；会保留已有笔记。'}


def _confirm_import(self,paper: dict,*,parent: str,note_key: str,expected_fingerprint: str) -> str:
    if not re.fullmatch(r'[A-Za-z0-9]{8}',str(parent)) or not re.fullmatch(r'[A-Za-z0-9]{8}',str(note_key)):
        raise ZoteroSyncError('Zotero 文献编号无效')
    matched=self.preview(paper,'check remote note')
    if matched['zotero_parent']!=parent:
        raise ZoteroConflict('Zotero 中对应的 DOI 条目已发生变化，请重新预览')
    _,item,_=self._send(self.prefix+'/items/'+note_key)
    if not isinstance(item,dict):raise ZoteroSyncError('Zotero 未返回对应的子笔记')
    data=item.get('data') or {}
    if data.get('itemType')!='note' or data.get('parentItem')!=parent:
        raise ZoteroSyncError('Zotero 子笔记已不再属于该论文')
    current=_remote_fingerprint({**data,'key':item.get('key',note_key)})
    if current!=expected_fingerprint:
        raise ZoteroConflict('Zotero 笔记在预览后已变更，已取消导入')
    return _import_text(data.get('note',''))


