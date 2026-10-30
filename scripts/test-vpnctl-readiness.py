"""Readiness ждёт фактический healthy в пределах срока, не маскируя постоянный сбой."""
from pathlib import Path
import sys
import unittest
from unittest import mock

PROJECT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT/'lib'))
from vpnctl_manager import Manager
from vpnctl_common import ManagerError


class Readiness(unittest.TestCase):
    def manager(self,states):
        manager=Manager(PROJECT)
        manager.inspect_container=mock.Mock(side_effect=states)
        return manager

    def test_unhealthy_during_start_can_recover_before_deadline(self):
        states=[{'State':{'Running':True,'Health':{'Status':value}}} for value in ['starting','unhealthy','unhealthy','healthy']]
        manager=self.manager(states);now=[0]
        with mock.patch('vpnctl_manager.time.monotonic',side_effect=lambda:now[0]),mock.patch('vpnctl_manager.time.sleep',side_effect=lambda value:now.__setitem__(0,now[0]+value)):
            manager.health({'container':'тестовый'},timeout=5)
        self.assertEqual(manager.inspect_container.call_count,4)

    def test_persistent_unhealthy_is_rejected_at_deadline(self):
        state={'State':{'Running':True,'Health':{'Status':'unhealthy'}}}
        manager=self.manager([state]*10);now=[0]
        with mock.patch('vpnctl_manager.time.monotonic',side_effect=lambda:now[0]),mock.patch('vpnctl_manager.time.sleep',side_effect=lambda value:now.__setitem__(0,now[0]+value)):
            with self.assertRaisesRegex(ManagerError,'Истёк.*unhealthy'):manager.health({'container':'тестовый'},timeout=3)
        self.assertEqual(now[0],3)

    def test_stopped_container_fails_without_waiting(self):
        manager=self.manager([{'State':{'Running':False}}])
        with mock.patch('vpnctl_manager.time.sleep') as sleep:
            with self.assertRaisesRegex(ManagerError,'не работает'):manager.health({'container':'тестовый'})
            sleep.assert_not_called()


if __name__=='__main__':unittest.main()
