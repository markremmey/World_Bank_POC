import azure.durable_functions as df
import logging
from pipelineUtils.blob_functions import list_blobs, get_blob_content, write_to_blob
from pipelineUtils import get_month_date
from azure.identity import DefaultAzureCredential
from azure.ai.documentintelligence import DocumentIntelligenceClient
from azure.ai.documentintelligence.models import AnalyzeResult
import base64
import json
import os
import io
import requests
from PyPDF2 import PdfReader, PdfWriter  # 👈 for PDF trimming

from configuration import Configuration
config = Configuration()

endpoint = config.get_value("AIMULTISERVICES_ENDPOINT")

name = "runDocIntel"
bp = df.Blueprint()

def normalize_blob_name(container: str, raw_name: str) -> str:
    if raw_name.startswith(container + "/"):
        return raw_name[len(container) + 1:]
    return raw_name

@bp.function_name(name)
@bp.activity_trigger(input_name="blobObj")
def extract_text_from_blob(blobObj: dict):
    logging.info(f"[runDocIntel] raw input type={type(blobObj)} preview={repr(blobObj)[:200]}")

    if isinstance(blobObj, str):
        try:
            blobObj = json.loads(blobObj)
        except Exception as e:
            raise TypeError(f"runDocIntel expected dict or JSON string; got str that failed JSON decode: {e}")
    if not isinstance(blobObj, dict):
        raise TypeError(f"runDocIntel expected dict; got {type(blobObj)}")
    
    max_pages = blobObj.get("max_pages", None)  # ✅ new parameter

    try:
        client = DocumentIntelligenceClient(endpoint=endpoint, credential=config.credential)
        logging.info(f"BlobObj: {blobObj}")

        blob_name = normalize_blob_name(blobObj["container"], blobObj["name"])
        logging.info(f"Normalized Blob Name: {blob_name}")

        blob_content = get_blob_content(
            container_name=blobObj["container"],
            blob_path=blob_name
        )

        # If it's a PDF, restrict to first 5 pages
        if blob_name.lower().endswith(".pdf"):
            # logging.info("PDF detected - trimming to first 5 pages")
            pdf_reader = PdfReader(io.BytesIO(blob_content))
            pdf_writer = PdfWriter()

            # for i in range(min(5, len(pdf_reader.pages))):
            pages_to_use = min(max_pages, len(pdf_reader.pages)) if max_pages else len(pdf_reader.pages)
            # for i in range(len(pdf_reader.pages)):
            for i in range(pages_to_use):
                pdf_writer.add_page(pdf_reader.pages[i])
                
            # ✅ Log the original and trimmed page counts
            logging.info(f"Original PDF pages: {len(pdf_reader.pages)}")
            logging.info(f"Trimmed PDF pages: {len(pdf_writer.pages)}")

            output_stream = io.BytesIO()
            pdf_writer.write(output_stream)
            output_stream.seek(0)
            blob_content = output_stream.read()

        logging.info(f"Starting analyze document: first {100} bytes: {blob_content[:100]}")

        # poller = client.begin_analyze_document(
        #     "prebuilt-read", blob_content
        # )
        
        # poller = client.begin_analyze_document(
        #     model_id="prebuilt-read",
        #     body=blob_content,
        #     content_type="application/pdf" if blob_name.lower().endswith(".pdf") else "application/octet-stream"
        # )

        # result: AnalyzeResult = poller.result()
        # logging.info(f"Analyze document completed with status: {result}")
        # if result.paragraphs:    
        #     paragraphs = "\n".join([paragraph.content for paragraph in result.paragraphs])            
        
        # return paragraphs
        
        
        poller = client.begin_analyze_document(
            model_id="prebuilt-read",
            body=blob_content,
            content_type="application/pdf" if blob_name.lower().endswith(".pdf") else "application/octet-stream"
        )

        result: AnalyzeResult = poller.result()
        logging.info(f"Analyze document completed with status: {poller.status()}")

        paragraphs = ""
        if result.paragraphs:
            paragraph_lines = []
            for p in result.paragraphs:
                page_number = p.bounding_regions[0].page_number if p.bounding_regions else "Unknown"
                paragraph_lines.append(f"[Page {page_number}] {p.content}")
            paragraphs = "\n".join(paragraph_lines)

        return paragraphs

                
    except Exception as e:
        logging.error(f"Error processing {blobObj}: {e}")
        return None