"""Offline transport and registration tests; no production business data."""
import importlib.util
import sys
from pathlib import Path
import socket
import unittest
from unittest.mock import patch, Mock
ROOT=Path(__file__).resolve().parents[2]
PLUGIN=ROOT/"integrations/hermes/plugins/komatso-public-research"
spec=importlib.util.spec_from_file_location("phase1_research",PLUGIN/"__init__.py",submodule_search_locations=[str(PLUGIN)])
pkg=importlib.util.module_from_spec(spec); sys.modules[spec.name]=pkg; spec.loader.exec_module(pkg)
network=sys.modules["phase1_research.network"]

def answer(ip,port=443):
    family=socket.AF_INET6 if ":" in ip else socket.AF_INET
    return [(family,socket.SOCK_STREAM,6,"",(ip,port))]
class PublicNetworkTests(unittest.TestCase):
    def test_private_network_urls_never_dial(self):
        urls=["http://127.0.0.1","http://localhost","http://localhost.","http://10.0.1.2","http://172.16.1.2","http://192.168.1.1","http://[::1]","http://[fc00::1]","http://[fe80::1]","file:///C:/Windows/win.ini","C:\\Windows","\\\\server\\share","http://0.0.0.0","http://169.254.169.254","http://[::ffff:127.0.0.1]","http://[64:ff9b::a00:1]"]
        for url in urls:
            with self.subTest(url=url), patch.object(network.socket,"getaddrinfo",return_value=answer("10.1.2.3")), patch.object(network.socket,"socket") as dial:
                with self.assertRaises(network.PublicNetworkDenied): network.public_url(url)
                dial.assert_not_called()
    def test_public_sites_have_no_domain_allowlist(self):
        for host in ["example.com","www.wikipedia.org","www.komatsu.com","arbitrary-public-site.net"]:
            with self.subTest(host=host), patch.object(network.socket,"getaddrinfo",return_value=answer("93.184.216.34")):
                self.assertEqual(network.public_url("https://"+host).hostname,host)
    def test_all_dns_answers_checked_and_connect_uses_numeric_ip(self):
        with patch.object(network.socket,"getaddrinfo",return_value=answer("93.184.216.34")+answer("10.1.2.3")),patch.object(network.socket,"socket") as factory:
            with self.assertRaises(network.PublicNetworkDenied): network.dial_public("example.com",443)
            factory.assert_not_called()
        with patch.object(network.socket,"getaddrinfo",return_value=answer("93.184.216.34")) as dns,patch.object(network.socket,"socket") as factory:
            network.dial_public("example.com",443)
            factory.return_value.connect.assert_called_once_with(("93.184.216.34",443))
            dns.assert_called_once()
    def test_rebinding_is_rechecked_at_each_new_connection(self):
        with patch.object(network.socket,"getaddrinfo",side_effect=[answer("93.184.216.34"),answer("192.168.1.1")]),patch.object(network.socket,"socket") as factory:
            network.public_url("https://example.com")
            with self.assertRaises(network.PublicNetworkDenied): network.dial_public("example.com",443)
            factory.assert_not_called()
    def test_private_proxy_requests_return_deny_without_host_connection(self):
        proxy=network.PublicProxy()
        try:
            with patch.object(network,"dial_public",side_effect=network.PublicNetworkDenied("denied")) as dial:
                with socket.create_connection(proxy.server.server_address) as sock:
                    sock.sendall(b"CONNECT 10.1.2.3:443 HTTP/1.1\r\nHost: 10.1.2.3\r\n\r\n")
                    self.assertIn(b"403 Forbidden",sock.recv(4096))
                dial.assert_called_once_with("10.1.2.3",443)
        finally: proxy.close()
    def test_registration_has_research_without_host_control(self):
        captured=[]
        ctx=Mock(); ctx.register_tool.side_effect=lambda **kw:captured.append(kw)
        pkg.register(ctx)
        names={item["name"] for item in captured}
        self.assertIn("public_browser_navigate",names)
        self.assertIn("public_browser_console",names)
        self.assertIn("public_browser_screenshot",names)
        self.assertFalse(names & {"terminal","read_file","skill_manage","execute_code","manage_connections"})
        self.assertTrue(all(i["toolset"]=="komatso_public_browser" for i in captured))
        # Every schema rejects arbitrary paths/argv and every command is fixed at registration.
        self.assertTrue(all(i["schema"]["parameters"]["additionalProperties"] is False for i in captured))

class ScopedWorkerCleanupTests(unittest.TestCase):
    def test_timeout_kills_only_registered_worker_tree(self):
        import subprocess,types,threading
        browser=sys.modules["phase1_research.browser"]
        worker=browser.BrowserWorker.__new__(browser.BrowserWorker)
        worker._proxy_lock=threading.Lock()
        worker.proxy=Mock()
        worker.proc=Mock(pid=321)
        worker.proc.poll.return_value=None
        worker.proc.wait.side_effect=[subprocess.TimeoutExpired("fixed-browser-worker",5),0]
        deadline=types.ModuleType("agent.deadline")
        deadline.kill_process_tree=Mock()
        agent=types.ModuleType("agent")
        with patch.dict(sys.modules,{"agent":agent,"agent.deadline":deadline}):
            worker.close()
        deadline.kill_process_tree.assert_called_once_with(321)
        worker.proc.kill.assert_not_called()

if __name__=="__main__": unittest.main()
