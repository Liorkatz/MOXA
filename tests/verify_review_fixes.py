"""Regression tests for the four independently reproduced review defects."""
import _bootstrap
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic
import json
import unittest
from unittest.mock import patch
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox
from PySide6.QtTest import QTest
import main

app = QApplication.instance() or QApplication([])

class DeferredQueue:
    def __init__(self): self.pending = []
    def submit(self, uid, operation, success, failure): self.pending.append((operation, success, failure))
    def run_next(self):
        operation, success, failure = self.pending.pop(0)
        try: value = operation()
        except Exception as exc: failure(str(exc))
        else: success(value)
    def close(self): self.pending.clear()

class RelayPeer:
    def __init__(self):
        self.states = [False]*6
        self.writes = []
        self.before_reply = None
    def write(self, channel, value):
        self.states[channel] = bool(value)
        self.writes.append((channel, bool(value)))
        if self.before_reply:
            callback, self.before_reply = self.before_reply, None
            callback()
        return list(self.states)

class ReviewFixTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(dir=main.ROOT)
        self.root = Path(self.temp.name)
        self.c = main.Controller()
        self.c.pollTimer.stop()
        self.d = self.c.devices[0]
    def tearDown(self):
        self.c.cancel_countdown()
        for d in self.c.devices:
            for i in range(6): self.c.cancel(d,i)
        if self.c.saveTimer is not None: self.c.saveTimer.stop()
        self.c.config_path = None
        self.c.hardware.close()
        self.c.window.deleteLater()
        app.processEvents()
        self.temp.cleanup()
    def real_peer(self):
        self.c.hardware.close()
        self.c.hardware = DeferredQueue()
        self.d.update(mode='real', connected=True)
        peer = RelayPeer()
        self.c.clients[self.d['uid']] = peer
        self.c.schedule(self.d,0,dict(first=60000,pulse=1000,repeat=2,between=100))
        return peer, self.c.jobs[self.d['uid'],0]
    def test_queued_on_waits_for_continue(self):
        peer,job = self.real_peer()
        self.c.hardware.run_next()
        self.c.job_step(self.d,0,job)
        self.c.pause_device(self.d)
        self.c.hardware.run_next()
        self.assertFalse(peer.states[0], 'Queued ON must not run while paused')
        self.assertEqual(peer.writes, [(0,False)])
        self.c.continue_or_run(self.d)
        self.c.hardware.run_next()
        self.assertEqual(peer.writes, [(0,False),(0,True)])
        self.assertTrue(job['timer'].isActive())
    def test_queued_off_waits_for_continue(self):
        peer,job = self.real_peer()
        self.c.hardware.run_next()
        self.c.job_step(self.d,0,job)
        self.c.hardware.run_next()
        self.c.job_step(self.d,0,job)
        self.c.pause_device(self.d)
        self.c.hardware.run_next()
        self.assertTrue(peer.states[0], 'Queued OFF must not run while paused')
        self.assertEqual(job['remaining'],2)
        self.c.continue_or_run(self.d)
        self.c.hardware.run_next()
        self.assertFalse(peer.states[0])
        self.assertEqual(job['remaining'],1)
    def test_initial_write_waits_when_paused_before_dispatch(self):
        peer,job = self.real_peer()
        peer.states[0] = self.d['states'][0] = True
        self.c.pause_device(self.d)
        self.c.hardware.run_next()
        self.assertTrue(peer.states[0])
        self.c.continue_or_run(self.d)
        self.c.hardware.run_next()
        self.assertFalse(peer.states[0])
        self.assertTrue(job['timer'].isActive())
    def test_continue_before_worker_dispatch_does_not_duplicate_write(self):
        peer,job = self.real_peer()
        self.c.pause_device(self.d)
        self.c.continue_or_run(self.d)
        self.assertFalse(job['timer'].isActive(), 'Delay must start after write acknowledgement')
        self.c.hardware.run_next()
        self.assertEqual(peer.writes,[(0,False)])
        self.assertTrue(job['timer'].isActive())
    def test_deferred_result_arriving_after_continue_resubmits_once(self):
        peer,job = self.real_peer()
        operation,success,failure = self.c.hardware.pending.pop(0)
        self.c.pause_device(self.d)
        self.c.continue_or_run(self.d)
        # Worker saw PAUSE, then UI resumed before deferred completion arrived.
        job['write_deferred'] = True
        success(None)
        self.assertEqual(len(self.c.hardware.pending),1)
        self.c.hardware.run_next()
        self.assertEqual(peer.writes,[(0,False)])
        self.assertTrue(job['timer'].isActive())
    def test_inflight_acknowledgement_does_not_start_paused_timer(self):
        peer,job = self.real_peer()
        self.c.hardware.run_next()
        self.c.job_step(self.d,0,job)
        peer.before_reply = lambda: self.c.pause_device(self.d)
        self.c.hardware.run_next()
        self.assertTrue(peer.states[0])  # Already transmitted; cannot be recalled.
        self.assertTrue(job.get('paused'))
        self.assertFalse(job['timer'].isActive())
        self.c.continue_or_run(self.d)
        self.assertTrue(job['timer'].isActive())
        self.assertEqual(peer.writes,[(0,False),(0,True)])
    def test_reset_cancels_deferred_transition(self):
        peer,job = self.real_peer()
        self.c.hardware.run_next()
        self.c.job_step(self.d,0,job)
        self.c.pause_device(self.d)
        self.c.hardware.run_next()
        self.c.stop_device(self.d)
        while self.c.hardware.pending: self.c.hardware.run_next()
        self.assertNotIn((0,True),peer.writes)
        self.assertFalse(self.c.jobs)
    def test_toolbar_reset_preserves_idle_unit(self):
        idle = self.c.devices[1]
        self.c.manual(idle,0,True)
        self.c.run_selected(self.d)
        self.c.pause_device(self.d)
        self.c.resetBothButton.click()
        self.assertTrue(idle['states'][0])
        self.assertFalse(self.c.jobs)
    def test_combined_reset_preserves_idle_unit(self):
        idle = self.c.devices[1]
        self.c.manual(idle,1,True)
        self.c.run_selected(self.d)
        self.c.pause_device(self.d)
        self.c.show_all_pulses()
        self.c.combinedResetButton.click()
        self.assertTrue(idle['states'][1])
        self.assertFalse(self.c.jobs)
    def configuration(self):
        path=self.root/'input.json'
        main.config_store.save(path,self.c.devices,0,True)
        return path,json.loads(path.read_text(encoding='utf-8'))
    def test_invalid_names_rejected_before_model_replacement(self):
        path,data=self.configuration()
        data['devices'][0]['names'][0]=123
        path.write_text(json.dumps(data),encoding='utf-8')
        old=self.c.devices
        self.c.config_path=path
        self.assertFalse(self.c.load_configuration())
        self.assertIs(self.c.devices,old)
    def test_invalid_types_rejected_at_boundary(self):
        path,valid=self.configuration()
        for field,value in [('repeat',True),('trigger',1.5),('active','yes')]:
            data=deepcopy(valid)
            data['devices'][0]['config'][0][field]=value
            path.write_text(json.dumps(data),encoding='utf-8')
            with self.subTest(field=field),self.assertRaises(ValueError): main.config_store.load(path)
    def test_invalid_selections_rejected_at_boundary(self):
        path,data=self.configuration()
        data['devices'][0]['manual_selection']={'continuousSelect':[True,False,'ON',False,False,False]}
        path.write_text(json.dumps(data),encoding='utf-8')
        with self.assertRaises(ValueError): main.config_store.load(path)
    def test_invalid_late_setting_keeps_entire_current_document(self):
        path,data=self.configuration()
        data['devices'][0]['name']='Must not be imported'
        data['countdown']={'enabled':True,'seconds':'three'}
        path.write_text(json.dumps(data),encoding='utf-8')
        old=self.c.devices
        self.c.config_path=path
        self.assertFalse(self.c.load_configuration())
        self.assertIs(self.c.devices,old)
        self.assertNotEqual(self.c.devices[0]['name'],'Must not be imported')
    def test_runtime_fields_not_imported(self):
        path,data=self.configuration()
        data['devices'][0].update(connecting=True,baseline=[True]*6,inputs=[True]*6,error='forged')
        path.write_text(json.dumps(data),encoding='utf-8')
        self.c.config_path=path
        self.assertTrue(self.c.load_configuration())
        loaded=self.c.devices[0]
        for key in ('connecting','baseline','error'): self.assertNotIn(key,loaded)
        self.assertEqual(loaded['inputs'],[False]*6)
    def test_invalid_open_preserves_current_document(self):
        path,data=self.configuration()
        data['devices'][1]['names'][5]=False
        path.write_text(json.dumps(data),encoding='utf-8')
        old=self.c.devices
        with patch.object(QFileDialog,'getOpenFileName',return_value=(str(path),'')),patch.object(QMessageBox,'warning'):
            self.assertFalse(self.c.open_configuration_file())
        self.assertIs(self.c.devices,old)
        self.assertEqual(json.loads(path.read_text())['devices'][1]['names'][5],False)
    def saved_controller(self):
        self.c.config_path=self.root/'saved.json'
        self.c.saveTimer=main.SettingsSaveTimer(self.c)
        self.c.saveTimer.setSingleShot(True)
        self.c.saveTimer.timeout.connect(self.c.save_configuration)
        self.assertTrue(self.c.save_configuration())
    def wait_saved(self):
        deadline=monotonic()+2
        while self.c.saveTimer.isActive() and monotonic()<deadline: QTest.qWait(20)
        self.assertEqual(self.c.saveStatus.text(),'Saved')
    def test_both_manual_selection_groups_autosave(self):
        self.saved_controller()
        for prefix in ('continuousSelect','momentarySelect'):
            with self.subTest(prefix=prefix):
                getattr(self.c.panels[0],f'{prefix}2').setChecked(True)
                self.assertTrue(self.c.saveTimer.isActive())
                self.assertEqual(self.c.saveStatus.text(),'Saving…')
                self.wait_saved()
                data=main.config_store.load(self.c.config_path)
                self.assertTrue(data['devices'][0]['manual_selection'][prefix][1])
    def test_collapse_preference_autosaves(self):
        self.saved_controller()
        self.c.collapse_unit(self.c.panels[0],self.d)
        self.assertTrue(self.c.saveTimer.isActive())
        self.wait_saved()
        self.assertTrue(main.config_store.load(self.c.config_path)['devices'][0]['collapsed'])

if __name__=='__main__': unittest.main(verbosity=2)
