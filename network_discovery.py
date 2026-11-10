"""Read-only Modbus TCP discovery; no relay writes or password attempts."""
import socket
import struct
from concurrent.futures import ThreadPoolExecutor, as_completed
from ipaddress import IPv4Address, IPv4Network
from PySide6.QtCore import QThread, Signal, Qt
from PySide6.QtNetwork import QNetworkInterface
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
                              QPushButton, QTableWidget, QTableWidgetItem,
                              QAbstractItemView, QHeaderView, QSpinBox)


def receive(sock, size):
    data = b''
    while len(data) < size:
        part = sock.recv(size - len(data))
        if not part:
            raise OSError('Incomplete reply')
        data += part
    return data


def probe_mei(ip, port):
    try:
        with socket.create_connection((ip, port), timeout=0.5) as sock:
            sock.settimeout(0.7)
            # FC43 / MEI14: basic device identification, unit identifier 1.
            sock.sendall(bytes.fromhex('000100000005012b0e0100'))
            transaction, protocol, length, unit = struct.unpack('>HHHB', receive(sock, 7))
            if transaction != 1 or protocol != 0 or unit != 1 or not 2 <= length <= 254:
                return None
            body = receive(sock, length - 1)
            if body[0] == 0xAB and len(body) == 2:
                return dict(ip=ip, port=port, identity='Modbus TCP · model unverified')
            if len(body) < 7 or body[:3] != b'\x2b\x0e\x01':
                return None
            objects, offset = {}, 7
            for _ in range(body[6]):
                if offset + 2 > len(body):
                    return None
                key, size = body[offset:offset+2]
                offset += 2
                if offset + size > len(body):
                    return None
                objects[key] = body[offset:offset+size].decode('utf-8', 'replace')
                offset += size
            identity = ' · '.join(objects[k] for k in (0, 1, 2) if objects.get(k))
            return dict(ip=ip, port=port, identity=identity or 'Modbus TCP · model unverified')
    except (OSError, ValueError, struct.error):
        return None


def probe(ip, port):
    result = probe_mei(ip,port)
    if result and 'unverified' not in result['identity']:
        return result
    # Older E1214 firmware may not implement FC43; use documented model registers.
    from moxa_backend import E1214Client
    try:
        model = E1214Client(ip,port,500).identify()
        return dict(ip=ip,port=port,identity=model)
    except Exception:
        return result


class ScanWorker(QThread):
    found = Signal(dict)
    progress = Signal(int, int)

    def __init__(self, hosts, port, parent=None):
        super().__init__(parent)
        self.hosts, self.port = hosts, port

    def run(self):
        with ThreadPoolExecutor(max_workers=24) as pool:
            futures = [pool.submit(probe, ip, self.port) for ip in self.hosts]
            for count, future in enumerate(as_completed(futures), 1):
                if self.isInterruptionRequested():
                    for pending in futures:
                        pending.cancel()
                    break
                result = future.result()
                if result:
                    self.found.emit(result)
                self.progress.emit(count, len(futures))


class DiscoveryDialog(QDialog):
    selected = Signal(dict)

    def __init__(self, devices, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Find network units')
        self.resize(790, 470)
        self.devices, self.results, self.worker = devices, {}, None
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel('Enter an IPv4 address only · scan its local range (read-only)'))
        row = QHBoxLayout()
        self.network = QComboBox()
        self.network.setEditable(True)
        networks = set()
        for interface in QNetworkInterface.allInterfaces():
            for entry in interface.addressEntries():
                ip = entry.ip().toString()
                try:
                    network = IPv4Network(f'{ip}/{entry.prefixLength()}', strict=False)
                    if not network.is_loopback:
                        networks.add(ip)
                except ValueError:
                    pass
        self.network.addItems(sorted(networks) or ['169.254.10.1'])
        row.addWidget(self.network, 1)
        row.addWidget(QLabel('Port'))
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(502)
        row.addWidget(self.port)
        self.scan = QPushButton('Scan network')
        self.scan.clicked.connect(self.start_scan)
        row.addWidget(self.scan)
        self.cancel = QPushButton('Cancel scan')
        self.cancel.setEnabled(False)
        self.cancel.clicked.connect(self.cancel_scan)
        row.addWidget(self.cancel)
        layout.addLayout(row)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(['IP address', 'Port', 'Identification', 'Added / New'])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setFixedHeight(180)
        self.table.setShowGrid(True)
        self.table.setGridStyle(Qt.PenStyle.SolidLine)
        self.table.verticalHeader().setDefaultSectionSize(28)
        self.table.verticalHeader().hide()
        layout.addWidget(self.table)
        self.status = QLabel('Saved units are listed below. Scan to find additional units; identification may be unavailable on some models.')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.use = QPushButton('Use selected unit in add / edit form')
        self.use.setEnabled(False)
        self.use.clicked.connect(self.use_selected)
        self.table.itemSelectionChanged.connect(lambda: self.use.setEnabled(self.table.currentRow() >= 0 and not (self.worker and self.worker.isRunning())))
        layout.addWidget(self.use)
        self.show_saved()

    def show_saved(self):
        for device in self.devices:
            self.add_result(dict(ip=device['ip'], port=device['port'], identity=f'{device["name"]} · saved, not scanned'))

    def add_result(self, result):
        key = (result['ip'], result['port'])
        row = self.results.get(key)
        if row is None:
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.results[key] = row
        added = any(d['ip'] == result['ip'] for d in self.devices)
        for col, text in enumerate((result['ip'], str(result['port']), result['identity'], 'Already added' if added else 'New · verify model')):
            item = QTableWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, result)
            self.table.setItem(row, col, item)

    def start_scan(self):
        try:
            address = str(IPv4Address(self.network.currentText().strip()))
            network = IPv4Network(address + '/24', strict=False)
            if network.num_addresses > 256 or network.is_multicast or network.is_unspecified:
                raise ValueError()
        except ValueError:
            self.status.setText('Enter an IPv4 address, for example 192.168.1.10.')
            return
        self.table.setRowCount(0)
        self.results.clear()
        self.show_saved()
        self.scan.setEnabled(False)
        self.cancel.setEnabled(True)
        self.use.setEnabled(False)
        self.network.setEnabled(False)
        self.port.setEnabled(False)
        self.status.setText('Scanning…')
        self.worker = ScanWorker([str(ip) for ip in network.hosts()], self.port.value(), self)
        self.worker.found.connect(self.add_result)
        self.worker.progress.connect(lambda n, total: self.status.setText(f'Scanning {n} / {total} addresses…'))
        self.worker.finished.connect(self.scan_finished)
        self.worker.start()

    def cancel_scan(self):
        if self.worker:
            self.worker.requestInterruption()
            self.status.setText('Cancelling scan…')

    def scan_finished(self):
        cancelled = self.worker.isInterruptionRequested()
        self.status.setText(('Scan cancelled.' if cancelled else 'Scan complete.') + ' Select a result, then save it in the form. Unidentified or protected devices can be added manually.')
        self.scan.setEnabled(True)
        self.cancel.setEnabled(False)
        self.network.setEnabled(True)
        self.port.setEnabled(True)
        self.use.setEnabled(self.table.currentRow() >= 0)

    def use_selected(self):
        item = self.table.item(self.table.currentRow(), 0)
        if item:
            self.selected.emit(item.data(Qt.ItemDataRole.UserRole))

    def reject(self):
        if self.worker and self.worker.isRunning():
            self.cancel_scan()
            return
        super().reject()

