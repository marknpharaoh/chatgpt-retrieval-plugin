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

def get_all_fields(base_id, table_name):
    """ Fetch all field names dynamically for a given table """
    airtable_url = f"https://api.airtable.com/v0/{base_id}/{table_name}?maxRecords=1"
    response = requests.get(airtable_url, headers=HEADERS)
    response.raise_for_status()
    records = response.json().get("records", [])
    if records:
        return list(records[0].get("fields", {}).keys())  # Extract field names
    return []

@app.post("/query", response_model=QueryResponse)
async def query(request: QueryRequest = Body(...)):
    """ Query all Airtable bases and tables dynamically across all fields """
    bases = get_all_bases()
    query_text = request.queries[0].query
    results = []

    for base_id, base_name in bases.items():
        tables = get_all_tables(base_id)

        for table_id, table_name in tables.items():
            fields = get_all_fields(base_id, table_name)  # Dynamically get fields

            if not fields:
                continue  # Skip tables with no fields

            # Constructing OR condition to search across all fields
            filter_formula = "OR(" + ",".join(
                [f"FIND('{query_text}', {{{field}}})" for field in fields]
            ) + ")"

            airtable_url = f"https://api.airtable.com/v0/{base_id}/{table_name}"
            
            try:
                response = requests.get(airtable_url, headers=HEADERS, params={"filterByFormula": filter_formula})
                response.raise_for_status()
                
                for rec in response.json().get("records", []):
                    results.append({
                        "id": rec["id"],
                        "table": table_name,
                        "fields": rec["fields"],  # Return all fields
                    })
            except requests.exceptions.RequestException as e:
                logger.error(f"Failed to query {base_name} -> {table_name}: {e}")

    return QueryResponse(results=results)


@app.on_event("startup")
async def startup():
    logger.info("Airtable-backed Retrieval Plugin started")


def start():
    uvicorn.run("server.main:app", host="0.0.0.0", port=8000, reload=True)
