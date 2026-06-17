"""
Test Supabase connection: insert, read, delete from searches table.
Run: python tests/test_supabase.py
"""
import os
import sys

# Load .env
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '..', 'backend', '.env'))

from supabase import create_client

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"

def run():
    url = os.environ.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_KEY", "")

    print(f"\nSupabase Connection Test")
    print(f"URL: {url}")
    print("─" * 50)

    if not url or not key:
        print(f"  {FAIL}  Missing SUPABASE_URL or SUPABASE_KEY in .env")
        sys.exit(1)

    try:
        client = create_client(url, key)
        print(f"  {PASS}  Client created")
    except Exception as e:
        print(f"  {FAIL}  Client creation: {e}")
        sys.exit(1)

    # Insert test row
    test_row = {"query": "_test_parceliq", "folio": "_test_0000", "address": "_test_addr"}
    try:
        r = client.table("searches").insert(test_row).execute()
        inserted_id = r.data[0]["id"]
        print(f"  {PASS}  Insert into searches (id={inserted_id[:8]}…)")
    except Exception as e:
        print(f"  {FAIL}  Insert: {e}")
        print(f"\n        → Run the CREATE TABLE SQL in Supabase SQL Editor first.")
        sys.exit(1)

    # Read it back
    try:
        r = client.table("searches").select("*").eq("query", "_test_parceliq").execute()
        assert len(r.data) >= 1
        print(f"  {PASS}  Read back from searches ({len(r.data)} row(s))")
    except Exception as e:
        print(f"  {FAIL}  Read: {e}")
        sys.exit(1)

    # Delete the test row
    try:
        client.table("searches").delete().eq("query", "_test_parceliq").execute()
        print(f"  {PASS}  Cleanup (test row deleted)")
    except Exception as e:
        print(f"  {FAIL}  Delete: {e}")

    # Check reports table exists
    try:
        r = client.table("reports").select("id").limit(1).execute()
        print(f"  {PASS}  reports table exists ({len(r.data)} cached row(s))")
    except Exception as e:
        print(f"  {FAIL}  reports table: {e}")
        sys.exit(1)

    print(f"\n  SUPABASE: {PASS}\n")

if __name__ == "__main__":
    run()
