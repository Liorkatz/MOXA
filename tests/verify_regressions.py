"""Connection fault and automation edge-case suite; all transports are mocked."""
import _bootstrap  # Allow direct execution from the tests folder.
import struct
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
import main
from moxa_backend import E1214Client, ModbusError, HardwareQueue

app = QApplication.instance() or QApplication([])


class Reply:
    def __init__(self, data): self.data = data
    def __enter__(self): return self
    def __exit__(self,*args): pass
    def settimeout(self,*args): pass
    def sendall(self,*args): pass
    def recv(self,size):
        result,self.data = self.data[:1],self.data[1:]
        return result


class ProtocolTests(unittest.TestCase):
    def check_reply(self,body,tid=1,protocol=0,unit=1,length=None):
        packet=struct.pack('>HHHB',tid,protocol,len(body)+1 if length is None else length,unit)+body
        with patch('moxa_backend.socket.create_connection',return_value=Reply(packet)):
            return E1214Client('127.0.0.1').bits(1)

    def test_fragmented_bits(self):
        self.assertEqual(self.check_reply(b'\x01\x01\x25'),[True,False,True,False,False,True])

    def test_reject_invalid_responses(self):
        cases=[(b'\x01\x01\0',dict(tid=9)),(b'\x01\x01\0',dict(protocol=1)),
               (b'\x01\x01\0',dict(unit=2)),(b'\x01\x01\0',dict(length=255)),
               (b'\x81\x02',{}),(b'\x02\x01\0',{}),(b'\x01\x02\0',{})]
        for body,kwargs in cases:
            with self.subTest(body=body,kwargs=kwargs), self.assertRaises(ModbusError):
                self.check_reply(body,**kwargs)

    def test_truncated_reply(self):
        with self.assertRaises(ModbusError): self.check_reply(b'\x01',length=8)

    def test_timeout(self):
        with patch('moxa_backend.socket.create_connection',side_effect=TimeoutError), self.assertRaises(TimeoutError):
            E1214Client('127.0.0.1').connect()

    def test_invalid_channel_no_network(self):
        with patch('moxa_backend.socket.create_connection') as connection:
            for channel in (-1,6):
                with self.assertRaises(ModbusError): E1214Client('127.0.0.1').write(channel,True)
            connection.assert_not_called()

    def test_readback_mismatch(self):
        client=E1214Client('127.0.0.1')
        with patch.object(client,'request',return_value=struct.pack('>HH',0,0xFF00)), patch.object(client,'bits',return_value=[False]*6), self.assertRaises(ModbusError):
            client.write(0,True)


class AutomationTests(unittest.TestCase):
    def setUp(self):
        self.c=main.Controller()
        self.c.pollTimer.stop()
        self.d=self.c.devices[0]
        for d in self.c.devices:
            for cfg in d['config']: cfg.update(active=False,first=100,pulse=80,between=50,repeat=2,trigger=0)

    def tearDown(self):
        for d in self.c.devices:
            for i in range(6): self.c.cancel(d,i)
        self.c.hardware.close()
        self.c.window.deleteLater()
        app.processEvents()

    def start(self,**values):
        self.d['config'][0].update(active=True,**values)
        self.c.run_selected(self.d)

    def test_pause_first_delay(self):
        self.start(first=150,pulse=500)
        QTest.qWait(35)
        self.c.pause_device(self.d)
        remaining=self.c.jobs[(self.d['uid'],0)]['remaining_ms']
        deadline = time.monotonic() + 2
        while self.c.jobs and time.monotonic() < deadline:
            QTest.qWait(10)
        self.assertFalse(self.d['states'][0])
        self.assertEqual(self.c.jobs[(self.d['uid'],0)]['remaining_ms'],remaining)
        self.c.continue_or_run(self.d)
        self.assertLessEqual(self.c.jobs[(self.d['uid'],0)]['timer'].remainingTime(),remaining+10)
        deadline=time.monotonic()+1
        while not self.d['states'][0] and time.monotonic()<deadline:
            app.processEvents()
            time.sleep(0.005)
        self.assertTrue(self.d['states'][0])

    def test_pause_on_state_and_reset_baseline(self):
        self.c.manual(self.d,0,True)
        self.start(first=0,pulse=400)
        QTest.qWait(25)
        self.c.pause_device(self.d)
        QTest.qWait(50)
        self.assertTrue(self.d['states'][0])
        self.c.stop_device(self.d)
        self.assertTrue(self.d['states'][0])
        self.assertIsNone(self.c.panels[0].timeline.elapsed)
        self.assertFalse(self.c.jobs)

    def test_repeat_count(self):
        self.start(first=0,pulse=15,between=10,repeat=3)
        QTest.qWait(170)
        self.assertFalse(self.c.jobs)
        self.assertFalse(self.d['states'][0])
        self.assertEqual(sum('pulse ON requested' in line for _,line in self.c.entries),3)

    def test_zero_repeats(self):
        self.start(repeat=0)
        self.assertFalse(self.c.jobs)

    def test_manual_override_only_cancels_target(self):
        self.d['config'][1]['active']=True
        self.start(first=200)
        self.c.manual(self.d,0,True)
        self.assertNotIn((self.d['uid'],0),self.c.jobs)
        self.assertIn((self.d['uid'],1),self.c.jobs)

    def test_manual_pulse_not_automation(self):
        self.c.pulse(self.d,1)
        self.assertEqual(self.c.panels[0].runSelectedButton.text(),'RUN')
        self.c.pause_device(self.d)
        self.assertNotIn('paused',self.c.jobs[(self.d['uid'],1)])

    def test_all_pause_continue_reset(self):
        for d in self.c.devices: d['config'][0]['active']=True
        self.c.run_both()
        self.c.run_both()
        self.assertTrue(all(j.get('paused') for j in self.c.jobs.values()))
        self.c.run_both()
        self.assertTrue(all(not j.get('paused') for j in self.c.jobs.values()))
        for d in self.c.devices: self.c.stop_device(d)
        self.assertFalse(self.c.jobs)

    def test_connection_loss_cancels_without_claiming_off(self):
        self.start(first=0,pulse=500)
        QTest.qWait(20)
        self.c.hardware_error(self.d,'test timeout')
        self.assertTrue(self.d['states'][0])
        self.assertFalse(self.d['connected'])
        self.assertFalse(self.c.jobs)

    def test_no_di_start_while_paused(self):
        self.start()
        self.c.pause_device(self.d)
        self.d['config'][1].update(active=True,trigger=1)
        class Client:
            def bits(self,function): return [True]*6 if function==2 else list(self.d['states'])
        client=Client()
        client.d=self.d
        self.c.clients[self.d['uid']]=client
        self.d['mode']='real'
        self.c.poll_inputs()
        deadline=time.monotonic()+1
        while self.c.polling and time.monotonic()<deadline:
            app.processEvents(); time.sleep(0.005)
        self.assertNotIn((self.d['uid'],1),self.c.jobs)

    def test_edit_during_connect_rejected(self):
        self.d.update(mode='real',connected=False,connecting=True)
        self.c.load_connection(0)
        old=self.d['ip']
        self.c.connections.ipInput.setText('192.168.12.34')
        self.c.save_connection()
        self.assertEqual(self.d['ip'],old)

    def test_corrupt_configuration_preserved(self):
        with tempfile.TemporaryDirectory(dir=main.ROOT) as directory:
            path=Path(directory)/'config.json'
            path.write_text('{invalid',encoding='utf-8')
            controller=main.Controller(config_path=path)
            controller.save_configuration()
            self.assertEqual(path.read_text(encoding='utf-8'),'{invalid')
            controller.pollTimer.stop()
            controller.hardware.close()
            controller.window.deleteLater()

    def test_queue_ignores_submit_after_close(self):
        queue=HardwareQueue()
        queue.close()
        result=[]
        queue.submit(1,lambda: result.append(1),lambda r: None,lambda r: None)
        self.assertFalse(result)


if __name__=='__main__':
    unittest.main(verbosity=2)
