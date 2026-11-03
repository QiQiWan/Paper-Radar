# Copyright (c) 2026 @EatRice. SPDX-License-Identifier: MIT
"""Small progress emitter shared by manual and scheduled operations.

Progress percentages represent coarse pipeline milestones. Unmeasurable
network waits remain at their current milestone instead of displaying a fake
percentage. The optional callback is (dict) -> None.
"""
from __future__ import annotations
from datetime import datetime
import time

class TaskProgress:
    def __init__(self, callback=None, label='检索'):
        self.callback = callback
        self.start = time.monotonic()
        self.last_percent = 0
        self.label = label

    def emit(self, percent: float, stage: str, *, current: int | None = None,
             total: int | None = None, detail: str = '') -> dict:
        percent = max(self.last_percent, min(100, max(0, int(round(percent)))))
        self.last_percent = percent
        data = {'percent':percent, 'stage':stage,
                'current':current,'total':total,'detail':detail[:260],
                'elapsed_seconds':int(time.monotonic()-self.start),
                'updated_at':datetime.now().astimezone().isoformat(timespec='seconds')}
        if self.callback:
            self.callback(data)
        return data
