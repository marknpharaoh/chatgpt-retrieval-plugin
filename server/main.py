import os
import requests
from typing import Optional
import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, Depends, Body, UploadFile
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

# Security for API requests
bearer_scheme = HTTPBearer()
BEARER_TOKEN = os.environ.get("BEARER_TOKEN")
AIRTABLE_PAT = os.environ.get("AIRTABLE_PAT")

assert BEARER_TOKEN is not None, "BEARER_TOKEN is not set"
assert AIRTABLE_PAT is not None, "AIRTABLE_PAT is not set"

AIRTABLE_META_URL = "https://api.airtable.com/v0/meta/bases"

HEADERS = {
    "Authorization": f"Bearer {AIRTABLE_PAT}",
    "Content-Type": "application/json"
}

def validate_token(credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme)):
    if credentials.scheme != "Bearer" or credentials.credentials != BEARER_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid or missing token")
    return credentials


app = FastAPI(dependencies=[Depends(validate_token)])
app.mount("/.well-known", StaticFiles(directory=".well-known"), name="static")


def get_all_bases():
    """ Fetch all bases the API key has access to """
    response = requests.get(AIRTABLE_META_URL, headers=HEADERS)
    response.raise_for_status()
    return {base["id"]: base["name"] for base in response.json().get("bases", [])}


def get_all_tables(base_id):
    """ Fetch all tables in a given base """
    tables_url = f"https://api.airtable.com/v0/meta/bases/{base_id}/tables"
    response = requests.get(tables_url, headers=HEADERS)
    response.raise_for_status()
    return {table["id"]: table["name"] for table in response.json().get("tables", [])}


@app.post("/upsert", response_model=UpsertResponse)
async def upsert(request: UpsertRequest = Body(...)):
    """ Insert or update records in all Airtable tables """
    bases = get_all_bases()
    results = []

    for base_id, base_name in bases.items():
        tables = get_all_tables(base_id)

        for table_id, table_name in tables.items():
            airtable_url = f"https://api.airtable.com/v0/{base_id}/{table_name}"

            records = []
            for doc in request.documents:
                record = {
                    "fields": {
                        "DocumentID": doc.id,
                        "Text": doc.text,
                        "Metadata": str(doc.metadata.dict())
                    }
                }
                records.append(record)

            try:
                response = requests.post(airtable_url, json={"records": records}, headers=HEADERS)
                response.raise_for_status()
                results.extend([r["id"] for r in response.json().get("records", [])])
            except requests.exceptions.RequestException as e:
                logger.error(f"Failed to upsert into {base_name} -> {table_name}: {e}")

    return UpsertResponse(ids=results)


@app.post("/query", response_model=QueryResponse)
async def query(request: QueryRequest = Body(...)):
    """ Query all Airtable bases and tables """
    bases = get_all_bases()
    query_text = request.queries[0].query
    results = []

    for base_id, base_name in bases.items():
        tables = get_all_tables(base_id)

        for table_id, table_name in tables.items():
            airtable_url = f"https://api.airtable.com/v0/{base_id}/{table_name}"
            
            # 🔹 Change "Text" to the correct field name in your Airtable
            FIELD_TO_SEARCH = "YourActualFieldNameHere"

            try:
                response = requests.get(
                    airtable_url,
                    headers=HEADERS,
                    params={"filterByFormula": f"FIND('{query_text}', {{{FIELD_TO_SEARCH}}})"}
                )
                response.raise_for_status()
                
                for rec in response.json().get("records", []):
                    results.append({
                        "id": rec["id"],
                        "text": rec["fields"].get(FIELD_TO_SEARCH, ""),
                        "metadata": rec["fields"].get("Metadata", {})
                    })
            except requests.exceptions.RequestException as e:
                logger.error(f"Failed to query {base_name} -> {table_name}: {e}")

    return QueryResponse(results=results)


@app.delete("/delete", response_model=DeleteResponse)
async def delete(request: DeleteRequest = Body(...)):
    """ Delete records from all Airtable bases/tables """
    if not request.ids:
        raise HTTPException(status_code=400, detail="Must provide record IDs to delete")

    bases = get_all_bases()
    deleted = 0

    for base_id, base_name in bases.items():
        tables = get_all_tables(base_id)

        for table_id, table_name in tables.items():
            for record_id in request.ids:
                airtable_url = f"https://api.airtable.com/v0/{base_id}/{table_name}/{record_id}"
                
                try:
                    response = requests.delete(airtable_url, headers=HEADERS)
                    response.raise_for_status()
                    deleted += 1
                except requests.exceptions.RequestException as e:
                    logger.error(f"Failed to delete from {base_name} -> {table_name}: {e}")

    return DeleteResponse(success=deleted > 0)


@app.on_event("startup")
async def startup():
    logger.info("Airtable-backed Retrieval Plugin started")


def start():
    uvicorn.run("server.main:app", host="0.0.0.0", port=8000, reload=True)
