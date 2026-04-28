# -*- coding: utf-8 -*-
"""
HTML Templates for PMI Reports (Combined Library)
Location: /opt/pmi/lib/html_templates.py
"""

# ─────────────────────────────────────────────────────────────
# 1. COMMON CSS (FIXED: DOUBLE BRACES FOR FORMAT)
# ─────────────────────────────────────────────────────────────
CSS_STYLES = """
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; background: #eaeff2; color: #333; margin: 0; padding: 20px; }}
.container {{ max-width: 1200px; margin: 0 auto; background: white; padding: 0; border-radius: 8px; box-shadow: 0 4px 15px rgba(0,0,0,0.05); overflow: hidden; }}

/* HEADER STYLES */
.report-header {{ background: #2c3e50; color: white; padding: 30px 40px; }}
.report-label {{ font-size: 12px; font-weight: 700; text-transform: uppercase; letter-spacing: 2px; color: #bdc3c7; margin-bottom: 10px; }}
.scenario-title {{ font-size: 28px; font-weight: 700; margin: 0; line-height: 1.2; }}
.scenario-subtitle {{ font-size: 18px; color: #3498db; margin-top: 8px; font-weight: 500; }} /* <-- НОВЫЙ СТИЛЬ */
.session-meta {{ margin-top: 15px; font-size: 14px; color: #ecf0f1; opacity: 0.8; font-family: monospace; }}

/* CONTENT PADDING */
.content {{ padding: 40px; }}

h2 {{ color: #34495e; margin-top: 40px; margin-bottom: 20px; font-size: 1.5em; border-left: 5px solid #3498db; padding-left: 15px; }}

/* META GRID (KPIs) */
.meta-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 20px; margin-bottom: 40px; }}
.meta-card {{ background: #f8f9fa; padding: 20px; border-radius: 8px; border: 1px solid #eee; }}
.meta-label {{ font-size: 11px; color: #7f8c8d; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 5px; }}
.meta-value {{ font-size: 24px; font-weight: 700; color: #2c3e50; }}

/* TABLES */
table {{ width: 100%; border-collapse: collapse; margin-bottom: 20px; font-size: 14px; }}
th {{ text-align: left; background: #ecf0f1; padding: 12px 15px; font-weight: 600; color: #7f8c8d; text-transform: uppercase; font-size: 12px; }}
td {{ padding: 12px 15px; border-bottom: 1px solid #eee; vertical-align: middle; }}
tr:hover td {{ background: #fdfdfd; }}

/* STATUS BADGES */
.status-pass {{ color: #27ae60; font-weight: bold; background: rgba(39, 174, 96, 0.1); padding: 4px 8px; border-radius: 4px; display: inline-block; }}
.status-fail {{ color: #e74c3c; font-weight: bold; background: rgba(231, 76, 60, 0.1); padding: 4px 8px; border-radius: 4px; display: inline-block; }}
/* НОВЫЕ КЛАССЫ: Желтый для частичного пробития и Синий для успешного блока */
.status-warning {{ color: #f39c12; font-weight: bold; background: rgba(243, 156, 18, 0.1); padding: 4px 8px; border-radius: 4px; display: inline-block; }}
.status-blocked {{ color: #2980b9; font-weight: bold; background: rgba(41, 128, 185, 0.1); padding: 4px 8px; border-radius: 4px; display: inline-block; }}

/* ITERATION CARDS */
.iter-card {{ border: 1px solid #e0e0e0; border-radius: 8px; margin-bottom: 30px; overflow: hidden; background: #fff; box-shadow: 0 2px 5px rgba(0,0,0,0.02); }}
.iter-header {{ background: #f8f9fa; padding: 15px 20px; border-bottom: 1px solid #eee; display: flex; justify-content: space-between; align-items: center; }}
.iter-title {{ font-weight: 700; font-size: 16px; color: #2c3e50; }}
.iter-meta {{ font-size: 13px; color: #7f8c8d; font-family: monospace; }}
.iter-body {{ padding: 20px; }}

/* LOG VIEWER */
.log-view {{ background: #2c3e50; color: #f1f1f1; padding: 15px; margin: 0; font-family: monospace; font-size: 12px; overflow-x: auto; max-height: 600px; white-space: pre-wrap; }}

/* BUTTONS & LINKS */
.btn-group {{ display: flex; flex-wrap: wrap; gap: 8px; }}
.btn {{ display: inline-flex; align-items: center; padding: 6px 12px; background: #fff; color: #2c3e50; text-decoration: none; border-radius: 4px; font-size: 12px; font-weight: 500; border: 1px solid #bdc3c7; transition: all 0.2s; }}
.btn:hover {{ background: #f1f2f6; border-color: #95a5a6; }}

/* Primary (HTML Report) */
.btn-primary {{ background: #3498db; color: white; border-color: #2980b9; }}
.btn-primary:hover {{ background: #2980b9; color: white; }}

/* Console Log (Dark style) */
.btn-console {{ background: #34495e; color: #ecf0f1; border-color: #2c3e50; font-family: monospace; }}
.btn-console:hover {{ background: #2c3e50; color: white; }}

.footer {{ margin-top: 50px; text-align: center; color: #bdc3c7; font-size: 12px; padding-bottom: 20px; }}
"""

# ─────────────────────────────────────────────────────────────
# 2. SESSION REPORT TEMPLATE
# ─────────────────────────────────────────────────────────────
SESSION_REPORT_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Load Test: {session_id}</title>
    <style>
        """ + CSS_STYLES + """
    </style>
</head>
<body>
<div class="container">
    <div class="report-header">
        <div class="report-label">Load Testing Report</div>
        <div class="scenario-title">{label}</div>
        <div class="scenario-subtitle">{subtitle}</div><div class="session-meta">SESSION ID: {session_id}</div>
    </div>

    <div class="content">
        <div class="meta-grid">
            <div class="meta-card">
                <div class="meta-label">Start Time</div>
                <div class="meta-value" style="font-size:18px">{start_time}</div>
            </div>
            <div class="meta-card">
                <div class="meta-label">Total Duration</div>
                <div class="meta-value">{total_duration}</div>
            </div>
            <div class="meta-card">
                <div class="meta-label">Iterations</div>
                <div class="meta-value">{run_count}</div>
            </div>
            <div class="meta-card">
                <div class="meta-label">Total Tests</div>
                <div class="meta-value" style="color:#2980b9;">{total_tests}</div>
            </div>
            <div class="meta-card">
                <div class="meta-label">Avg Session RPS</div>
                <div class="meta-value">{avg_rps}</div>
            </div>
        </div>

        <h2>Execution Overview</h2>
        <table>
            <thead>
                <tr>
                    <th>Target Profile</th>
                    <th>Start Time</th>
                    <th>Duration</th>
                    <th>Load Config</th>
                    <th>Actual RPS</th>
                    <th>Response Time</th> <th>Errors</th>
                    <th>Status</th>
                </tr>
            </thead>
            <tbody>
                {overview_rows}
            </tbody>
        </table>

        <h2>Artifacts & Downloads</h2>
        {artifacts_section}

        {target_health_section}

        <h2>Session Details (Log)</h2>
        {log_section}

        <div class="footer">
            Generated by NetStorm Load Testing Orchestrator | {gen_date}
        </div>
    </div>
</div>
</body>
</html>
"""

# ─────────────────────────────────────────────────────────────
# 3. DASHBOARD INDEX TEMPLATES
# ─────────────────────────────────────────────────────────────

BASE_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>PMI Dashboard</title>
    <style>
        """ + CSS_STYLES + """
        .header {{ background: white; padding: 20px 40px; border-bottom: 1px solid #eee; display:flex; justify-content:space-between; align-items:center; }}
        .header h1 {{ margin: 0; font-size: 24px; color: #2c3e50; border: none; padding: 0; }}
        .dash-content {{ padding: 40px; }}
       
        .day-group {{ margin-bottom: 40px; }}
        .day-header {{ display: flex; align-items: center; margin-bottom: 15px; padding-left: 10px; border-left: 4px solid #3498db; }}
        .day-title {{ font-size: 20px; font-weight: 700; color: #2c3e50; margin-right: 15px; }}
       
        .run-card {{ background: white; border-radius: 8px; padding: 20px; margin-bottom: 15px; box-shadow: 0 2px 5px rgba(0,0,0,0.03); border-left: 5px solid #ccc; transition: transform 0.2s; }}
        .run-card:hover {{ transform: translateY(-2px); box-shadow: 0 5px 15px rgba(0,0,0,0.08); }}
        .run-card.has-report {{ border-left-color: #2ecc71; }}
        .run-card.no-report {{ border-left-color: #e74c3c; }}
       
        .run-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 15px; }}
        .run-time {{ font-weight: 700; font-size: 18px; color: #2c3e50; }}
        .run-id {{ font-family: monospace; color: #95a5a6; font-size: 13px; background: #f8f9fa; padding: 2px 6px; border-radius: 4px; }}
       
        .empty-state {{ text-align: center; padding: 60px; color: #bdc3c7; border: 2px dashed #eee; border-radius: 10px; }}
    </style>
</head>
<body>
    <div class="header">
        <h1>PMI Test Dashboard</h1>
        <div style="color:#7f8c8d; font-size:14px;">{total_runs} sessions recorded</div>
    </div>
    <div class="dash-content">
        {body}
        <div class="footer">Dashboard Updated: {generated_ts}</div>
    </div>
</body>
</html>
"""

DAY_CARD_TEMPLATE = """
<div class="day-group">
    <div class="day-header">
        <span class="day-title">{day_label}</span>
        {weekday_html}
    </div>
    {runs_html}
</div>
"""

RUN_CARD_TEMPLATE = """
<div class="run-card {status_class}">
    <div class="run-header">
        <div>
            <span class="run-time">{time_label}</span>
            <span style="margin-left: 10px; color: #7f8c8d;">{ago}</span>
        </div>
        <span class="run-id">{dir_name}</span>
    </div>
    <div class="btn-group">
        {files_html}
    </div>
</div>
"""

FILE_LINK_TEMPLATE = """<a href="{path}" class="btn {style_class}" target="_blank">{name}</a>"""
EMPTY_BODY_TEMPLATE = """<div class="empty-state"><h2>No test results found</h2><p>Run a scenario to see data here.</p></div>"""