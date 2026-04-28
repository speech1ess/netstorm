#!/usr/bin/env python3
import os
import csv
import json

def build_target_chart_html(csv_path):
    """
    Reads target_metrics.csv and returns an HTML string with a Chart.js graph.
    """
    if not os.path.exists(csv_path):
        return ""

    timestamps = []
    cpu_vals = []
    ram_vals = []

    try:
        with open(csv_path, 'r') as f:
            reader = csv.DictReader(f)
            for row in reader:
                timestamps.append(float(row['time_offset']))
                cpu_vals.append(float(row['cpu']))
                ram_vals.append(float(row['ram']))
    except Exception:
        return ""

    if not timestamps:
        return ""

    chart_id = "targetMetricsChart"

    # Генерируем HTML с Canvas и скриптом Chart.js
    # Используем CDN. Для оффлайн-стендов можно заменить src на локальный путь.
    html = f"""
    <div class="iter-card">
        <div class="iter-header">
            <span class="iter-title">Target Server Health (CPU/RAM)</span>
        </div>
        <div class="iter-body">
            <div style="position: relative; height:300px; width:100%">
                <canvas id="{chart_id}"></canvas>
            </div>
            <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
            <script>
                (function() {{
                    const ctx = document.getElementById('{chart_id}').getContext('2d');
                    new Chart(ctx, {{
                        type: 'line',
                        data: {{
                            labels: {json.dumps(timestamps)},
                            datasets: [
                                {{
                                    label: 'CPU (%)',
                                    data: {json.dumps(cpu_vals)},
                                    borderColor: '#e74c3c',
                                    backgroundColor: 'rgba(231, 76, 60, 0.1)',
                                    borderWidth: 2,
                                    pointRadius: 0,
                                    pointHoverRadius: 4,
                                    tension: 0.2,
                                    fill: true
                                }},
                                {{
                                    label: 'RAM (%)',
                                    data: {json.dumps(ram_vals)},
                                    borderColor: '#3498db',
                                    backgroundColor: 'rgba(52, 152, 219, 0.1)',
                                    borderWidth: 2,
                                    pointRadius: 0,
                                    pointHoverRadius: 4,
                                    tension: 0.2,
                                    fill: true
                                }}
                            ]
                        }},
                        options: {{
                            responsive: true,
                            maintainAspectRatio: false,
                            interaction: {{
                                mode: 'index',
                                intersect: false,
                            }},
                            scales: {{
                                x: {{
                                    title: {{ display: true, text: 'Time (seconds)' }},
                                    ticks: {{ maxTicksLimit: 20 }}
                                }},
                                y: {{
                                    min: 0,
                                    max: 100,
                                    title: {{ display: true, text: 'Usage (%)' }}
                                }}
                            }}
                        }}
                    }});
                }})();
            </script>
        </div>
    </div>
    """
    return html