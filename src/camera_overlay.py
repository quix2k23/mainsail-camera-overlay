#!/usr/bin/env python3
"""MJPEG proxy for a webcam: while a print runs, burn a status bar (progress, time remaining, nozzle size, ...)
into the top of every frame; between jobs show the last job's nozzle size; otherwise pass frames through.

  upstream  http://127.0.0.1:$OVERLAY_UPSTREAM_PORT/stream  (an MJPEG streamer, default 8080)
  serves    http://127.0.0.1:$OVERLAY_PORT/stream and /snapshot  (default 8082)
  settings  Moonraker database, namespace camera_overlay (edited from the Mainsail webcam dialog)
  --demo    always draw sample text, for checking the rendering with no print running
"""
import http.client
import http.server
import io
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from PIL import Image, ImageDraw, ImageFont

UPSTREAM = ("127.0.0.1", int(os.environ.get("OVERLAY_UPSTREAM_PORT", "8080")))
MOONRAKER = os.environ.get("OVERLAY_MOONRAKER", "http://127.0.0.1:7125")
GCODES = os.environ.get("OVERLAY_GCODES", os.path.expanduser("~/printer_data/gcodes/"))
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"
PORT = int(os.environ.get("OVERLAY_PORT", "8082"))
DEMO = "--demo" in sys.argv
if "--port" in sys.argv:
    PORT = int(sys.argv[sys.argv.index("--port") + 1])

frame = {"jpeg": b"", "n": 0}
cond = threading.Condition()
status = {"text": None}  # None => not printing, pass frames through
_meta = {}  # filename -> (nozzle string, time found); "--" results are retried after 30 s


def fmt_remaining(sec):
    if sec is None or sec < 0:
        return "--"
    sec = int(sec)
    h, m = divmod(sec // 60, 60)
    return f"{h}h {m:02d}m" if h else f"{m}m"


def moonraker(path):
    with urllib.request.urlopen(MOONRAKER + path, timeout=4) as r:
        return json.load(r)["result"]


def fmt_nozzle(v):
    s = f"{float(v):g}"
    return s if "." in s else s + ".0"


def nozzle_for(filename):
    hit = _meta.get(filename)
    if hit and time.time() - hit[1] < (600 if hit[0] != "--" else 30):  # re-read now and then: a file may be re-uploaded
        return hit[0]
    noz = None
    try:
        m = moonraker("/server/files/metadata?filename=" + urllib.parse.quote(filename))
        if m.get("nozzle_diameter"):
            noz = fmt_nozzle(m['nozzle_diameter'])
        else:  # Cura writes it near the top; PrusaSlicer/Orca near the end
            path = os.path.realpath(GCODES + filename)
            if not path.startswith(os.path.realpath(GCODES) + os.sep):
                raise ValueError("file outside the gcodes folder")
            with open(path, "rb") as f:
                head = f.read(30000).decode("latin-1")
                f.seek(0, 2)
                f.seek(max(0, f.tell() - 30000))
                tail = f.read().decode("latin-1")
            hit = re.search(r"NOZZLE\.DIAMETER[:=]\s*([\d.]+)|nozzle_diameter\s*=\s*([\d.]+)", head + tail)
            if hit:
                noz = fmt_nozzle(hit.group(1) or hit.group(2))
    except Exception:
        pass
    _meta[filename] = (noz or "--", time.time())
    return _meta[filename][0]


def fmt_temp(v):
    return "--" if v is None else f"{v:.0f}°C"


# id -> text for one overlay field; ids must match the list in the Mainsail webcam-menu script
FIELDS = {
    "progress": lambda c: f"Print Progress {c['prog'] * 100:.0f}%",
    "remaining": lambda c: f"Time Remaining {fmt_remaining(c['remaining'])}",
    "elapsed": lambda c: f"Elapsed {fmt_remaining(c['elapsed'])}",
    "eta": lambda c: "Finish " + (time.strftime("%H:%M", time.localtime(time.time() + c["remaining"]))
                                  if c["remaining"] is not None else "--"),
    "nozzle": lambda c: f"Nozzle {c['nozzle']} mm",
    "layer": lambda c: "Layer " + (f"{c['layer'][0]}/{c['layer'][1]}" if c["layer"] else "--"),
    "filename": lambda c: os.path.splitext(c["filename"])[0][:22] or "--",
    "extruder": lambda c: "Extruder " + fmt_temp(c["extruder"]),
    "bed": lambda c: "Bed " + fmt_temp(c["bed"]),
    "chamber": lambda c: "Chamber " + fmt_temp(c["chamber"]),
    "filament": lambda c: f"Filament {c['filament']:.1f} m",
}
DEFAULT_SETTINGS = {"enabled": True, "fields": ["progress", "remaining", "nozzle"]}
_last_good = {"cfg": DEFAULT_SETTINGS}
DEMO_CTX = {"prog": 0.42, "elapsed": 1800, "remaining": 5000, "nozzle": "0.4", "layer": (63, 150),
            "filename": "benchy.gcode", "extruder": 215, "bed": 60, "chamber": 35, "filament": 3.2}


_remembered = {"noz": None, "t": 0.0}
_db_last = {"v": "0.0"}


def db_nozzle(value=None):
    """Read, or with a value write, the nozzle size kept in the Moonraker database ("0.0" if never set)."""
    item = "/server/database/item"
    if value is None:
        try:
            _db_last["v"] = moonraker(item + "?namespace=camera_overlay&key=last_nozzle")["value"] or "0.0"
        except urllib.error.HTTPError as e:
            if e.code == 404:  # never set
                _db_last["v"] = "0.0"
        except Exception:
            pass  # transient error: keep showing the last value read
        return _db_last["v"]
    body = json.dumps({"namespace": "camera_overlay", "key": "last_nozzle", "value": value}).encode()
    req = urllib.request.Request(MOONRAKER + item, data=body, headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=4).close()


def remember_nozzle(noz):
    if noz and noz != "--" and noz != _remembered["noz"]:
        try:
            db_nozzle(noz)
            _remembered["noz"] = noz
        except Exception:
            pass


def idle_nozzle():
    """Nozzle size reported by the last print job; falls back to the remembered value, then 0.0."""
    if time.time() - _remembered["t"] > 30:  # the history lookup reads a file, so do it at most every 30 s
        _remembered["t"] = time.time()
        try:
            jobs = moonraker("/server/history/list?limit=1")["jobs"]
            if jobs:
                noz = (jobs[0].get("metadata") or {}).get("nozzle_diameter")
                remember_nozzle(fmt_nozzle(noz) if noz else nozzle_for(jobs[0]["filename"]))
        except Exception:
            pass
    return db_nozzle()


def load_settings():
    """Overlay on/off and field choice from the Moonraker database (written by the Mainsail webcam menu)."""
    try:
        v = moonraker("/server/database/item?namespace=camera_overlay&key=settings")["value"]
        raw = v.get("fields", []) if isinstance(v.get("fields"), list) else []
        _last_good["cfg"] = {"enabled": v.get("enabled", True) is not False,
                             "fields": [f for f in raw if isinstance(f, str) and f in FIELDS][:4]}
    except urllib.error.HTTPError as e:
        if e.code == 404:  # nothing saved yet
            _last_good["cfg"] = DEFAULT_SETTINGS
    except Exception:
        pass  # Moonraker busy or bad value: keep the last good settings, so an overlay switched off stays off
    return _last_good["cfg"]


def build_text():
    """Overlay text for the current job, or None when nothing is printing or the overlay is switched off."""
    cfg = load_settings()
    if not cfg["enabled"] or not cfg["fields"]:
        return None
    if DEMO:
        ctx = DEMO_CTX
    else:
        try:
            s = moonraker("/printer/objects/query?print_stats&virtual_sdcard&display_status&extruder"
                          "&heater_bed&heater_generic%20chamber_heater")["status"]
            ps = s["print_stats"]
            printing = ps["state"] in ("printing", "paused")
        except urllib.error.HTTPError as e:
            if e.code != 503:
                raise  # a real Moonraker error: poll_status rides it out instead of flipping to the idle text
            printing = False  # 503 = Klipper stopped (printer switched off): same as idle
        if not printing:
            # between jobs: show the nozzle size of the last job, if the nozzle field is selected
            return f"Nozzle {idle_nozzle()} mm" if "nozzle" in cfg["fields"] else None
        prog = min(1.0, s["display_status"]["progress"] or s["virtual_sdcard"]["progress"])
        elapsed = ps["print_duration"]
        remaining = None
        if prog >= 0.01:
            remaining = max(0, elapsed / prog - elapsed)
        else:
            try:
                est = moonraker("/server/files/metadata?filename=" + urllib.parse.quote(ps["filename"]))["estimated_time"]
                remaining = est - elapsed
            except Exception:
                pass
        info = ps.get("info") or {}
        layer = (info["current_layer"], info["total_layer"]) if info.get("current_layer") is not None \
            and info.get("total_layer") else None
        noz = nozzle_for(ps["filename"])
        remember_nozzle(noz)
        ctx = {"prog": prog, "elapsed": elapsed, "remaining": remaining, "layer": layer,
               "filename": ps["filename"], "nozzle": noz,
               "extruder": s.get("extruder", {}).get("temperature"), "bed": s.get("heater_bed", {}).get("temperature"),
               "chamber": s.get("heater_generic chamber_heater", {}).get("temperature"),
               "filament": ps["filament_used"] / 1000}
    return "   ".join(FIELDS[f](ctx) for f in cfg["fields"])


def poll_status():
    fails = 0
    while True:
        try:
            status["text"] = build_text()
            fails = 0
        except Exception as e:
            fails += 1
            if fails == 1:
                print("overlay status failed:", repr(e), file=sys.stderr, flush=True)
            if fails >= 3:  # ride out a one-off Moonraker hiccup without flicker
                status["text"] = None
        time.sleep(2)


_fonts = {}
_logged = []
_upstream_down = []


def overlay(jpeg, text):
    im = Image.open(io.BytesIO(jpeg)).convert("RGB")
    size = max(12, im.width // 40)
    while True:  # shrink until the text fits, so more fields never run off the frame
        font = _fonts.get(size) or _fonts.setdefault(size, ImageFont.truetype(FONT, size))
        if size <= 9 or font.getlength(text) <= im.width - 16:
            break
        size -= 1
    bar = size + 10
    shade = Image.new("RGBA", (im.width, bar), (0, 0, 0, 170))
    im.paste(shade, (0, 0), shade)
    ImageDraw.Draw(im).text((8, 5), text, font=font, fill=(255, 221, 0))
    out = io.BytesIO()
    im.save(out, "JPEG", quality=80)
    return out.getvalue()


def read_parts(resp):
    """Yield the JPEG of each part of ustreamer's multipart stream."""
    while True:
        length = None
        while True:
            line = resp.readline()
            if not line:
                return
            line = line.strip()
            if line.lower().startswith(b"content-length:"):
                length = int(line.split(b":")[1])
            if not line and length is not None:
                break
        yield resp.read(length)


def pump():
    while True:
        conn = http.client.HTTPConnection(*UPSTREAM, timeout=10)
        try:
            conn.request("GET", "/stream")
            for jpeg in read_parts(conn.getresponse()):
                text = status["text"]
                if text:
                    try:
                        jpeg = overlay(jpeg, text)
                    except Exception as e:
                        if not _logged:  # once, so a missing font shows in the journal
                            _logged.append(1)
                            print("overlay failed:", repr(e), file=sys.stderr, flush=True)
                _upstream_down.clear()
                with cond:
                    frame["jpeg"], frame["n"] = jpeg, frame["n"] + 1
                    cond.notify_all()
        except Exception as e:
            if not _upstream_down:
                _upstream_down.append(1)
                print("camera stream unavailable:", repr(e), file=sys.stderr, flush=True)
        finally:
            conn.close()
        time.sleep(2)  # upstream down, restarting, or it closed the stream


class Handler(http.server.BaseHTTPRequestHandler):
    timeout = 10  # a client that stops reading must not hold a thread forever

    def log_message(self, *a):
        pass

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/snapshot":
            with cond:
                jpeg = frame["jpeg"]
            if not jpeg:
                return self.send_error(503)
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(jpeg)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(jpeg)
        elif path == "/stream":
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace;boundary=boundarydonotcross")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            last = 0
            try:
                while True:
                    with cond:
                        cond.wait_for(lambda: frame["n"] != last, timeout=10)
                        jpeg, last = frame["jpeg"], frame["n"]
                    if not jpeg:
                        continue
                    self.wfile.write(b"--boundarydonotcross\r\nContent-Type: image/jpeg\r\nContent-Length: "
                                     + str(len(jpeg)).encode() + b"\r\n\r\n" + jpeg + b"\r\n")
            except OSError:
                pass
        else:
            self.send_error(404)


def self_check():
    assert fmt_remaining(None) == "--" and fmt_remaining(59) == "0m"
    assert fmt_remaining(3600 + 23 * 60) == "1h 23m" and fmt_remaining(5 * 60) == "5m"


if __name__ == "__main__":
    self_check()
    threading.Thread(target=poll_status, daemon=True).start()
    threading.Thread(target=pump, daemon=True).start()
    http.server.ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
