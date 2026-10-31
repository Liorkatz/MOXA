"""Compact timing preview of each channel's configured pulse sequence."""
from time import monotonic
from PySide6.QtCore import Qt, QPointF, QRectF, QTimer
from PySide6.QtGui import QColor, QPainter, QPen, QPainterPath
from PySide6.QtWidgets import QWidget


class AutomationLegend(QWidget):
    """Always-visible key for the automation settings, matching the reference."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(391, 75)
        self.setToolTip('Digital In / Run starts First Delay. Each pulse stays ON for Pulse, '
                        'then OFF for Delay Between. Repeat is the total number of pulses.')

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.scale(self.width() / 460, self.height() / 88)
        font = painter.font()
        font.setPointSize(8)
        painter.setFont(font)
        painter.setPen(QColor('#53708c'))
        center = Qt.AlignmentFlag.AlignCenter
        for rect, text in [
            (QRectF(0, 22, 100, 32), 'Digital In\n/ Run'),
            (QRectF(109, 18, 69, 32), 'First\nDelay'),
            (QRectF(180, 0, 55, 24), 'Pulse'),
            (QRectF(236, 18, 64, 32), 'Delay\nBetween'),
            (QRectF(300, 0, 55, 24), 'Pulse'),
            (QRectF(356, 18, 80, 32), 'Delay\nBetween'),
        ]:
            painter.drawText(rect, center, text)
        painter.setPen(QPen(QColor('#1668b2'), 2))
        path = QPainterPath(QPointF(101, 56))
        for x, y in [(180, 56), (180, 27), (235, 27), (235, 56),
                     (300, 56), (300, 27), (355, 27), (355, 56), (438, 56)]:
            path.lineTo(x, y)
        painter.drawPath(path)
        painter.drawLine(QPointF(101, 56), QPointF(95, 52))
        painter.drawLine(QPointF(101, 56), QPointF(95, 60))
        painter.setPen(QPen(QColor('#8da9c8'), 1))
        painter.drawLine(180, 63, 180, 69)
        painter.drawLine(180, 69, 355, 69)
        painter.drawLine(355, 69, 355, 63)
        painter.setPen(QColor('#53708c'))
        painter.drawText(QRectF(180, 70, 175, 18), center, 'Repeat × N pulses')


class AutomationTimeline(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.device = None
        self.started = None
        self.elapsed = None
        self.running_configs = None
        self.paused = False
        self.timer = QTimer(self)
        self.timer.setInterval(40)
        self.timer.timeout.connect(self.advance)
        self.setMinimumHeight(410)
        self.setToolTip('Configuration preview in milliseconds. Up to 10 pulses shown per channel. '
                        'First Delay → ON for Pulse → OFF for Delay Between → repeat.')

    def set_device(self, device):
        self.device = device
        self.update()

    def set_jobs(self, jobs):
        active = {i: job for (uid, i), job in jobs.items() if uid == self.device['uid'] and job['config'].get('kind') != 'manual'}
        if active:
            started = self.device.get('_clock_started', min(job['started'] for job in active.values()))
            if self.started != started:
                self.elapsed = 0
            self.started = started
            self.running_configs = [dict(c) for c in self.device['config']]
            for i, job in active.items():
                self.running_configs[i] = job['config']
            self.paused = all(job.get('paused') for job in active.values())
            if self.paused:
                self.elapsed = max(0, (min(job['paused'] for job in active.values()) - started) * 1000)
                self.timer.stop()
            else:
                self.timer.start()
                self.advance()
        else:
            self.paused = False
            if self.timer.isActive():
                self.advance()
            self.timer.stop()
            self.started = None
        self.update()

    def advance(self):
        if self.started is not None:
            self.elapsed = max(0, (monotonic() - self.started) * 1000)
        if self.isVisible():
            self.update()

    def paintEvent(self, event):
        if self.device is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor('#f3f7fc'))
        painter.setPen(QColor('#20314b'))
        painter.drawText(12, 21, 'Timing preview · ms · first 10 pulses')
        font = painter.font()
        font.setPointSize(8)
        painter.setFont(font)
        painter.setPen(QColor('#53708c'))
        painter.drawText(12, 41, 'ms')
        configs = self.running_configs if self.timer.isActive() or self.paused else self.device['config']
        def duration(cfg):
            n = min(10, cfg['repeat'])
            return cfg['first'] + n * cfg['pulse'] + max(0, n - 1) * cfg['between']
        total = max([duration(c) for c in configs if c['active'] and c['repeat']] or [1])
        left, right = 108, max(109, self.width() - 64)
        scale = (right - left) / max(1, total)
        boundaries = {0}
        guides = []
        for i, cfg in enumerate(configs):
            y = 76 + i * 52
            active = cfg['active'] and cfg['repeat'] > 0
            painter.setPen(QColor('#20314b' if active else '#8e98a6'))
            name = self.device['names'][i]
            name = painter.fontMetrics().elidedText(name, Qt.TextElideMode.ElideRight, 90)
            painter.drawText(10, y - 5, name)
            painter.setPen(QPen(QColor('#d8e1ec'), 1))
            painter.drawLine(QPointF(left, y), QPointF(right, y))
            if not active:
                painter.setPen(QColor('#8e98a6'))
                painter.drawText(int(left + 5), y - 5, 'Skipped')
                continue
            path = QPainterPath(QPointF(left, y))
            t = cfg['first']
            boundaries.add(t)
            guides.append((t, y))
            path.lineTo(left + t * scale, y)
            for pulse in range(min(10, cfg['repeat'])):
                path.lineTo(left + t * scale, y - 15)
                t += cfg['pulse']
                boundaries.add(t)
                guides.append((t, y))
                path.lineTo(left + t * scale, y - 15)
                path.lineTo(left + t * scale, y)
                if pulse < min(10, cfg['repeat']) - 1:
                    t += cfg['between']
                    boundaries.add(t)
                    guides.append((t, y))
                    path.lineTo(left + t * scale, y)
            painter.setPen(QPen(QColor('#1668b2'), 2))
            painter.drawPath(path)
            painter.setPen(QColor('#53708c'))
            painter.drawText(int(right + 8), y - 4, f'×{cfg["repeat"]}')
        painter.setPen(QPen(QColor('#a8bdd5'), 1, Qt.PenStyle.DashLine))
        guide_starts = {}
        for value, y in guides:
            guide_starts[value] = min(y, guide_starts.get(value, y))
        for value, y in guide_starts.items():
            x = left + value * scale
            painter.drawLine(QPointF(x, y + 3), QPointF(x, 357))
        painter.setPen(QColor('#53708c'))
        painter.drawLine(QPointF(left, 357), QPointF(right, 357))
        lanes = []
        for value in sorted(boundaries):
            x = left + value * scale
            label = f'{value:,}'
            width = painter.fontMetrics().horizontalAdvance(label) + 8
            labelLeft = max(left - 8, min(x - width / 2, right - width + 8))
            lane = next((i for i, edge in enumerate(lanes) if edge + 8 <= labelLeft), len(lanes))
            if lane == len(lanes):
                lanes.append(0)
            lanes[lane] = labelLeft + width
            painter.drawLine(QPointF(x, 354), QPointF(x, 361))
            painter.drawText(QRectF(labelLeft, 365 + lane * 18, width, 18), Qt.AlignmentFlag.AlignCenter, label)
        footer = 390 + len(lanes) * 18
        required = footer + 14
        if self.minimumHeight() != required:
            self.setMinimumHeight(required)
        if self.elapsed is not None:
            x = left + min(total, self.elapsed) * scale
            painter.setPen(QPen(QColor('#e17422'), 2))
            for i in range(6):
                y = 76 + i * 52
                painter.drawLine(QPointF(x, y - 20), QPointF(x, y + 3))
            painter.setPen(QColor('#ad5311'))
            state = 'Paused' if self.paused else ('Running' if self.timer.isActive() else 'Stopped / finished')
            painter.drawText(int(left), footer, f'{state} · {self.elapsed:,.0f} ms')
