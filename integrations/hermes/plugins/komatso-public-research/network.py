"""Public HTTP(S) egress proxy: every TCP dial uses a validated numeric IP."""
import ipaddress
import select
import socket
import socketserver
import threading
from urllib.parse import urlsplit

class PublicNetworkDenied(ValueError):
    pass

def public_addresses(host, port):
    host = host.rstrip(".").lower()
    if not host or host in {"localhost", "metadata.google.internal", "metadata.goog"} or host.endswith((".localhost", ".local", ".internal")):
        raise PublicNetworkDenied("Private/local hostname denied")
    if "%" in host:
        raise PublicNetworkDenied("Scoped address denied")
    answers = socket.getaddrinfo(host, port, socket.AF_UNSPEC, socket.SOCK_STREAM)
    safe = []
    for family, _, _, _, addr in answers:
        ip = ipaddress.ip_address(addr[0])
        embedded = ip.ipv4_mapped if isinstance(ip, ipaddress.IPv6Address) else None
        if isinstance(ip, ipaddress.IPv6Address) and ip in ipaddress.ip_network("::ffff:0:0:0/96"):
            embedded = ipaddress.IPv4Address(int(ip) & 0xffffffff)
        checked = embedded or ip
        if not checked.is_global or checked.is_multicast or checked.is_reserved or checked == ipaddress.ip_address("100.100.100.200"):
            raise PublicNetworkDenied("Private/local IP denied")
        # Transition mechanisms can otherwise tunnel an internal IPv4 through a global IPv6.
        if isinstance(ip, ipaddress.IPv6Address) and (
            ip in ipaddress.ip_network("64:ff9b::/96") or ip.sixtofour is not None or ip.teredo is not None
        ):
            raise PublicNetworkDenied("IPv6 transition address denied")
        if (family,addr) not in safe:
            safe.append((family,addr))
    if not safe:
        raise PublicNetworkDenied("DNS returned no public addresses")
    return safe

def public_url(url, resolve=True):
    if not isinstance(url, str) or any(c in url for c in "\r\n\\"):
        raise PublicNetworkDenied("Only public HTTP/HTTPS URLs are allowed")
    try:
        parsed=urlsplit(url)
        if parsed.scheme.lower() not in {"http","https"} or not parsed.hostname or parsed.username or parsed.password:
            raise PublicNetworkDenied("Only public HTTP/HTTPS URLs without userinfo are allowed")
        port=parsed.port or (443 if parsed.scheme.lower()=="https" else 80)
        if not 1 <= port <= 65535:
            raise PublicNetworkDenied("Invalid port")
        if resolve:
            public_addresses(parsed.hostname,port)
        return parsed
    except ValueError as exc:
        raise PublicNetworkDenied(str(exc)) from exc

def dial_public(host, port):
    # Validate ALL DNS answers, then connect by sockaddr. Never re-resolve the hostname.
    addresses=public_addresses(host,port)
    error=None
    for family,addr in addresses[:8]:
        sock=socket.socket(family,socket.SOCK_STREAM)
        sock.settimeout(12)
        try:
            sock.connect(addr)
            return sock
        except OSError as exc:
            sock.close(); error=exc
    raise error or PublicNetworkDenied("No public connection available")

class _Handler(socketserver.StreamRequestHandler):
    def handle(self):
        upstream=None
        try:
            self.connection.settimeout(20)
            line=self.rfile.readline(8193)
            if len(line)>8192: raise PublicNetworkDenied("Request line too large")
            method,target,version=line.decode("ascii").strip().split(" ")
            headers=[]; size=0
            while True:
                line=self.rfile.readline(8193); size+=len(line)
                if size>65536 or len(line)>8192: raise PublicNetworkDenied("Headers too large")
                if line in {b"\r\n",b"\n"}: break
                if not line: return
                headers.append(line)
            if method=="CONNECT":
                parsed=public_url("https://"+target,resolve=False)
                if parsed.path not in {"","/"} or parsed.query or parsed.fragment:
                    raise PublicNetworkDenied("Invalid CONNECT target")
                upstream=dial_public(parsed.hostname,parsed.port or 443)
                self.wfile.write(b"HTTP/1.1 200 Connection Established\r\n\r\n"); self.wfile.flush()
            else:
                parsed=public_url(target,resolve=False)
                if parsed.scheme!="http": raise PublicNetworkDenied("Use CONNECT for HTTPS")
                upstream=dial_public(parsed.hostname,parsed.port or 80)
                path=(parsed.path or "/")+("?" + parsed.query if parsed.query else "")
                # No ambiguous upstream destination headers, no proxy credential forwarding.
                kept=[h for h in headers if h.split(b":",1)[0].lower() not in {b"host",b"proxy-authorization",b"proxy-connection",b"connection"}]
                authority=parsed.netloc.encode("ascii")
                req=f"{method} {path} HTTP/1.1\r\n".encode("ascii")+b"Host: "+authority+b"\r\n"+b"".join(kept)+b"Connection: close\r\n\r\n"
                upstream.sendall(req)
            # Unbuffered reader means no TLS/body bytes are stranded in a read-ahead buffer.
            sockets=[self.connection,upstream]
            while True:
                ready,_,_=select.select(sockets,[],[],45)
                if not ready: break
                for src in ready:
                    chunk=src.recv(65536)
                    if not chunk: return
                    (upstream if src is self.connection else self.connection).sendall(chunk)
        except (OSError,ValueError,UnicodeError):
            try:
                self.wfile.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"); self.wfile.flush()
            except OSError:
                pass
        finally:
            if upstream: upstream.close()

class _Server(socketserver.ThreadingTCPServer):
    daemon_threads=True
    allow_reuse_address=False
    request_queue_size=64

_Handler.rbufsize=0

class PublicProxy:
    def __init__(self):
        self.server=_Server(("127.0.0.1",0),_Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,name="public-browser-egress",daemon=True)
        self.thread.start()
        self.url="http://127.0.0.1:"+str(self.server.server_address[1])
    def close(self):
        self.server.shutdown(); self.server.server_close()
