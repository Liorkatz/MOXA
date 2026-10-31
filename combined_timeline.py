"""Shared time scale for all configured MOXA pulse sequences."""
from time import monotonic
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget


class CombinedTimeline(QWidget):
    COLORS = ('#1668b2', '#c35b13', '#16835c', '#8754ba', '#bb3e79')

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.setMinimumWidth(800)
        self.refresh = QTimer(self)
        self.refresh.setInterval(40)
        self.refresh.timeout.connect(self.update)
        self.refresh.start()

    def paintEvent(self, event):
        devices, jobs = self.controller.devices, self.controller.jobs
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor('#f7f9fc'))
        traces = []
        total = 1
        for device in devices:
            rows = []
            for i, config in enumerate(device['config']):
                job = jobs.get((device['uid'], i))
                if job and job['config'].get('kind') == 'manual': job = None
                cfg = job['config'] if job else config
                active = cfg.get('active', True) and cfg['repeat'] > 0
                n = min(10, cfg['repeat'])
                duration = cfg['first'] + n * cfg['pulse'] + max(0, n - 1) * cfg['between']
                if active:
                    total = max(total, duration)
                rows.append((cfg, active, job))
            traces.append(rows)
        left, right = 170, self.width() - 50
        scale = (right - left) / total
        painter.setPen(QColor('#53708c'))
        painter.drawText(12, 23, 'All units · shared time axis (ms) · first 10 pulses per channel')
        axis = 55 + len(devices) * 222
        boundaries = {0}
        guides = {}
        for unit, (device, rows) in enumerate(zip(devices, traces)):
            color = QColor(self.COLORS[unit % len(self.COLORS)])
            top = 50 + unit * 222
            painter.setPen(color)
            painter.drawText(12, top, f'{device["name"]} · {device["ip"]}')
            for i, (cfg, active, job) in enumerate(rows):
                y = top + 28 + i * 30
                painter.setPen(QColor('#53708c'))
                name = painter.fontMetrics().elidedText(device['names'][i], Qt.TextElideMode.ElideRight, 150)
                painter.drawText(12, y, name)
                painter.setPen(QPen(QColor('#dce5f0'), 1))
                painter.drawLine(QPointF(left, y), QPointF(right, y))
                if not active:
                    continue
                path = QPainterPath(QPointF(left, y))
                t = cfg['first']
                boundaries.add(t)
                guides[t] = min(y, guides.get(t, y))
                path.lineTo(left + t * scale, y)
                for pulse in range(min(10, cfg['repeat'])):
                    path.lineTo(left + t * scale, y - 14)
                    t += cfg['pulse']
                    boundaries.add(t)
                    guides[t] = min(y, guides.get(t, y))
                    path.lineTo(left + t * scale, y - 14)
                    path.lineTo(left + t * scale, y)
                    if pulse < min(10, cfg['repeat']) - 1:
                        t += cfg['between']
                        boundaries.add(t)
                        guides[t] = min(y, guides.get(t, y))
                        path.lineTo(left + t * scale, y)
                painter.setPen(QPen(color, 2))
                painter.drawPath(path)
                if job:
                    elapsed = ((job.get('paused') or monotonic()) - job['started']) * 1000
                    x = left + min(total, max(0, elapsed)) * scale
                    painter.setPen(QPen(color.darker(130), 2, Qt.PenStyle.DashLine))
                    painter.drawLine(QPointF(x, y - 19), QPointF(x, y + 4))
        painter.setPen(QPen(QColor('#a8bdd5'), 1, Qt.PenStyle.DashLine))
        for value, y in guides.items():
            x = left + value * scale
            painter.drawLine(QPointF(x, y + 3), QPointF(x, axis))
        painter.setPen(QColor('#53708c'))
        painter.drawLine(QPointF(left, axis), QPointF(right, axis))
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
            painter.drawLine(QPointF(x, axis - 3), QPointF(x, axis + 4))
            painter.drawText(QRectF(labelLeft, axis + 8 + lane * 18, width, 18), Qt.AlignmentFlag.AlignCenter, label)
        required = axis + 38 + len(lanes) * 18
        if self.minimumHeight() != required:
            self.setMinimumHeight(required)
