"""Проверяет preflight без запуска команд на реальном хосте."""
import copy
import importlib.util
import json
from pathlib import Path
import unittest

spec=importlib.util.spec_from_file_location('dual_preflight',Path(__file__).resolve().parents[1]/'lib/dual_preflight.py')
p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)

def instances():
    return {'awg':{'backend':'amneziawg','table':'awg_easy_3','interface':'awg0','container':'awg-easy-3','network4':'10.8.0.0/24','address4':'10.8.0.1','udp_port':39516,'panel_port':51821,'data_directory':'/srv/vpn/data'},
            'wg':{'backend':'wireguard','table':'wg_easy_3','interface':'wg0','container':'wg-easy-dual','network4':'10.9.0.0/24','address4':'10.9.0.1','udp_port':51824,'panel_port':51825,'data_directory':'/srv/vpn/data-wg'}}

class Checks(unittest.TestCase):
    def test_resources_and_overlaps(self):
        self.assertEqual(len(p.validate_instances(instances())),2)
        for key in ['interface','container','table','udp_port','panel_port','data_directory']:
            with self.subTest(key=key):
                data=instances();data['wg'][key]=data['awg'][key]
                with self.assertRaises(p.PreflightError): p.validate_instances(data)
        data=instances();data['wg']['network4']='10.8.0.128/25';data['wg']['address4']='10.8.0.129'
        with self.assertRaises(p.PreflightError): p.validate_instances(data)
        data=instances();data['wg']['data_directory']='/srv/vpn/data/nested'
        with self.assertRaises(p.PreflightError): p.validate_instances(data)

    def test_conflicts_are_read_only(self):
        calls=[]
        def run(args):
            calls.append(args)
            if args[0]=='ss': return ''
            if args[:3]==['ip','-j','link']: return '[{"ifname":"eth0"}]'
            if args[0]=='ip': return '[{"dst":"default","dev":"eth0"}]'
            if args[0]=='nft': return '{"nftables":[]}'
            if args[1]=='inspect': return 'null'
            return '{"ID":"111111111111","Names":"other"}\n{"ID":"222222222222","Names":"second"}'
        self.assertFalse(p.inspect_conflicts(instances(),run=run)['conflicts'])
        self.assertTrue(all(not any(word in args for word in ['add','delete','flush','up','down']) for args in calls))
        def routes(args):
            if args[:3]==['ip','-j','-4']: return '[{"dst":"10.0.0.0/8","dev":"foreign"}]'
            return run(args)
        with self.assertRaisesRegex(p.PreflightError,'маршрутом'): p.inspect_conflicts(instances(),run=routes)
        def published(args):
            if args[:2]==['docker','inspect']: return '{"80/udp":[{"HostPort":"51824"}]}'
            return run(args)
        with self.assertRaisesRegex(p.PreflightError,'опубликован'): p.inspect_conflicts(instances(),run=published)
        def socket(args): return 'занят' if args[0]=='ss' else run(args)
        with self.assertRaisesRegex(p.PreflightError,'Порт'): p.inspect_conflicts(instances(),run=socket)
        def link(args): return '[{"ifname":"wg0"}]' if args[:3]==['ip','-j','link'] else run(args)
        with self.assertRaisesRegex(p.PreflightError,'интерфейс'): p.inspect_conflicts(instances(),run=link)

if __name__=='__main__': unittest.main()
