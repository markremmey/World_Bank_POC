import azure.functions as func
import azure.durable_functions as df

from azure.ai.documentintelligence import DocumentIntelligenceClient
from azure.ai.documentintelligence.models import AnalyzeResult, AnalyzeDocumentRequest

from activities import getBlobContent, runDocIntel, callAoai, writeToBlob
from configuration import Configuration

from pipelineUtils.prompts import load_prompts
from pipelineUtils.blob_functions import get_blob_content, write_to_blob, BlobMetadata
from pipelineUtils.azure_openai import run_prompt

config = Configuration()

NEXT_STAGE = config.get_value("NEXT_STAGE")

app = df.DFApp(http_auth_level=func.AuthLevel.ANONYMOUS)

import logging

# Blob-triggered starter
@app.function_name(name="start_orchestrator_on_blob")
@app.blob_trigger(
    arg_name="blob",
    path="bronze/{name}",
    connection="DataStorage",
)
@app.durable_client_input(client_name="client")
async def start_orchestrator_on_blob(
    blob: func.InputStream,
    client: df.DurableOrchestrationClient,
):
    logging.info(f"Blob Received: {blob}") 
    logging.info(f"path: {blob.name}")
    logging.info(f"Size: {blob.length} bytes")
    logging.info(f"URI: {blob.uri}")   

    blob_metadata = BlobMetadata(
        name=blob.name,          
        url=blob.uri,            
        container="bronze",
    ).to_dict()
    
    # ✅ Add UI metadata (date, document_type) if available
    if hasattr(blob, "metadata") and blob.metadata:
        if "date" in blob.metadata:
            blob_metadata["date"] = blob.metadata["date"]
        if "document_type" in blob.metadata:
            blob_metadata["document_type"] = blob.metadata["document_type"]
        if "persona" in blob.metadata:
            blob_metadata["persona"] = blob.metadata["persona"]
        if "supplier_name" in blob.metadata:
                blob_metadata["supplier_name"] = blob.metadata["supplier_name"]
        if "contract_number" in blob.metadata:
            blob_metadata["contract_number"] = blob.metadata["contract_number"]
        if "amount" in blob.metadata:
            blob_metadata["amount"] = blob.metadata["amount"]


    # ✅ If it’s a PDF, set max_pages to 5
    if blob_metadata["name"].lower().endswith(".pdf"):
        blob_metadata["max_pages"] = 5
        logging.info(f"PDF detected, restricting to first 5 pages: {blob_metadata['name']}")

    logging.info(f"Blob Metadata: {blob_metadata}")
    instance_id = await client.start_new("orchestrator", client_input=[blob_metadata])
    logging.info(f"Started orchestration {instance_id} for blob {blob.name}")


# An HTTP-triggered function with a Durable Functions client binding
@app.route(route="client")
@app.durable_client_input(client_name="client")
async def http_start(req: func.HttpRequest, client):
    try:
        body = req.get_json()
    except ValueError:
        return func.HttpResponse("Invalid JSON.", status_code=400)

    logging.info(f"Request body: {body}")
    logging.info(f"config.get_value('DATA_STORAGE_ACCOUNT_NAME'): {config.get_value('DATA_STORAGE_ACCOUNT_NAME')}")

    blobs = body.get("blobs")
    if not isinstance(blobs, list) or not blobs:
        return func.HttpResponse("Invalid request: 'blobs' must be a non-empty array.", status_code=400)

    required = ("name", "url", "container")
    for i, b in enumerate(blobs):
        if not isinstance(b, dict):
            return func.HttpResponse(f"Invalid request: blobs[{i}] must be an object.", status_code=400)
        if any(k not in b or not isinstance(b[k], str) or not b[k].strip() for k in required):
            return func.HttpResponse(f"Invalid request: blobs[{i}] must contain non-empty string keys {required}.", status_code=400)

        # ✅ If it’s a PDF, set max_pages to 5
        if b["name"].lower().endswith(".pdf"):
            b["max_pages"] = 5
            logging.info(f"PDF detected in HTTP request, restricting to first 5 pages: {b['name']}")

    instance_id = await client.start_new('orchestrator', client_input=blobs)
    logging.info(f"Started orchestration with Batch ID = '{instance_id}'.")

    response = client.create_check_status_response(req, instance_id)
    return response


# Orchestrator
@app.function_name(name="orchestrator")
@app.orchestration_trigger(context_name="context")
def run(context):
    input_data = context.get_input()
    logging.info(f"Context {context}")
    logging.info(f"Input data: {input_data}")
  
    sub_tasks = []

    for blob_metadata in input_data:
        logging.info(f"Calling sub orchestrator for blob: {blob_metadata}")
        sub_tasks.append(context.call_sub_orchestrator("ProcessBlob", blob_metadata))

    results = yield context.task_all(sub_tasks)
    logging.info(f"Results: {results}")
    return results


# Sub orchestrator
# @app.function_name(name="ProcessBlob")
# @app.orchestration_trigger(context_name="context")
# def process_blob(context):
#     blob_metadata = context.get_input()
#     sub_orchestration_id = context.instance_id 
#     logging.info(f"Process Blob sub Orchestration - Processing blob_metadata: {blob_metadata} with sub orchestration id: {sub_orchestration_id}")

#     text_result = yield context.call_activity("runDocIntel", blob_metadata)

#     call_aoai_input = {
#         "text_result": text_result,
#         "instance_id": sub_orchestration_id , 
#         "blob_metadata": blob_metadata  # <-- pass it here
#     }

#     json_str = yield context.call_activity("callAoai", call_aoai_input)
  
#     task_result = yield context.call_activity(
#         "writeToBlob", 
#         {
#             "json_str": json_str, 
#             "blob_name": blob_metadata["name"]
#         }
#     )
#     return {
#         "blob": blob_metadata,
#         "text_result": text_result,
#         "task_result": task_result
#     }   

# Sub orchestrator with Silver → Pre-Gold → Gold
@app.function_name(name="ProcessBlob")
@app.orchestration_trigger(context_name="context")
def process_blob(context):
    blob_metadata = context.get_input()
    sub_orchestration_id = context.instance_id 

    logging.info(f"Processing blob_metadata: {blob_metadata} (Sub Orchestration ID: {sub_orchestration_id})")

    # Step 1: Silver AOAI → trim PDF to first 5 pages
    blob_metadata_silver = dict(blob_metadata)
    if blob_metadata_silver["name"].lower().endswith(".pdf"):
        blob_metadata_silver["max_pages"] = 5
    
    # Step 1: Extract text using Document Intelligence
    # text_result = yield context.call_activity("runDocIntel", blob_metadata) 
    text_result_silver = yield context.call_activity("runDocIntel", blob_metadata_silver)
    
    
    # Step 2: Silver AOAI
    call_aoai_input_silver = {
        # "text_result": text_result,
        "text_result": text_result_silver,  # use filtered result
        "instance_id": sub_orchestration_id, 
        "prompt_file": "prompts_silver.yaml",
        "blob_metadata": blob_metadata
    }
    
    json_str_silver = yield context.call_activity("callAoai", call_aoai_input_silver)
    task_result_silver = yield context.call_activity(
        "writeToBlob", 
        {"json_str": json_str_silver, "blob_name": blob_metadata["name"]}
    )

    blob_metadata_full = dict(blob_metadata)
    if "max_pages" in blob_metadata_full:
        del blob_metadata_full["max_pages"]  # remove trimming for full processing
        
    text_result = yield context.call_activity("runDocIntel", blob_metadata_full)
    
    # Step 3: Pre-Gold AOAI
    call_aoai_input_pre_gold = {
        "text_result": text_result,
        "instance_id": sub_orchestration_id,
        "blob_metadata": blob_metadata,
        "prompt_file": "prompts_pre_gold.yaml",
        "target_folder": "pre-gold"
    }
    json_str_pre_gold = yield context.call_activity("callAoai", call_aoai_input_pre_gold)
    task_result_pre_gold = yield context.call_activity(
        "writeToBlob", 
        {"json_str": json_str_pre_gold, "blob_name": blob_metadata["name"], "target_folder": "pre-gold"}
    )

    # Step 4: Gold AOAI
    call_aoai_input_gold = {
        "text_result": text_result,
        "instance_id": sub_orchestration_id,
        "blob_metadata": blob_metadata,
        "prompt_file": "prompts_gold.yaml",
        "target_folder": "gold"
    }
    json_str_gold = yield context.call_activity("callAoai", call_aoai_input_gold)
    task_result_gold = yield context.call_activity(
        "writeToBlob",
        {"json_str": json_str_gold, "blob_name": blob_metadata["name"], "target_folder": "gold"}
    )

    return {
        "blob": blob_metadata,
        "text_result": text_result,
        "silver_task_result": task_result_silver,
        "pre_gold_task_result": task_result_pre_gold,
        "gold_task_result": task_result_gold
    }


app.register_functions(getBlobContent.bp)
app.register_functions(runDocIntel.bp)
app.register_functions(callAoai.bp)
app.register_functions(writeToBlob.bp)
