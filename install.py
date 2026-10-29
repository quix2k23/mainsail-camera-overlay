#!/usr/bin/env python3
"""Install (or remove) the Mainsail camera overlay on a Mainsail host. Run it ON the host, as root:

    sudo python3 install.py --webcam nozzle        # install, and point the webcam "nozzle" at the overlay
    sudo python3 install.py --remove               # uninstall

What it does: installs Pillow, copies camera_overlay.py + a systemd unit, adds the "Print Overlay" section to
Mainsail's Edit Webcam dialog (one <script> line in index.html, sw.js revision updated), and optionally
points a Moonraker webcam at the overlay stream. Safe to re-run. Backups: *.pre-camera-overlay (first run only).
"""
import argparse
import glob
import hashlib
import json
import os
import pwd
import re
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
TAG = re.compile(r'<script src="/cam-overlay-menu-[0-9a-f]+\.js" defer></script>')
SW_REV = re.compile(r'(url:"index\.html",revision:")[0-9a-f]+(")')
UNIT = "/etc/systemd/system/camera-overlay.service"
MOONRAKER = "http://127.0.0.1:7125"


def md5(data):
    return hashlib.md5(data).hexdigest()


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def write(path, text, uid=None, gid=None):
    """Atomic: a power cut mid-write must never leave a truncated index.html behind."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    if uid is not None:
        os.chown(tmp, uid, gid)
    os.replace(tmp, path)


def backup_once(path):
    if os.path.exists(path) and not os.path.exists(path + ".pre-camera-overlay"):
        with open(path, "rb") as src, open(path + ".pre-camera-overlay", "wb") as dst:
            dst.write(src.read())


def patch_mainsail(mdir, tag, uid, gid):
    """Replace/remove the overlay <script> tag in index.html and keep sw.js's precache revision in step."""
    index, sw = os.path.join(mdir, "index.html"), os.path.join(mdir, "sw.js")
    html = TAG.sub("", read(index))
    if tag:
        if "</head>" not in html:
            sys.exit("index.html has no </head>; is this a Mainsail folder?")
        html = html.replace("</head>", tag + "</head>", 1)
    backup_once(index)
    write(index, html, uid, gid)
    if os.path.exists(sw):
        js = read(sw)
        if SW_REV.search(js):
            backup_once(sw)
            write(sw, SW_REV.sub(lambda m: m.group(1) + md5(html.encode()) + m.group(2), js), uid, gid)
        else:
            print("warning: sw.js has no index.html revision entry; hard-refresh Mainsail if the menu does not appear")


def moonraker(path, body=None):
    req = urllib.request.Request(MOONRAKER + path, data=json.dumps(body).encode() if body else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=8) as r:
        return json.load(r)["result"]


def point_webcam(name, stream_path):
    cams = {c["name"]: c for c in moonraker("/server/webcams/list")["webcams"]}
    item = {"name": name, "stream_url": stream_path + "stream", "snapshot_url": stream_path + "snapshot"}
    if name not in cams:
        item.update(service="mjpegstreamer", location="printer", target_fps=15)
        print(f"webcam '{name}' did not exist: created it")
    moonraker("/server/webcams/item", item)
    print(f"webcam '{name}' now uses {stream_path}stream")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--user", default="pi", help="user that runs Klipper/Moonraker (default pi)")
    ap.add_argument("--mainsail-dir", help="Mainsail web folder (default ~USER/mainsail)")
    ap.add_argument("--stream-path", default="/webcam3/", help="URL path nginx serves the overlay on (default /webcam3/)")
    ap.add_argument("--port", default="8082", help="local port the overlay listens on; nginx must proxy --stream-path to it")
    ap.add_argument("--upstream-port", default="8080", help="local port of the camera's own MJPEG stream (default 8080)")
    ap.add_argument("--webcam", help="name of the Moonraker webcam to point at the overlay")
    ap.add_argument("--no-apt", action="store_true", help="do not install packages")
    ap.add_argument("--no-service", action="store_true", help="only patch Mainsail (skip apt, systemd, webcam)")
    ap.add_argument("--remove", action="store_true", help="uninstall")
    a = ap.parse_args()
    if not re.fullmatch(r"/[A-Za-z0-9._~/-]*/", a.stream_path):
        sys.exit("--stream-path must look like /webcam3/ (letters, digits, . _ ~ - and slashes only)")
    pw = pwd.getpwnam(a.user)
    mdir = a.mainsail_dir or os.path.join(pw.pw_dir, "mainsail")
    if not os.path.exists(os.path.join(mdir, "index.html")):
        sys.exit(f"no index.html in {mdir}: pass --mainsail-dir")
    script = os.path.join(pw.pw_dir, "camera_overlay.py")

    if a.remove:
        if not a.no_service and os.path.exists(UNIT):
            subprocess.run(["systemctl", "disable", "--now", "camera-overlay"], check=False)
            os.remove(UNIT)
            subprocess.run(["systemctl", "daemon-reload"], check=False)
        for old in glob.glob(os.path.join(mdir, "cam-overlay-menu-*.js")):
            os.remove(old)
        patch_mainsail(mdir, "", pw.pw_uid, pw.pw_gid)
        if not a.no_service and os.path.exists(script):  # with --no-service the unit stays, so keep its script
            os.remove(script)
        print("removed. Point the webcam back at its own stream (Settings > Webcams > Edit).")
        print("The saved settings stay in the Moonraker database (namespace camera_overlay).")
        return

    if not a.no_service and not a.no_apt:
        try:
            import PIL  # noqa: F401
        except ImportError:
            subprocess.run(["apt-get", "install", "-y", "python3-pil", "fonts-dejavu-core"], check=True)
    if not a.no_service:
        try:
            import PIL  # noqa: F401
        except ImportError:
            sys.exit("Pillow is missing: run without --no-apt, or install python3-pil yourself")
    if not os.path.exists("/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"):
        print("warning: DejaVu Sans Mono Bold font not found; install fonts-dejavu-core")

    js = read(os.path.join(HERE, "src", "cam-overlay-menu.js")).replace("__STREAM_PATH__", a.stream_path)
    name = f"cam-overlay-menu-{md5(js.encode())[:8]}.js"
    write(os.path.join(mdir, name), js, pw.pw_uid, pw.pw_gid)
    patch_mainsail(mdir, f'<script src="/{name}" defer></script>', pw.pw_uid, pw.pw_gid)
    for old in glob.glob(os.path.join(mdir, "cam-overlay-menu-*.js")):  # only once index.html points at the new one
        if os.path.basename(old) != name:
            os.remove(old)
    print(f"Mainsail patched: {name}")
    if a.no_service:
        return

    write(script, read(os.path.join(HERE, "src", "camera_overlay.py")), pw.pw_uid, pw.pw_gid)
    unit = read(os.path.join(HERE, "src", "camera-overlay.service"))
    for k, v in (("@USER@", a.user), ("@HOME@", pw.pw_dir), ("@PORT@", a.port), ("@UPSTREAM_PORT@", a.upstream_port)):
        unit = unit.replace(k, v)
    write(UNIT, unit)
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "enable", "camera-overlay"], check=True)
    subprocess.run(["systemctl", "restart", "camera-overlay"], check=True)
    print("service camera-overlay started")
    err = None
    for _ in range(10):  # the service needs a moment to receive its first frame
        try:
            urllib.request.urlopen(f"http://127.0.0.1{a.stream_path}snapshot", timeout=5).close()
            print(f"OK: {a.stream_path}snapshot answers through nginx")
            err = None
            break
        except Exception as e:
            err = e
            time.sleep(1.5)
    if err:
        print(f"warning: http://127.0.0.1{a.stream_path}snapshot failed ({err}).")
        print(f"If the camera is running, nginx needs: location {a.stream_path} {{ postpone_output 0; "
              f"proxy_buffering off; proxy_pass http://127.0.0.1:{a.port}/; }}")
        if a.webcam:
            print(f"The webcam '{a.webcam}' was NOT changed. Fix the route, then run this again.")
    elif a.webcam:
        point_webcam(a.webcam, a.stream_path)  # only once the overlay is known to work
    print("Done. Hard-refresh Mainsail (Ctrl+Shift+R), then Settings > Webcams > Edit.")


if __name__ == "__main__":
    main()
