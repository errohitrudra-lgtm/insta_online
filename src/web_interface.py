"""
FastAPI web dashboard with upload controls.

Routes
------
GET  /             – HTML dashboard
GET  /api/status   – JSON status
POST /api/upload/{job_id}  – upload a single job
POST /api/upload-all       – upload all pending
POST /api/toggle-auto      – toggle auto-upload
"""

from __future__ import annotations

import html
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

if TYPE_CHECKING:
    from .main import Application
    from .monitor import ReelMonitor
    from .queue_manager import MemoryQueue
    from .reels_db import ReelsDatabase
    from .state_manager import StateManager

app = FastAPI(title="Instagram Reel Monitor", docs_url=None, redoc_url=None)

_monitor: Optional["ReelMonitor"] = None
_queue: Optional["MemoryQueue"] = None
_state: Optional["StateManager"] = None
_application: Optional["Application"] = None


def configure(
    monitor: "ReelMonitor",
    queue: "MemoryQueue",
    state: "StateManager",
    application: "Application",
) -> None:
    global _monitor, _queue, _state, _application
    _monitor = monitor
    _queue = queue
    _state = state
    _application = application


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _ago(ts: float) -> str:
    """Return a human-readable 'ago' string."""
    if ts <= 0:
        return "never"
    delta = int(time.time() - ts)
    if delta < 60:
        return f"{delta}s ago"
    if delta < 3600:
        return f"{delta // 60}m ago"
    if delta < 86400:
        return f"{delta // 3600}h ago"
    return f"{delta // 86400}d ago"


def _badge(status: str) -> str:
    """Status badge with colours."""
    colours = {
        "pending": "#f59e0b",
        "downloading": "#3b82f6",
        "downloaded": "#10b981",
        "uploading": "#6366f1",
        "uploaded": "#22c55e",
        "upload_queued": "#8b5cf6",
        "failed": "#ef4444",
    }
    bg = colours.get(status, "#6b7280")
    return f'<span class="badge" style="background:{bg}">{status}</span>'


# ------------------------------------------------------------------
# API endpoints
# ------------------------------------------------------------------

@app.get("/api/status")
async def api_status():
    if not _monitor or not _queue:
        return JSONResponse({"error": "Not initialised"}, 503)

    jobs = list(_queue.jobs.values())
    return {
        "status": "ok",
        "time": datetime.now().isoformat(),
        "monitoring": _monitor.is_running,
        "targets": [t.username for t in _monitor.config.targets],
        "poll_interval": _monitor.config.poll_interval_seconds,
        "auto_upload": _application.config.upload.auto_upload if _application else False,
        "upload_enabled": _application.config.upload.enabled if _application else False,
        "ai_enabled": _application.config.ai.enabled if _application else False,
        "stats": {
            "total": len(jobs),
            "pending": sum(1 for j in jobs if j.status.value == "pending"),
            "downloading": sum(1 for j in jobs if j.status.value == "downloading"),
            "downloaded": sum(1 for j in jobs if j.status.value == "downloaded"),
            "uploading": sum(1 for j in jobs if j.status.value == "uploading"),
            "uploaded": sum(1 for j in jobs if j.status.value == "uploaded"),
            "failed": sum(1 for j in jobs if j.status.value == "failed"),
        },
        "jobs": [
            {
                "id": j.id,
                "target": j.target_username,
                "shortcode": j.reel_shortcode,
                "views": j.view_count,
                "status": j.status.value,
                "local_path": j.local_path,
                "attempts": j.attempts,
                "error": j.error,
                "created": j.created_at,
                "updated": j.updated_at,
            }
            for j in sorted(jobs, key=lambda j: j.created_at, reverse=True)
        ],
    }


@app.post("/api/upload/{job_id}")
async def api_upload_single(job_id: str):
    if not _application:
        return JSONResponse({"error": "Not initialised"}, 503)
    result = await _application.upload_single_job(job_id)
    return {"result": result}


@app.post("/api/upload-all")
async def api_upload_all():
    if not _application:
        return JSONResponse({"error": "Not initialised"}, 503)
    count = await _application.upload_all_pending(max_count=0)
    return {"uploaded": count}


@app.post("/api/toggle-auto")
async def api_toggle_auto():
    if not _application:
        return JSONResponse({"error": "Not initialised"}, 503)
    _application.config.upload.auto_upload = not _application.config.upload.auto_upload
    return {"auto_upload": _application.config.upload.auto_upload}


@app.get("/api/reels-db")
async def api_reels_db():
    """Return all tracked reels from the persistent database."""
    if not _application:
        return JSONResponse({"error": "Not initialised"}, 503)
    import dataclasses
    reels = _application.reels_db.get_all()
    return {
        "total": len(reels),
        "downloaded": sum(1 for r in reels if r.downloaded),
        "uploaded": sum(1 for r in reels if r.uploaded),
        "reels": [dataclasses.asdict(r) for r in reels],
    }


@app.post("/api/upload-reel/{shortcode}")
async def api_upload_reel(shortcode: str):
    """Upload a single reel by shortcode from the reels DB."""
    if not _application:
        return JSONResponse({"error": "Not initialised"}, 503)
    result = await _application.upload_reel_by_shortcode(shortcode)
    return {"result": result}


@app.post("/api/upload-batch")
async def api_upload_batch(request: Request):
    """Upload multiple selected reels. First uploads immediately, then 20+ min gaps."""
    if not _application:
        return JSONResponse({"error": "Not initialised"}, 503)
    body = await request.json()
    shortcodes = body.get("shortcodes", [])
    if not shortcodes:
        return JSONResponse({"error": "No reels selected", "queued": 0})
    import asyncio
    asyncio.create_task(_application.upload_selected_reels(shortcodes))
    return {"queued": len(shortcodes), "message": f"Uploading {len(shortcodes)} reels – first one starting now!"}


@app.get("/api/batch-status")
async def api_batch_status():
    """Return current batch upload progress."""
    if not _application:
        return JSONResponse({"error": "Not initialised"}, 503)
    return _application.get_batch_status()


@app.get("/api/stats")
async def api_stats():
    """Return daily stats and system health."""
    if not _application:
        return JSONResponse({"error": "Not initialised"}, 503)
    stats = _application.get_stats()
    db = _application.reels_db
    stats["db_total"] = db.total
    stats["db_downloaded"] = db.downloaded_count
    stats["db_uploaded"] = db.uploaded_count
    stats["db_pending"] = len(db.get_downloaded_not_uploaded())
    stats["retry_queue"] = len(_application._retry_counts)
    return stats


@app.post("/api/refresh")
async def api_refresh():
    """Trigger immediate poll refresh."""
    if not _application:
        return JSONResponse({"error": "Not initialised"}, 503)
    _application.monitor.force_refresh.set()
    return {"result": "Refresh triggered"}


# ------------------------------------------------------------------
# HTML Dashboard
# ------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    if not _monitor or not _queue or not _application:
        return HTMLResponse("<h1>Starting …</h1>", 503)

    try:
        return _build_dashboard()
    except Exception as exc:
        import traceback
        tb = html.escape(traceback.format_exc())
        return HTMLResponse(
            f"<h1>Dashboard Error</h1><pre style='color:red'>{tb}</pre>", 500
        )


def _build_dashboard() -> HTMLResponse:
    cfg = _application.config
    jobs = sorted(_queue.jobs.values(), key=lambda j: j.created_at, reverse=True)

    total = len(jobs)
    downloaded = sum(1 for j in jobs if j.status.value == "downloaded")
    uploaded = sum(1 for j in jobs if j.status.value == "uploaded")
    failed = sum(1 for j in jobs if j.status.value == "failed")
    pending = sum(1 for j in jobs if j.status.value in ("pending", "downloading"))

    # Build job rows
    job_rows = ""
    for j in jobs[:100]:
        esc = html.escape
        fname = Path(j.local_path).name if j.local_path else "—"
        cap = (esc(j.caption[:60] + "…") if j.caption and len(j.caption) > 60
               else esc(j.caption or "—"))
        err = esc(j.error[:80]) if j.error else ""

        upload_btn = ""
        if j.status.value == "downloaded" and cfg.upload.enabled:
            upload_btn = f"""<button class="btn btn-sm"
                onclick="doUpload('{j.id}')">Upload</button>"""

        job_rows += f"""
        <tr>
            <td>@{esc(j.target_username)}</td>
            <td><a href="{esc(j.permalink or '#')}" target="_blank">{esc(j.reel_shortcode)}</a></td>
            <td>{j.view_count:,}</td>
            <td>{_badge(j.status.value)}</td>
            <td title="{esc(fname)}">{esc(fname[:30])}</td>
            <td>{cap}</td>
            <td>{_ago(j.updated_at)}</td>
            <td>{err} {upload_btn}</td>
        </tr>"""

    # Build reels DB rows
    db_reels = _application.reels_db.get_all()
    db_total = len(db_reels)
    db_dl = sum(1 for r in db_reels if r.downloaded)
    db_up = sum(1 for r in db_reels if r.uploaded)
    reel_rows = ""
    for r in db_reels:
        esc = html.escape
        link = esc(r.permalink or f'https://www.instagram.com/reel/{r.shortcode}/')
        dl_icon = '✅' if r.downloaded else '⏳'
        up_icon = '✅' if r.uploaded else ('❌' if r.error else '⏳')
        raw_cap = str(r.caption or '')
        cap_preview = esc(raw_cap[:50] + '…') if len(raw_cap) > 50 else esc(raw_cap or '—')
        tags_list = r.hashtags if isinstance(r.hashtags, list) else []
        tags = esc(' '.join(tags_list[:5])) if tags_list else '—'
        fname = Path(r.local_path).name if r.local_path else '—'
        err_txt = esc(str(r.error or '')[:60])
        disc_time = _ago(r.discovered_at) if isinstance(r.discovered_at, (int, float)) and r.discovered_at > 0 else '—'

        # Upload button + checkbox for downloaded but not uploaded reels
        actions_html = ''
        if r.downloaded and not r.uploaded and r.local_path:
            actions_html = (
                f'<input type="checkbox" class="reel-cb" value="{esc(r.shortcode)}" '
                f'style="margin-right:0.4rem;accent-color:#6366f1">'
                f'<button class="btn btn-sm" onclick="uploadReel(\'{esc(r.shortcode)}\')">Upload</button>'
            )
        elif r.uploaded:
            actions_html = '<span style="color:#22c55e;font-size:0.75rem">✅ Done</span>'

        reel_rows += f"""
        <tr>
            <td><a href="{link}" target="_blank">{esc(r.shortcode)}</a></td>
            <td>@{esc(r.username)}</td>
            <td title="{esc(raw_cap)}">{cap_preview}</td>
            <td>{tags}</td>
            <td style="text-align:center">{dl_icon}</td>
            <td style="text-align:center">{up_icon}</td>
            <td title="{esc(fname)}">{esc(fname[:25])}</td>
            <td style="font-size:0.75rem;color:#64748b">{disc_time}</td>
            <td>{actions_html}</td>
        </tr>"""

    auto_state = "ON" if cfg.upload.auto_upload else "OFF"
    auto_colour = "#22c55e" if cfg.upload.auto_upload else "#ef4444"
    upload_flag = "enabled" if cfg.upload.enabled else "disabled"
    ai_flag = "enabled" if cfg.ai.enabled else "disabled"

    schedule_info = ""
    if cfg.upload.schedule_enabled and cfg.upload.schedule_times:
        schedule_info = f" | Schedule: {', '.join(cfg.upload.schedule_times)}"

    # Gather daily stats
    app_stats = _application.get_stats()
    retry_flag = "on" if cfg.upload.retry_failed else "off"
    catchup_flag = "on" if cfg.upload.catchup_enabled else "off"
    db_pending = len(_application.reels_db.get_downloaded_not_uploaded())

    page = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Instagram Reel Monitor</title>
<style>
  :root {{ --bg: #0f172a; --card: #1e293b; --txt: #e2e8f0; --accent: #6366f1; }}
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ background: var(--bg); color: var(--txt); font-family: 'Segoe UI', system-ui, sans-serif; padding: 1.5rem; }}
  h1 {{ color: var(--accent); margin-bottom: 1rem; font-size: 1.6rem; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 1rem; margin-bottom: 1.5rem; }}
  .card {{ background: var(--card); border-radius: 0.75rem; padding: 1rem; text-align: center; }}
  .card .num {{ font-size: 2rem; font-weight: 700; }}
  .card .lbl {{ font-size: 0.75rem; text-transform: uppercase; color: #94a3b8; margin-top: 0.3rem; }}
  .controls {{ display: flex; gap: 0.75rem; flex-wrap: wrap; margin-bottom: 1.5rem; align-items: center; }}
  .btn {{ background: var(--accent); color: #fff; border: none; padding: 0.5rem 1rem; border-radius: 0.5rem;
          cursor: pointer; font-size: 0.85rem; transition: opacity .2s; }}
  .btn:hover {{ opacity: 0.85; }}
  .btn:disabled {{ opacity: 0.4; cursor: not-allowed; }}
  .btn-sm {{ padding: 0.3rem 0.6rem; font-size: 0.75rem; }}
  .btn-danger {{ background: #ef4444; }}
  .indicator {{ display: inline-block; padding: 0.3rem 0.8rem; border-radius: 1rem; font-size: 0.75rem; font-weight: 600; }}
  table {{ width: 100%; border-collapse: collapse; background: var(--card); border-radius: 0.75rem; overflow: hidden; }}
  th {{ background: #334155; text-align: left; padding: 0.6rem 0.8rem; font-size: 0.75rem; text-transform: uppercase; color: #94a3b8; }}
  td {{ padding: 0.6rem 0.8rem; border-top: 1px solid #334155; font-size: 0.85rem; }}
  a {{ color: #818cf8; text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  .badge {{ display: inline-block; padding: 0.15rem 0.5rem; border-radius: 0.5rem; font-size: 0.7rem; font-weight: 600; color: #fff; }}
  .meta {{ font-size: 0.8rem; color: #64748b; margin-bottom: 1rem; }}
  #toast {{ position: fixed; top: 1rem; right: 1rem; padding: 0.75rem 1.25rem; background: var(--accent); color: #fff;
            border-radius: 0.5rem; display: none; font-size: 0.85rem; z-index: 999; }}
</style>
</head>
<body>
  <h1>📷 Instagram Reel Monitor</h1>
  <p class="meta">
    Targets: {', '.join('@' + t.username for t in cfg.targets)} |
    Upload: {upload_flag} | AI: {ai_flag} | Retry: {retry_flag} | Catchup: {catchup_flag}{schedule_info}
  </p>

  <div class="grid">
    <div class="card"><div class="num">{db_total}</div><div class="lbl">Discovered</div></div>
    <div class="card"><div class="num" style="color:#10b981">{db_dl}</div><div class="lbl">Downloaded</div></div>
    <div class="card"><div class="num" style="color:#22c55e">{db_up}</div><div class="lbl">Uploaded</div></div>
    <div class="card"><div class="num" style="color:#f59e0b">{db_pending}</div><div class="lbl">Pending Upload</div></div>
    <div class="card"><div class="num" style="color:#ef4444">{failed}</div><div class="lbl">Failed</div></div>
    <div class="card"><div class="num" style="color:#3b82f6">{app_stats.get('downloads_today', 0)}</div><div class="lbl">DL Today</div></div>
    <div class="card"><div class="num" style="color:#22c55e">{app_stats.get('uploads_today', 0)}</div><div class="lbl">UL Today</div></div>
    <div class="card"><div class="num" style="color:#8b5cf6">{app_stats.get('retries_today', 0)}</div><div class="lbl">Retries Today</div></div>
  </div>

  <div class="controls">
    <button class="btn" onclick="uploadAll()" {'disabled' if not cfg.upload.enabled else ''}>⬆ Upload All Pending</button>
    <button class="btn" onclick="toggleAuto()" id="autoBtn"
      style="background:{auto_colour}">Auto-Upload: {auto_state}</button>
    <span style="font-size:0.8rem;color:#94a3b8">Monitoring {"✅ active" if _monitor.is_running else "⚠ stopped"}</span>
  </div>

  <div id="toast"></div>

  <div id="batchBanner" style="display:none;background:#1e293b;border:1px solid #6366f1;border-radius:0.75rem;padding:1rem;margin-bottom:1rem">
    <div style="display:flex;justify-content:space-between;align-items:center">
      <span style="font-weight:600;color:#818cf8" id="batchTitle">📤 Batch Upload</span>
      <span id="batchMsg" style="font-size:0.85rem;color:#94a3b8"></span>
    </div>
    <div style="margin-top:0.5rem;background:#334155;border-radius:0.5rem;height:8px;overflow:hidden">
      <div id="batchBar" style="height:100%;background:#6366f1;width:0%;transition:width 0.5s"></div>
    </div>
    <div style="margin-top:0.4rem;font-size:0.75rem;color:#94a3b8" id="batchDetail"></div>
  </div>

  <h2 style="margin-bottom:0.5rem;font-size:1.2rem;color:#818cf8">📋 Reels Database ({db_total} discovered | {db_dl} downloaded | {db_up} uploaded)</h2>
  <div style="margin-bottom:0.75rem;display:flex;gap:0.5rem;align-items:center">
    <label style="font-size:0.8rem;color:#94a3b8;cursor:pointer">
      <input type="checkbox" id="selectAll" onchange="toggleSelectAll()" style="accent-color:#6366f1"> Select All
    </label>
    <button class="btn btn-sm" onclick="uploadSelected()" style="background:#22c55e">⬆ Upload Selected (20+ min gaps)</button>
    <span id="selectedCount" style="font-size:0.75rem;color:#94a3b8"></span>
  </div>
  <table style="margin-bottom:2rem">
    <thead>
      <tr>
        <th>Shortcode</th>
        <th>Account</th>
        <th>Caption</th>
        <th>Hashtags</th>
        <th>DL</th>
        <th>UL</th>
        <th>File</th>
        <th>Discovered</th>
        <th>Actions</th>
      </tr>
    </thead>
    <tbody>
      {reel_rows if reel_rows else '<tr><td colspan="9" style="text-align:center;color:#64748b;padding:2rem">No reels tracked yet …</td></tr>'}
    </tbody>
  </table>

  <h2 style="margin-bottom:0.5rem;font-size:1.2rem;color:#818cf8">⚙ Job Queue</h2>
  <table>
    <thead>
      <tr>
        <th>Account</th>
        <th>Reel</th>
        <th>Views</th>
        <th>Status</th>
        <th>File</th>
        <th>Caption</th>
        <th>Updated</th>
        <th>Actions</th>
      </tr>
    </thead>
    <tbody>
      {job_rows if job_rows else '<tr><td colspan="8" style="text-align:center;color:#64748b;padding:2rem">No jobs yet – monitoring for new reels …</td></tr>'}
    </tbody>
  </table>

  <script>
    function toast(msg) {{
      const t = document.getElementById('toast');
      t.textContent = msg; t.style.display = 'block';
      setTimeout(() => t.style.display = 'none', 5000);
    }}
    async function doUpload(jobId) {{
      toast('Uploading …');
      const r = await fetch('/api/upload/' + jobId, {{ method: 'POST' }});
      const d = await r.json();
      toast(d.result || 'Done');
      setTimeout(() => location.reload(), 2000);
    }}
    async function uploadReel(shortcode) {{
      toast('Uploading reel ' + shortcode + ' …');
      const r = await fetch('/api/upload-reel/' + shortcode, {{ method: 'POST' }});
      const d = await r.json();
      toast(d.result || 'Done');
      setTimeout(() => location.reload(), 2000);
    }}
    async function uploadAll() {{
      toast('Uploading all pending with 20+ min gaps …');
      const r = await fetch('/api/upload-all', {{ method: 'POST' }});
      const d = await r.json();
      toast('Queued: ' + d.uploaded);
    }}
    async function uploadSelected() {{
      const cbs = document.querySelectorAll('.reel-cb:checked');
      if (cbs.length === 0) {{ toast('No reels selected'); return; }}
      const shortcodes = Array.from(cbs).map(cb => cb.value);
      toast('Uploading ' + shortcodes.length + ' reels with 20+ min gaps …');
      const r = await fetch('/api/upload-batch', {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/json' }},
        body: JSON.stringify({{ shortcodes }})
      }});
      const d = await r.json();
      if (d.error) {{ toast('Error: ' + d.error); return; }}
      toast(d.message || 'Queued');
      startBatchPoll();
    }}
    let _batchPollId = null;
    function startBatchPoll() {{
      if (_batchPollId) return;
      document.getElementById('batchBanner').style.display = 'block';
      _batchPollId = setInterval(pollBatch, 3000);
      pollBatch();
    }}
    async function pollBatch() {{
      try {{
        const r = await fetch('/api/batch-status');
        const s = await r.json();
        const banner = document.getElementById('batchBanner');
        const bar = document.getElementById('batchBar');
        const msg = document.getElementById('batchMsg');
        const detail = document.getElementById('batchDetail');
        const title = document.getElementById('batchTitle');
        if (!s.active && s.uploaded === 0 && s.total === 0) {{
          banner.style.display = 'none';
          clearInterval(_batchPollId); _batchPollId = null;
          return;
        }}
        banner.style.display = 'block';
        const pct = s.total > 0 ? Math.round((s.uploaded / s.total) * 100) : 0;
        bar.style.width = pct + '%';
        title.textContent = s.active ? '📤 Batch Upload In Progress' : '✅ Batch Upload Complete';
        msg.textContent = s.uploaded + ' / ' + s.total + ' uploaded';
        let det = 'Current: ' + (s.current || '—');
        if (s.wait_remaining_sec > 0) {{
          det += ' | ⏳ Next upload in ' + s.wait_remaining_str;
        }}
        detail.textContent = det;
        if (!s.active) {{
          clearInterval(_batchPollId); _batchPollId = null;
          bar.style.background = '#22c55e';
          setTimeout(() => location.reload(), 3000);
        }}
      }} catch(e) {{ /* ignore fetch errors */ }}
    }}
    // Check batch status on page load in case a batch is running
    (async () => {{
      try {{
        const r = await fetch('/api/batch-status');
        const s = await r.json();
        if (s.active) startBatchPoll();
      }} catch(e) {{}}
    }})();
    function toggleSelectAll() {{
      const all = document.getElementById('selectAll').checked;
      document.querySelectorAll('.reel-cb').forEach(cb => cb.checked = all);
      updateSelectedCount();
    }}
    function updateSelectedCount() {{
      const n = document.querySelectorAll('.reel-cb:checked').length;
      const el = document.getElementById('selectedCount');
      el.textContent = n > 0 ? n + ' selected' : '';
    }}
    document.addEventListener('change', e => {{
      if (e.target.classList.contains('reel-cb')) updateSelectedCount();
    }});
    async function toggleAuto() {{
      const r = await fetch('/api/toggle-auto', {{ method: 'POST' }});
      const d = await r.json();
      const btn = document.getElementById('autoBtn');
      btn.textContent = 'Auto-Upload: ' + (d.auto_upload ? 'ON' : 'OFF');
      btn.style.background = d.auto_upload ? '#22c55e' : '#ef4444';
      toast('Auto-upload: ' + (d.auto_upload ? 'ON' : 'OFF'));
    }}
    // Auto-refresh every 60 seconds (longer interval since uploads run in background)
    setTimeout(() => location.reload(), 60000);
  </script>
</body>
</html>"""
    return HTMLResponse(page)
