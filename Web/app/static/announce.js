/* 全站公告弹窗 —— 由 nginx 注入到所有 HTML 页面(主站/用户空间/后台) */
(function () {
  if (window.__announceLoaded) return;
  window.__announceLoaded = true;

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function show(d) {
    if (document.getElementById('__announce_ov')) return;
    var ov = document.createElement('div');
    ov.id = '__announce_ov';
    ov.style.cssText =
      'position:fixed;inset:0;background:rgba(0,0,0,.5);z-index:2147483647;' +
      'display:flex;align-items:center;justify-content:center;padding:20px;' +
      'font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif';
    ov.innerHTML =
      '<div style="background:#fff;border-radius:16px;max-width:430px;width:100%;max-height:80vh;' +
      'overflow:auto;box-shadow:0 20px 60px rgba(0,0,0,.3)">' +
      '<div style="padding:20px 22px 4px;display:flex;align-items:center;gap:8px">' +
      '<span style="font-size:20px">📢</span>' +
      '<span style="font-size:17px;font-weight:700;color:#e8590c">' + esc(d.title || '公告') + '</span></div>' +
      '<div style="padding:10px 22px 16px;font-size:14px;color:#3d2b1f;line-height:1.75;white-space:pre-wrap;word-break:break-word">' +
      esc(d.content || '') + '</div>' +
      '<div style="padding:0 22px 20px;text-align:right">' +
      '<button id="__announce_ok" style="background:#e8590c;color:#fff;border:none;border-radius:10px;' +
      'padding:10px 20px;font-size:14px;font-weight:600;cursor:pointer">我知道了</button></div>' +
      '</div>';
    (document.body || document.documentElement).appendChild(ov);
    var btn = document.getElementById('__announce_ok');
    if (btn) btn.onclick = function () {
      try { localStorage.setItem('__announce_seen', String(d.id || '')); } catch (e) {}
      ov.remove();
    };
  }

  function run() {
    try {
      fetch('/__announce__', { cache: 'no-store' })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (d) {
          if (!d || !d.enabled || !d.content) return;
          var seen = null;
          try { seen = localStorage.getItem('__announce_seen'); } catch (e) {}
          if (seen === String(d.id)) return;
          if (document.body) show(d);
          else document.addEventListener('DOMContentLoaded', function () { show(d); });
        })
        .catch(function () {});
    } catch (e) {}
  }

  if (document.readyState === 'loading')
    document.addEventListener('DOMContentLoaded', run);
  else
    run();
})();
