# Mainsail Camera Overlay

Draws live print information **onto the video** of a webcam in [Mainsail](https://docs.mainsail.xyz/), the Klipper web interface, and adds a **Print Overlay** section to Mainsail's *Edit Webcam* dialog to switch it on or off and choose what it shows.

```
┌ nozzle ───────────────────────────────────────────┐
│ Print Progress 42%   Time Remaining 1h 23m   Nozzle 0.4 mm │   <- drawn into the video
│                                                    │
│                  (camera picture)                  │
└────────────────────────────────────────────────────┘
```

* While a print runs, the bar shows the fields you picked: progress, time remaining, elapsed time, finish time, nozzle size, layer, file name, extruder / bed / chamber temperature, filament used. Four slots, each a dropdown.
* **Between jobs** (and while Klipper is stopped) it keeps showing the nozzle size of the *last print job*. Until a job (or you) has recorded one, it shows `0.0 mm`.
* The text is burned into the stream, so it appears everywhere that stream is shown: Mainsail's dashboard, the Webcam page, phones, snapshots.
* Settings are saved in Moonraker's database and applied within about 2 seconds, with no restart.
* Anything else that reads the camera's own stream (for example a print-failure detector or a timelapse) is not affected: it keeps using the original stream.

## How it works

```
camera ──► MJPEG streamer (e.g. ustreamer / crowsnest, :8080) ──► camera-overlay service (:8082) ──► nginx /webcam3/ ──► Mainsail
                                                                         ▲
                                                  Moonraker (print status, file metadata, settings)
```

`camera_overlay.py` reads the camera's stream, and while a job is printing re-encodes each frame with a text bar (Pillow). While it draws it needs roughly 12% of one CPU core at 640x480 and 17 frames/s (measured on an old Core 2 Quad). With the overlay switched off, or every slot set to *(nothing)*, frames pass straight through and it costs almost nothing. Note the default settings include *Nozzle size*, which keeps the bar up between jobs. One small script (`cam-overlay-menu.js`) is added to Mainsail's `index.html` to add the settings section to the webcam dialog.

## Requirements

* A Mainsail host running Moonraker and nginx, reachable over ssh (MainsailOS or similar, Debian-based). Tested on Mainsail 2.19 with Debian 13.
* A webcam already streaming MJPEG on the host, for example with [crowsnest](https://github.com/mainsail-crew/crowsnest) or [ustreamer](https://github.com/pikvm/ustreamer), reachable at `http://127.0.0.1:8080/stream` (change with `--upstream-port`).
* nginx serving **`/webcam3/`** to `127.0.0.1:8082`. Crowsnest's nginx config on MainsailOS proxies `/webcam/` to `/webcam4/` to ports 8080 to 8083. Check yours with `grep -rn webcam3 /etc/nginx/`; if it is missing, see [Troubleshooting](#troubleshooting).
* Mainsail's web interface in English (the menu script finds the dialog by its English labels).

## Install (over ssh)

1. Log in to the machine that runs Mainsail. Use your own login and address:

   ```bash
   ssh pi@your-printer.local
   ```

2. Download this repository and enter it (needs `git`: `sudo apt install git`):

   ```bash
   git clone https://github.com/quix2k23/mainsail-camera-overlay.git
   cd mainsail-camera-overlay
   ```

3. **First note the webcam's current stream and snapshot URLs** (*Settings > Webcams > Edit*), in case you want to switch back: the installer replaces them. Then run the installer as root. `--webcam` is the name of the Moonraker webcam (Mainsail *Settings > Webcams*) that should show the overlay:

   ```bash
   sudo python3 install.py --webcam nozzle
   ```

   If Pillow is not installed yet it installs `python3-pil` and `fonts-dejavu-core`. It copies the service, starts it, adds the settings section to Mainsail, and, once `/webcam3/snapshot` answers through nginx, points that webcam at `/webcam3/stream`. If that check fails the webcam is left unchanged.

4. In your browser, hard-refresh Mainsail (**Ctrl+Shift+R**). Then open *Settings (gear icon) > Webcams > Edit* on that webcam: the **Print Overlay** section is at the bottom of the left column.

Useful options (`python3 install.py --help` lists them all):

| Option | Meaning |
|---|---|
| `--user NAME` | The user that runs Klipper and Moonraker (default `pi`). |
| `--upstream-port 8080` | Port of the camera's own MJPEG stream. |
| `--port 8082` | Port the overlay listens on. nginx must proxy `--stream-path` to it. |
| `--stream-path /webcam3/` | URL path the overlay is served on. |
| `--mainsail-dir DIR` | Mainsail's web folder, if it is not `~USER/mainsail`. |
| `--no-apt` | Skip package installation (Pillow and a DejaVu font must already be present). |

The installer is safe to run again, and it keeps one-time backups of the two Mainsail files it edits (`index.html.pre-camera-overlay`, `sw.js.pre-camera-overlay`) for reference. They go stale after a Mainsail update, so `--remove` edits the current files instead of restoring them.

## Check that it works

```bash
systemctl status camera-overlay                      # should say active (running)
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1/webcam3/snapshot    # 200
journalctl -u camera-overlay -n 20                   # errors, if any
```

To see the bar without printing, run a demo copy on a spare port and fetch a frame (stop it with Ctrl+C):

```bash
OVERLAY_PORT=8092 python3 ~/camera_overlay.py --demo &
curl -s http://127.0.0.1:8092/snapshot -o /tmp/demo.jpg     # open /tmp/demo.jpg
kill %1
```

## Using the menu

*Settings > Webcams > Edit* on the overlay webcam:

* **Show overlay**: master switch. Off passes the camera through untouched.
* **Slot 1 to 4**: what each of the four positions shows (or *nothing*). The text shrinks to a minimum size if it would not fit; with long values, use fewer fields.
* Changes are **saved immediately** (you do not need to press *Update Webcam*) and take effect within about 2 seconds.
* **Nozzle size** is also shown between jobs. It comes from the last print job: the nozzle diameter in Moonraker's file metadata, or the slicer's G-code header (Cura, PrusaSlicer/Orca). If no job has recorded one yet it shows `0.0`. To set it yourself, for example after fitting a new nozzle:

  ```bash
  curl -X POST http://127.0.0.1:7125/server/database/item -H 'Content-Type: application/json' \
       -d '{"namespace":"camera_overlay","key":"last_nozzle","value":"0.6"}'
  ```

  The next print job replaces it with that file's nozzle size. So does a restart of the service, if the print history already holds a job.

## Troubleshooting

* **No "Print Overlay" section in the dialog.** Hard-refresh (Ctrl+Shift+R). The section only appears for the webcam whose *URL Stream* contains `/webcam3/` (or your `--stream-path`), and only with Mainsail in English. Mainsail updates overwrite `index.html`: run `sudo python3 install.py` again.
* **Webcam shows an error or stays on "Connecting".** Check `systemctl status camera-overlay` and `curl http://127.0.0.1:8080/snapshot` (the camera's own stream). If the first works but `http://127.0.0.1/webcam3/snapshot` gives *502*, nginx has no route to the overlay. Add inside your Mainsail `server { }` block (the file is usually `/etc/nginx/sites-available/mainsail`), then `sudo nginx -t && sudo systemctl reload nginx`:

  ```nginx
  location /webcam3/ {
      postpone_output 0;
      proxy_buffering off;
      proxy_pass http://127.0.0.1:8082/;
  }
  ```
* **No text appears during a print.** Is *Show overlay* ticked, and is at least one slot not *(nothing)*? Check `journalctl -u camera-overlay`. A line `overlay failed:` usually means the DejaVu font is missing (`sudo apt install fonts-dejavu-core`).
* **Nozzle shows `--` during a print.** The file's metadata has no nozzle diameter and the G-code header could not be read. Re-upload the file, or check that your slicer writes the nozzle size.
* **Time remaining looks off early in a print.** It is extrapolated from progress, so it settles after the first few percent.
* **Going back to the original camera stream.** Settings > Webcams > Edit, and put the old URLs back.

## Uninstall

```bash
cd ~/mainsail-camera-overlay
sudo python3 install.py --remove
```

This stops and removes the service, deletes the script, and removes the line from `index.html` (correcting the `sw.js` entry). It does **not**:

* change your webcam's URLs: set them back in *Settings > Webcams > Edit*;
* remove an nginx `location /webcam3/` block, if you had to add one yourself;
* remove the saved settings in Moonraker's database (namespace `camera_overlay`), the packages it installed, or the cloned `mainsail-camera-overlay` folder.

## Limits

* Checked on one setup (Debian 13, Mainsail 2.19, a USB camera behind ustreamer). Other versions of Mainsail may change the webcam dialog's layout, which could hide the menu section; the overlay itself keeps working with the last saved settings.
* English interface only. One overlay camera.
* The settings menu is an extra script, not part of Mainsail itself, so Mainsail updates remove it until the installer is run again.

## Licence

MIT, see [LICENSE](LICENSE).
