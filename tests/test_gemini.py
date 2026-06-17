"""
Test Google Gemini AI narrative generation.
Run: python tests/test_gemini.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '..', 'backend', '.env'))

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"

SAMPLE_REPORT = {
    "property": {
        "address": "19931 ANGEL LN",
        "city": "Odessa",
        "zip": "34655",
        "owner": "PATRICIA ANNE SEVIGNY TRUSTEE",
        "acreage_gross": 4.71,
        "use": "VACANT RESIDENTIAL < 20 AC",
    },
    "zoning": {"nzone_desc": "Agricultural - Single-Family Conventional"},
    "buyer_insights": {
        "buildability": {"score": 84, "label": "Good"},
        "top_findings": [
            {
                "risk_level": "High",
                "title": "Mapped wetland signal found",
                "buyer_meaning": "Professional delineation required before building.",
            },
            {
                "risk_level": "Low",
                "title": "FEMA Zone X",
                "buyer_meaning": "Minimal flood hazard at sampled parcel area.",
            },
        ],
    },
}

def run():
    api_key = os.environ.get("GOOGLE_API_KEY", "")
    print(f"\nGemini AI Test")
    print("─" * 50)

    if not api_key or api_key == "paste_your_NEW_gemini_key_here":
        print(f"  {FAIL}  GOOGLE_API_KEY not set in .env")
        print("        → Get a key at aistudio.google.com/apikey")
        sys.exit(1)

    try:
        import google.generativeai as genai
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel("gemini-1.5-flash")
        print(f"  {PASS}  Gemini client configured")
    except Exception as e:
        print(f"  {FAIL}  Client setup: {e}")
        sys.exit(1)

    try:
        prop = SAMPLE_REPORT["property"]
        insights = SAMPLE_REPORT["buyer_insights"]
        score = insights["buildability"]
        findings_text = "\n".join(
            f"- [{f['risk_level']}] {f['title']}: {f['buyer_meaning']}"
            for f in insights["top_findings"]
        )

        prompt = f"""You are a property due diligence expert writing a plain-English buyer briefing.

PROPERTY: {prop['address']}, {prop.get('city')} FL {prop.get('zip')}
Owner: {prop['owner']}
Acreage: {prop['acreage_gross']} acres | Use: {prop['use']}
Zoning: {SAMPLE_REPORT['zoning']['nzone_desc']}
Buildability Score: {score['score']}/100 — {score['label']}

TOP FINDINGS:
{findings_text}

Write a buyer briefing with: SUMMARY, TOP RISKS, POSITIVE SIGNALS, WHAT TO DO NEXT, IMPORTANT.
Rules: plain English, first-time buyer level, under 400 words, never say property is safe."""

        response = model.generate_content(prompt)
        text = response.text.strip()

        if not text:
            print(f"  {FAIL}  Empty response from Gemini")
            sys.exit(1)

        print(f"  {PASS}  Narrative generated ({len(text)} chars)")
        print(f"\n  First 200 chars:\n  {text[:200]}…\n")
        print(f"  GEMINI: {PASS}\n")

    except Exception as e:
        print(f"  {FAIL}  Generation: {e}")
        sys.exit(1)

if __name__ == "__main__":
    run()
