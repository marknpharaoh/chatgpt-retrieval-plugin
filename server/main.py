import os
import requests
import uvicorn
from typing import Optional
from fastapi import FastAPI, HTTPException, Depends, Body
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.staticfiles import StaticFiles
from loguru import logger

from models.api import (
    DeleteRequest,
    DeleteResponse,
    QueryRequest,
    QueryResponse,
    UpsertRequest,
    UpsertResponse,
)
from models.models import DocumentMetadata, Source

# Authentication setup
bearer_scheme = HTTPBearer()
BEARER_TOKEN = os.getenv("BEARER_TOKEN")
AIRTABLE_PAT = os.getenv("AIRTABLE_PAT")

assert BEARER_TOKEN, "BEARER_TOKEN is missing from environment variables"
assert AIRTABLE_PAT, "AIRTABLE_PAT is missing from environment variables"

# Get base IDs and table names from environment
AIRTABLE_BASE_IDS = os.getenv("AIRTABLE_BASE_IDS", "").split(",")
AIRTABLE_TABLES = os.getenv("AIRTABLE_TABLES", "").split(",")

HEADERS = {
    "Authorization": f"Bearer {AIRTABLE_PAT}",
    "Content-Type": "application/json",
}

# FastAPI App Setup
app = FastAPI(dependencies=[Depends(lambda credentials: validate_token(credentials))])
app.mount("/.well-known", StaticFiles(directory=".well-known"), name="static")


def validate_token(credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme)):
    """Validate API requests using Bearer Token"""
    if credentials.scheme != "Bearer" or credentials.credentials != BEARER_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid or missing token")
    return credentials


def fetch_airtable_data():
    """Fetches all records from all Airtable bases and tables"""
    results = []
    for base_id in AIRTABLE_BASE_IDS:
        for table_name in AIRTABLE_TABLES:
            url = f"https://api.airtable.com/v0/{base_id}/{table_name}"
            try:
                response = requests.get(url, headers=HEADERS)
                response.raise_for_status()
                records = response.json().get("records", [])
                results.extend(records)
            except requests.exceptions.RequestException as e:
                logger.error(f"Error fetching {table_name} from base {base_id}: {e}")

    return results


@app.post("/upsert", response_model=UpsertResponse)
async def upsert(request: UpsertRequest = Body(...)):
    """Upserts records into all Airtable tables"""
    results = []

    for base_id in AIRTABLE_BASE_IDS:
        for table_name in AIRTABLE_TABLES:
            airtable_url = f"https://api.airtable.com/v0/{base_id}/{table_name}"
            records = [{"fields": {"DocumentID": doc.id, "Text": doc.text, "Metadata": str(doc.metadata.dict())}} for doc in request.documents]

            try:
                response = requests.post(airtable_url, json={"records": records}, headers=HEADERS)
                response.raise_for_status()
                results.extend([r["id"] for r in response.json().get("records", [])])
            except requests.exceptions.RequestException as e:
                logger.error(f"Failed to upsert into {table_name} in {base_id}: {e}")

    return UpsertResponse(ids=results)


@app.post("/query", response_model=QueryResponse)
async def query(request: QueryRequest = Body(...)):
    """Queries records from all Airtable bases and tables"""
    query_text = request.queries[0].query
    results = []

    for base_id in AIRTABLE_BASE_IDS:
        for table_name in AIRTABLE_TABLES:
            airtable_url = f"https://api.airtable.com/v0/{base_id}/{table_name}"
            params = {"filterByFormula": f"FIND('{query_text}', Text)"}

            try:
                response = requests.get(airtable_url, headers=HEADERS, params=params)
                response.raise_for_status()
                for rec in response.json().get("records", []):
                    results.append({
                        "id": rec["id"],
                        "text": rec["fields"].get("Text", ""),
                        "metadata": rec["fields"].get("Metadata", {}),
                    })
            except requests.exceptions.RequestException as e:
                logger.error(f"Failed to query {table_name} in {base_id}: {e}")

    return QueryResponse(results=results)


@app.delete("/delete", response_model=DeleteResponse)
async def delete(request: DeleteRequest = Body(...)):
    """Deletes records from all Airtable bases and tables"""
    if not request.ids:
        raise HTTPException(status_code=400, detail="Must provide record IDs to delete")

    deleted = 0

    for base_id in AIRTABLE_BASE_IDS:
        for table_name in AIRTABLE_TABLES:
            for record_id in request.ids:
                airtable_url = f"https://api.airtable.com/v0/{base_id}/{table_name}/{record_id}"
                try:
                    response = requests.delete(airtable_url, headers=HEADERS)
                    response.raise_for_status()
                    deleted += 1
                except requests.exceptions.RequestException as e:
                    logger.error(f"Failed to delete {record_id} from {table_name} in {base_id}: {e}")

    return DeleteResponse(success=deleted > 0)


@app.on_event("startup")
async def startup():
    logger.info("Airtable-backed Retrieval Plugin started")


def start():
    uvicorn.run("server.main:app", host="0.0.0.0", port=8000, reload=True)
