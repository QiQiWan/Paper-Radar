# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Optional metadata-only email digest; off until explicitly authorized.

Credentials are never stored in settings/database or transmitted to the UI.
SMTP is at-least-once on process crashes between network send and DB receipt.
"""
from __future__ import annotations
from email.message import EmailMessage
from email.utils import make_msgid
import os
import smtplib

from . import digest, preference, utc_now


def email_ready(env=None) -> bool:
    env=os.environ if env is None else env
    return bool(env.get('PAPER_RADAR_SMTP_HOST') and env.get('PAPER_RADAR_SMTP_FROM'))


def send_digest(db, topic_id: str, *, env=None, transport=None, limit=50) -> dict:
    env=os.environ if env is None else env
    prefs=preference(db,topic_id)
    if not prefs['enabled'] or not prefs['email_enabled']:
        return {'state':'disabled','sent':0}
    if not email_ready(env):
        return {'state':'not_configured','sent':0}
    rows=db.execute('''SELECT n.event_id,e.title,e.kind FROM following_notifications n
        JOIN following_events e ON e.id=n.event_id
        WHERE n.topic_id=? AND n.email_sent_at='' ORDER BY n.id LIMIT ?''',
        (topic_id,max(1,min(int(limit),50)))).fetchall()
    if not rows:
        return {'state':'empty','sent':0}
    # Only the app's local metadata is sent. No PDF contents, prompts, API keys.
    labels={'new':'新收录','revised':'新版本','metadata_updated':'元数据变更',
            'published_version':'发表信息','withdrawal_signal':'撤稿线索（待复核）'}
    body='# Paper-Radar 研究跟进\n\n主题：'+topic_id+'\n\n'+\
        '\n'.join('- '+labels.get(row['kind'],row['kind'])+' · '+row['title']
                  for row in rows)+'\n'
    message=EmailMessage()
    message['From']=env['PAPER_RADAR_SMTP_FROM']
    message['To']=prefs['email_to']
    message['Subject']='Paper-Radar · 研究方向更新（%d 条）'%len(rows)
    message['Message-ID']=make_msgid(domain='paper-radar.local')
    message.set_content(body)
    if transport is not None:
        transport(message)
    else:
        host=env['PAPER_RADAR_SMTP_HOST']
        port=int(env.get('PAPER_RADAR_SMTP_PORT','465'))
        if not 1<=port<=65535:raise ValueError('SMTP 端口不正确')
        user=env.get('PAPER_RADAR_SMTP_USER','')
        password=env.get('PAPER_RADAR_SMTP_PASSWORD','')
        # TLS is mandatory. Plain SMTP is intentionally unsupported.
        if port==465:
            with smtplib.SMTP_SSL(host,port,timeout=12) as smtp:
                if user:smtp.login(user,password)
                smtp.send_message(message)
        else:
            with smtplib.SMTP(host,port,timeout=12) as smtp:
                smtp.ehlo();smtp.starttls();smtp.ehlo()
                if user:smtp.login(user,password)
                smtp.send_message(message)
    # A failed send never updates the outbox; retries are explicit.
    ids=[int(row['event_id']) for row in rows]
    for event_id in ids:
        db.execute('''UPDATE following_notifications SET email_sent_at=?
            WHERE topic_id=? AND event_id=? AND email_sent_at='' ''',
            (utc_now(),topic_id,event_id))
    return {'state':'sent','sent':len(ids)}
