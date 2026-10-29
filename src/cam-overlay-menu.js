/* Print-overlay settings in Mainsail's "Edit Webcam" dialog (local addition, loaded from index.html).
   The camera-overlay service (camera_overlay.py on the host) burns a status bar into a camera
   stream (the one whose stream URL contains STREAM_PATH). This adds a "Print Overlay" section to the Edit Webcam dialog of that webcam:
   an on/off checkbox and four dropdowns choosing what the bar shows. Changes save immediately to the
   Moonraker database (namespace camera_overlay, key settings), which the service re-reads every 2 s.
   English UI only: the dialog is found by its "Edit Webcam" title and the "URL Stream" field. */
(function () {
  var MARK = 'cam-overlay-section';
  var URL = '/server/database/item';
  var STREAM_PATH = '__STREAM_PATH__';  // replaced by install.py: the overlay webcam's stream URL contains this
  var OPTIONS = [
    ['none', '(nothing)'], ['progress', 'Print progress'], ['remaining', 'Time remaining'],
    ['elapsed', 'Elapsed time'], ['eta', 'Finish time'], ['nozzle', 'Nozzle size (also shown between jobs: last job\'s size, 0.0 if never set)'],
    ['layer', 'Layer'], ['filename', 'File name'], ['extruder', 'Extruder temperature'],
    ['bed', 'Bed temperature'], ['chamber', 'Chamber temperature'], ['filament', 'Filament used']
  ];
  var DEFAULTS = { enabled: true, fields: ['progress', 'remaining', 'nozzle', 'none'] };
  var SLOTS = 4;

  function el(tag, css, text) {
    var e = document.createElement(tag);
    if (css) e.style.cssText = css;
    if (text) e.textContent = text;
    return e;
  }

  function api(method, body) {
    var opt = { method: method, headers: { 'Content-Type': 'application/json' } };
    if (body) opt.body = JSON.stringify(body);
    var q = method === 'GET' ? '?namespace=camera_overlay&key=settings' : '';
    return fetch(URL + q, opt).then(function (r) { return r.ok ? r.json() : null; });
  }

  // resolves to the saved settings, the defaults when nothing is saved yet (404), or null when the
  // load failed, so a failed load can never be mistaken for "defaults" and saved over the real settings
  function load() {
    var ctl = window.AbortController ? new AbortController() : null;
    var t = ctl ? setTimeout(function () { ctl.abort(); }, 5000) : null;
    return fetch(URL + '?namespace=camera_overlay&key=settings', ctl ? { signal: ctl.signal } : {})
      .then(function (r) {
        if (r.status === 404) return DEFAULTS;
        if (!r.ok) return null;
        return r.json().then(function (j) {
          var v = j && j.result && j.result.value;
          if (!v) return null;
          var f = (Array.isArray(v.fields) ? v.fields : []).slice(0, SLOTS);
          while (f.length < SLOTS) f.push('none');
          return { enabled: v.enabled !== false, fields: f };
        });
      })
      .catch(function () { return null; })
      .then(function (s) { if (t) clearTimeout(t); return s; });
  }

  function findDialog() {
    var dialogs = document.querySelectorAll('.v-dialog--active');
    for (var i = 0; i < dialogs.length; i++) {
      var t = dialogs[i].querySelector('.v-card__title');
      if (t && /edit webcam/i.test(t.textContent) && dialogs[i].querySelector('.v-card__text')) return dialogs[i];
    }
    return null;
  }

  // the dialog is for the overlay camera when its stream URL contains STREAM_PATH
  function isOverlayCam(dlg) {
    var labels = dlg.querySelectorAll('label');
    for (var i = 0; i < labels.length; i++) {
      if (/^\s*URL Stream\s*$/i.test(labels[i].textContent)) {
        var id = labels[i].getAttribute('for');
        var input = id && document.getElementById(id);
        return !!input && input.value.indexOf(STREAM_PATH) !== -1;
      }
    }
    return false;
  }

  function build(settings) {
    var box = el('div', 'margin-top:16px;padding-top:8px');
    box.className = MARK;
    box.appendChild(el('div', 'font-size:1rem;margin-bottom:8px', 'Print Overlay:'));
    var note = el('div', 'font-size:.8rem;opacity:.65;margin-bottom:8px',
      'Text drawn on this camera while a print is running. Saved automatically. With Nozzle size selected, ' +
      'the bar stays up between jobs showing the last job\'s nozzle size (0.0 until one has been recorded).');
    box.appendChild(note);
    if (!settings) {
      box.appendChild(el('div', 'font-size:.85rem;color:#f44336', 'Could not load the overlay settings. Reopen this dialog to retry.'));
      return box;
    }

    var row =el('label', 'display:flex;align-items:center;gap:8px;cursor:pointer;margin-bottom:8px');
    var cb = el('input'); cb.type = 'checkbox'; cb.checked = settings.enabled;
    row.appendChild(cb); row.appendChild(el('span', '', 'Show overlay'));
    box.appendChild(row);

    var selects = [];
    for (var i = 0; i < SLOTS; i++) {
      var line = el('div', 'display:flex;align-items:center;gap:8px;margin-bottom:6px');
      line.appendChild(el('span', 'min-width:56px;white-space:nowrap;font-size:.85rem;opacity:.8', 'Slot ' + (i + 1)));
      var sel = el('select', 'flex:1;background:#1e1e1e;color:#fff;border:1px solid #555;border-radius:4px;padding:6px;font:inherit');
      OPTIONS.forEach(function (o) {
        var op = el('option', '', o[1]); op.value = o[0]; sel.appendChild(op);
      });
      sel.value = settings.fields[i] || 'none';
      selects.push(sel);
      line.appendChild(sel);
      box.appendChild(line);
    }
    var saved = el('div', 'font-size:.8rem;min-height:1.1em;color:#4caf50');
    box.appendChild(saved);

    var seq = 0;  // only the newest save may update the status text
    var chain = Promise.resolve();  // saves go out one after another, so the last change always wins
    function save() {
      var value = { enabled: cb.checked, fields: selects.map(function (s) { return s.value; }) };
      var mine = ++seq;
      saved.style.color = '#aaa'; saved.textContent = 'Saving...';
      chain = chain.then(function () {
        return api('POST', { namespace: 'camera_overlay', key: 'settings', value: value });
      }).then(function (r) {
        if (mine !== seq) return;
        saved.style.color = r ? '#4caf50' : '#f44336';
        saved.textContent = r ? 'Saved' : 'Could not save';
      }).catch(function () {
        if (mine !== seq) return;
        saved.style.color = '#f44336'; saved.textContent = 'Could not save';
      });
    }
    cb.addEventListener('change', save);
    selects.forEach(function (s) { s.addEventListener('change', save); });
    return box;
  }

  var busy = false;
  function check() {
    if (busy) return;
    var dlg = findDialog();
    if (!dlg || dlg.querySelector('.' + MARK) || !isOverlayCam(dlg)) return;
    busy = true;
    load().then(function (settings) {
      var again = findDialog();  // the dialog may have closed, or been reopened for another webcam, meanwhile
      if (again !== dlg || again.querySelector('.' + MARK)) return;
      var col = again.querySelector('.v-card__text .col-sm-6') || again.querySelector('.v-card__text');
      col.appendChild(build(settings));
    }).then(function () { busy = false; }, function () { busy = false; });
  }

  var timer = null;
  new MutationObserver(function () {
    if (timer) return;
    timer = setTimeout(function () { timer = null; try { check(); } catch (e) { /* never break Mainsail */ } }, 200);
  }).observe(document.documentElement, { childList: true, subtree: true });
})();
