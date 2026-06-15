# ParcelIQ — Backend MVP

Property due diligence platform for Hillsborough County, FL.
Turns public records into a buyer decision report — not a Zillow clone.

## Structure

```
parceliq/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI app entrypoint
│   │   ├── core/
│   │   │   ├── config.py        # settings, source URLs
│   │   │   └── insight_engine.py # rules -> buyer_insights.json
│   │   ├── collectors/
│   │   │   ├── parcel.py        # HCPA parcel attributes + geometry (01, 04)
│   │   │   ├── flood.py         # FEMA NFHL (02)
│   │   │   ├── wetlands.py       # USFWS NWI (03)
│   │   │   ├── zoning.py         # Hillsborough zoning + FLU (05, 06)
│   │   │   ├── utilities.py      # water/sewer service area (07A, 07B)
│   │   │   ├── roads.py          # road authority (08)
│   │   │   ├── permits.py        # issued permits + CO (09, 10)
│   │   │   ├── evac.py           # evacuation zone (11)
│   │   │   └── address_point.py  # official address point (12)
│   │   ├── models/
│   │   │   └── schema.py         # pydantic models matching 06_DATA_MODEL_SCHEMA.json
│   │   └── routers/
│   │       ├── search.py         # parcel search endpoint
│   │       └── report.py         # full report generation endpoint
│   ├── data/
│   │   └── manual_sources.json   # placeholder for manual-source entries (13-18)
│   ├── requirements.txt
│   └── .env.example
└── frontend/
    └── parceliq_v5.html           # existing UI (copy of uploaded file)
```

## Setup

```bash
cd backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

## Endpoints (MVP)

- `GET /api/search?q=<address|folio|strap|pin>` — search HCPA for candidate parcels
- `GET /api/report/{folio}` — runs full pipeline: collectors -> normalize -> insight engine -> returns property_report.json + buyer_insights.json

## Source coverage (this build)

| ID | Source | Collector | Status |
|----|--------|-----------|--------|
| 01 | HCPA Parcel Attributes | collectors/parcel.py | API |
| 02 | FEMA Flood | collectors/flood.py | API |
| 03 | NWI Wetlands | collectors/wetlands.py | API |
| 04 | HCPA Parcel Geometry | collectors/parcel.py | API |
| 05 | Zoning | collectors/zoning.py | API |
| 06 | Future Land Use | collectors/zoning.py | API |
| 07A/B | Water/Sewer Service Area | collectors/utilities.py | API |
| 08 | Road Authority | collectors/roads.py | Partial API |
| 09/10 | Permits / CO | collectors/permits.py | API, match often empty |
| 11 | Evacuation Zone | collectors/evac.py | API |
| 12 | Address Point | collectors/address_point.py | API |
| 13-18 | Tax, Deeds, Code, Septic, Well, EPC | data/manual_sources.json | Manual — not automated yet |

Manual sources (13-18) are surfaced in the report as `"status": "manual_verification_required"` per the master prompt rule: never fake results.

## Next steps (per 07_IMPLEMENTATION_PLAN.md)

- Playwright collectors for manual sources (tax collector, clerk deeds, code enforcement, DOH eBridge)
- Shapely/GeoPandas polygon overlap (wetlands/flood/zoning vs parcel boundary)
- Road-parcel proximity for legal access verification
- Comps module
- PostgreSQL/PostGIS persistence layer (this MVP runs collectors live, no DB yet)
