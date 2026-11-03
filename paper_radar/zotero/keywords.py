# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Lightweight, reproducible keyphrase extraction and reference-similarity ranking."""
from __future__ import annotations
from collections import Counter
from math import log, sqrt
import re


def _normal_title(title):
    return re.sub(r"[^a-z0-9]+", "", (title or "").casefold())

def _normal_doi(doi):
    return re.sub(r"^https?://(?:dx\.)?doi\.org/", "", (doi or "").casefold().strip())

from .models import Reference

STOP = set('''a an and are as at be been between both but by can could data dataset datasets each for from further have has how in into is it its may method methods model models new no not of on or our over paper papers proposed result results study studies such than that the their these this those through to using via was were which while with within without work works we you your show shows based towards toward under they'''.split())
GENERIC = set('''analysis approach approaches learning deep machine artificial neural network networks research framework frameworks task tasks system systems technique techniques training train trained performance benchmark experiments experimental results applications application comparison different general generic large based novel efficient accurate effective recent future article review proposed authors report data'''.split())
WORD = re.compile(r"[a-zA-Z][a-zA-Z0-9+\-]*")


def tokens(s: str) -> list[str]:
    return [x.lower() for x in WORD.findall(s or '')]


def candidate_phrases(text: str, max_words: int = 3) -> Counter:
    terms=tokens(text)
    acc=Counter()
    for size in range(1,max_words+1):
        for i in range(len(terms)-size+1):
            group=terms[i:i+size]
            if any(len(t)<3 or t in STOP for t in group):continue
            if size==1 and group[0] in GENERIC:continue
            # Keep research phrases like "neural operator" and "operator learning".
            # A generic word is acceptable when paired with a domain term.
            if all(t in GENERIC for t in group):continue
            phrase=' '.join(group)
            acc[phrase]+=1
    return acc


def extract_keywords(references: list[Reference], limit=10) -> list[str]:
    """Tags + repeated phrases in titles/abstracts, with redundancy filtering."""
    limit=max(1,min(int(limit),20))
    refs=[r for r in references if r.title][:500]
    if not refs:return []
    weighted=Counter();docfreq=Counter()
    for ref in refs:
        title=candidate_phrases(ref.title)
        abstract=candidate_phrases(ref.abstract[:4000])
        seen=set(title)|set(abstract)
        for phrase in seen:docfreq[phrase]+=1
        for phrase,count in title.items():
            weighted[phrase]+=min(count,2)*4.5
        for phrase,count in abstract.items():
            weighted[phrase]+=min(count,3)*0.35
        for tag in ref.tags:
            norm=' '.join(tokens(tag))
            if not norm or len(norm)>60 or len(norm.split())>5:continue
            if norm in GENERIC or norm in STOP:continue
            weighted[norm]+=9
            docfreq[norm]+=1
    scores=[]
    n=len(refs)
    for phrase,total in weighted.items():
        parts=phrase.split()
        if len(parts)==1 and n>1 and docfreq[phrase]<2:continue
        # Repeated phrases across reference papers are a more useful search topic.
        coverage=log(1+docfreq[phrase])
        specificity=1.0+0.40*(len(parts)-1)
        scores.append((total*coverage*specificity,phrase))
    scores.sort(key=lambda item:(-item[0],item[1]))
    chosen=[]
    for score,phrase in scores:
        if score<=0:continue
        # Skip near duplicates (one phrase fully contained in the other).
        toks=set(phrase.split())
        if any((toks <= set(other.split()) or set(other.split()) <= toks) and
               len(toks.intersection(set(other.split())))>=min(len(toks),len(other.split())) for other in chosen):
            continue
        chosen.append(phrase)
        if len(chosen)>=limit:break
    return chosen


def _vector(text: str):
    words=[w for w in tokens(text) if w not in STOP and w not in GENERIC and len(w)>=3]
    return Counter(words)


def _cosine(left:Counter,right:Counter)->float:
    if not left or not right:return 0.0
    dot=sum(c*right.get(term,0) for term,c in left.items())
    mag_a=sqrt(sum(x*x for x in left.values()))
    mag_b=sqrt(sum(x*x for x in right.values()))
    return dot/(mag_a*mag_b) if mag_a and mag_b else 0.0


class SimilarityRanker:
    """Cosine similarity of paper text to selected Zotero references, no model download."""
    def __init__(self,references:list[Reference]):
        self.vectors=[]
        self.seed_titles={_normal_title(r.title) for r in references if r.title}
        self.seed_dois={_normal_doi(r.doi) for r in references if r.doi}
        for r in references[:600]:
            if not r.title:continue
            v=_vector((r.title+' ')*3+(r.abstract or '')[:2500]+' '+' '.join(r.tags)*2)
            if v:self.vectors.append(v)

    def already_collected(self,paper)->bool:
        title=_normal_title(paper.title)
        doi=_normal_doi(getattr(paper,'doi',''))
        return bool((doi and doi in self.seed_dois) or (title and title in self.seed_titles))

    def score(self, paper)->float:
        candidate=_vector((paper.title+' ')*3+paper.abstract[:2500])
        if not candidate:return 0.0
        similarities=sorted((_cosine(candidate,v) for v in self.vectors),reverse=True)
        top=similarities[:min(3,len(similarities))]
        return sum(top)/len(top) if top else 0.0
