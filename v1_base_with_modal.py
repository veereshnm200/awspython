import boto3
import json
from datetime import datetime, timedelta, UTC
from typing import List, Dict, Optional

# AWS Credentials for testing
aws_access_key = ""
aws_secret_key = ""
aws_region = "us-east-1"

def get_cost_explorer_client():
    return boto3.client(
        'ce',
        aws_access_key_id=aws_access_key,
        aws_secret_access_key=aws_secret_key,
        region_name=aws_region
    )

class CostUsageDataPoint:
    def __init__(self, date: str, amount: str, unit: str):
        self.Date = date
        self.Amount = amount
        self.Unit = unit

class RootCauseEntry:
    def __init__(self, service, region, usage_type, linked_account, linked_account_name, cost_impact):
        self.Service = service
        self.Region = region
        self.UsageType = usage_type
        self.LinkedAccount = linked_account
        self.LinkedAccountName = linked_account_name
        self.Tags = {"Environment": "Production", "Owner": "FinanceTeam"}
        self.CostImpact = f"{float(cost_impact):.2f}" if cost_impact else "0.00"
        self.CostUsageGraph: List[CostUsageDataPoint] = []

class AnomalyEntry:
    def __init__(self, anomaly_id, start_date, end_date, impact, duration, root_causes):
        self.AnomalyId = anomaly_id
        self.StartDate = start_date
        self.EndDate = end_date
        self.LastDetectedDate = end_date
        self.DurationInDays = duration
        self.TotalCostImpact = f"{impact:.2f}"
        self.AverageDailyCost = f"{(impact / duration):.2f}" if duration > 0 else f"{impact:.2f}"
        self.Currency = "USD"
        self.RootCauses = root_causes

async def fetch_anomalies():
    client = get_cost_explorer_client()
    output = []
    start = (datetime.now(UTC) - timedelta(days=90)).strftime('%Y-%m-%d')
    end = datetime.now(UTC).strftime('%Y-%m-%d')

    next_token = None

    while True:
        request = {
            'DateInterval': {'StartDate': start, 'EndDate': end}
        }
        if next_token:
            request['NextPageToken'] = next_token

        response = client.get_anomalies(**request)

        for anomaly in response.get('Anomalies', []):
            anomaly_start = datetime.strptime(anomaly['AnomalyStartDate'].split('T')[0], '%Y-%m-%d')
            anomaly_end = datetime.strptime(anomaly['AnomalyEndDate'].split('T')[0], '%Y-%m-%d')
            duration = (anomaly_end - anomaly_start).days + 1 or 1

            impact = float(anomaly.get('Impact', {}).get('TotalImpact', 0))
            root_causes = []

            for rc in anomaly.get('RootCauses', []):
                root_cause = RootCauseEntry(
                    service=rc.get('Service'),
                    region=rc.get('Region'),
                    usage_type=rc.get('UsageType'),
                    linked_account=rc.get('LinkedAccount'),
                    linked_account_name=rc.get('LinkedAccountName'),
                    cost_impact=rc.get('Impact', {}).get('Contribution', 0)
                )

                root_cause.CostUsageGraph = await fetch_cost_usage_for_root_cause(
                    client, anomaly_start, anomaly_end, root_cause
                )

                root_causes.append(root_cause)

            entry = AnomalyEntry(
                anomaly_id=anomaly.get('AnomalyId'),
                start_date=anomaly.get('AnomalyStartDate'),
                end_date=anomaly.get('AnomalyEndDate'),
                impact=impact,
                duration=duration,
                root_causes=root_causes
            )

            output.append(entry)

        next_token = response.get('NextPageToken')
        if not next_token:
            break

    return output

async def fetch_cost_usage_for_root_cause(client, start_date, end_date, root_cause):
    filter_list = []

    def add_filter(key, value):
        if value:
            filter_list.append({
                'Dimensions': {
                    'Key': key,
                    'Values': [value]
                }
            })

    add_filter('SERVICE', root_cause.Service)
    add_filter('REGION', root_cause.Region)
    add_filter('USAGE_TYPE', root_cause.UsageType)
    add_filter('LINKED_ACCOUNT', root_cause.LinkedAccount)

    if len(filter_list) == 1:
        filters = filter_list[0]
    elif len(filter_list) > 1:
        filters = {'And': filter_list}
    else:
        filters = None

    try:
        response = client.get_cost_and_usage(
            TimePeriod={
                'Start': start_date.strftime('%Y-%m-%d'),
                'End': (end_date + timedelta(days=1)).strftime('%Y-%m-%d')
            },
            Granularity='DAILY',
            Metrics=['UnblendedCost'],
            Filter=filters
        )

        points = []
        for time_period in response['ResultsByTime']:
            amount_data = time_period['Total'].get('UnblendedCost', {})
            points.append(CostUsageDataPoint(
                date=time_period['TimePeriod']['Start'],
                amount=amount_data.get('Amount', "0"),
                unit=amount_data.get('Unit', "USD")
            ))
        return points

    except Exception as e:
        print(f"Error fetching cost usage for root cause {root_cause.Service}: {e}")
        return []

def save_to_json(entries, filename):
    def default_serializer(o):
        if isinstance(o, (AnomalyEntry, RootCauseEntry, CostUsageDataPoint)):
            return o.__dict__
        return str(o)

    with open(filename, 'w') as f:
        json.dump(entries, f, indent=4, default=default_serializer)

    print(f"Saved {len(entries)} entries to {filename}")

def generate_accordion_html(entry, idx, start_readable, end_readable):
    return f"""
    <div class="accordion-item">
        <button type="button" 
                class="accordion-header" 
                data-target="anomaly{idx}"
                aria-expanded="false">
            <div class="header-section">
                <span class="header-label">Anomaly ID</span>
                <span class="header-value">{entry.AnomalyId}</span>
            </div>
            <div class="header-section">
                <span class="header-label">Date Range</span>
                <span class="header-value">{start_readable} to {end_readable}</span>
            </div>
            <div class="header-section cost-impact">
                <span class="header-label">Total Cost Impact</span>
                <span class="header-value">${entry.TotalCostImpact}</span>
                <span class="share-icon" title="Share via Email">📧</span>
            </div>
        </button>
        <div class="accordion-content" id="anomaly{idx}">
            <table class="info-table">
                <tr>
                    <th>Duration</th>
                    <th>Total Cost Impact</th>
                    <th>Average Daily Cost</th>
                    <th>Currency</th>
                </tr>
                <tr>
                    <td>{entry.DurationInDays} days</td>
                    <td>${entry.TotalCostImpact}</td>
                    <td>${entry.AverageDailyCost}</td>
                    <td>{entry.Currency}</td>
                </tr>
            </table>
            <h3 class="root-causes-title">Root Causes:</h3>
    """

def generate_root_cause_html(rc, idx, rc_idx, chart_id):
    # Format cost impact with proper decimal places
    try:
        cost_impact = float(rc.CostImpact)
        formatted_cost_impact = f"${cost_impact:.2f}"
    except (ValueError, TypeError):
        formatted_cost_impact = "$0.00"
    
    # Format tags for display
    tags_html = ""
    for key, value in rc.Tags.items():
        tags_html += f"""
            <div class="tag">
                <span class="tag-key">{key}:</span>
                <span>{value}</span>
            </div>
        """
    
    return f"""
        <div class="root-cause">
            <div class="root-cause-header" 
                 id="root-cause-header-{idx}-{rc_idx}" 
                 onclick="toggleRootCause({idx}, {rc_idx}, event)">
                <span class="root-cause-toggle-icon">▼</span>
                <div class="root-cause-info">
                    <span class="root-cause-title">{rc.Service}</span>
                    <span class="root-cause-subtitle">{rc.Region} - {rc.UsageType}</span>
                </div>
            </div>
            <div class="root-cause-content" id="root-cause-content-{idx}-{rc_idx}">
                <table class="root-cause-table">
                    <tr>
                        <th>Service</th>
                        <th>Region</th>
                        <th>Usage Type</th>
                        <th>Linked Account</th>
                        <th>Cost Impact</th>
                        <th>Tags</th>
                    </tr>
                    <tr>
                        <td>{rc.Service}</td>
                        <td>{rc.Region}</td>
                        <td>{rc.UsageType}</td>
                        <td>{rc.LinkedAccount} ({rc.LinkedAccountName})</td>
                        <td class="cost-impact">{formatted_cost_impact}</td>
                        <td class="tags-cell">{tags_html}</td>
                    </tr>
                </table>
                <h4>Cost Usage Graph:</h4>
                <div class="chart-container"><canvas id="{chart_id}"></canvas></div>
                <table class="root-cause-table">
                    <tr>
                        <th>Date</th>
                        <th>Amount</th>
                        <th>Unit</th>
                    </tr>
    """

def generate_cost_usage_rows(rc):
    rows = ""
    for point in rc.CostUsageGraph:
        rows += f"""
                        <tr>
                            <td>{point.Date}</td>
                            <td>${float(point.Amount):.2f}</td>
                            <td>{point.Unit}</td>
                        </tr>
        """
    return rows

def generate_html_report(entries: List[AnomalyEntry], filename: str):
    # Collect unique services from anomalies
    unique_services = set()
    for entry in entries:
        for rc in entry.RootCauses:
            if rc.Service:
                unique_services.add(rc.Service)
    services_list = sorted(list(unique_services))

    # Collect unique linked accounts from anomalies
    unique_linked_accounts = set()
    for entry in entries:
        for rc in entry.RootCauses:
            if rc.LinkedAccount:
                unique_linked_accounts.add(f"{rc.LinkedAccount} ({rc.LinkedAccountName})")
    linked_accounts_list = sorted(list(unique_linked_accounts))

    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <title>AWS Cost Anomalies Report</title>
        <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
        <script src="https://cdnjs.cloudflare.com/ajax/libs/jspdf/2.5.1/jspdf.umd.min.js"></script>
        <script src="https://cdnjs.cloudflare.com/ajax/libs/jspdf-autotable/3.5.31/jspdf.plugin.autotable.min.js"></script>
        <script src="https://cdn.sheetjs.com/xlsx-0.20.1/package/dist/xlsx.full.min.js"></script>
        <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/flatpickr/dist/flatpickr.min.css">
        <script src="https://cdn.jsdelivr.net/npm/flatpickr"></script>
        <style>
            body { font-family: Arial, sans-serif; padding: 20px; background: #f7f9fa; }
            h1 { margin-bottom: 30px; text-align: center; color: #2c3e50; }
            .controls { 
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
                gap: 20px;
                margin-bottom: 30px;
                padding: 25px;
                background: white;
                border-radius: 8px;
                box-shadow: 0 2px 4px rgba(0,0,0,0.1);
                max-width: 90%;  /* Match accordion width */
                margin-left: auto;
                margin-right: auto;
            }
            .filter-group {
                display: flex;
                flex-direction: column;
                gap: 8px;
                min-width: 0;  /* Allow shrinking */
            }
            .filter-group label {
                font-size: 0.9em;
                color: #666;
                font-weight: bold;
                margin-bottom: 2px;
            }
            .filter-input {
                padding: 8px 12px;
                font-size: 0.9em;
                border: 1px solid #ddd;
                border-radius: 4px;
                width: 100%;
                box-sizing: border-box;
                height: 38px;  /* Match height with buttons */
            }
            .filter-input:focus {
                border-color: #4682b4;
                outline: none;
                box-shadow: 0 0 0 2px rgba(70,130,180,0.2);
            }
            .export-group {
                display: flex;
                gap: 12px;
                grid-column: 1 / -1;
                justify-content: flex-end;
                margin-top: 10px;
                padding-top: 15px;
                border-top: 1px solid #e6f0f7;
                align-items: center;  /* Align buttons vertically */
            }
            .export-btn {
                padding: 8px 12px;  /* Increased padding */
                font-size: 0.9em;
                border-radius: 4px;
                border: none;
                background: #4682b4;
                color: white;
                cursor: pointer;
                display: inline-flex;
                align-items: center;
                gap: 6px;
                transition: all 0.2s;
                min-width: 160px;
                justify-content: center;
                height: 38px;  /* Increased height to match dropdown */
                line-height: 1.2;  /* Added line height for better text alignment */
            }
            .export-btn:hover {
                background: #3a6a9a;
                transform: translateY(-1px);
                box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            }
            .export-btn:active {
                background: #2c5282;
                transform: translateY(0);
            }
            .export-btn span {
                font-size: 1.1em;
            }
            .email-btn {
                padding: 4px 8px;
                font-size: 0.85em;
                border-radius: 4px;
                border: 1px solid #4682b4;
                background: white;
                color: #4682b4;
                cursor: pointer;
                display: inline-flex;
                align-items: center;
                gap: 4px;
                transition: all 0.2s;
                margin-left: 10px;
            }
            .email-btn:hover {
                background: #f0f7fc;
                border-color: #3a6a9a;
                color: #3a6a9a;
            }
            .email-btn:active {
                background: #e6f0f7;
            }
            .email-btn span {
                font-size: 1.1em;
            }
            #filterSummary {
                grid-column: 1 / -1;  /* Span all columns */
                text-align: right;
                color: #666;
                font-size: 0.9em;
                margin-top: 10px;
                padding-top: 10px;
                border-top: 1px solid #e6f0f7;
            }
            @media (min-width: 1200px) {
                .controls {
                    grid-template-columns: repeat(5, 1fr);  /* 5 columns for larger screens */
                }
                .export-group {
                    grid-column: auto;  /* Don't span all columns on large screens */
                    border-top: none;
                    margin-top: 0;
                    padding-top: 0;
                }
            }
            @media (max-width: 1199px) {
                .controls {
                    grid-template-columns: repeat(2, 1fr);  /* 2 columns for medium screens */
                }
            }
            @media (max-width: 767px) {
                .controls {
                    grid-template-columns: 1fr;  /* 1 column for small screens */
                }
            }
            .accordion {
                background: #fff;
                border-radius: 8px;
                box-shadow: 0 2px 8px rgba(0,0,0,0.07);
                margin-bottom: 20px;
                overflow: hidden;
                max-width: 90%;  /* Reduced width for accordion */
                margin-left: auto;
                margin-right: auto;
            }
            .accordion-item {
                border-bottom: 1px solid #e0e0e0;
            }
            .accordion-header {
                background: #4682b4;
                color: #fff;
                cursor: pointer;
                padding: 15px 20px;
                font-size: 0.95em;
                font-weight: bold;
                transition: background 0.2s;
                outline: none;
                border: none;
                width: 100%;
                text-align: left;
                display: grid;
                grid-template-columns: auto 2fr 2fr 1fr;
                gap: 20px;
                align-items: center;
                position: relative;
            }

            .accordion-header::before {
                content: '▼';
                font-size: 0.8em;
                transition: transform 0.3s ease;
                grid-column: 1;
                justify-self: start;
                margin-right: 10px;
            }

            .accordion-header.active::before {
                transform: rotate(180deg);
            }

            .header-section {
                display: flex;
                flex-direction: column;
                gap: 5px;
            }

            .header-section:first-of-type {
                grid-column: 2;
            }

            .header-section:nth-of-type(2) {
                grid-column: 3;
            }

            .header-section.cost-impact {
                grid-column: 4;
                display: flex;
                align-items: center;
                gap: 10px;
                justify-content: flex-end;
            }

            .share-icon {
                cursor: pointer;
                color: white;
                font-size: 1.2em;
                padding: 4px;
                border-radius: 4px;
                transition: all 0.2s;
                opacity: 0.8;
            }

            .share-icon:hover {
                opacity: 1;
                background: rgba(255, 255, 255, 0.1);
            }

            .root-causes-title {
                font-size: 0.95em;  /* Match Anomaly ID font size */
                font-weight: bold;
                color: #2c3e50;
                margin: 15px 0;
            }

            /* Remove the old arrow styles */
            .accordion-header::after {
                display: none;
            }

            .header-label {
                font-size: 0.8em;
                opacity: 0.9;
                font-weight: normal;
            }
            .header-value {
                font-size: 0.95em;  /* Reduced font size */
                font-weight: bold;
            }
            .cost-impact {
                text-align: right;
                font-size: 1.1em;  /* Reduced font size */
                color: #ffffff !important;  /* Force white color */
            }
            .accordion-content {
                display: none;
                padding: 20px 24px;
                background: #f9f9f9;
                animation: fadeIn 0.3s;
            }
            .accordion-content.show {
                display: block;
            }
            @keyframes fadeIn {
                from { opacity: 0; }
                to { opacity: 1; }
            }
            table {
                border-collapse: collapse;
                width: 100%;
                margin-bottom: 20px;
                background: white;
            }
            th, td {
                border: 1px solid #ddd;
                padding: 12px 8px;
                text-align: left;
            }
            th {
                background-color: #e6f0f7;  /* Light steel blue */
                font-weight: 600;
                color: #2c3e50;
                text-transform: uppercase;
                font-size: 0.85em;
                letter-spacing: 0.5px;
                border-bottom: 2px solid #4682b4;  /* Steel blue border */
            }
            th:first-child {
                border-top-left-radius: 8px;
            }
            th:last-child {
                border-top-right-radius: 8px;
            }
            tr:nth-child(even) {
                background-color: #f9f9f9;
            }
            .root-cause {
                background: #fff;
                border: 1px solid #e6f0f7;
                border-radius: 8px;
                margin-bottom: 15px;
                padding: 15px;
                max-width: 85%;
                margin-left: 0;
                margin-right: auto;
            }
            .root-cause-header {
                display: flex;
                align-items: center;
                padding: 12px 15px;
                cursor: pointer;
                background: #f0f7fc;
                border-radius: 6px;
                margin-bottom: 12px;
                width: 100%;
            }
            .root-cause-toggle-icon {
                font-size: 1em;
                transition: transform 0.2s;
                color: #4682b4;
                margin-right: 10px;
                flex-shrink: 0;  /* Prevent arrow from shrinking */
            }
            .root-cause-info {
                display: flex;
                flex-direction: column;
                flex-grow: 1;  /* Allow info to take remaining space */
                text-align: left;
            }
            .root-cause-title {
                font-size: 1em;
                font-weight: 600;
                color: #2c3e50;
                margin-bottom: 4px;
                text-align: left;
            }
            .root-cause-subtitle {
                font-size: 0.85em;
                color: #666;
                text-align: left;
            }
            .root-cause-content {
                display: none;
                padding: 20px;
            }
            .root-cause-content.show {
                display: block;
            }
            .root-cause-table {
                width: 85%;  /* Further reduced width */
                margin: 12px auto;  /* Reduced margin */
                font-size: 0.9em;  /* Reduced font size */
            }
            .root-cause-table th,
            .root-cause-table td {
                padding: 10px 12px;  /* Reduced padding */
                font-size: 0.9em;  /* Reduced font size */
            }
            .root-cause-table th {
                background: #e6f0f7;
                font-weight: 600;
                color: #2c3e50;
                text-transform: uppercase;
                font-size: 0.8em;
                letter-spacing: 0.5px;
                border-bottom: 2px solid #4682b4;
            }
            .root-cause-table th:first-child {
                border-top-left-radius: 8px;
            }
            .root-cause-table th:last-child {
                border-top-right-radius: 8px;
            }
            .root-cause-table tr:last-child td {
                border-bottom: none;
            }
            .root-cause-table tr:hover td {
                background: #f5f9fc;
            }
            .tags-cell {
                display: flex;
                gap: 8px;
                flex-wrap: wrap;
            }
            .tag {
                background: #e9ecef;
                color: #495057;
                padding: 3px 6px;  /* Reduced padding */
                border-radius: 4px;
                font-size: 0.8em;  /* Reduced font size */
                display: inline-flex;
                align-items: center;
                gap: 4px;
            }
            .tag-key {
                font-weight: 600;
                color: #2c3e50;
            }
            .chart-container {
                width: 85%;  /* Further reduced width */
                margin: 15px auto;  /* Reduced margin */
            }
            .pagination-container {
                display: flex;
                justify-content: space-between;
                align-items: center;
                margin: 20px 0;
                padding: 10px;
                background: white;
                border-radius: 8px;
                box-shadow: 0 2px 4px rgba(0,0,0,0.1);
                max-width: 90%;  /* Reduced width */
                margin-left: auto;
                margin-right: auto;
            }
            
            .pagination-controls {
                display: flex;
                align-items: center;
                gap: 10px;
            }
            
            .pagination-btn {
                padding: 6px 10px;  /* Reduced padding */
                border: 1px solid #ddd;
                background: white;
                color: #4682b4;
                cursor: pointer;
                border-radius: 4px;
                min-width: 40px;
                text-align: center;
                transition: all 0.2s;
            }
            
            .pagination-btn:hover:not(:disabled) {
                background: #f0f0f0;
                border-color: #4682b4;
            }
            
            .pagination-btn.active {
                background: #4682b4;
                color: white;
                border-color: #4682b4;
            }
            
            .pagination-btn:disabled {
                opacity: 0.5;
                cursor: not-allowed;
            }
            
            .pagination-ellipsis {
                padding: 8px;
                color: #666;
            }
            
            .items-per-page {
                display: flex;
                align-items: center;
                gap: 8px;
            }
            
            .items-per-page select {
                padding: 6px;
                border: 1px solid #ddd;
                border-radius: 4px;
                background: white;
                color: #333;
            }
            
            #pageInfo {
                color: #666;
                font-size: 0.9em;
            }

            .info-table {
                width: 90%;  /* Reduced width */
                margin: 15px auto;  /* Reduced margin */
                font-size: 0.9em;  /* Reduced font size */
                border-collapse: separate;
                border-spacing: 0;
                margin: 20px 0;
                background: white;
                border-radius: 8px;
                overflow: hidden;
                box-shadow: 0 2px 4px rgba(0,0,0,0.05);
            }

            .info-table th,
            .info-table td {
                padding: 15px;
                text-align: left;
                border-bottom: 1px solid #e6f0f7;
            }

            .info-table th {
                background: #e6f0f7;  /* Light steel blue */
                font-weight: 600;
                color: #2c3e50;
                text-transform: uppercase;
                font-size: 0.85em;
                letter-spacing: 0.5px;
                border-bottom: 2px solid #4682b4;  /* Steel blue border */
            }

            .info-table th:first-child {
                border-top-left-radius: 8px;
            }

            .info-table th:last-child {
                border-top-right-radius: 8px;
            }

            .info-table tr:last-child td {
                border-bottom: none;
            }

            .info-table tr:hover td {
                background: #f5f9fc;  /* Very light steel blue */
            }

            .root-cause-table {
                width: 100%;
                border-collapse: separate;
                border-spacing: 0;
                margin: 15px 0;
                background: white;
                border-radius: 8px;
                overflow: hidden;
                box-shadow: 0 2px 4px rgba(0,0,0,0.05);
            }

            .root-cause-table th,
            .root-cause-table td {
                padding: 12px 15px;
                text-align: left;
                border-bottom: 1px solid #e6f0f7;
                font-size: 0.95em;
            }

            .root-cause-table th {
                background: #e6f0f7;  /* Light steel blue */
                font-weight: 600;
                color: #2c3e50;
                text-transform: uppercase;
                font-size: 0.8em;
                letter-spacing: 0.5px;
                border-bottom: 2px solid #4682b4;  /* Steel blue border */
            }

            .root-cause-table th:first-child {
                border-top-left-radius: 8px;
            }

            .root-cause-table th:last-child {
                border-top-right-radius: 8px;
            }

            .root-cause-table tr:last-child td {
                border-bottom: none;
            }

            .root-cause-table tr:hover td {
                background: #f5f9fc;  /* Very light steel blue */
            }

            .tags-cell {
                display: flex;
                gap: 8px;
                flex-wrap: wrap;
            }

            .tag {
                background: #e9ecef;
                color: #495057;
                padding: 4px 8px;
                border-radius: 4px;
                font-size: 0.85em;
                display: inline-flex;
                align-items: center;
                gap: 4px;
            }

            .tag-key {
                font-weight: 600;
                color: #2c3e50;
            }

            /* Add subtle hover effect for table headers */
            .info-table th:hover,
            .root-cause-table th:hover {
                background: #d4e4f0;  /* Slightly darker on hover */
            }

            /* Update table row hover to be more subtle */
            .info-table tr:hover td,
            .root-cause-table tr:hover td {
                background: #f5f9fc;  /* Very light steel blue */
            }

            /* Add subtle border to table cells */
            .info-table td,
            .root-cause-table td {
                border-bottom: 1px solid #e6f0f7;
            }

            /* Nested accordion styles */
            .root-cause-accordion {
                margin: 15px 0;
                border-radius: 8px;
                overflow: hidden;
                background: #fff;
                box-shadow: 0 1px 3px rgba(0,0,0,0.05);
            }

            .root-cause-header {
                background: #f0f7fc;  /* Lighter steel blue */
                color: #2c3e50;
                cursor: pointer;
                padding: 15px 20px;
                font-size: 1em;
                font-weight: 600;
                transition: all 0.2s;
                outline: none;
                border: none;
                width: 100%;
                text-align: left;
                display: flex;
                justify-content: space-between;
                align-items: center;
                border-bottom: 1px solid #e6f0f7;
            }

            .root-cause-header:hover,
            .root-cause-header.active {
                background: #e6f0f7;  /* Slightly darker on hover */
            }

            .root-cause-content {
                display: none;
                padding: 20px;
                background: #fff;
                animation: fadeIn 0.3s;
            }

            .root-cause-content.show {
                display: block;
            }

            .root-cause-summary {
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
                gap: 15px;
                margin-bottom: 15px;
                padding: 15px;
                background: #f8fafc;
                border-radius: 6px;
            }

            .summary-item {
                display: flex;
                flex-direction: column;
                gap: 5px;
            }

            .summary-label {
                font-size: 0.8em;
                color: #666;
                text-transform: uppercase;
                letter-spacing: 0.5px;
            }

            .summary-value {
                font-size: 1.1em;
                color: #2c3e50;
                font-weight: 500;
            }

            .cost-impact-value {
                color: #e74c3c;
                font-weight: 600;
            }

            /* Update existing root-cause styles */
            .root-cause {
                background-color: #fff;
                margin-bottom: 15px;
                border-radius: 8px;
                overflow: hidden;
            }

            .root-causes-container {
                margin: 20px 0;
            }

            .root-causes-toggle {
                display: flex;
                align-items: center;
                gap: 10px;
                margin-bottom: 15px;
                padding: 10px 15px;
                background: #f0f7fc;
                border-radius: 6px;
                cursor: pointer;
                user-select: none;
            }

            .root-causes-toggle:hover {
                background: #e6f0f7;
            }

            .root-causes-toggle-icon {
                font-size: 1.2em;
                transition: transform 0.3s;
            }

            .root-causes-toggle-icon.expanded {
                transform: rotate(180deg);
            }

            .root-causes-content {
                display: none;
                margin-top: 15px;
            }

            .root-causes-content.show {
                display: block;
                animation: fadeIn 0.3s;
            }

            .root-cause-item {
                background: #fff;
                border: 1px solid #e6f0f7;
                border-radius: 8px;
                margin-bottom: 15px;
                overflow: hidden;
            }

            .root-cause-header {
                background: #f8fafc;
                padding: 15px 20px;
                border-bottom: 1px solid #e6f0f7;
            }

            .root-cause-title {
                font-size: 1.1em;
                font-weight: 600;
                color: #2c3e50;
                margin-bottom: 5px;
            }

            .root-cause-subtitle {
                color: #666;
                font-size: 0.9em;
            }

            .root-cause-details {
                padding: 20px;
            }

            .root-cause-grid {
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
                gap: 20px;
                margin-bottom: 20px;
            }

            .detail-item {
                display: flex;
                flex-direction: column;
                gap: 5px;
            }

            .detail-label {
                font-size: 0.8em;
                color: #666;
                text-transform: uppercase;
                letter-spacing: 0.5px;
            }

            .detail-value {
                font-size: 1em;
                color: #2c3e50;
            }

            .cost-impact {
                color: #e74c3c;
                font-weight: 600;
            }

            .root-causes-section {
                margin: 20px 0;
            }

            .root-causes-toggle {
                background: #f0f7fc;
                padding: 12px 20px;
                border-radius: 6px;
                cursor: pointer;
                margin-bottom: 15px;
                display: flex;
                justify-content: space-between;
                align-items: center;
                border: 1px solid #e6f0f7;
            }

            .root-causes-toggle:hover {
                background: #e6f0f7;
            }

            .root-causes-toggle-text {
                font-weight: 600;
                color: #2c3e50;
            }

            .root-causes-toggle-icon {
                font-size: 1.2em;
                transition: transform 0.2s;
            }

            .root-causes-toggle-icon.expanded {
                transform: rotate(180deg);
            }

            .root-causes-list {
                display: none;
                margin-top: 15px;
            }

            .root-causes-list.show {
                display: block;
            }

            .root-cause {
                background: #fff;
                border: 1px solid #e6f0f7;
                border-radius: 8px;
                margin-bottom: 15px;
                padding: 20px;
            }

            .root-cause-header {
                margin-bottom: 15px;
                padding-bottom: 10px;
                border-bottom: 1px solid #e6f0f7;
            }

            .root-cause-title {
                font-size: 1.1em;
                font-weight: 600;
                color: #2c3e50;
                margin-bottom: 5px;
            }

            .root-cause-subtitle {
                color: #666;
                font-size: 0.9em;
            }

            .accordion-content {
                display: none;
                opacity: 0;
                transition: opacity 0.3s ease-in-out;
            }
            
            .accordion-content.show {
                display: block;
                opacity: 1;
            }
            
            .accordion-header {
                position: relative;
                cursor: pointer;
                transition: background-color 0.2s ease;
            }
            
            .accordion-header::after {
                display: none;
            }
            
            .accordion-header:hover {
                background-color: #3a6a9a;
            }

            select.filter-input {
                appearance: none;
                background-image: url("data:image/svg+xml;charset=UTF-8,%3csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3e%3cpolyline points='6 9 12 15 18 9'%3e%3c/polyline%3e%3c/svg%3e");
                background-repeat: no-repeat;
                background-position: right 8px center;
                background-size: 16px;
                padding-right: 32px;
            }

            /* Email modal styles */
            .email-modal {
                display: none;
                position: fixed;
                top: 0;
                left: 0;
                width: 100%;
                height: 100%;
                background: rgba(0, 0, 0, 0.5);
                z-index: 1000;
                justify-content: center;
                align-items: center;
            }

            .email-modal.show {
                display: flex;
            }

            .email-modal-content {
                background: white;
                padding: 25px;
                border-radius: 8px;
                width: 90%;
                max-width: 500px;
                box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
            }

            .email-modal-header {
                display: flex;
                justify-content: space-between;
                align-items: center;
                margin-bottom: 20px;
            }

            .email-modal-title {
                font-size: 1.2em;
                font-weight: bold;
                color: #2c3e50;
            }

            .email-modal-close {
                cursor: pointer;
                font-size: 1.5em;
                color: #666;
                padding: 4px;
                line-height: 1;
            }

            .email-form {
                display: flex;
                flex-direction: column;
                gap: 15px;
            }

            .email-form-group {
                display: flex;
                flex-direction: column;
                gap: 5px;
            }

            .email-form-group label {
                font-size: 0.9em;
                color: #666;
                font-weight: bold;
            }

            .email-form-group input,
            .email-form-group textarea {
                padding: 8px 12px;
                border: 1px solid #ddd;
                border-radius: 4px;
                font-size: 0.9em;
            }

            .email-form-group textarea {
                min-height: 100px;
                resize: vertical;
            }

            .email-form-actions {
                display: flex;
                justify-content: flex-end;
                gap: 10px;
                margin-top: 10px;
            }

            .email-btn {
                padding: 8px 16px;
                border-radius: 4px;
                font-size: 0.9em;
                cursor: pointer;
                transition: all 0.2s;
            }

            .email-btn-primary {
                background: #4682b4;
                color: white;
                border: none;
            }

            .email-btn-secondary {
                background: white;
                color: #666;
                border: 1px solid #ddd;
            }

            .email-btn:hover {
                opacity: 0.9;
                transform: translateY(-1px);
            }
        </style>
        <script>
            // Define charts object first and attach to window immediately
            window.charts = {
                initialize: function(chartId, labels, data) {
                    try {
                        if (!chartId || !Array.isArray(labels) || !Array.isArray(data)) {
                            console.warn('Invalid parameters for chart initialization');
                            return;
                        }
                        const ctx = document.getElementById(chartId);
                        if (!ctx) {
                            console.warn(`Chart canvas not found: ${chartId}`);
                            return;
                        }
                        if (window.chartInstances && window.chartInstances[chartId]) {
                            window.chartInstances[chartId].destroy();
                        }
                        const chart = new Chart(ctx.getContext('2d'), {
                            type: 'line',
                            data: {
                                labels: labels,
                                datasets: [{
                                    label: 'Cost',
                                    data: data,
                                    borderColor: '#4682b4',
                                    backgroundColor: 'rgba(70,130,180,0.1)',
                                    fill: true,
                                    tension: 0.2
                                }]
                            },
                            options: {
                                responsive: true,
                                maintainAspectRatio: false,
                                plugins: {
                                    legend: { display: false },
                                    tooltip: {
                                        callbacks: {
                                            label: function(context) {
                                                return '$' + parseFloat(context.raw).toFixed(2);
                                            }
                                        }
                                    }
                                },
                                scales: {
                                    x: { 
                                        title: { display: true, text: 'Date', font: { weight: 'bold' } }
                                    },
                                    y: { 
                                        title: { display: true, text: 'Amount (USD)', font: { weight: 'bold' } },
                                        ticks: {
                                            callback: function(value) {
                                                return '$' + value.toFixed(2);
                                            }
                                        }
                                    }
                                }
                            }
                        });
                        if (!window.chartInstances) window.chartInstances = {};
                        window.chartInstances[chartId] = chart;
                    } catch (error) {
                        console.error(`Error initializing chart ${chartId}:`, error);
                    }
                }
            };

            // Define and attach all other functions to window immediately
            window.toggleAccordion = function(id, event) {
                if (event) { 
                    event.preventDefault(); 
                    event.stopPropagation(); 
                }
                const content = document.getElementById(id);
                const header = document.querySelector(`[data-target="${id}"]`);
                if (!content || !header) {
                    console.warn(`Could not find content or header for id: ${id}`);
                    return;
                }
                
                // Close all other accordions first
                document.querySelectorAll('.accordion-content.show').forEach(item => {
                    if (item.id !== id) {
                        item.classList.remove('show');
                        const otherHeader = document.querySelector(`[data-target="${item.id}"]`);
                        if (otherHeader) {
                            otherHeader.classList.remove('active');
                            otherHeader.setAttribute('aria-expanded', 'false');
                        }
                    }
                });
                
                // Toggle current accordion
                const isExpanding = !content.classList.contains('show');
                content.classList.toggle('show');
                header.classList.toggle('active');
                header.setAttribute('aria-expanded', isExpanding);
                
                // Log for debugging
                console.log(`Toggled accordion ${id}, isExpanding: ${isExpanding}`);
            };

            window.toggleRootCause = function(anomalyId, rootCauseId, event) {
                if (event) { event.preventDefault(); event.stopPropagation(); }
                const content = document.getElementById(`root-cause-content-${anomalyId}-${rootCauseId}`);
                const header = document.getElementById(`root-cause-header-${anomalyId}-${rootCauseId}`);
                if (!content || !header) return;
                const icon = header.querySelector('.root-cause-toggle-icon');
                content.classList.toggle('show');
                if (icon) {
                    icon.style.transform = content.classList.contains('show') ? 'rotate(180deg)' : 'rotate(0deg)';
                }
            };

            window.openEmailClient = function(idx, event) {
                if (event) { event.preventDefault(); event.stopPropagation(); }
                try {
                    const anomalyContainer = document.querySelectorAll('.accordion-item')[idx];
                    if (!anomalyContainer) throw new Error('Anomaly not found');
                    const header = anomalyContainer.querySelector('.accordion-header');
                    const content = anomalyContainer.querySelector('.accordion-content');
                    if (!header || !content) throw new Error('Header/content not found');
                    const anomalyId = header.querySelector('.header-value')?.textContent?.trim() || 'Unknown';
                    const dateText = header.querySelectorAll('.header-value')[1]?.textContent?.trim() || 'Unknown';
                    const costText = header.querySelectorAll('.header-value')[2]?.textContent?.trim() || 'Unknown';
                    const infoTable = content.querySelector('.info-table');
                    const duration = infoTable?.querySelector('tr:nth-child(2) td:nth-child(1)')?.textContent?.trim() || 'Unknown';
                    const totalCost = infoTable?.querySelector('tr:nth-child(2) td:nth-child(2)')?.textContent?.trim() || 'Unknown';
                    const avgDailyCost = infoTable?.querySelector('tr:nth-child(2) td:nth-child(3)')?.textContent?.trim() || 'Unknown';
                    const currency = infoTable?.querySelector('tr:nth-child(2) td:nth-child(4)')?.textContent?.trim() || 'Unknown';
                    let body = `AWS Cost Anomaly Alert\n\nAnomaly ID: ${anomalyId}\nDate Range: ${dateText}\nTotal Cost Impact: ${costText}\nDuration: ${duration}\nTotal Cost: ${totalCost}\nAverage Daily Cost: ${avgDailyCost}\nCurrency: ${currency}`;
                    window.location.href = `mailto:?subject=${encodeURIComponent('AWS Cost Anomaly Alert: ' + anomalyId)}&body=${encodeURIComponent(body)}`;
                } catch (err) {
                    console.error('Error opening email client:', err);
                    alert('Failed to open email client: ' + err.message);
                }
            };

            // Initialize when DOM is loaded
            document.addEventListener('DOMContentLoaded', function() {
                try {
                    // Initialize date picker
                    const dateRangeInput = document.getElementById('dateRange');
                    if (dateRangeInput && window.flatpickr) {
                        window.dateRangePicker = flatpickr(dateRangeInput, {
                            mode: "range",
                            dateFormat: "Y-m-d",
                            altInput: true,
                            altFormat: "F j, Y",
                            placeholder: "Select date range...",
                            allowInput: true,
                            onChange: function(selectedDates) {
                                if (selectedDates.length === 2) {
                                    const startDate = selectedDates[0].toISOString().split('T')[0];
                                    const endDate = selectedDates[1].toISOString().split('T')[0];
                                    dateRangeInput.value = `${startDate} to ${endDate}`;
                                    window.filterAnomalies();
                                }
                            }
                        });
                    }

                    // Initialize filter listeners
                    ['searchText', 'minCost', 'maxCost', 'serviceFilter', 'linkedAccountFilter'].forEach(id => {
                        const el = document.getElementById(id);
                        if (el) {
                            const eventType = id === 'searchText' ? 'input' : 'change';
                            el.addEventListener(eventType, window.filterAnomalies);
                        }
                    });

                    // Initialize accordion listeners with improved event handling
                    document.querySelectorAll('.accordion-header').forEach(header => {
                        const targetId = header.getAttribute('data-target');
                        if (targetId) {
                            header.addEventListener('click', function(e) {
                                // Don't toggle if clicking the email icon
                                if (e.target.closest('.share-icon')) {
                                    return;
                                }
                                // Prevent event bubbling
                                e.preventDefault();
                                e.stopPropagation();
                                // Toggle the accordion
                                window.toggleAccordion(targetId, e);
                            });
                        }
                    });

                    // Initialize email listeners separately
                    document.querySelectorAll('.share-icon').forEach(icon => {
                        icon.addEventListener('click', function(e) {
                            e.preventDefault();
                            e.stopPropagation();
                            const anomalyItem = icon.closest('.accordion-item');
                            if (anomalyItem) {
                                const index = Array.from(document.querySelectorAll('.accordion-item')).indexOf(anomalyItem);
                                window.openEmailClient(index, e);
                            }
                        });
                    });

                    // Initialize pagination
                    window.filteredItems = Array.from(document.querySelectorAll('.accordion-item'));
                    window.currentPage = 1;
                    window.itemsPerPage = 5;
                    window.updatePagination();
                    window.updateFilterSummary();
                } catch (err) {
                    console.error('Error during initialization:', err);
                }
            });

            // Attach remaining functions to window
            window.filterAnomalies = function() {
                try {
                    const searchText = (document.getElementById('searchText')?.value || '').toLowerCase().trim();
                    const dateRange = document.getElementById('dateRange')?.value || '';
                    const minCost = parseFloat(document.getElementById('minCost')?.value) || 0;
                    const maxCost = parseFloat(document.getElementById('maxCost')?.value) || Infinity;
                    const serviceFilter = document.getElementById('serviceFilter')?.value || '';
                    const linkedAccountFilter = document.getElementById('linkedAccountFilter')?.value || '';
                    const items = Array.from(document.querySelectorAll('.accordion-item'));
                    window.filteredItems = items.filter(item => {
                        const header = item.querySelector('.accordion-header');
                        const content = item.querySelector('.accordion-content');
                        if (!header || !content) return false;
                        const anomalyId = header.querySelector('.header-value')?.textContent?.trim() || '';
                        const dateText = header.querySelectorAll('.header-value')[1]?.textContent?.trim() || '';
                        const costText = header.querySelectorAll('.header-value')[2]?.textContent?.trim() || '';
                        const costImpact = parseFloat(costText.replace(/[$,]/g, '')) || 0;
                        const [startDateStr, endDateStr] = dateText.split(' to ').map(d => d.trim());
                        const anomalyStart = new Date(startDateStr);
                        const anomalyEnd = new Date(endDateStr);
                        let filterStart = null, filterEnd = null;
                        if (dateRange && dateRange.includes(' to ')) {
                            const [filterStartStr, filterEndStr] = dateRange.split(' to ').map(d => d.trim());
                            if (filterStartStr && filterEndStr) {
                                filterStart = new Date(filterStartStr);
                                filterEnd = new Date(filterEndStr);
                                if (filterEnd) filterEnd.setHours(23, 59, 59, 999);
                            }
                        }
                        const services = Array.from(content.querySelectorAll('.root-cause'))
                            .map(rc => rc.querySelector('.root-cause-title')?.textContent?.trim() || '')
                            .filter(Boolean);
                        const linkedAccounts = Array.from(content.querySelectorAll('.root-cause'))
                            .map(rc => {
                                const accountCell = rc.querySelector('td:nth-child(4)');
                                if (!accountCell) return '';
                                const accountText = accountCell.textContent.trim();
                                const match = accountText.match(/(\d+)\s*\(([^)]+)\)/);
                                return match ? `${match[1]} (${match[2]})` : accountText;
                            })
                            .filter(Boolean);
                        const matchesSearch = !searchText || 
                            anomalyId.toLowerCase().includes(searchText) ||
                            dateText.toLowerCase().includes(searchText) ||
                            costText.toLowerCase().includes(searchText);
                        const matchesDate = !filterStart || !filterEnd || 
                            (anomalyStart && anomalyEnd && anomalyStart <= filterEnd && anomalyEnd >= filterStart);
                        const matchesCost = costImpact >= minCost && costImpact <= maxCost;
                        const matchesService = !serviceFilter || services.some(service => service === serviceFilter);
                        const matchesLinkedAccount = !linkedAccountFilter || linkedAccounts.some(account => account === linkedAccountFilter);
                        return matchesSearch && matchesDate && matchesCost && matchesService && matchesLinkedAccount;
                    });
                    window.currentPage = 1;
                    window.updatePagination();
                    window.updateFilterSummary();
                } catch (err) {
                    console.error('Error in filterAnomalies:', err);
                }
            };

            window.clearFilters = function() {
                ['searchText', 'minCost', 'maxCost', 'serviceFilter', 'linkedAccountFilter'].forEach(id => {
                    const el = document.getElementById(id);
                    if (el) el.value = '';
                });
                if (window.dateRangePicker) window.dateRangePicker.clear();
                window.filteredItems = Array.from(document.querySelectorAll('.accordion-item'));
                window.currentPage = 1;
                window.updatePagination();
                window.updateFilterSummary();
            };

            window.updateFilterSummary = function() {
                const visibleItems = document.querySelectorAll('.accordion-item[style=""]').length;
                const totalItems = document.querySelectorAll('.accordion-item').length;
                const filterSummary = document.getElementById('filterSummary');
                if (filterSummary) {
                    filterSummary.textContent = `Showing ${visibleItems} of ${totalItems} anomalies`;
                }
            };

            window.updatePagination = function() {
                const start = (window.currentPage - 1) * window.itemsPerPage;
                const end = start + window.itemsPerPage;
                document.querySelectorAll('.accordion-item').forEach(item => {
                    item.style.display = 'none';
                });
                window.filteredItems.slice(start, end).forEach(item => {
                    if (item) item.style.display = '';
                });
                window.updatePaginationControls();
            };

            window.updatePaginationControls = function() {
                const totalPages = Math.ceil(window.filteredItems.length / window.itemsPerPage);
                const paginationContainer = document.getElementById('pagination');
                const pageInfo = document.getElementById('pageInfo');
                if (!paginationContainer || !pageInfo) return;
                const start = (window.currentPage - 1) * window.itemsPerPage + 1;
                const end = Math.min(window.currentPage * window.itemsPerPage, window.filteredItems.length);
                pageInfo.textContent = `Showing ${start}-${end} of ${window.filteredItems.length} anomalies`;
                let paginationHTML = '';
                paginationHTML += `<button class="pagination-btn" onclick="changePage(${window.currentPage - 1})" ${window.currentPage === 1 ? 'disabled' : ''}>Previous</button>`;
                for (let i = 1; i <= totalPages; i++) {
                    if (i === 1 || i === totalPages || (i >= window.currentPage - 2 && i <= window.currentPage + 2)) {
                        paginationHTML += `<button class="pagination-btn ${i === window.currentPage ? 'active' : ''}" onclick="changePage(${i})">${i}</button>`;
                    } else if ((i === window.currentPage - 3 && window.currentPage > 4) || (i === window.currentPage + 3 && window.currentPage < totalPages - 3)) {
                        paginationHTML += `<span class="pagination-ellipsis">...</span>`;
                    }
                }
                paginationHTML += `<button class="pagination-btn" onclick="changePage(${window.currentPage + 1})" ${window.currentPage === totalPages ? 'disabled' : ''}>Next</button>`;
                paginationContainer.innerHTML = paginationHTML;
            };

            window.changePage = function(newPage) {
                const totalPages = Math.ceil(window.filteredItems.length / window.itemsPerPage);
                if (newPage < 1 || newPage > totalPages) return;
                window.currentPage = newPage;
                window.updatePagination();
            };

            window.changeItemsPerPage = function(newValue) {
                const value = parseInt(newValue);
                if (isNaN(value) || value < 1) return;
                window.itemsPerPage = value;
                window.currentPage = 1;
                window.updatePagination();
            };
        </script>
    </head>
    <body>
        <h1>AWS Cost Anomalies Report</h1>
        <div class="controls">
            <div class="filter-group">
                <label for="searchText">Search Anomalies</label>
                <input type="text" id="searchText" class="filter-input" placeholder="Search by ID, date, or amount...">
            </div>
            <div class="filter-group">
                <label for="dateRange">Date Range</label>
                <input type="text" id="dateRange" class="filter-input" placeholder="Select date range...">
            </div>
            <div class="filter-group">
                <label for="minCost">Min Cost ($)</label>
                <input type="number" id="minCost" class="filter-input" placeholder="Min cost..." min="0" step="0.01">
            </div>
            <div class="filter-group">
                <label for="maxCost">Max Cost ($)</label>
                <input type="number" id="maxCost" class="filter-input" placeholder="Max cost..." min="0" step="0.01">
            </div>
            <div class="filter-group">
                <label for="serviceFilter">Service</label>
                <select id="serviceFilter" class="filter-input">
                    <option value="">All Services</option>
    """

    # Add service options
    for service in services_list:
        html += f"""
                    <option value="{service}">{service}</option>
        """

    html += """
                </select>
            </div>
            <div class="filter-group">
                <label for="linkedAccountFilter">Linked Account</label>
                <select id="linkedAccountFilter" class="filter-input">
                    <option value="">All Accounts</option>
    """

    # Add linked account options
    for account in linked_accounts_list:
        html += f"""
                    <option value="{account}">{account}</option>
        """

    html += """
                </select>
            </div>
            <div class="export-group">
                <button class="export-btn" onclick="clearFilters()">
                    <span>🗑️</span> Clear Filters
                </button>
                <button class="export-btn" onclick="exportToPDF()">
                    <span>📄</span> Export to PDF
                </button>
                <button class="export-btn" onclick="exportToExcel()">
                    <span>📊</span> Export to Excel
                </button>
            </div>
            <div id="filterSummary"></div>
        </div>
        <div class="accordion" id="anomalyAccordion">
    """

    chart_idx = 0
    for idx, entry in enumerate(entries):
        try:
            start_dt = datetime.strptime(entry.StartDate.split('T')[0], '%Y-%m-%d')
            end_dt = datetime.strptime(entry.EndDate.split('T')[0], '%Y-%m-%d')
            start_readable = start_dt.strftime('%m/%d/%Y')
            end_readable = end_dt.strftime('%m/%d/%Y')
        except Exception as e:
            start_readable = entry.StartDate.split('T')[0]
            end_readable = entry.EndDate.split('T')[0]

        html += generate_accordion_html(entry, idx, start_readable, end_readable)
        
        for rc_idx, rc in enumerate(entry.RootCauses):
            chart_id = f"chart_{idx}_{rc_idx}"
            chart_idx += 1
            
            # Prepare data for Chart.js
            labels = [point.Date for point in rc.CostUsageGraph]
            data = [float(point.Amount) for point in rc.CostUsageGraph]
            
            html += generate_root_cause_html(rc, idx, rc_idx, chart_id)
            html += generate_cost_usage_rows(rc)
            html += """
                        </table>
                    </div>
                </div>
            """
            
            # Add Chart.js initialization code
            html += f"""
            <script>
            (function() {{
                const labels = {labels};
                const data = {data};
                window.charts.initialize('{chart_id}', labels, data);
            }})();
            </script>
            """
        html += """
                </div>
            </div>
        """

    html += """
        </div>
        <div class="pagination-container">
            <div class="pagination-controls">
                <div class="items-per-page">
                    <label for="itemsPerPage">Items per page:</label>
                    <select id="itemsPerPage" onchange="changeItemsPerPage(this.value)">
                        <option value="5">5</option>
                        <option value="10">10</option>
                        <option value="20">20</option>
                        <option value="50">50</option>
                    </select>
                </div>
                <div id="pagination"></div>
            </div>
            <div id="pageInfo"></div>
        </div>
    </body>
    </html>
    """

    with open(filename, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"Saved HTML report to {filename}")

# Entry point
import asyncio

if __name__ == "__main__":
    anomalies = asyncio.run(fetch_anomalies())
    save_to_json(anomalies, "aws_anomalies_detailed.json")
    generate_html_report(anomalies, "aws_anomalies_detailed.html")
