# ==================== STANDARD IMPORTS ====================
import sys
import asyncio
import httpx
import random
import json
import socket
import struct
import time
import os
import uuid
import itertools
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any

if sys.platform == "win32":
    try:
        if hasattr(sys.stdout, 'reconfigure'):
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        if hasattr(sys.stderr, 'reconfigure'):
            sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

from google_play_scraper import app as play_scraper
from Cryptodome.Cipher import AES
from Cryptodome.Util.Padding import pad
from protobuf_decoder.protobuf_decoder import Parser
from message_ids import MESSAGE_ID_TO_NAME
import thunderFF_pb2
from dashboard_server import bot_state, start_web_dashboard

WEB_HOST = "0.0.0.0"
WEB_PORT = int(os.environ.get("PORT", 5000))
ACCOUNTS_FILE = os.environ.get("ACCOUNTS_FILE", "accounts.json")
TOKEN_CACHE_FILE = "token_cache.json"
DEVICES_FILE = "devices.json"
TOKEN_CACHE_TTL = 1200

START_MATCH_INTERVAL = 3.0
NEW_MATCH_DELAY = 3.0
MAX_MATCH_DURATION = 700
MATCH_IDLE_TIMEOUT = 8.0
PRIORITY_REGIONS = ["BD","IND", "SG", "TH", "PH", "VN", "MY", "ID", "HK", "TW"]

MAX_CONSECUTIVE_PARSE_FAILURES = 5.0
NON_MATCH_RECONNECT_DELAY = 1.0

FALLBACK_UID = ""
FALLBACK_PASSWORD = ""

DEBUG_HTTP = True
DEBUG_BODY_LIMIT = 4096


def get_device_for_account(account_identifier: str) -> dict:
    devices = {}
    if os.path.exists(DEVICES_FILE):
        try:
            with open(DEVICES_FILE, "r", encoding="utf-8") as f:
                devices = json.load(f)
        except Exception:
            pass
    acc_key = str(account_identifier)
    if acc_key in devices:
        return devices[acc_key]
    device_list = [
        ("Samsung", "SM-G998B", "Adreno (TM) 660", "Android OS 12 / API-31"),
        ("Xiaomi", "2201122G", "Adreno (TM) 730", "Android OS 13 / API-33"),
        ("Realme", "RMX3700", "Mali-G710", "Android OS 14 / API-34"),
        ("OnePlus", "CPH2451", "Adreno (TM) 740", "Android OS 13 / API-33"),
        ("OPPO", "CPH2611", "Adreno (TM) 720", "Android OS 14 / API-34"),
        ("Vivo", "V2203", "Mali-G710", "Android OS 12 / API-31"),
        ("Poco", "M2102J20SG", "Adreno (TM) 660", "Android OS 13 / API-33"),
    ]
    brand, model, gpu, os_ver = random.choice(device_list)
    new_device = {
        "unique_device_id": f"Google|{str(uuid.uuid4())}",
        "brand": brand,
        "model": model,
        "gpu_renderer": gpu,
        "system_software": os_ver,
        "screen_width": random.choice([1080, 1440, 720, 1280]),
        "screen_height": random.choice([2400, 3200, 1600, 2400]),
        "screen_dpi": str(random.randint(300, 420)),
        "memory": random.randint(2800, 6500),
        "processor_details": f"ARM64 FP ASIMD AES VMH | {random.randint(2200, 3200)} | {random.randint(6, 12)}",
        "client_ip": f"{random.randint(103, 223)}.{random.randint(10, 250)}.{random.randint(10, 250)}.{random.randint(10, 250)}"
    }
    devices[acc_key] = new_device
    try:
        with open(DEVICES_FILE, "w", encoding="utf-8") as f:
            json.dump(devices, f, indent=4)
    except Exception as e:
        print_error(f"Failed to save device mapping: {e}")
    return new_device


CLOUDFLARE_PRIMARY_DNS = "1.1.1.1"
CLOUDFLARE_SECONDARY_DNS = "1.0.0.1"
_DNS_CACHE: Dict[str, Tuple[str, float]] = {}
_DNS_CACHE_TTL = 300.0


async def resolve_host_cloudflare(hostname: str) -> str:
    if not hostname:
        return hostname
    parts = hostname.split('.')
    if len(parts) == 4 and all(p.isdigit() and 0 <= int(p) <= 255 for p in parts):
        return hostname
    now = time.time()
    if hostname in _DNS_CACHE:
        ip, exp = _DNS_CACHE[hostname]
        if now < exp:
            return ip

    def _query_cloudflare(server_ip: str) -> Optional[str]:
        s = None
        try:
            tx_id = random.randint(1000, 65535)
            header = struct.pack(">HHHHHH", tx_id, 0x0100, 1, 0, 0, 0)
            qname = b"".join(bytes([len(part)]) + part.encode('ascii') for part in hostname.split('.')) + b"\x00"
            query_pkt = header + qname + struct.pack(">HH", 1, 1)
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(1.2)
            s.sendto(query_pkt, (server_ip, 53))
            resp, _ = s.recvfrom(1024)
            if len(resp) >= 12:
                ancount = struct.unpack(">H", resp[6:8])[0]
                if ancount > 0:
                    offset = 12 + len(qname) + 4
                    for _ in range(ancount):
                        if offset >= len(resp):
                            break
                        if (resp[offset] & 0xC0) == 0xC0:
                            offset += 2
                        else:
                            while offset < len(resp) and resp[offset] != 0:
                                offset += 1 + resp[offset]
                            offset += 1
                        if offset + 10 > len(resp):
                            break
                        rtype, rclass, ttl, rdlen = struct.unpack(">HHIH", resp[offset:offset+10])
                        offset += 10
                        if rtype == 1 and rdlen == 4 and offset + 4 <= len(resp):
                            return socket.inet_ntoa(resp[offset:offset+4])
                        offset += rdlen
        except Exception:
            pass
        finally:
            if s:
                try:
                    s.close()
                except Exception:
                    pass
        return None

    loop = asyncio.get_running_loop()
    ip = await loop.run_in_executor(None, _query_cloudflare, CLOUDFLARE_PRIMARY_DNS)
    if not ip:
        ip = await loop.run_in_executor(None, _query_cloudflare, CLOUDFLARE_SECONDARY_DNS)
    if not ip:
        try:
            ip_info = await loop.getaddrinfo(hostname, None, family=socket.AF_INET)
            if ip_info:
                ip = ip_info[0][4][0]
        except Exception:
            ip = hostname
    if ip:
        _DNS_CACHE[hostname] = (ip, now + _DNS_CACHE_TTL)
    return ip or hostname


def optimize_tcp_socket(sock: socket.socket):
    try:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 65536)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 65536)
    except Exception:
        pass


def optimize_udp_socket(sock: socket.socket):
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 131072)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 131072)
        if hasattr(socket, 'SIO_UDP_CONNRESET') and os.name == 'nt':
            try:
                sock.ioctl(socket.SIO_UDP_CONNRESET, False)
            except Exception:
                pass
    except Exception:
        pass


client = httpx.AsyncClient(
    verify=False,
    timeout=10.0,
    limits=httpx.Limits(max_connections=100, max_keepalive_connections=50)
)

headers = {
    'User-Agent': 'UnityPlayer/2018.4.12f1 (UnityWebRequest/1.0, libcurl/8.5.0-DEV)',
    'Connection': 'Keep-Alive',
    'Accept-Encoding': 'gzip',
    'Content-Type': 'application/x-www-form-urlencoded',
    'Expect': '100-continue',
    'X-Unity-Version': '2018.4.12f1',
    'X-GA-SV': '1789535859',
    'X-GA': 'v1 1',
    'ReleaseVersion': 'OB55'
}

AES_KEY = b'Yg&tc%DEuh6%Zc^8'
AES_IV = b'6oyZDr22E3ychjM%'

CRC7_TABLE = bytes([
    0, 9, 18, 27, 36, 45, 54, 63, 72, 65, 90, 83, 108, 101, 126, 119,
    25, 16, 11, 2, 61, 52, 47, 38, 81, 88, 67, 74, 117, 124, 103, 110,
    50, 59, 32, 41, 22, 31, 4, 13, 122, 115, 104, 97, 94, 87, 76, 69,
    43, 34, 57, 48, 15, 6, 29, 20, 99, 106, 113, 120, 71, 78, 85, 92,
    100, 109, 118, 127, 64, 73, 82, 91, 44, 37, 62, 55, 8, 1, 26, 19,
    125, 116, 111, 102, 89, 80, 75, 66, 53, 60, 39, 46, 17, 24, 3, 10,
    86, 95, 68, 77, 114, 123, 96, 105, 30, 23, 12, 5, 58, 51, 40, 33,
    79, 70, 93, 84, 107, 98, 121, 112, 7, 14, 21, 28, 35, 42, 49, 56,
    65, 72, 83, 90, 101, 108, 119, 126, 9, 0, 27, 18, 45, 36, 63, 54,
    88, 81, 74, 67, 124, 117, 110, 103, 16, 25, 2, 11, 52, 61, 38, 47,
    115, 122, 97, 104, 87, 94, 69, 76, 59, 50, 41, 32, 31, 22, 13, 4,
    106, 99, 120, 113, 78, 71, 92, 85, 34, 43, 48, 57, 6, 15, 20, 29,
    37, 44, 55, 62, 1, 8, 19, 26, 109, 100, 127, 118, 73, 64, 91, 82,
    60, 53, 46, 39, 24, 17, 10, 3, 116, 125, 102, 111, 80, 89, 66, 75,
    23, 30, 5, 12, 51, 58, 33, 40, 95, 86, 77, 68, 123, 114, 105, 96,
    14, 7, 28, 21, 42, 35, 56, 49, 70, 79, 84, 93, 98, 107, 112, 121,
])

_DELTA = 0x9E3779B9
_ROUNDS = 16
_FIELD_SIZES = {0: 1, 1: 2, 2: 2, 3: 1, 4: 2}
_FIELD_NAMES = {0: "sendOption", 1: "cmd", 2: "orderId", 3: "flags", 4: "length"}

sai_tail_dul = bytes.fromhex(
    "0101030101045452000103000100000410312e3133302e3232"
    "1432303139313231303430ca0163736f7665727365612e737472"
    "6f6e67686f6c642e66726565666972656d6f62696c652e636f6d"
    "3b302e302e302e303b33342e3132362e37362e34353b33342e38"
    "372e3137372e31343b33342e38372e3137302e3233303b33352e"
    "3138352e3138332e353700000000000001000000000000000000"
    "0000000100000000000100000000000100b8eeec91c5d7ffde110200"
)


def _dbg_hx(data, limit=DEBUG_BODY_LIMIT):
    if data is None:
        return "<None>"
    if isinstance(data, str):
        data = data.encode("utf-8", errors="replace")
    if not isinstance(data, (bytes, bytearray)):
        return f"<{type(data).__name__}>"
    h = bytes(data).hex()
    if len(h) > limit:
        return h[:limit] + f"...[+{len(h)-limit} hex chars]"
    return h


def _dbg_txt(data, limit=DEBUG_BODY_LIMIT):
    if data is None:
        return "<None>"
    if isinstance(data, str):
        s = data
    elif isinstance(data, (bytes, bytearray)):
        try:
            s = bytes(data).decode("utf-8", errors="replace")
        except Exception:
            return _dbg_hx(data, limit)
    else:
        s = str(data)
    if len(s) > limit:
        return s[:limit] + f"...[+{len(s)-limit} chars]"
    return s


async def _http_post_debug(label, url, headers=None, data=None, json_body=None, timeout=10.0):
    if not DEBUG_HTTP:
        return await client.post(url, headers=headers, data=data, json=json_body, timeout=timeout)
    print_colored(f"\n{'='*70}", Colors.MAGENTA)
    print_colored(f"[HTTP->] {label}", Colors.MAGENTA)
    print_colored(f"  URL     : {url}", Colors.WHITE)
    print_colored(f"  Method  : POST", Colors.WHITE)
    if headers:
        print_colored(f"  Headers :", Colors.WHITE)
        for k, v in headers.items():
            print_colored(f"    {k}: {v}", Colors.WHITE)
    if data is not None:
        if isinstance(data, (bytes, bytearray)):
            print_colored(f"  Body    : <bytes {len(data)}>", Colors.WHITE)
            print_colored(f"    hex   : {_dbg_hx(data)}", Colors.WHITE)
            if len(data) % 16 == 0 and len(data) > 0:
                print_colored(f"    (len % 16 == 0 -> looks like AES-CBC ciphertext)", Colors.WHITE)
        else:
            print_colored(f"  Body    : {_dbg_txt(data)}", Colors.WHITE)
    if json_body is not None:
        print_colored(f"  JSON    : {json.dumps(json_body)}", Colors.WHITE)
    t0 = time.time()
    try:
        resp = await client.post(url, headers=headers, data=data, json=json_body, timeout=timeout)
        dt = (time.time() - t0) * 1000.0
        print_colored(f"[HTTP<-] {label}  status={resp.status_code}  {dt:.0f}ms", Colors.MAGENTA)
        print_colored(f"  Resp headers:", Colors.WHITE)
        for k, v in resp.headers.items():
            print_colored(f"    {k}: {v}", Colors.WHITE)
        raw = resp.content
        print_colored(f"  Resp len : {len(raw)} bytes", Colors.WHITE)
        txt = _dbg_txt(raw)
        printable = all((c >= 0x20 and c < 0x7F) or c in (0x09, 0x0A, 0x0D) for c in raw[:256]) if raw else True
        if printable:
            print_colored(f"  Resp body: {txt}", Colors.WHITE)
        else:
            print_colored(f"  Resp hex : {_dbg_hx(raw)}", Colors.WHITE)
        print_colored(f"{'='*70}\n", Colors.MAGENTA)
        ct = resp.headers.get("content-type", "")
        if "json" in ct.lower():
            try:
                j = resp.json()
                print_colored(f"[HTTP<-] {label} parsed JSON:\n{json.dumps(j, indent=2)}", Colors.GREEN)
            except Exception as e:
                print_warning(f"[HTTP<-] {label}: JSON parse failed: {e}")
        return resp
    except Exception as e:
        dt = (time.time() - t0) * 1000.0
        print_colored(f"[HTTPX] {label}  FAILED after {dt:.0f}ms", Colors.FAIL)
        print_colored(f"  Exception: {type(e).__name__}: {e}", Colors.FAIL)
        print_colored(f"{'='*70}\n", Colors.MAGENTA)
        raise


async def _http_get_debug(label, url, headers=None, timeout=10.0):
    if not DEBUG_HTTP:
        return await client.get(url, headers=headers, timeout=timeout)
    print_colored(f"\n{'='*70}", Colors.MAGENTA)
    print_colored(f"[HTTP->] {label} (GET)", Colors.MAGENTA)
    print_colored(f"  URL     : {url}", Colors.WHITE)
    if headers:
        print_colored(f"  Headers :", Colors.WHITE)
        for k, v in headers.items():
            print_colored(f"    {k}: {v}", Colors.WHITE)
    t0 = time.time()
    try:
        resp = await client.get(url, headers=headers, timeout=timeout)
        dt = (time.time() - t0) * 1000.0
        print_colored(f"[HTTP<-] {label}  status={resp.status_code}  {dt:.0f}ms", Colors.MAGENTA)
        raw = resp.content
        print_colored(f"  Resp len : {len(raw)} bytes", Colors.WHITE)
        print_colored(f"  Resp body: {_dbg_txt(raw)}", Colors.WHITE)
        print_colored(f"{'='*70}\n", Colors.MAGENTA)
        return resp
    except Exception as e:
        dt = (time.time() - t0) * 1000.0
        print_colored(f"[HTTPX] {label}  FAILED after {dt:.0f}ms", Colors.FAIL)
        print_colored(f"  Exception: {type(e).__name__}: {e}", Colors.FAIL)
        print_colored(f"{'='*70}\n", Colors.MAGENTA)
        raise
