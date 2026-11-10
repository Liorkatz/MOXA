"""MOXA Control for physical ioLogik E1214 units."""
from pathlib import Path
from datetime import datetime
from ipaddress import IPv4Address
import sys
from copy import deepcopy
from time import monotonic
from math import ceil
from automation_timeline import AutomationTimeline, AutomationLegend  # Numeric event times are drawn below the preview.
from network_discovery import DiscoveryDialog
from combined_timeline import CombinedTimeline
from moxa_backend import E1214Client, HardwareQueue, make_client
import config_store
from PySide6.QtCore import QFile, QTimer, Qt, QObject, QEvent, QStandardPaths
from PySide6.QtUiTools import QUiLoader
from PySide6.QtGui import QIcon
from shiboken6 import isValid
from PySide6.QtWidgets import (QApplication, QAbstractItemView, QHeaderView,
                              QTableWidgetItem, QMessageBox, QFileDialog,
                              QWidget, QHBoxLayout, QVBoxLayout, QDialog,
                              QPushButton, QSizePolicy, QBoxLayout, QComboBox, QLineEdit, QTabBar, QFrame, QLabel, QTabWidget, QScrollArea, QCheckBox, QTableWidget, QLayout, QSpinBox)

ROOT = Path(__file__).resolve().parent
DI_ACTION_HELP = {
    'start': 'Starts this channel\'s pulse sequence using its configured timings and repeat count.',
    'off': 'Cancels this channel\'s current action and turns its output OFF. Other channels are unchanged.',
    'pause': 'Pauses automation on all channels in this unit without changing output states.',
    'continue': 'Resumes paused automation in this unit from where it stopped. Does nothing if no automation is paused.',
    'reset': 'Cancels automation in all units and restores the Continuous output states saved before the run.',
}


def load_ui(name):
    file = QFile(str(ROOT / name))
    if not file.open(QFile.OpenModeFlag.ReadOnly):
        raise RuntimeError(file.errorString())
    loader = QUiLoader()
    try:
        widget = loader.load(file)
        if widget is None:
            raise RuntimeError(loader.errorString())
        return widget
    finally:
        file.close()


STYLE = (ROOT / 'style.qss').read_text(encoding='utf-8').replace('__ASSET_ROOT__', (ROOT / 'assets').as_posix())


class SettingsSaveTimer(QTimer):
    def __init__(self, controller):
        super().__init__(controller.window)
        self.controller = controller

    def start(self, *args):
        self.controller.saveStatus.setText('Saving…')
        super().start(*args)


class SaveOnClose(QObject):
    def __init__(self, controller):
        super().__init__(controller.window)
        self.controller = controller
        controller.window.installEventFilter(self)

    def eventFilter(self, watched, event):
        c = self.controller
        if event.type() in (QEvent.Type.Close, QEvent.Type.WindowDeactivate):
            c.release_all_momentary()
        if event.type() == QEvent.Type.Close:
            for d in c.devices:
                for i in range(6):
                    if (d['uid'], i) in c.jobs: c.cancel(d, i)
            c.flush_outputs()
            c.cancel_countdown()
        if event.type() == QEvent.Type.Close and c.config_path:
            if c.saveTimer is not None: c.saveTimer.stop()
            while not c.save_configuration():
                choice = QMessageBox.warning(c.window, 'Settings not saved',
                    'Your latest settings could not be saved.\nRetry saving, exit without saving, or cancel to stay in the application.\n\n' + c.save_error,
                    QMessageBox.StandardButton.Retry | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                    QMessageBox.StandardButton.Cancel)
                if choice == QMessageBox.StandardButton.Retry: continue
                if choice == QMessageBox.StandardButton.Discard: break
                event.ignore()
                return True
            c.closeHandled = True
        return False


class MomentaryGuard(QObject):
    def __init__(self, controller, button):
        super().__init__(button)
        self.controller = controller
        button.installEventFilter(self)

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.Hide, QEvent.Type.FocusOut, QEvent.Type.WindowDeactivate):
            self.controller.release_momentary(watched)
        return False


class PanelLayoutAdapter(QObject):
    def __init__(self, panel):
        super().__init__(panel)
        self.panel = panel
        self.pending = False
        self.baseMargins = panel.manualGrid.contentsMargins()
        panel.installEventFilter(self)

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.Resize, QEvent.Type.LayoutRequest):
            p = self.panel
            needed = p.manualGroup.width() + p.automationGroup.width() + 40
            direction = QBoxLayout.Direction.LeftToRight
            minimum = max(p.deviceBar.sizeHint().width() + 24,
                          needed if p.automationGroup.isVisible() else p.manualGroup.width() + 24)
            if p.minimumWidth() != minimum:
                p.setMinimumWidth(minimum)
            maximum = 16777215 if p.automationGroup.isVisible() else minimum
            if p.maximumWidth() != maximum:
                p.setMaximumWidth(maximum)
            if p.controlColumns.direction() != direction:
                p.controlColumns.setDirection(direction)
            if not self.pending:
                self.pending = True
                QTimer.singleShot(0, self.align_rows)
        return False

    def align_rows(self):
        self.pending = False
        p = self.panel
        if not isValid(p): return
        if getattr(p, 'collapsed', False):
            return
        p.automationGroup.layout().activate()
        p.automationGrid.activate()
        autoCenter = p.automationGroup.geometry().center().x()
        p.automationHeading.move(autoCenter - p.automationHeading.width() // 2,
                                 p.deviceBar.geometry().top() + 5)
        margins = p.manualGrid.contentsMargins()
        top = self.baseMargins.top()
        if p.automationGroup.isVisible() and p.controlColumns.direction() == QBoxLayout.Direction.LeftToRight:
            manual = p.channelName1.mapTo(p, p.channelName1.rect().center()).y()
            auto = p.firstDelay1.mapTo(p, p.firstDelay1.rect().center()).y()
            top = max(top, margins.top() + auto - manual)
            if manual > auto and margins.top() == self.baseMargins.top():
                autoMargins = p.automationGrid.contentsMargins()
                p.automationGrid.setContentsMargins(
                    autoMargins.left(), autoMargins.top() + manual - auto,
                    autoMargins.right(), autoMargins.bottom())
        if margins.top() != top:
            p.manualGrid.setContentsMargins(margins.left(), top, margins.right(), margins.bottom())
        if p.automationGroup.isVisible() and p.controlColumns.direction() == QBoxLayout.Direction.LeftToRight:
            height = max(p.manualGroup.sizeHint().height(), p.automationGroup.sizeHint().height())
            for group in (p.manualGroup, p.automationGroup):
                if group.height() != height:
                    group.setFixedHeight(height)
        elif not p.automationGroup.isVisible():
            p.manualGroup.setMinimumHeight(0)
            p.manualGroup.setMaximumHeight(16777215)
        for index, line in enumerate(p.rowSeparators, 1):
            field = getattr(p, f'firstDelay{index}')
            following = getattr(p, f'firstDelay{index + 1}')
            bottom = field.mapTo(line.parentWidget(), field.rect().bottomLeft()).y()
            nextTop = following.mapTo(line.parentWidget(), following.rect().topLeft()).y()
            line.setGeometry(4, (bottom + nextTop) // 2, line.parentWidget().width() - 8, 1)
            line.lower()
        for index, background in enumerate(p.rowBackgrounds, 1):
            field = getattr(p, f'firstDelay{index}')
            top = field.mapTo(background.parentWidget(), field.rect().topLeft()).y() - 3
            background.setGeometry(0, top, background.parentWidget().width(), 36)
            background.lower()


class Controller:
    def __init__(self, config_path=None):
        self.config_path = config_path
        self.default_mode = 'real'
        self.pending_outputs = {}
        self.held_momentary = {}
        self.reset_input = {'ip': '', 'channel': 1}
        self.scenarios = {}
        self.config_load_failed = False
        self.saveTimer = None
        self.save_error = ''
        self.closeHandled = False
        self.countdownDialog = None
        self.window = load_ui('moxa_control.ui')
        self.window.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, True)
        self.window.setWindowFlag(Qt.WindowType.WindowTitleHint, True)
        self.fitTimer = QTimer(self.window)
        self.fitTimer.setSingleShot(True)
        self.fitTimer.timeout.connect(self.fit_window_height)
        self.maximizeTimer = QTimer(self.window)
        self.maximizeTimer.setSingleShot(True)
        self.maximizeTimer.timeout.connect(self.maximize_automation)
        self.window.brandLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.window.mainLayout.setSpacing(2)
        self.window.mainLayout.setContentsMargins(6, 2, 6, 4)
        self.home = load_ui('home.ui')
        self.automationLegend = AutomationLegend(self.window)
        self.automationLegend.setFixedSize(414, 70)
        legendPolicy = self.automationLegend.sizePolicy()
        legendPolicy.setRetainSizeWhenHidden(False)
        self.automationLegend.setSizePolicy(legendPolicy)
        self.automationLegend.hide()
        self.manage = load_ui('manage_moxa.ui')
        self.connections = load_ui('add_moxa.ui')
        self.logs = load_ui('log.ui')
        self.settings = load_ui('settings.ui')
        self.settings.setParent(self.window, Qt.WindowType.Dialog)
        self.hardware = HardwareQueue(self.window)
        self.clients = {}
        self.output_versions = {}
        self.window.destroyed.connect(self.hardware.close)
        self.connections.manageContainer.layout().addWidget(self.manage)
        self.connections.testConnectionButton.setText('Test connection')
        self.manage.toggleDeviceButton.setText('Connect / Disconnect')
        self.manage.deviceHint.setText('Add or edit units here. Connect beside the IP on the Home page.')
        self.connections.passwordInput.setToolTip('Optional MOXA MXIO login. Requires official 64-bit MXIO.dll in vendor/. Password is saved in the text settings file.')
        self.connections.connectionsLayout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.connections.connectionsLayout.setSpacing(8)
        self.connections.connectionsLayout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        self.connections.settingsButton.setFixedWidth(110)
        self.connections.connectionTitleBar.setStretch(0, 0)
        self.connections.connectionTitleBar.setAlignment(Qt.AlignmentFlag.AlignLeft)
        form = self.connections.connectionForm
        while form.count():
            form.takeAt(0)
        form.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(8)
        self.passwordDefaultHint = QLabel('Factory default: moxa · leave blank if no password is required.', self.connections)
        self.passwordDefaultHint.setWordWrap(True)
        form.addWidget(self.passwordDefaultHint, 4, 2, 1, 2)
        for row, left, right in [(0, 'unitSelector', 'portInput'),
                                 (1, 'nameInput', 'timeoutInput'),
                                 (2, 'ipInput', 'passwordInput')]:
            for col, name in [(0, left), (2, right)]:
                form.addWidget(getattr(self.connections, name + 'Label'), row, col)
                field = getattr(self.connections, name)
                field.setFixedWidth(240 if name not in ('portInput', 'timeoutInput') else 120)
                form.addWidget(field, row, col + 1, Qt.AlignmentFlag.AlignLeft)
        self.connections.connectionActions.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.scanNetworkButton = QPushButton('Scan / Add unit…', self.connections)
        self.scanNetworkButton.setFixedWidth(180)
        self.connections.connectionTitleBar.insertWidget(1, self.scanNetworkButton)
        self.scanNetworkButton.clicked.connect(self.open_discovery)
        for name in ('newUnitButton', 'saveConnectionButton', 'testConnectionButton'):
            getattr(self.connections, name).setFixedWidth(200 if name == 'testConnectionButton' else 120)
        self.connectionEditor = QWidget()
        editorLayout = QVBoxLayout(self.connectionEditor)
        editorLayout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.editorLayoutItems = []
        for part in (self.connections.connectionForm, self.connections.connectionActions):
            for index in range(self.connections.connectionsLayout.count()):
                if self.connections.connectionsLayout.itemAt(index).layout() is part:
                    self.editorLayoutItems.append(self.connections.connectionsLayout.takeAt(index))
                    break
            part.setParent(None)
            editorLayout.addLayout(part)
            for index in range(part.count()):
                widget = part.itemAt(index).widget()
                if widget is not None:
                    widget.setParent(self.connectionEditor)
        self.connections.connectionsLayout.removeWidget(self.connections.connectionMessage)
        editorLayout.addWidget(self.connections.connectionMessage)
        self.connectionEditor.hide()
        self.connections.connectionsHelp.setText('Scan for network units or add one manually. Select a saved unit below to edit it.')
        self.manage.deviceTable.setFixedHeight(160)
        self.manage.deviceTable.setMaximumWidth(1000)
        self.manage.deviceTable.setShowGrid(True)
        self.manage.deviceTable.setGridStyle(Qt.PenStyle.SolidLine)
        self.manage.deviceTable.verticalHeader().setDefaultSectionSize(30)
        self.manage.deviceTable.setAlternatingRowColors(True)
        self.manage.manageLayout.setSpacing(8)
        self.manage.manageLayout.setContentsMargins(0, 8, 0, 8)
        self.manage.manageLayout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        self.manage.manageLayout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.connections.manageContainer.layout().setAlignment(Qt.AlignmentFlag.AlignTop)
        self.manage.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self.connections.manageContainer.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        for name in ('editDeviceButton', 'removeDeviceButton', 'toggleDeviceButton'):
            button = getattr(self.manage, name)
            button.setProperty('compactManage', True)
            button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.manage.manageActions.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.manage.manageActions.setSpacing(6)
        self.pages = [self.home, self.connections, self.logs]
        self.homeToolbar = QWidget()
        toolbar = QHBoxLayout(self.homeToolbar)
        toolbar.setContentsMargins(8, 0, 8, 0)
        toolbar.setSpacing(8)
        while self.home.toolbar.count():
            toolbar.addWidget(self.home.toolbar.takeAt(0).widget())
        self.home.homeLayout.removeItem(self.home.toolbar)
        toolbar.removeWidget(self.home.showAutomation)
        self.navigationButtons = []
        for index, title in enumerate(('Home', 'Settings', 'LOG')):
            button = QPushButton(title)
            button.setObjectName('navigationButton')
            button.setCheckable(True)
            button.setChecked(index == 0)
            button.clicked.connect(lambda checked=False, index=index: self.window.screens.setCurrentIndex(index))
            self.navigationButtons.append(button)
            toolbar.insertWidget(index, button)
        toolbar.insertWidget(2, self.home.showAutomation)
        toolbar.removeWidget(self.home.askConfirm)
        self.window.screens.tabBar().hide()
        self.window.screens.currentChanged.connect(self.on_page_changed)
        self.home.runBothButton.setFixedWidth(140)
        self.home.runBothButton.setProperty('compactManage', True)
        self.allPulsesButton = QPushButton('All pulses…', self.homeToolbar)
        self.allPulsesButton.setFixedWidth(100)
        self.allPulsesButton.setProperty('compactManage', True)
        allPulsesPolicy = self.allPulsesButton.sizePolicy()
        allPulsesPolicy.setRetainSizeWhenHidden(False)
        self.allPulsesButton.setSizePolicy(allPulsesPolicy)
        self.allPulsesButton.hide()
        self.allPulsesButton.clicked.connect(self.show_all_pulses)
        toolbar.addWidget(self.allPulsesButton)
        self.resetBothButton = QPushButton('RESET ALL', self.homeToolbar)
        self.resetBothButton.setFixedWidth(100)
        self.resetBothButton.setProperty('compactManage', True)
        resetPolicy = self.resetBothButton.sizePolicy()
        resetPolicy.setRetainSizeWhenHidden(False)
        self.resetBothButton.setSizePolicy(resetPolicy)
        self.resetBothButton.hide()
        self.resetBothButton.clicked.connect(self.reset_all_devices)
        toolbar.addWidget(self.resetBothButton)
        self.home.runBothButton.setVisible(self.home.showAutomation.isChecked())
        toolbar.insertStretch(5, 1)
        toolbar.insertWidget(6, self.automationLegend)
        toolbar.insertStretch(7, 1)
        self.homeToolbar.setFixedHeight(40)
        self.homeToolbar.setMinimumWidth(toolbar.sizeHint().width())
        self.toolbarScroll = QScrollArea(self.window)
        self.toolbarScroll.setWidgetResizable(True)
        self.toolbarScroll.setFrameShape(QFrame.Shape.NoFrame)
        self.toolbarScroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.toolbarScroll.setFixedHeight(42)
        self.toolbarScroll.setWidget(self.homeToolbar)
        self.window.mainLayout.insertWidget(1, self.toolbarScroll)
        self.saveStatus = QLabel('Automatic saving', self.window)
        self.saveStatus.setMinimumWidth(105)
        self.saveStatus.setToolTip('Settings are saved automatically after changes and when closing the application.')
        self.window.statusbar.addPermanentWidget(self.saveStatus)
        self.lampState = 'idle'
        self.lampLit = False
        self.lampTimer = QTimer(self.window)
        self.lampTimer.setInterval(450)
        self.lampTimer.timeout.connect(self.blink_run_lamp)
        self.window.screens.currentChanged.connect(
            lambda index: self.automationLegend.setVisible(index == 0 and self.home.showAutomation.isChecked()))
        for name, page in zip(['homeTab','connectionsTab','logTab'], self.pages):
            if page is self.connections:
                self.connectionsScroll = QScrollArea()
                self.connectionsScroll.setWidgetResizable(True)
                self.connectionsScroll.setWidget(page)
                getattr(self.window, name).layout().addWidget(self.connectionsScroll)
            else:
                getattr(self.window, name).layout().addWidget(page)
            getattr(self.window, name).layout().setContentsMargins(4, 2, 4, 4)
        self.home.homeLayout.setContentsMargins(4, 2, 4, 4)
        self.default_pulse = 0
        self.timestamps = True
        self.connections.settingsButton.hide()
        self.settings.setParent(self.connections, Qt.WindowType.Widget)
        self.settings.setMaximumWidth(600)
        self.settings.setMinimumWidth(520)
        self.settings.settingsLayout.setContentsMargins(10, 8, 10, 8)
        self.settings.settingsLayout.setSpacing(6)
        self.settings.settingsLayout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        self.settings.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.settings.dialogButtons.hide()
        self.settings.defaultPulse.setFixedWidth(150)
        self.settings.defaultPulse.setMinimum(0)
        self.settings.defaultPulse.setValue(0)
        self.settings.defaultPulse.hide()
        self.settings.durationLabel.hide()
        self.connections.connectionsLayout.addWidget(self.settings, 0, Qt.AlignmentFlag.AlignLeft)
        self.settings.show()
        self.autoPulsePreview = QCheckBox('Open pulse preview when automation starts', self.settings)
        self.autoPulsePreview.setToolTip('Opens the unit pulse graph on START, or the combined graph on RUN ALL.')
        self.settings.settingsLayout.addWidget(self.autoPulsePreview)
        countdownRow = QHBoxLayout()
        self.countdownEnabled = QCheckBox('Countdown before RUN', self.settings)
        self.countdownSeconds = QSpinBox(self.settings)
        self.countdownSeconds.setRange(1, 120)
        self.countdownSeconds.setValue(3)
        self.countdownSeconds.setSuffix(' s')
        self.countdownSeconds.setFixedWidth(90)
        self.countdownSeconds.setEnabled(False)
        countdownRow.addWidget(self.countdownEnabled)
        countdownRow.addWidget(self.countdownSeconds)
        countdownRow.addStretch()
        self.settings.settingsLayout.addLayout(countdownRow)
        self.countdownEnabled.toggled.connect(self.countdownSeconds.setEnabled)
        for signal in (self.countdownEnabled.toggled, self.countdownSeconds.valueChanged):
            signal.connect(lambda _: self.saveTimer.start(400) if self.saveTimer is not None else None)
        self.autoPulsePreview.toggled.connect(lambda _: self.saveTimer.start(400) if self.saveTimer is not None else None)
        self.pendingPulseUnits = set()
        self.pendingPulseAll = False
        self.autoPulseTimer = QTimer(self.window)
        self.autoPulseTimer.setSingleShot(True)
        self.autoPulseTimer.timeout.connect(self.open_automatic_pulses)
        confirmationSection = QFrame(self.connections)
        confirmationSection.setObjectName('confirmationSection')
        confirmationSection.setMaximumWidth(600)
        confirmationSection.setMinimumWidth(520)
        confirmationSection.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        confirmationLayout = QVBoxLayout(confirmationSection)
        confirmationLayout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        confirmationLayout.setContentsMargins(10, 8, 10, 8)
        confirmationLayout.setSpacing(6)
        confirmationLayout.addWidget(QLabel('Action confirmation'))
        confirmationHelp = QLabel('Ask before changing multiple outputs\nor starting automation with RUN ALL.')
        confirmationLayout.addWidget(confirmationHelp)
        self.home.askConfirm.setParent(confirmationSection)
        self.home.askConfirm.setText('Require confirmation before running actions')
        self.home.askConfirm.setToolTip('Ask for confirmation before bulk manual actions or RUN ALL.')
        confirmationLayout.addWidget(self.home.askConfirm)
        self.connections.connectionsLayout.addWidget(confirmationSection, 0, Qt.AlignmentFlag.AlignLeft)
        self.home.askConfirm.show()
        self.home.askConfirm.toggled.connect(lambda _: self.saveTimer.start(400) if self.saveTimer is not None else None)
        self.settings.settingsNote.setText('Settings are saved locally.')
        self.devices = []
        self.entries = []
        self.panels = []
        self.auto_panels = []
        self.jobs = {}
        self.polling = set()
        self.pollTimer = QTimer(self.window)
        self.pollTimer.setInterval(250)
        self.pollTimer.timeout.connect(self.poll_inputs)
        self.pollTimer.start()
        self.settings.defaultPulse.valueChanged.connect(self.apply_inline_settings)
        self.settings.timestampsCheck.toggled.connect(self.apply_inline_settings)
        self.editing = None
        self.connections.settingsButton.clicked.connect(self.show_settings)
        self.settings.dialogButtons.accepted.connect(self.save_settings)
        self.settings.dialogButtons.rejected.connect(self.settings.reject)
        self.home.showAutomation.toggled.connect(self.toggle_automation)
        self.home.devicesScroll.widget().layout().setSpacing(24)
        self.home.runBothButton.clicked.connect(self.run_both)
        self.manage.editDeviceButton.clicked.connect(self.edit_connection)
        self.manage.removeDeviceButton.clicked.connect(self.remove_device)
        self.manage.toggleDeviceButton.hide()
        self.connections.newUnitButton.clicked.connect(self.new_connection)
        self.connections.unitSelector.currentIndexChanged.connect(self.load_connection)
        self.connections.saveConnectionButton.clicked.connect(self.save_connection)
        self.connections.testConnectionButton.clicked.connect(self.test_connection)
        self.logs.filterInput.textChanged.connect(self.render_log)
        self.logs.clearLogButton.clicked.connect(self.clear_log)
        self.logs.exportLogButton.clicked.connect(self.export_log)
        table = self.manage.deviceTable
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.manage.manageLayout.setAlignment(table, Qt.AlignmentFlag.AlignLeft)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.verticalHeader().hide()
        self.settings.setMinimumWidth(450)
        self.window.resize(1280, 900)
        self.window.setMinimumSize(950, 650)
        if self.config_path:
            self.load_configuration()
            self.saveTimer = SettingsSaveTimer(self)
            self.saveTimer.setSingleShot(True)
            self.saveTimer.timeout.connect(self.save_configuration)
            self.settings.defaultPulse.setValue(self.default_pulse)
            self.settings.timestampsCheck.setChecked(self.timestamps)
        self.rebuild()
        self.log('MOXA Control ready. Real units require an explicit Connect; no automatic output changes at startup.')
        self.window.screens.currentChanged.connect(lambda _: self.update_window_width())
        self.window.screens.currentChanged.connect(lambda _: self.update_home_actions())
        self.update_window_width()
        self.saveCloseGuard = SaveOnClose(self)
        fileMenu = self.window.menuBar().addMenu('&File')
        for title, shortcut, callback in [('&Open…','Ctrl+O',self.open_configuration_file), ('&Save','Ctrl+S',self.save_file), ('Save &As…','Ctrl+Shift+S',self.save_file_as)]:
            action = fileMenu.addAction(title)
            action.setShortcut(shortcut)
            action.triggered.connect(callback)
        self.update_file_title()

    def update_file_title(self):
        name = Path(self.config_path).name if self.config_path else 'Untitled'
        self.window.setWindowTitle(f'MOXA Control — {name}')

    def save_file(self):
        if not self.config_path: return self.save_file_as()
        if self.saveTimer is not None: self.saveTimer.stop()
        if not self.save_configuration():
            QMessageBox.warning(self.window,'Save failed',self.save_error)
            return False
        return True

    def save_file_as(self):
        filename, _ = QFileDialog.getSaveFileName(self.window,'Save configuration as',str(self.config_path or ROOT/'moxa_settings.json'),'MOXA configuration (*.json)')
        if not filename: return False
        if not Path(filename).suffix: filename += '.json'
        old_path, old_failed = self.config_path, self.config_load_failed
        self.config_path, self.config_load_failed = Path(filename), False
        if self.saveTimer is not None: self.saveTimer.stop()
        if not self.save_configuration():
            self.config_path, self.config_load_failed = old_path, old_failed
            QMessageBox.warning(self.window,'Save failed',self.save_error)
            return False
        if self.saveTimer is None:
            self.saveTimer = SettingsSaveTimer(self)
            self.saveTimer.setSingleShot(True)
            self.saveTimer.timeout.connect(self.save_configuration)
        self.update_file_title()
        return True

    def open_configuration_file(self):
        self.cancel_countdown()
        if self.jobs or any(d.get('connecting') for d in self.devices):
            QMessageBox.information(self.window,'Automation active','RESET automation and wait for connections to finish before opening another configuration.')
            return False
        filename, _ = QFileDialog.getOpenFileName(self.window,'Open configuration',str(self.config_path or ROOT),'MOXA configuration (*.json)')
        if not filename: return False
        # Flush the working configuration before replacing it.
        if self.config_path and not self.config_load_failed and not self.save_file(): return False
        if self.saveTimer is not None: self.saveTimer.stop()
        old_path, old_failed = self.config_path, self.config_load_failed
        self.config_path, self.config_load_failed = Path(filename), False
        if not self.load_configuration():
            self.config_path, self.config_load_failed = old_path, old_failed
            if self.saveTimer is not None: self.saveTimer.stop()
            self.saveStatus.setText('Open failed')
            QMessageBox.warning(self.window,'Open failed','The configuration could not be loaded. The current configuration and the selected file were preserved.')
            return False
        for uid, client in list(self.clients.items()):
            if hasattr(client,'close'):
                self.hardware.submit(uid,client.close,lambda result: None,lambda error: None)
        self.clients.clear()
        if self.saveTimer is None:
            self.saveTimer = SettingsSaveTimer(self)
            self.saveTimer.setSingleShot(True)
            self.saveTimer.timeout.connect(self.save_configuration)
        self.settings.timestampsCheck.setChecked(self.timestamps)
        self.rebuild()
        if self.saveTimer is not None: self.saveTimer.stop()
        self.saveStatus.setText('Saved')
        self.update_file_title()
        return True

    def update_window_width(self):
        managing = self.window.screens.currentIndex() == 1
        self.home.showAutomation.setVisible(not managing)
        self.navigationButtons[2].setVisible(not managing)
        compact = self.window.screens.currentIndex() == 0 and not self.home.showAutomation.isChecked()
        if compact:
            card_width = max((max(p.deviceBar.sizeHint().width() + 24,
                                  p.manualGroup.width() + 24) for p in self.panels), default=660)
            width = max(720, card_width + 60, self.homeToolbar.layout().sizeHint().width() + 12)
            # A fixed top-level width makes Windows disable maximization and
            # can leave its native window state stale on the next toggle.
            self.window.setMaximumWidth(16777215)
            if self.window.isMaximized():
                self.window.showNormal()
            self.window.setMinimumWidth(width)
            self.window.resize(width, self.window.height())
            self.fitTimer.start(30)
        else:
            self.window.setMaximumWidth(16777215)
            self.window.setMinimumWidth(950)
            self.fitTimer.stop()
            self.window.setMinimumHeight(min(650, self.window.screen().availableGeometry().height() - 40))
            self.window.resize(max(950, self.window.width()), max(self.window.minimumHeight(), self.window.height()))

    def fit_window_height(self):
        if not isValid(self.window): return
        if self.home.showAutomation.isChecked() or self.window.screens.currentIndex() != 0:
            return
        self.window.setMinimumHeight(240)
        height = sum((p.height() if getattr(p, 'collapsed', False) else p.sizeHint().height())
                     for p in self.panels)
        height += max(0, len(self.panels) - 1) * 24 + 180
        self.window.resize(self.window.width(), min(max(240, height), self.window.screen().availableGeometry().height() - 40))

    def new_device(self, number):
        return dict(uid=number, name=f'MOXA {number}', ip=f'169.254.10.{80+number}', port=502,
                    timeout=2000, password='', mode='real', connected=False, inputs=[False]*6, names=[f'Channel{i+1}' for i in range(6)],
                    states=[False]*6, config=[dict(active=True, first=(i*500), pulse=1000,
                    repeat=3, between=1000, trigger=0) for i in range(6)])

    def log(self, text):
        self.entries.append((datetime.now(), text))
        self.entries = self.entries[-2000:]
        self.render_log()
        if self.saveTimer is not None:
            self.saveTimer.start(400)

    def load_configuration(self):
        if not Path(self.config_path).exists(): return False
        try:
            data = config_store.load(self.config_path)
            devices = []
            for record in data['devices']:
                d = self.new_device(record['uid'])
                d.update(record, mode='real', connected=False)
                d['states'] = list(record['continuous_states'])
                devices.append(d)
            # Parsing/validation finishes before replacing the current document.
            self.devices = devices
            self.default_mode = 'real'
            self.scenarios = data['scenarios']
            self.default_pulse = 0
            self.timestamps = data['timestamps']
            self.home.askConfirm.setChecked(data['confirm_actions'])
            self.autoPulsePreview.setChecked(data['auto_pulse_preview'])
            self.countdownEnabled.setChecked(data['countdown']['enabled'])
            self.countdownSeconds.setValue(data['countdown']['seconds'])
            self.reset_input = {'ip': '', 'channel': 1}  # Use per-row RESET all.
            self.config_load_failed = False
            return True
        except Exception as exc:
            self.config_load_failed = True
            self.log(f'Saved settings could not be loaded: {type(exc).__name__}. File was preserved.')
            return False

    def save_configuration(self):
        if not self.config_path: return True
        if self.config_load_failed:
            self.save_error = 'The existing settings file could not be loaded. It has been preserved.'
            self.saveStatus.setText('Not saved')
            return False
        try:
            for d,p in zip(self.devices,self.panels):
                d['manual_selection'] = {prefix: [getattr(p,f'{prefix}{i}').isChecked() for i in range(1,7)] for prefix in ('continuousSelect','momentarySelect')}
            config_store.save(self.config_path,self.devices,self.default_pulse,self.timestamps,self.scenarios,self.default_mode,self.reset_input,self.home.askConfirm.isChecked(),self.autoPulsePreview.isChecked(),{'enabled':self.countdownEnabled.isChecked(),'seconds':self.countdownSeconds.value()})
        except Exception as exc:
            self.save_error = f'{type(exc).__name__}: {exc}'
            self.saveStatus.setText('Save failed')
            self.saveStatus.setToolTip(self.save_error)
            self.connections.connectionMessage.setText(f'Settings could not be saved: {type(exc).__name__}')
            return False
        self.save_error = ''
        self.saveStatus.setText('Saved')
        self.saveStatus.setToolTip('All current settings are saved locally.')
        return True

    def render_log(self):
        query = self.logs.filterInput.text().lower()
        lines = [(time.strftime('%H:%M:%S  ') if self.timestamps else '')+text
                 for time,text in self.entries if query in text.lower()]
        self.logs.logOutput.setPlainText('\n'.join(lines))
        scrollbar = self.logs.logOutput.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def clear_log(self):
        self.entries.clear()
        self.render_log()

    def export_log(self):
        filename, _ = QFileDialog.getSaveFileName(self.window,'Export log',str(ROOT/'moxa_log.txt'),'Text files (*.txt)')
        if filename:
            try:
                Path(filename).write_text('\n'.join(t.isoformat(timespec='seconds')+' '+v for t,v in self.entries),encoding='utf-8')
            except OSError as exc:
                QMessageBox.warning(self.window,'Export failed',str(exc))

    def rebuild(self):
        self.release_all_momentary()
        for page, scroll in [(self.home,'devicesScroll')]:
            target = getattr(page,scroll).widget().layout()
            while target.count():
                old = target.takeAt(0)
                if old.widget():
                    old.widget().hide()
                    old.widget().deleteLater()
        self.panels, self.auto_panels = [], []
        if not self.devices:
            empty = QWidget()
            layout = QVBoxLayout(empty)
            self.emptyUnitsHint = QLabel('No units configured. Scan / add a unit to begin.')
            layout.addWidget(self.emptyUnitsHint)
            add = QPushButton('Scan / Add unit…')
            add.clicked.connect(self.open_discovery)
            layout.addWidget(add)
            self.home.devicesScroll.widget().layout().addWidget(empty)
        for d in self.devices:
            for auto_only, page, scroll in [(False,self.home,'devicesScroll')]:
                panel = load_ui('device_panel.ui')
                panel.setObjectName('deviceCard')
                panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
                panel.deviceName.setText(d['name'])
                panel.controlColumns.setAlignment(Qt.AlignmentFlag.AlignLeft)
                panel.setMinimumWidth(760)
                panel.setMinimumHeight(0)
                panel.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
                panel.manualGroup.setVisible(not auto_only)
                panel.automationGroup.setVisible(auto_only or self.home.showAutomation.isChecked())
                getattr(page,scroll).widget().layout().addWidget(panel)
                (self.auto_panels if auto_only else self.panels).append(panel)
                self.bind_panel(panel,d)
                for prefix,values in d.get('manual_selection',{}).items():
                    if prefix in ('continuousSelect','momentarySelect') and isinstance(values,list) and len(values)==6:
                        for i,value in enumerate(values,1):
                            field=getattr(panel,f'{prefix}{i}')
                            field.blockSignals(True)
                            field.setChecked(bool(value))
                            field.blockSignals(False)
                self.apply_collapse(panel,d)
                panel.layoutAdapter = PanelLayoutAdapter(panel)
        for page,scroll in [(self.home,'devicesScroll')]:
            getattr(page,scroll).widget().layout().addStretch()
        self.refresh_table()
        selector = self.connections.unitSelector
        selector.blockSignals(True)
        selector.clear()
        selector.addItems([d['name'] for d in self.devices]+['+ New unit'])
        selector.blockSignals(False)
        self.load_connection(0)
        self.render()
        self.fitTimer.start(30)

    def bind_panel(self,p,d):
        p.deviceLayout.setSpacing(2)
        p.manualGrid.setContentsMargins(4, 0, 4, 4)
        p.manualGrid.setVerticalSpacing(2)
        p.manualGrid.setAlignment(Qt.AlignmentFlag.AlignTop)
        p.deviceBar.setSpacing(10)
        p.deviceBar.setContentsMargins(0, 0, 0, 0)
        nameLabel = p.deviceName
        p.deviceName = QLineEdit(d['name'], p)
        p.deviceName.setObjectName('deviceNameEdit')
        p.deviceName.setFixedWidth(160)
        p.deviceName.setToolTip('Edit unit name · Enter to save')
        p.deviceBar.replaceWidget(nameLabel, p.deviceName)
        nameLabel.hide()
        nameLabel.deleteLater()
        p.deviceName.editingFinished.connect(lambda: self.rename_device(d,p))
        p.ipCaption.setText('IP:')
        p.ipCaption.setFixedWidth(24)
        p.ipAddress.hide()
        p.ipSelector = QComboBox(p)
        p.ipSelector.setObjectName('ipSelector')
        p.ipSelector.setFixedWidth(190)
        p.ipSelector.setToolTip('Select a unit saved in Settings; its controls and settings follow the selected IP.')
        for device in self.devices:
            p.ipSelector.addItem(device['ip'], device['uid'])
        p.ipSelector.setMaxVisibleItems(5)
        p.ipSelector.view().setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        p.ipSelector.view().setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        rowHeight = max(28, p.ipSelector.view().sizeHintForRow(0))
        p.ipSelector.view().setFixedHeight(rowHeight * p.ipSelector.count() + 4)
        p.ipSelector.setCurrentIndex(self.devices.index(d))
        p.deviceBar.insertWidget(3, p.ipSelector)
        p.connectButton = QPushButton('Connect', p)
        p.connectButton.setFixedWidth(110)
        p.connectButton.clicked.connect(lambda: self.toggle_connection(d))
        p.deviceBar.insertWidget(4, p.connectButton)
        p.deviceBar.setStretch(0, 0)
        p.deviceBar.setStretch(1, 0)
        p.deviceBar.setStretch(2, 0)
        p.deviceBar.addStretch(1)
        p.connectionStatus.setFixedWidth(210)
        p.connectionStatus.setMinimumHeight(36)
        p.runLamp = QFrame(p)
        p.runLamp.setObjectName('runLamp')
        p.runLamp.setFixedSize(14, 14)
        p.lampState = 'idle'
        p.lampLit = False
        statusIndex = p.deviceBar.indexOf(p.connectionStatus)
        p.deviceBar.insertWidget(statusIndex + 1, p.runLamp, 0, Qt.AlignmentFlag.AlignVCenter)
        self.paint_run_lamp(p)
        p.ipSelector.activated.connect(lambda index: self.select_ip(d, p.ipSelector.itemData(index)))
        p.collapsed = d.get('collapsed', False)
        p.collapseButton = QPushButton(p)
        p.collapseButton.setObjectName('collapseButton')
        p.collapseButton.setFixedSize(28, 28)
        p.collapseButton.setToolTip('Collapse / expand unit')
        p.deviceBar.insertWidget(0, p.collapseButton, 0, Qt.AlignmentFlag.AlignVCenter)
        p.collapseButton.clicked.connect(lambda: self.collapse_unit(p,d))
        p.previewDialog = QDialog(self.window)
        p.destroyed.connect(p.previewDialog.deleteLater)
        p.previewDialog.setWindowTitle(f'{d["name"]} · Pulse preview')
        previewLayout = QVBoxLayout(p.previewDialog)
        p.timeline = AutomationTimeline(p.previewDialog)
        previewLayout.addWidget(p.timeline)
        previewActions = QHBoxLayout()
        p.previewRun = QPushButton('RUN')
        p.previewStop = QPushButton('STOP')
        p.previewRun.clicked.connect(lambda: self.automation_action(d))
        p.previewStop.setText('RESET')
        p.previewStop.clicked.connect(lambda: self.stop_device(d))
        previewActions.addWidget(p.previewRun)
        previewActions.addWidget(p.previewStop)
        previewActions.addStretch()
        previewLayout.addLayout(previewActions)
        p.previewDialog.resize(850, 480)
        p.previewButton = QPushButton('Preview pulses…', p.automationGroup)
        for control in (p.runSelectedButton, p.stopButton, p.triggerButton, p.automationStatus):
            p.automationGrid.removeWidget(control)
        p.automationStatus.hide()
        p.automationGrid.addWidget(p.stopButton, 7, 0, 1, 2)
        p.triggerButton.hide()
        p.previewButton.setProperty('compactManage', True)
        p.previewButton.setFixedWidth(145)
        p.runSelectedButton.setFixedWidth(125)
        p.runSelectedButton.setObjectName('compactRunButton')
        p.automationGrid.removeWidget(p.stopButton)
        p.stopButton.setFixedWidth(65)
        p.stopButton.setObjectName('compactStopButton')
        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(6)
        actions.addWidget(p.runSelectedButton)
        actions.addWidget(p.stopButton)
        actions.addWidget(p.previewButton)
        actions.addStretch()
        p.automationGrid.addLayout(actions, 7, 0, 1, 8)
        p.automationGroup.setTitle('')
        p.automationHeading = QLabel('Automation Configuration', p)
        p.automationHeading.setObjectName('automationHeading')
        p.automationHeading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        p.automationHeading.setFixedSize(290, 30)
        p.automationHeading.setVisible(self.home.showAutomation.isChecked())
        p.previewButton.clicked.connect(lambda: self.show_preview(p,d))
        p.automationGrid.setHorizontalSpacing(8)
        p.automationGrid.setVerticalSpacing(4)
        p.diActionHeader = QLabel('DI Action', p.automationGroup)
        p.diActionHeader.setToolTip('The action runs when this row\'s digital input changes from OFF to ON. Hover over an action for details.')
        p.diActionHeader.setAlignment(Qt.AlignmentFlag.AlignCenter)
        p.automationGrid.addWidget(p.diActionHeader, 0, 6)
        for i in range(6):
            action = QComboBox(p.automationGroup)
            for title, value in [('START channel','start'), ('OFF channel','off'), ('PAUSE unit','pause'), ('CONTINUE unit','continue'), ('RESET all','reset')]:
                action.addItem(title, value)
                action.setItemData(action.count()-1, DI_ACTION_HELP[value], Qt.ItemDataRole.ToolTipRole)
            action.setFixedWidth(112)
            action.setFixedHeight(26)
            action.setCurrentIndex(action.findData(d['config'][i].get('action', 'start')))
            action.setToolTip(DI_ACTION_HELP[action.currentData()])
            action.currentIndexChanged.connect(lambda _, d=d, i=i, w=action: self.change_config(d,i,'action',w.currentData()))
            setattr(p, f'diAction{i+1}', action)
            p.automationGrid.addWidget(action, i+1, 6)
        for col in range(6):
            header = getattr(p, f'autoHeader{col}')
            header.setAlignment(Qt.AlignmentFlag.AlignCenter)
            header.setWordWrap(True)
            header.setFixedWidth(72 if col in (3, 5) else 96)
        for row in range(1, 7):
            for prefix in ('firstDelay', 'pulseTime', 'repeat', 'between'):
                field = getattr(p, f'{prefix}{row}')
                field.setAlignment(Qt.AlignmentFlag.AlignCenter)
                sample = '10' if prefix == 'repeat' else '30000'
                field.setFixedWidth(field.fontMetrics().horizontalAdvance(sample) + 34)
            trigger = getattr(p, f'trigger{row}')
            trigger.setProperty('compactDI', True)
            trigger.setFixedWidth(72)
            trigger.setFixedHeight(24)
            for col in range(6):
                p.automationGrid.itemAtPosition(row, col).setAlignment(Qt.AlignmentFlag.AlignCenter)
        automationFields = QWidget(p.automationGroup)
        automationFields.setLayout(p.automationGrid)
        p.rowSeparators = []
        p.rowBackgrounds = []
        for row in range(6):
            background = QFrame(automationFields)
            background.setObjectName('automationRowEven' if row % 2 == 0 else 'automationRowOdd')
            background.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            p.rowBackgrounds.append(background)
        for _ in range(5):
            line = QFrame(automationFields)
            line.setObjectName('automationRowSeparator')
            line.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            p.rowSeparators.append(line)
        automationLayout = QVBoxLayout(p.automationGroup)
        automationLayout.setContentsMargins(8, 8, 8, 8)
        automationLayout.setSpacing(8)
        automationLayout.setAlignment(Qt.AlignmentFlag.AlignTop)
        automationLayout.addWidget(automationFields)
        p.automationGroup.setFixedWidth(p.automationGroup.sizeHint().width())
        p.automationGroup.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        autoPolicy = p.automationGroup.sizePolicy()
        autoPolicy.setRetainSizeWhenHidden(False)
        p.automationGroup.setSizePolicy(autoPolicy)
        p.manualGroup.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        p.manualGroup.setFixedWidth(660)
        for group in (p.manualGroup, p.automationGroup):
            p.controlColumns.setAlignment(group, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        for col in range(5):
            grid = getattr(p, f'manualColumn{col}')
            grid.setContentsMargins(8, 8, 8, 8)
            getattr(p, f'operationFrame{col}').setFixedHeight(264)
            grid.setVerticalSpacing(4)
            for row in range(7):
                grid.setRowMinimumHeight(row, 32)
        for row in range(1, 7):
            p.automationGrid.setRowMinimumHeight(row, 32)
        p.turnOnButton.setCheckable(True)
        p.turnOnButton.clicked.connect(lambda: self.toggle_selected(d,p))
        if hasattr(p, 'turnOffButton'):
            p.turnOffButton.clicked.connect(lambda: self.selected(d,p,False,False))
        self.bind_momentary(p.momentaryButton, d, lambda: [i for i in range(6) if getattr(p, f'momentarySelect{i+1}').isChecked()])
        if hasattr(p, 'selectAllButton'):
            p.selectAllButton.clicked.connect(lambda: self.select_panel(p,True))
        if hasattr(p, 'clearButton'):
            p.clearButton.clicked.connect(lambda: self.select_panel(p,False))
        p.runSelectedButton.clicked.connect(lambda: self.automation_action(d))
        p.stopButton.clicked.connect(lambda: self.stop_device(d))
        p.triggerButton.clicked.connect(lambda: self.run_selected(d,triggered=True))
        for i in range(6):
            n=i+1
            trigger = getattr(p,f'trigger{n}')
            trigger.clear()
            trigger.addItem('None', 0)
            trigger.addItem(f'DI {n}', n)
            trigger.setMaxVisibleItems(2)
            trigger.view().setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            trigger.view().setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            trigger.view().setMinimumHeight(60)
            for prefix in ('continuousSelect','momentarySelect'):
                getattr(p,f'{prefix}{n}').toggled.connect(lambda _,d=d,p=p: self.manual_selection_changed(d,p))
            getattr(p,f'channelName{n}').editingFinished.connect(lambda d=d,p=p,i=i: self.rename(d,p,i))
            getattr(p,f'continuous{n}').toggled.connect(lambda value,d=d,i=i: self.manual(d,i,value))
            self.bind_momentary(getattr(p,f'pulse{n}'), d, lambda i=i: [i])
            for prefix,key in [('active','active'),('firstDelay','first'),('pulseTime','pulse'),('repeat','repeat'),('between','between'),('trigger','trigger')]:
                w=getattr(p,f'{prefix}{n}')
                signal = w.toggled if prefix=='active' else w.currentIndexChanged if prefix=='trigger' else w.valueChanged
                signal.connect(lambda value,d=d,i=i,key=key: self.change_config(d,i,key,(i+1 if value else 0) if key=='trigger' else value))

    def collapse_unit(self,p,d):
        d['collapsed'] = not d.get('collapsed', False)
        self.apply_collapse(p,d)
        self.fitTimer.start(30)
        self.configuration_changed()

    def configuration_changed(self):
        if self.saveTimer is not None:
            self.saveTimer.start(400)
        else:
            self.saveStatus.setText('Not saved')

    def manual_selection_changed(self,d,p):
        d['manual_selection'] = {prefix: [getattr(p,f'{prefix}{i}').isChecked() for i in range(1,7)]
                                 for prefix in ('continuousSelect','momentarySelect')}
        self.configuration_changed()
        self.render()

    def apply_collapse(self,p,d):
        p.collapsed = d.get('collapsed', False)
        p.collapseButton.setIcon(QIcon(str(ROOT / 'assets' / ('chevron-right.svg' if p.collapsed else 'chevron-down.svg'))))
        p.collapseButton.setAccessibleName('Expand unit' if p.collapsed else 'Collapse unit')
        p.manualGroup.setVisible(not p.collapsed)
        p.automationGroup.setVisible(not p.collapsed and self.home.showAutomation.isChecked())
        p.automationHeading.setVisible(not p.collapsed and self.home.showAutomation.isChecked())
        if p.collapsed:
            p.setFixedHeight(p.deviceBar.sizeHint().height() + 24)
        else:
            p.setMinimumHeight(0)
            p.setMaximumHeight(16777215)

    def current_mode(self):
        modes = {d.get('mode', 'real') for d in self.devices}
        return next(iter(modes)) if len(modes) == 1 else ('mixed' if modes else self.default_mode)

    def reset_input_active(self):
        return False

    def global_input_reset(self):
        self.reset_all_devices()
        self.log(f'Global RESET: {self.reset_input["ip"]} / DI {self.reset_input["channel"]}; all automation cancelled and initial Continuous states requested.')

    def reset_all_devices(self):
        self.cancel_countdown()
        for d in self.devices: self.stop_device(d)

    def handle_input_sample(self, d, inputs):
        previous = d.get('inputs', [False] * 6)
        d['inputs'] = list(inputs)
        index = self.reset_input['channel'] - 1
        if d['ip'] == self.reset_input['ip'] and inputs[index] and not previous[index]:
            self.global_input_reset()
            return
        # A held reset input prevents automation from restarting, including
        # triggers from other units arriving later in the polling cycle.
        if self.reset_input_active(): return
        paused = any(uid == d['uid'] and job.get('paused') for (uid, ch), job in self.jobs.items())
        for i, cfg in enumerate(d['config']):
            trigger = i if cfg['trigger'] else -1
            if cfg['active'] and 0 <= trigger < 6 and not previous[trigger] and inputs[trigger] and (cfg.get('action','start') != 'start' or (not paused and (d['uid'], i) not in self.jobs)):
                action = cfg.get('action', 'start')
                if action == 'reset':
                    self.global_input_reset()
                    return
                if action == 'off': self.manual(d,i,False)
                elif action == 'pause': self.pause_device(d)
                elif action == 'continue':
                    if any(uid == d['uid'] and job.get('paused') for (uid,ch),job in self.jobs.items()): self.continue_or_run(d)
                elif action == 'start' and cfg['repeat'] > 0:
                    if not any(uid == d['uid'] and job['config'].get('kind') != 'manual' for (uid,ch),job in self.jobs.items()):
                        d['baseline'] = list(d['states'])
                        d['_clock_started'] = monotonic()
                    self.schedule(d,i,cfg)

    def communication_status(self,d):
        last = d.get('last_read','—')
        if d.get('connecting'): state,title='connecting','Connecting…'
        elif d.get('error'): state,title='error','Communication error'
        elif not d['connected']: state,title='disconnected','Disconnected'
        elif monotonic()-d.get('last_read_tick',0) > max(2,d['timeout']/500): state,title='stale','No recent response'
        else: state,title='connected','Connected · E1214'
        return state,title,f'Last read: {last}'

    def update_communication_label(self,p,d):
        state,title,last=self.communication_status(d)
        p.connectionStatus.setText(f'{title}\n{last}')
        p.connectionStatus.setToolTip(d.get('error') or f'{d["ip"]}:{d["port"]} · {title}\n{last}')
        if p.connectionStatus.property('communicationState') != state:
            p.connectionStatus.setProperty('communicationState',state)
            p.connectionStatus.style().unpolish(p.connectionStatus)
            p.connectionStatus.style().polish(p.connectionStatus)

    def show_scenarios(self):
        dialog=QDialog(self.window)
        dialog.setWindowTitle('Automation scenarios')
        layout=QVBoxLayout(dialog)
        layout.addWidget(QLabel('Save / load channel names and automation for all configured units. Loading does not start outputs.'))
        selector=QComboBox()
        selector.addItems(sorted(self.scenarios))
        layout.addWidget(selector)
        name=QLineEdit()
        name.setPlaceholderText('Scenario name')
        layout.addWidget(name)
        message=QLabel('Existing names are updated when saved again.')
        message.setWordWrap(True)
        actions=QHBoxLayout()
        save=QPushButton('Save current')
        load=QPushButton('Load selected')
        def save_clicked():
            try:
                title=self.save_scenario(name.text())
                selector.clear(); selector.addItems(sorted(self.scenarios)); selector.setCurrentText(title)
                message.setText(f'Saved: {title}')
            except ValueError as exc: message.setText(str(exc))
        def load_clicked():
            try:
                self.load_scenario(selector.currentText())
                message.setText('Loaded. Review the values, then RUN when ready.')
            except ValueError as exc: message.setText(str(exc))
        save.clicked.connect(save_clicked)
        load.clicked.connect(load_clicked)
        actions.addWidget(save); actions.addWidget(load)
        layout.addLayout(actions); layout.addWidget(message)
        dialog.resize(650,220)
        self.scenarioDialog=dialog
        dialog.open()

    def save_scenario(self,name):
        name=name.strip()
        if not name or len(name)>80: raise ValueError('Enter a name of 1–80 characters.')
        if not self.devices: raise ValueError('Add at least one unit first.')
        self.scenarios[name]=[dict(ip=d['ip'],names=deepcopy(d['names']),config=deepcopy(d['config'])) for d in self.devices]
        self.log(f'Automation scenario saved: {name}')
        return name

    def load_scenario(self,name):
        if self.jobs: raise ValueError('RESET running or paused jobs before loading a scenario.')
        records=self.scenarios.get(name)
        if not isinstance(records,list) or not records: raise ValueError('Select a valid scenario.')
        pending=[]
        seen=set()
        for record in records:
            try:
                ip=str(IPv4Address(record['ip']))
                device=next((d for d in self.devices if d['ip']==ip),None)
                if device is None: raise ValueError(f'Unit {ip} is missing. Add it before loading this scenario.')
                if ip in seen or len(record['names']) != 6 or len(record['config']) != 6: raise ValueError('Invalid scenario channels.')
                for cfg in record['config']:
                    if cfg.get('action','start') not in ('start','off','pause','continue','reset'): raise ValueError('Invalid DI action')
                    for key in ('first','pulse','between'):
                        if type(cfg[key]) is not int or not 0<=cfg[key]<=3600000: raise ValueError('Invalid scenario timing.')
                    if type(cfg['repeat']) is not int or not 0<=cfg['repeat']<=1000 or type(cfg['trigger']) is not int or not 0<=cfg['trigger']<=6 or type(cfg['active']) is not bool: raise ValueError('Invalid scenario automation.')
                if not all(isinstance(n,str) for n in record['names']): raise ValueError('Invalid channel names.')
                pending.append((device,record)); seen.add(ip)
            except (KeyError,TypeError,StopIteration): raise ValueError('Invalid scenario format.') from None
        for device,record in pending:
            device['names']=deepcopy(record['names'])
            device['config']=deepcopy(record['config'])
        self.render()
        self.log(f'Automation scenario loaded: {name}')

    def open_automatic_pulses(self):
        units = self.pendingPulseUnits
        all_units = self.pendingPulseAll
        self.pendingPulseUnits = set()
        self.pendingPulseAll = False
        if not self.autoPulsePreview.isChecked(): return
        active = [d for d in self.devices if d['uid'] in units and any(uid==d['uid'] and job['config'].get('kind')!='manual' for (uid,ch),job in self.jobs.items())]
        if not active: return
        if all_units or len(active)>1:
            self.show_all_pulses()
        else:
            d=active[0]
            p=next(p for device,p in zip(self.devices,self.panels) if device is d)
            self.show_preview(p,d)

    def show_all_pulses(self):
        if not hasattr(self, 'combinedDialog'):
            self.combinedDialog = QDialog(self.window)
            self.combinedDialog.setWindowTitle('All MOXA units · Combined pulses')
            layout = QVBoxLayout(self.combinedDialog)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(CombinedTimeline(self, scroll))
            layout.addWidget(scroll)
            actions = QHBoxLayout()
            actions.setSpacing(6)
            self.combinedRunButton = QPushButton('RUN ALL')
            self.combinedResetButton = QPushButton('RESET ALL')
            self.combinedRunButton.setProperty('compactManage', True)
            self.combinedResetButton.setProperty('compactManage', True)
            self.combinedRunButton.clicked.connect(self.run_both)
            self.combinedResetButton.clicked.connect(self.reset_all_devices)
            actions.addWidget(self.combinedRunButton)
            actions.addWidget(self.combinedResetButton)
            actions.addStretch()
            layout.addLayout(actions)
            self.combinedDialog.resize(1000, min(760, self.window.screen().availableGeometry().height() - 80))
        self.render()
        self.combinedDialog.show()
        self.combinedDialog.raise_()

    def show_preview(self,p,d):
        p.previewDialog.setWindowTitle(f'{d["name"]} · Pulse preview')
        p.timeline.set_device(d)
        p.previewDialog.show()
        p.previewDialog.raise_()

    def select_ip(self, current, uid):
        selected = next((device for device in self.devices if device['uid'] == uid), None)
        if selected is None or selected is current:
            return
        first, second = self.devices.index(current), self.devices.index(selected)
        self.devices[first], self.devices[second] = self.devices[second], self.devices[first]
        self.rebuild()

    def rename_device(self,d,p):
        name = p.deviceName.text().strip()
        if not name or any(other is not d and other['name'].casefold() == name.casefold()
                           for other in self.devices):
            p.deviceName.setText(d['name'])
            self.log('Name unchanged: enter a non-empty, unique unit name.')
            p.deviceName.setToolTip('Name must be non-empty and unique. Edit and press Enter to save.')
            return
        if name == d['name']:
            return
        old = d['name']
        d['name'] = name
        for index, device in enumerate(self.devices):
            self.connections.unitSelector.setItemText(index, device['name'])
        if self.editing is d:
            self.connections.nameInput.setText(name)
        p.previewDialog.setWindowTitle(f'{name} · Pulse preview')
        self.refresh_table()
        self.render()
        self.log(f'Unit renamed: {old} → {name}.')

    def change_config(self,d,i,key,value):
        d['config'][i][key]=value
        self.render()
        if self.saveTimer is not None: self.saveTimer.start(400)

    def rename(self,d,p,i):
        d['names'][i]=getattr(p,f'channelName{i+1}').text().strip() or f'Channel{i+1}'
        self.render()
        if self.saveTimer is not None: self.saveTimer.start(400)

    def paint_run_lamp(self,p):
        # A confirmed connection is solid green; only active automation blinks.
        colors = {'idle': '#cbd4df', 'connected': '#20c875', 'paused': '#e4ad32',
                  'stale': '#e4ad32'}
        color = ('#20c875' if p.lampLit else '#cbd4df') if p.lampState == 'running' else colors[p.lampState]
        p.runLamp.setStyleSheet(f'QFrame#runLamp {{ background: {color}; border: 1px solid #9aaabc; border-radius: 7px; }}')
        text = {'idle':'Disconnected', 'connected':'Connected', 'running':'Connected · running',
                'paused':'Connected · paused', 'stale':'Connection response is stale'}[p.lampState]
        p.runLamp.setAccessibleName(f'Unit status: {text}')
        p.runLamp.setToolTip(f'Unit status: {text}')

    def blink_run_lamp(self):
        self.lampLit = not self.lampLit
        for p in self.panels + self.auto_panels:
            if p.lampState == 'running':
                p.lampLit = not p.lampLit
                self.paint_run_lamp(p)

    def update_run_lamp(self, running, paused):
        state = 'paused' if paused else ('running' if running else 'idle')
        if state == self.lampState: return
        self.lampState = state
        self.lampTimer.stop()
        self.lampLit = state == 'running'
        if state == 'running': self.lampTimer.start()

    def render(self):
        for panels in [self.panels,self.auto_panels]:
            for d,p in zip(self.devices,panels):
                if not p.deviceName.hasFocus():
                    p.deviceName.setText(d['name'])
                p.timeline.set_device(d)
                p.ipAddress.setText(d['ip'])
                self.update_communication_label(p,d)
                p.connectButton.setText('Connecting…' if d.get('connecting') else ('Disconnect' if d['connected'] else 'Connect'))
                p.connectButton.setEnabled(not d.get('connecting'))
                p.triggerButton.setText('Read D/I')
                p.manualGroup.setEnabled(d['connected'])
                p.runSelectedButton.setEnabled(d['connected'])
                p.triggerButton.setEnabled(d['connected'])
                selected = [i for i in range(6) if getattr(p,f'continuousSelect{i+1}').isChecked()]
                all_on = bool(selected) and all(d['states'][i] for i in selected)
                p.turnOnButton.setChecked(all_on)
                p.turnOnButton.setText('Turn Off' if all_on else 'Turn On')
                for i in range(6):
                    n=i+1
                    action = getattr(p,f'diAction{n}')
                    action.blockSignals(True)
                    action.setCurrentIndex(action.findData(d['config'][i].get('action','start')))
                    action.setToolTip(DI_ACTION_HELP[action.currentData()])
                    action.blockSignals(False)
                    name=getattr(p,f'channelName{n}')
                    if not name.hasFocus(): name.setText(d['names'][i])
                    w=getattr(p,f'continuous{n}')
                    w.blockSignals(True)
                    w.setChecked(d['states'][i])
                    w.setText(f'{n}  {"ON" if d["states"][i] else "OFF"}')
                    w.blockSignals(False)
                    for prefix,key in [('active','active'),('firstDelay','first'),('pulseTime','pulse'),('repeat','repeat'),('between','between'),('trigger','trigger')]:
                        w=getattr(p,f'{prefix}{n}')
                        w.blockSignals(True)
                        value=d['config'][i][key]
                        if prefix=='active': w.setChecked(value)
                        elif prefix=='trigger': w.setCurrentIndex(1 if value else 0)
                        else: w.setValue(value)
                        w.blockSignals(False)
                unit_jobs = [job for (uid,i),job in self.jobs.items() if uid == d['uid'] and job['config'].get('kind') != 'manual']
                count=len(unit_jobs)
                paused = bool(unit_jobs) and all(job.get('paused') for job in unit_jobs)
                communication_state = self.communication_status(d)[0]
                lampState = ('idle' if not d['connected'] else
                             'stale' if communication_state != 'connected' else
                             'paused' if paused else 'running' if count else 'connected')
                if p.lampState != lampState:
                    p.lampState = lampState
                    p.lampLit = lampState == 'running'
                # Repaint on every render: reconnects show green without automation.
                self.paint_run_lamp(p)
                p.timeline.set_jobs(self.jobs)
                action = 'CONTINUE' if paused else ('PAUSE' if count else 'RUN')
                p.previewStop.setVisible(paused)
                p.previewRun.setText(action)
                p.previewRun.setEnabled(d['connected'])
                p.runSelectedButton.setText(action)
                p.stopButton.setText('RESET')
                p.stopButton.setVisible(paused)
                p.automationStatus.setText(f'{count} active relay job(s)' if count else 'Ready · Repeat is the number of pulses.')
        automation_jobs = [job for job in self.jobs.values() if job['config'].get('kind') != 'manual']
        running=bool(automation_jobs)
        all_paused = running and all(job.get('paused') for job in automation_jobs)
        self.update_run_lamp(running, all_paused)
        self.home.runBothButton.setText('CONTINUE ALL' if all_paused else ('PAUSE ALL' if running else 'RUN ALL'))
        self.update_home_actions()
        if hasattr(self, 'combinedRunButton'):
            self.combinedRunButton.setText(self.home.runBothButton.text())
            self.combinedResetButton.setVisible(bool(all_paused))
        self.window.statusbar.showMessage(f'{sum(d.get("mode")=="real" for d in self.devices)} real / {len(self.devices)} units · {len(self.devices)*6} relays')

    def select_panel(self,p,value):
        for i in range(1,7):
            for prefix in ['continuousSelect','momentarySelect']: getattr(p,f'{prefix}{i}').setChecked(value)

    def confirm(self):
        return not self.home.askConfirm.isChecked() or QMessageBox.question(self.window,'Confirm action','Run the selected relay action? Real-mode units will change physical outputs.') == QMessageBox.StandardButton.Yes

    def toggle_selected(self,d,p):
        indices = [i for i in range(6) if getattr(p,f'continuousSelect{i+1}').isChecked()]
        value = not (bool(indices) and all(d['states'][i] for i in indices))
        self.selected(d,p,False,value)

    def selected(self,d,p,momentary,value=True):
        if not self.confirm(): return
        prefix='momentarySelect' if momentary else 'continuousSelect'
        indices=[i for i in range(6) if getattr(p,f'{prefix}{i+1}').isChecked()]
        if not indices: self.log('Select relay checkboxes below the header action first.')
        for i in indices:
            self.manual(d,i,value,update=False)
        if indices:
            self.flush_outputs()
            self.render()

    def begin_run(self, callback):
        if self.countdownDialog is not None: return
        if not self.countdownEnabled.isChecked():
            callback()
            return
        dialog = QDialog(self.window)
        dialog.setWindowTitle('Automation countdown')
        dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
        dialog.setFixedSize(330, 260)
        layout = QVBoxLayout(dialog)
        heading = QLabel('Automation starts in')
        heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(heading)
        self.countdownNumber = QLabel(str(self.countdownSeconds.value()))
        self.countdownNumber.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.countdownNumber.setStyleSheet('font-size: 64pt; font-weight: 600; color: #1668b2;')
        layout.addWidget(self.countdownNumber)
        cancel = QPushButton('Cancel')
        cancel.clicked.connect(dialog.reject)
        layout.addWidget(cancel)
        self.countdownDialog = dialog
        self.countdownCallback = callback
        self.countdownDeadline = monotonic() + self.countdownSeconds.value()
        self.countdownTimer = QTimer(dialog)
        self.countdownTimer.setInterval(50)
        self.countdownTimer.timeout.connect(self.tick_countdown)
        dialog.rejected.connect(self.cancel_countdown)
        dialog.finished.connect(dialog.deleteLater)
        self.countdownTimer.start()
        dialog.open()

    def cancel_countdown(self):
        dialog = self.countdownDialog
        if dialog is None: return
        self.countdownDialog = None
        self.countdownCallback = None
        self.countdownTimer.stop()
        dialog.reject()

    def tick_countdown(self):
        if self.countdownDialog is None: return
        remaining = ceil(self.countdownDeadline - monotonic())
        if remaining > 0:
            self.countdownNumber.setText(str(remaining))
            return
        callback, dialog = self.countdownCallback, self.countdownDialog
        self.countdownDialog = None
        self.countdownCallback = None
        self.countdownTimer.stop()
        dialog.accept()
        callback()

    def automation_action(self,d):
        active = [job for (uid,i),job in self.jobs.items() if uid == d['uid'] and job['config'].get('kind') != 'manual']
        if active and any(not job.get('paused') for job in active):
            self.pause_device(d)
        elif active:
            self.continue_or_run(d)
        elif d['connected'] and any(cfg['active'] and not cfg['trigger'] and cfg['repeat']>0 for cfg in d['config']):
            self.begin_run(lambda: self.run_selected(d) if d in self.devices else None)

    def pause_device(self,d):
        now = monotonic()
        if any(uid == d['uid'] and not job.get('paused') and job['config'].get('kind') != 'manual' for (uid,i),job in self.jobs.items()):
            d['_clock_paused'] = now
        for i in range(6):
            job = self.jobs.get((d['uid'],i))
            if job and job['config'].get('kind') != 'manual' and not job.get('paused'):
                remaining = job['timer'].remainingTime()
                job['remaining_ms'] = max(0, remaining if remaining >= 0 else job.get('pending_ms',0))
                job['timer'].stop()
                job['paused'] = now
                job['relay_state'] = d['states'][i]
        self.render()

    def continue_or_run(self,d):
        if not d['connected']:
            return
        if self.reset_input_active():
            self.log('Automation continue blocked: global RESET input is active.')
            return
        paused = [(i, job) for (uid,i),job in self.jobs.items() if uid == d['uid'] and job.get('paused')]
        if not paused:
            self.run_selected(d)
            return
        now = monotonic()
        if d.get('_clock_paused') is not None and d.get('_clock_started') is not None:
            d['_clock_started'] += now - d.pop('_clock_paused')
        for i, job in paused:
            job['started'] += now - job.pop('paused')
            job.pop('relay_state')
            remaining = job.pop('remaining_ms')
            pending = job.get('pending_write')
            if pending:
                if job.pop('write_deferred', False):
                    value, acknowledged = pending
                    self.request_job_output(d,i,job,value,acknowledged)
                # Queued/in-flight writes keep ownership of arming the timer.
            else:
                job['timer'].start(remaining)
        self.render()

    def cancel(self,d,i):
        key = (d['uid'],i)
        self.output_versions[key] = self.output_versions.get(key,0) + 1
        job=self.jobs.pop((d['uid'],i),None)
        if job:
            job['timer'].stop()
            job['timer'].deleteLater()

    def manual(self,d,i,value,update=True):
        if not d['connected']: return
        self.cancel(d,i)
        self.set_output(d,i,value)
        if 'baseline' in d: d['baseline'][i] = bool(value)
        self.log(f'{d["name"]} / {d["names"][i]} requested {"ON" if value else "OFF"} ({d.get("mode","real")})')
        if update: self.render()

    def bind_momentary(self, button, d, indices):
        button.setAutoRepeat(False)
        button.setToolTip('Hold to turn ON; release to turn OFF.')
        button.pressed.connect(lambda: self.start_momentary(button, d, indices()))
        button.released.connect(lambda: self.release_momentary(button))
        button.momentaryGuard = MomentaryGuard(self, button)

    def start_momentary(self, button, d, indices):
        if not d['connected'] or button in self.held_momentary: return
        # A confirmation dialog would consume the release event. Hold controls
        # are intentionally direct actions; normal toggle/automation prompts remain.
        indices = tuple(indices)
        if not indices: return
        self.held_momentary[button] = (d, indices)
        for i in indices: self.manual(d, i, True, update=False)
        # Queue the whole ON batch immediately, before the mouse is released.
        self.flush_outputs()
        self.render()

    def release_momentary(self, button):
        held = self.held_momentary.pop(button, None)
        if held:
            d, indices = held
            for i in indices: self.manual(d, i, False, update=False)
            self.flush_outputs()
            self.render()

    def release_all_momentary(self):
        for button in list(self.held_momentary): self.release_momentary(button)


    def hardware_error(self,d,error):
        self.cancel_countdown()
        for i in range(6):
            self.cancel(d,i)
        d['connected'] = False
        d['error'] = error
        client = self.clients.pop(d['uid'], None)
        if client and hasattr(client,'close'):
            self.hardware.submit(d['uid'],client.close,lambda result: None,lambda error: None)
        self.log(f'{d["name"]}: connection failed: {error}. Physical outputs may retain their last state.')
        self.connections.connectionMessage.setText(error)
        self.refresh_table()
        self.render()

    def poll_inputs(self):
        for d,p in zip(self.devices,self.panels): self.update_communication_label(p,d)
        for d in self.devices:
            client = self.clients.get(d['uid'])
            if d['connected'] and client is not None and d['uid'] not in self.polling:
                self.polling.add(d['uid'])
                def success(result,d=d,client=client):
                    self.polling.discard(d['uid'])
                    if self.clients.get(d['uid']) is not client: return
                    d['states'], inputs = result
                    d['last_read']=datetime.now().strftime('%H:%M:%S')
                    d['last_read_tick']=monotonic()
                    self.handle_input_sample(d, inputs)
                    self.render()
                def failure(error,d=d,client=client):
                    self.polling.discard(d['uid'])
                    if self.clients.get(d['uid']) is client:
                        self.hardware_error(d,error)
                self.hardware.submit(d['uid'],lambda client=client:(client.bits(1),client.bits(2)),success,failure)

    def set_output(self,d,i,value,done=None,job=None):
        client = self.clients.get(d['uid'])
        if not d['connected'] or client is None:
            return
        key = (d['uid'],i)
        version = self.output_versions.get(key,0) + 1
        self.output_versions[key] = version
        def success(states):
            if self.output_versions.get(key) != version or self.clients.get(d['uid']) is not client: return
            if states is None:
                # PAUSE can be released between the worker's pause check and
                # its deferred-result callback. Resume that transition here.
                if job is not None and self.jobs.get(key) is job and not job.get('paused') and job.get('write_deferred'):
                    pending = job.get('pending_write')
                    if pending: self.request_job_output(d,i,job,*pending)
                return
            d['states'] = states
            if done: done()
            self.render()
        def operation():
            if getattr(client,'failed',False) or self.output_versions.get(key) != version or self.clients.get(d['uid']) is not client:
                return None
            if job is not None:
                if self.jobs.get(key) is not job: return None
                if job.get('paused'):
                    job['write_deferred'] = True
                    return None
            return True
        def failure(error):
            if self.clients.get(d['uid']) is client:
                self.hardware_error(d,error)
        pending = self.pending_outputs.setdefault(d['uid'], [])
        pending.append((client, i, bool(value), operation, success, failure))
        if len(pending) == 1: QTimer.singleShot(0, self.flush_outputs)

    def flush_outputs(self):
        pending, self.pending_outputs = self.pending_outputs, {}
        for uid, requests in pending.items():
            def operation(requests=requests):
                active = [r for r in requests if r[3]()]
                if not active: return (None, [])
                client = active[0][0]
                try:
                    states = client.write_many({r[1]: r[2] for r in active})
                    return states, active
                except Exception:
                    client.failed = True
                    raise
            def success(result, requests=requests):
                states, active = result
                for request in requests:
                    request[4](states if request in active else None)
            def failure(error, requests=requests):
                requests[0][5](error)
            self.hardware.submit(uid, operation, success, failure)

    def request_job_output(self,d,i,job,value,acknowledged):
        """Retain a queued automation transition through PAUSE, until acknowledged."""
        if self.jobs.get((d['uid'],i)) is not job: return
        job['pending_write'] = (bool(value), acknowledged)
        job['write_deferred'] = False
        def complete():
            if self.jobs.get((d['uid'],i)) is not job: return
            job.pop('pending_write',None)
            job.pop('write_deferred',None)
            if job.get('paused'): job['relay_state'] = d['states'][i]
            acknowledged()
        self.set_output(d,i,value,complete,job=job)

    def schedule(self,d,i,config):
        self.cancel(d,i)
        timer=QTimer(self.window)
        timer.setSingleShot(True)
        job=dict(timer=timer, remaining=config['repeat'], phase='on', config=dict(config), started=monotonic())
        self.jobs[d['uid'],i]=job
        if config.get('kind') != 'manual' and self.autoPulsePreview.isChecked():
            self.pendingPulseUnits.add(d['uid'])
            self.autoPulseTimer.start(0)
        timer.timeout.connect(lambda: self.job_step(d,i,job))
        job['pending_ms'] = config['first']
        self.request_job_output(d,i,job,False,lambda: self.arm_job(d,i,job,config['first']))
        self.log(f'{d["name"]} / {d["names"][i]} scheduled: {config["repeat"]} pulse(s).')
        self.render()

    def arm_job(self,d,i,job,duration):
        if self.jobs.get((d['uid'],i)) is not job: return
        job['pending_ms'] = duration
        if job.get('paused'):
            job['remaining_ms'] = duration
        else:
            job['timer'].start(duration)

    def job_step(self,d,i,job):
        if self.jobs.get((d['uid'],i)) is not job or job.get('paused'): return
        cfg=job['config']
        if job['phase']=='on':
            job['phase']='off'
            job['pending_ms'] = max(1,cfg['pulse'])
            self.request_job_output(d,i,job,True,lambda: self.arm_job(d,i,job,max(1,cfg['pulse'])))
            self.log(f'{d["name"]} / {d["names"][i]} pulse ON requested')
        else:
            job['pending_ms'] = cfg['between']
            def off_done():
                if self.jobs.get((d['uid'],i)) is not job: return
                job['remaining']-=1
                self.log(f'{d["name"]} / {d["names"][i]} → OFF')
                if job['remaining']<=0: self.cancel(d,i)
                else:
                    job['phase']='on'
                    self.arm_job(d,i,job,cfg['between'])
                self.render()
            self.request_job_output(d,i,job,False,off_done)
        self.render()

    def run_selected(self,d,triggered=False,all_channels=False):
        if not d['connected']: return
        if self.reset_input_active():
            self.log('Automation start blocked: global RESET input is active.')
            return
        if triggered:
            self.poll_inputs()
            return
        if not any(uid == d['uid'] and job['config'].get('kind') != 'manual' for (uid,i),job in self.jobs.items()):
            d['baseline'] = list(d['states'])
            d['_clock_started'] = monotonic()
            d.pop('_clock_paused',None)
        count=0
        for i,cfg in enumerate(d['config']):
            if cfg['active'] and (all_channels or bool(cfg['trigger'])==triggered) and cfg['repeat']>0:
                self.schedule(d,i,cfg)
                count+=1
        self.log(f'{d["name"]}: {count} channels scheduled'+(' after reading D/I.' if triggered else '. Channels with a D/I trigger wait for their input.'))

    def stop_device(self,d):
        self.cancel_countdown()
        d.pop('_clock_started',None)
        d.pop('_clock_paused',None)
        baseline = d.pop('baseline', list(d['states']))
        for i in range(6):
            self.cancel(d,i)
            self.set_output(d,i,baseline[i])
        for panels in (self.panels,self.auto_panels):
            for device,panel in zip(self.devices,panels):
                if device is d:
                    panel.timeline.elapsed = None
                    panel.timeline.started = None
                    panel.timeline.timer.stop()
        self.log(f'{d["name"]}: reset; previous Continuous states restored.')
        self.render()

    def run_both(self):
        automation_jobs = [job for job in self.jobs.values() if job['config'].get('kind') != 'manual']
        if automation_jobs:
            if all(job.get('paused') for job in automation_jobs):
                for d in self.devices:
                    if any(uid == d['uid'] and job['config'].get('kind') != 'manual' for (uid,i),job in self.jobs.items()):
                        self.continue_or_run(d)
            else:
                for d in self.devices: self.pause_device(d)
        elif self.confirm():
            if any(d['connected'] and any(cfg['active'] and not cfg['trigger'] and cfg['repeat']>0 for cfg in d['config']) for d in self.devices):
                self.begin_run(self.start_all_automation)

    def start_all_automation(self):
        self.pendingPulseAll = self.autoPulsePreview.isChecked()
        for d in self.devices: self.run_selected(d)
        if not self.autoPulseTimer.isActive(): self.pendingPulseAll = False

    def on_page_changed(self, index):
        """Hide automation toolbar commands immediately when leaving Home."""
        for i, button in enumerate(self.navigationButtons):
            button.setChecked(i == index)
        self.update_home_actions()

    def update_home_actions(self):
        visible = self.window.screens.currentIndex() == 0 and self.home.showAutomation.isChecked()
        jobs = [j for j in self.jobs.values() if j['config'].get('kind') != 'manual']
        self.home.runBothButton.setVisible(visible)
        self.allPulsesButton.setVisible(visible)
        self.resetBothButton.setVisible(visible and bool(jobs) and all(j.get('paused') for j in jobs))
        self.automationLegend.setVisible(visible)
        for button in (self.allPulsesButton, self.resetBothButton):
            policy = button.sizePolicy()
            policy.setRetainSizeWhenHidden(visible)
            button.setSizePolicy(policy)
        self.homeToolbar.setFixedHeight(74 if visible else 40)
        self.toolbarScroll.setFixedHeight(76 if visible else 42)
        self.homeToolbar.layout().activate()
        self.homeToolbar.setMinimumWidth(self.homeToolbar.layout().sizeHint().width())

    def toggle_automation(self,visible):
        self.maximizeTimer.stop()
        self.fitTimer.stop()
        # Reserve action slots only in automation mode, keeping the legend
        # stationary during PAUSE without widening the manual-only window.
        for button in (self.allPulsesButton, self.resetBothButton):
            policy = button.sizePolicy()
            policy.setRetainSizeWhenHidden(visible)
            button.setSizePolicy(policy)
        self.allPulsesButton.setVisible(visible)
        self.window.setMaximumWidth(16777215)
        self.home.runBothButton.setVisible(visible)
        automation_jobs = [job for job in self.jobs.values() if job['config'].get('kind') != 'manual']
        self.resetBothButton.setVisible(visible and bool(automation_jobs) and all(job.get('paused') for job in automation_jobs))
        self.automationLegend.setVisible(visible and self.window.screens.currentIndex() == 0)
        self.homeToolbar.setFixedHeight(74 if visible else 40)
        self.toolbarScroll.setFixedHeight(76 if visible else 42)
        self.homeToolbar.setMinimumWidth(self.homeToolbar.layout().sizeHint().width())
        self.update_window_width()
        for p in self.panels:
            p.setMaximumWidth(16777215)
            p.automationGroup.setVisible(visible)
            p.automationHeading.setVisible(visible)
            p.setMinimumWidth(p.manualGroup.width() + p.automationGroup.width() + 40 if visible else 760)
        for p,d in zip(self.panels,self.devices):
            self.apply_collapse(p,d)
        if visible:
            self.maximize_automation()
            # Reapply after Qt has processed the new layout/native constraints.
            self.maximizeTimer.start(100)

        self.update_home_actions()

    def maximize_automation(self):
        if not isValid(self.window): return
        if self.home.showAutomation.isChecked():
            self.window.setMaximumWidth(16777215)
            self.window.layout().activate()
            # Let Windows maximize the complete decorated window. Applying the
            # screen rectangle to client geometry can push title controls offscreen.
            self.window.showMaximized()

    def refresh_table(self):
        table=self.manage.deviceTable
        table.setRowCount(len(self.devices))
        for r,d in enumerate(self.devices):
            for c,value in enumerate([d['name'],d['ip'],'6 relays','Connected' if d['connected'] else 'Disconnected']):
                table.setItem(r,c,QTableWidgetItem(value))
                table.item(r,c).setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        table.resizeColumnsToContents()
        rowHeight = max(26, table.fontMetrics().height() + 8)
        for row in range(table.rowCount()): table.setRowHeight(row, rowHeight)
        width = table.horizontalHeader().length() + table.frameWidth() * 2 + 4
        height = table.horizontalHeader().height() + sum(table.rowHeight(row) for row in range(table.rowCount())) + table.frameWidth() * 2 + 4
        table.setFixedSize(width, height)
        # Recompute the containing section after row count/height changes;
        # otherwise an old size hint can place the action row over the table.
        self.manage.manageLayout.invalidate()
        self.manage.manageLayout.activate()
        self.manage.setFixedHeight(self.manage.manageLayout.sizeHint().height())
        containerLayout = self.connections.manageContainer.layout()
        margins = containerLayout.contentsMargins()
        self.connections.manageContainer.setFixedHeight(self.manage.height() + margins.top() + margins.bottom())

    def open_discovery(self):
        self.discoveryDialog = DiscoveryDialog(self.devices, self.window)
        resultsPage = QWidget()
        resultsPage.setLayout(self.discoveryDialog.layout())
        resultsPage.layout().setAlignment(Qt.AlignmentFlag.AlignTop)
        outer = QVBoxLayout(self.discoveryDialog)
        tabs = QTabWidget()
        tabs.addTab(resultsPage, 'Network / Saved units')
        tabs.addTab(self.connectionEditor, 'Add / Edit unit')
        self.discoveryDialog.editorTabs = tabs
        outer.addWidget(tabs)
        manual = QPushButton('Add unit manually')
        manual.clicked.connect(self.new_connection)
        resultsPage.layout().addWidget(manual)
        self.discoveryDialog.resize(860, 460)
        self.discoveryDialog.selected.connect(self.use_discovered)
        self.discoveryDialog.open()

    def use_discovered(self, result):
        existing = next((i for i,d in enumerate(self.devices) if d['ip'] == result['ip']), None)
        self.window.screens.setCurrentIndex(1)
        if existing is not None:
            self.connections.unitSelector.setCurrentIndex(existing)
            self.load_connection(existing)
            if hasattr(self, 'discoveryDialog'):
                self.discoveryDialog.editorTabs.setCurrentIndex(1)
            return
        self.new_connection()
        self.connections.ipInput.setText(result['ip'])
        self.connections.portInput.setValue(result['port'])
        self.connections.connectionMessage.setText('Discovered address loaded. Verify model, name and optional password, then Save unit.')

    def new_connection(self):
        self.window.screens.setCurrentIndex(1)
        self.connections.unitSelector.setCurrentIndex(len(self.devices))
        self.load_connection(len(self.devices))
        if hasattr(self, 'discoveryDialog'):
            self.discoveryDialog.editorTabs.setCurrentIndex(1)

    def edit_connection(self):
        row=self.manage.deviceTable.currentRow()
        if row<0:
            self.manage.deviceHint.setText('Select a device row first.')
            return
        self.window.screens.setCurrentIndex(1)
        self.connections.unitSelector.setCurrentIndex(row)
        self.load_connection(row)
        self.open_discovery()
        self.discoveryDialog.editorTabs.setCurrentIndex(1)

    def load_connection(self,index):
        self.editing=self.devices[index] if 0<=index<len(self.devices) else None
        next_id=next((i for i in range(1,6) if all(d['uid']!=i for d in self.devices)),1)
        d=self.editing or self.new_device(next_id)
        if self.editing is None:
            d['mode'] = self.default_mode
        for field,key in [('nameInput','name'),('ipInput','ip')]: getattr(self.connections,field).setText(d[key])
        self.connections.portInput.setValue(d['port'])
        self.connections.timeoutInput.setValue(d['timeout'])
        self.connections.passwordInput.setText(d.get('password', ''))
        self.connections.passwordInput.setPlaceholderText('Default: moxa (if required)')
        self.passwordDefaultHint.setVisible(self.editing is None)
        self.connections.connectionMessage.setText('Save the unit, then press Connect beside its IP on the Home page.')
        self.connections.saveConnectionButton.setEnabled(self.editing is not None or len(self.devices)<5)

    def connection_values(self):
        name=self.connections.nameInput.text().strip()
        try: ip=str(IPv4Address(self.connections.ipInput.text().strip()))
        except ValueError: raise ValueError('Enter a valid IPv4 address.') from None
        if not name: raise ValueError('Enter a device name.')
        if self.connections.portInput.value()<1: raise ValueError('Port must be 1–65535.')
        if self.connections.timeoutInput.value()<1: raise ValueError('Timeout must be greater than zero.')
        if any(d is not self.editing and (d['ip']==ip or d['name'].lower()==name.lower()) for d in self.devices):
            raise ValueError('Each unit must have a unique name and IP address.')
        return dict(name=name,ip=ip,port=self.connections.portInput.value(),timeout=self.connections.timeoutInput.value(),password=self.connections.passwordInput.text(),mode='real')

    def save_connection(self):
        try: values=self.connection_values()
        except ValueError as exc:
            self.connections.connectionMessage.setText(str(exc)); return
        d=self.editing
        if d and d.get('mode') == 'real' and (d['connected'] or d.get('connecting')):
            self.connections.connectionMessage.setText('Wait for connection to finish, then disconnect before editing real connection settings.')
            return
        if d is None:
            if len(self.devices)>=5: return
            number=next(i for i in range(1,6) if all(x['uid']!=i for x in self.devices))
            d=self.new_device(number)
            self.devices.append(d)
        else: self.stop_device(d)
        d.update(values)
        d['connected'] = False
        index=self.devices.index(d)
        self.rebuild()
        self.connections.unitSelector.setCurrentIndex(index)
        self.connections.connectionMessage.setText('Saved. Real units remain disconnected until Connect is pressed.')
        if hasattr(self, 'discoveryDialog'):
            self.discoveryDialog.show_saved()
        self.log(f'Connection settings saved: {d["name"]} / {d["ip"]}:{d["port"]} ({d["mode"]}).')

    def test_connection(self):
        try: values=self.connection_values()
        except ValueError as exc:
            self.connections.connectionMessage.setText(str(exc)); return
        if values['mode'] == 'real':
            self.connections.connectionMessage.setText('Checking E1214 model and reading I/O…')
            client = make_client(values)
            def test():
                try: return client.connect()
                finally:
                    if hasattr(client,'close'): client.close()
            self.hardware.submit('test',test,
                lambda result: self.connections.connectionMessage.setText(f'Read-only test passed: {result["model"]}. No outputs changed.'),
                lambda error: self.connections.connectionMessage.setText(f'Test failed: {error}'))
            return

    def remove_device(self):
        row=self.manage.deviceTable.currentRow()
        if row<0: return
        d=self.devices[row]
        if d.get('mode') == 'real':
            for i in range(6): self.cancel(d,i)
            client = self.clients.pop(d['uid'],None)
            if client and hasattr(client,'close'):
                self.hardware.submit(d['uid'],client.close,lambda result: None,lambda error: None)
        else:
            self.stop_device(d)
        self.devices.pop(row)
        self.rebuild()
        self.log(f'Removed {d["name"]}.')

    def toggle_connection(self, d=None):
        if d is None:
            row = self.manage.deviceTable.currentRow()
            if row < 0: return
            d = self.devices[row]
        held = [button for button, (device, _) in self.held_momentary.items() if device is d]
        if held:
            for button in held: self.release_momentary(button)
            self.flush_outputs()
            self.hardware.submit(d['uid'], lambda: None,
                lambda _: self.toggle_connection(d) if d['connected'] else None,
                lambda error: self.hardware_error(d, error))
            return
        if d.get('mode') == 'real':
            if d.get('connecting'): return
            if d['connected']:
                for i in range(6): self.cancel(d,i)
                oldClient = self.clients.pop(d['uid'],None)
                if oldClient and hasattr(oldClient,'close'):
                    self.hardware.submit(d['uid'],oldClient.close,lambda result: None,lambda error: self.log(error))
                d['connected'] = False
                self.log(f'{d["name"]}: disconnected; physical outputs retain their state.')
                self.refresh_table()
                self.render()
            else:
                d['connecting'] = True
                d.pop('error',None)
                self.render()
                self.refresh_table()
                client = make_client(d)
                def connected(result):
                    d['connecting'] = False
                    if d not in self.devices:
                        if hasattr(client,'close'):
                            self.hardware.submit(d['uid'],client.close,lambda result: None,lambda error: None)
                        return
                    self.clients[d['uid']] = client
                    d.update(result,connected=True)
                    d.pop('error',None)
                    d['last_read']=datetime.now().strftime('%H:%M:%S')
                    d['last_read_tick']=monotonic()
                    self.refresh_table()
                    self.render()
                    self.log(f'{d["name"]}: connected to {result["model"]}; output states read from hardware.')
                def failed(error):
                    d['connecting'] = False
                    if d in self.devices: self.hardware_error(d,error)
                self.hardware.submit(d['uid'],client.connect,connected,failed)
            return
    def apply_inline_settings(self):
        self.default_pulse = self.settings.defaultPulse.value()
        self.timestamps = self.settings.timestampsCheck.isChecked()
        self.render_log()
        if self.saveTimer is not None: self.saveTimer.start(400)

    def show_settings(self):
        self.settings.defaultPulse.setValue(self.default_pulse)
        self.settings.timestampsCheck.setChecked(self.timestamps)
        self.settings.open()

    def save_settings(self):
        self.default_pulse=0
        self.timestamps=self.settings.timestampsCheck.isChecked()
        self.settings.accept()
        self.log('Application settings applied.')


def main():
    app=QApplication(sys.argv)
    app.setApplicationName('MOXA Control')
    app.setStyle('Fusion')
    app.setStyleSheet(STYLE)
    config_path = None
    if '--smoke-test' not in sys.argv:
        settings_dir = ROOT
        if getattr(sys, 'frozen', False):
            settings_dir = Path(QStandardPaths.writableLocation(
                QStandardPaths.StandardLocation.AppLocalDataLocation
            ))
            settings_dir.mkdir(parents=True, exist_ok=True)
        config_path = settings_dir / 'moxa_settings.json'
    controller = Controller(config_path=config_path)
    app.aboutToQuit.connect(lambda: controller.save_configuration() if controller.config_path and not controller.closeHandled else None)
    app.aboutToQuit.connect(controller.hardware.close)
    controller.window.show()
    if '--smoke-test' in sys.argv:
        QTimer.singleShot(250, lambda: (print('GUI launch passed.',flush=True),app.quit()))
    return app.exec()


if __name__=='__main__':
    raise SystemExit(main())

