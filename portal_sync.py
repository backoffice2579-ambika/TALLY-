"""
==============================================================================
AMBIKA MULTI SERVICES - LIVE PORTAL TO TALLY AUTO-SYNC BACKEND
==============================================================================
Description:
  Fetches live transaction data from the Ambika portal CSV and imports
  it directly into Tally Prime / Tally.ERP 9 via HTTP XML (Port 9000).

Voucher Format:
  - Voucher Type : Journal
  - Debit (Dr.)  : API BALANCE (-Amount)
  - Credit (Cr.) : Customer Mobile Number (+Amount)
  - Narration    : Description | Ref: UTR / Bank Ref | Bank: Remark
  - Party Ledger : Auto-created with 10-digit Mobile No & Outlet Name

Usage:
  python portal_sync.py --once    (Single run, used by Tally TDL button)
  python portal_sync.py           (Continuous background auto-sync every 60s)
==============================================================================
"""

import os
import sys
import time
import json
import hashlib
import urllib.request
import csv
from datetime import datetime
from xml.sax.saxutils import escape

# Ensure UTF-8 output in Windows Console
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Portal CSV Endpoint & Configuration
CSV_URL = "https://ambikamultiservices.co.in/ALL_IN_ONE_SITE/A1%20RETAILERS_DISTRIBUTOR_ADMIN_LEDGER/dataexport11.csv"
TALLY_URL = "http://localhost:9000"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(BASE_DIR, "synced_records.json")
XML_OUTPUT_FILE = os.path.join(BASE_DIR, "tally_import.xml")


def load_synced_state():
    """Loads previously synced transaction hashes to prevent duplicates."""
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except Exception as e:
            print(f"[!] Warning reading state file: {e}")
            return set()
    return set()


def save_synced_state(synced_set):
    """Saves synced transaction hashes to disk."""
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(list(synced_set), f, indent=2)
    except Exception as e:
        print(f"[!] Error saving state file: {e}")


def fetch_csv_content():
    """Fetches CSV data from the live portal."""
    req = urllib.request.Request(
        CSV_URL,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.read().decode("utf-8", errors="ignore")


def parse_date(date_str):
    """Converts portal date strings to Tally YYYYMMDD format."""
    if not date_str:
        return datetime.now().strftime("%Y%m%d")
    date_str = date_str.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(date_str.split(".")[0], fmt)
            return dt.strftime("%Y%m%d")
        except Exception:
            continue
    return datetime.now().strftime("%Y%m%d")


def generate_voucher_xml(rows):
    """
    Constructs Tally XML Envelope with:
    1. Auto-creation of 'API BALANCE' Bank ledger.
    2. Auto-creation of Party Mobile Ledgers under Sundry Debtors.
    3. Journal Vouchers with Dr: API BALANCE and Cr: Customer Mobile.
    """
    xml_parts = [
        '<ENVELOPE>',
        '  <HEADER>',
        '    <TALLYREQUEST>Import Data</TALLYREQUEST>',
        '  </HEADER>',
        '  <BODY>',
        '    <IMPORTDATA>',
        '      <REQUESTDESC>',
        '        <REPORTNAME>Vouchers</REPORTNAME>',
        '        <STATICVARIABLES>',
        '          <SVCURRENTCOMPANY>##SVCURRENTCOMPANY</SVCURRENTCOMPANY>',
        '        </STATICVARIABLES>',
        '      </REQUESTDESC>',
        '      <REQUESTDATA>',
        '        <!-- 1. Master: API BALANCE Ledger -->',
        '        <TALLYMESSAGE xmlns:UDF="TallyUDF">',
        '          <LEDGER NAME="API BALANCE" ACTION="Create">',
        '            <NAME>API BALANCE</NAME>',
        '            <PARENT>Bank Accounts</PARENT>',
        '            <OPENINGBALANCE>0</OPENINGBALANCE>',
        '          </LEDGER>',
        '        </TALLYMESSAGE>'
    ]

    # Auto-create party mobile ledgers
    seen_mobiles = {}
    for r in rows:
        mob = (r.get("_MobileNo") or "").strip()
        outlet = (r.get("_OutletName") or "").strip()
        if mob and mob not in seen_mobiles:
            seen_mobiles[mob] = outlet

    for mob, outlet in seen_mobiles.items():
        safe_mob = escape(mob)
        safe_outlet = escape(outlet)
        xml_parts.append(f'''        <TALLYMESSAGE xmlns:UDF="TallyUDF">
          <LEDGER NAME="{safe_mob}" ACTION="Create">
            <NAME>{safe_mob}</NAME>
            <PARENT>Sundry Debtors</PARENT>
            <MAILINGNAME.LIST>
              <MAILINGNAME>{safe_outlet}</MAILINGNAME>
            </MAILINGNAME.LIST>
            <OPENINGBALANCE>0</OPENINGBALANCE>
          </LEDGER>
        </TALLYMESSAGE>''')

    # Create Journal Vouchers
    for r in rows:
        date_str = parse_date(r.get("_EntryDate", ""))
        amount_val = float(r.get("_Amount", 0) or 0)
        mobile_ledger = (r.get("_MobileNo") or "Unknown").strip()
        outlet_name = (r.get("_OutletName") or "").strip()
        description = (r.get("_Description") or "").strip()
        bank_ref = (r.get(" ") or "").strip()  # Column 4 contains UPI/UTR string
        remark = (r.get("Remark") or "").strip()

        # Build comprehensive narration with UTR & Remark
        narration_parts = []
        if description:
            narration_parts.append(description)
        if bank_ref:
            narration_parts.append(f"Ref: {bank_ref}")
        if remark:
            narration_parts.append(f"Bank: {remark}")
        narration = " | ".join(narration_parts)

        safe_ledger = escape(mobile_ledger)
        safe_narration = escape(narration)
        formatted_amount = f"{amount_val:.2f}"

        xml_parts.append(f'''        <TALLYMESSAGE xmlns:UDF="TallyUDF">
          <VOUCHER VCHTYPE="Journal" ACTION="Create" OBJVIEW="Accounting Voucher View">
            <DATE>{date_str}</DATE>
            <VOUCHERTYPENAME>Journal</VOUCHERTYPENAME>
            <PARTYLEDGERNAME>{safe_ledger}</PARTYLEDGERNAME>
            <NARRATION>{safe_narration}</NARRATION>
            <EFFECTIVEDATE>{date_str}</EFFECTIVEDATE>
            
            <!-- Dr. API BALANCE -->
            <ALLLEDGERENTRIES.LIST>
              <LEDGERNAME>API BALANCE</LEDGERNAME>
              <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
              <ISPARTYLEDGER>No</ISPARTYLEDGER>
              <AMOUNT>-{formatted_amount}</AMOUNT>
            </ALLLEDGERENTRIES.LIST>

            <!-- Cr. Customer Mobile Number Ledger -->
            <ALLLEDGERENTRIES.LIST>
              <LEDGERNAME>{safe_ledger}</LEDGERNAME>
              <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
              <ISPARTYLEDGER>Yes</ISPARTYLEDGER>
              <AMOUNT>{formatted_amount}</AMOUNT>
            </ALLLEDGERENTRIES.LIST>
          </VOUCHER>
        </TALLYMESSAGE>''')

    xml_parts.extend([
        '      </REQUESTDATA>',
        '    </IMPORTDATA>',
        '  </BODY>',
        '</ENVELOPE>'
    ])

    return "\n".join(xml_parts)


def post_to_tally(xml_content):
    """Sends XML payload directly to Tally HTTP port 9000."""
    try:
        xml_bytes = xml_content.encode("utf-8")
        req = urllib.request.Request(
            TALLY_URL,
            data=xml_bytes,
            headers={"Content-Type": "text/xml; charset=utf-8"}
        )
        with urllib.request.urlopen(req, timeout=2) as resp:
            resp_body = resp.read().decode("utf-8", errors="ignore")
            return True, resp_body
    except Exception as e:
        return False, str(e)


def sync_once(synced_records):
    """Executes a single fetch and sync cycle."""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now_str}] Fetching CSV data from portal...")

    try:
        raw_csv = fetch_csv_content()
    except Exception as e:
        print(f"[-] Error fetching CSV from server: {e}")
        return 0

    lines = [
        line.strip() for line in raw_csv.splitlines()
        if line.strip() and not line.strip().startswith('(')
    ]
    if not lines:
        print("[-] CSV is empty or has no data rows.")
        return 0

    reader = csv.DictReader(lines)
    new_rows = []

    for row in reader:
        entry_date = (row.get("_EntryDate") or "").strip()
        mobile = (row.get("_MobileNo") or "").strip()
        amount = (row.get("_Amount") or "").strip()
        desc = (row.get("_Description") or "").strip()
        ref = (row.get(" ") or "").strip()
        outlet = (row.get("_OutletName") or "").strip()

        # Unique fingerprint per transaction
        key_str = f"{entry_date}|{mobile}|{amount}|{desc}|{ref}|{outlet}"
        row_id = hashlib.md5(key_str.encode("utf-8")).hexdigest()

        if row_id not in synced_records:
            new_rows.append((row_id, row))

    if not new_rows:
        print(f"[✓] Everything up to date! No new transactions found. (Total synced: {len(synced_records)})")
        return 0

    print(f"[+] Found {len(new_rows)} NEW transaction(s) to import into Tally!")

    # Generate XML
    rows_data = [r[1] for r in new_rows]
    xml_content = generate_voucher_xml(rows_data)

    # Save to tally_import.xml
    with open(XML_OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(xml_content)
    print(f"[+] Generated XML: {XML_OUTPUT_FILE}")

    # Push to Tally port 9000
    tally_ok, tally_resp = post_to_tally(xml_content)
    if tally_ok:
        print(f"[✓] Successfully pushed {len(new_rows)} voucher(s) into Tally!")
        if "<CREATED>" in tally_resp:
            print("    Tally Response: Vouchers created successfully.")
        # Mark rows as synced only when successfully sent to Tally
        for row_id, _ in new_rows:
            synced_records.add(row_id)
        save_synced_state(synced_records)
    else:
        print(f"[!] Tally HTTP Server (port 9000) not responding ({tally_resp}).")
        print(f"    XML file saved at '{XML_OUTPUT_FILE}'.")
        print("    Vouchers not marked as synced yet - will retry on next sync.")

    return len(new_rows)


def main():
    print("=" * 65)
    print("      AMBIKA MULTI SERVICES - PORTAL DATA SYNC")
    print("=" * 65)
    print(f"CSV URL     : {CSV_URL}")
    print(f"Tally URL   : {TALLY_URL}")
    print(f"XML File    : {XML_OUTPUT_FILE}")
    print(f"State File  : {STATE_FILE}")
    print("=" * 65)

    synced_records = load_synced_state()
    print(f"Loaded {len(synced_records)} existing transaction records.")

    if "--once" in sys.argv:
        sync_once(synced_records)
        return

    print("\nStarting background sync loop (every 60s). Press Ctrl+C to stop.\n")
    try:
        while True:
            try:
                sync_once(synced_records)
            except Exception as e:
                print(f"[!] Sync error: {e}")

            for s in range(60, 0, -1):
                sys.stdout.write(f"\rNext sync in {s}s...   ")
                sys.stdout.flush()
                time.sleep(1)
            sys.stdout.write("\r                       \r")
    except KeyboardInterrupt:
        print("\nSync stopped by user.")


if __name__ == "__main__":
    main()
