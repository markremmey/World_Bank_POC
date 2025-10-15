import azure.durable_functions as df

import logging
import os
from pipelineUtils.prompts import load_prompts, load_prompts_from_blob  
from pipelineUtils.blob_functions import get_blob_content, write_to_blob
from pipelineUtils.azure_openai import run_prompt
import json

name = "callAoai"
bp = df.Blueprint()

# @bp.function_name(name)
# @bp.activity_trigger(input_name="inputData")
# def run(inputData: dict):
#     """
#     Calls the Azure OpenAI service with the provided text result.
    
#     Args:
#         text_result (str): The text result to be processed by the Azure OpenAI service.
    
#     Returns:
#         str: The response from the Azure OpenAI service.
#     """
#     try:
#       # Load the prompt
#       text_result = inputData.get('text_result')
#       instance_id = inputData.get('instance_id')
#       blob_metadata = inputData.get("blob_metadata", {})  # <-- metadata passed from process_blob
      
#       # Extract optional metadata
#       date = blob_metadata.get("date", "N/A")
#       document_type = blob_metadata.get("document_type", "N/A")
#       persona = blob_metadata.get("persona", "N/A")
      
#       prompt_json = load_prompts()
      
#       # full_user_prompt = prompt_json['user_prompt'] + "\n\n" + text_result
#       full_user_prompt = f"{prompt_json['user_prompt']}\n\n- Date: {date}\n- Document Type: {document_type}\n- Persona: {persona}\n\nText:\n{text_result}"

 
#       # Call the Azure OpenAI service
#       logging.info(f"callAoai.py: Full user prompt: {full_user_prompt}")
#       response_content = run_prompt(prompt_json['system_prompt'], full_user_prompt)
#       if response_content.startswith('```json') and response_content.endswith('```'):
#         response_content = response_content.strip('`')
#         response_content = response_content.replace('json', '', 1).strip()
      
#       json_str = response_content
#       # Return the response
#       return json_str
  
#     except Exception as e:
#         logging.error(f"Error processing Sub Orchestration (callAoai): {instance_id}: {e}")
#         return None

@bp.function_name(name)
@bp.activity_trigger(input_name="inputData")
def run(inputData: dict):
    """
    Calls the Azure OpenAI service with the provided text result.
    
    Args:
        text_result (str): The text result to be processed by the Azure OpenAI service.
    
    Returns:
        str: The response from the Azure OpenAI service.
    """
    try:
        text_result = inputData.get('text_result')
        instance_id = inputData.get('instance_id')
        blob_metadata = inputData.get("blob_metadata", {})  
        prompt_file = inputData.get("prompt_file")  # <-- optional prompt file

        # Extract metadata
        date = blob_metadata.get("date", "N/A")
        document_type = blob_metadata.get("document_type", "N/A")
        persona = blob_metadata.get("persona", "N/A")
        supplier_name = blob_metadata.get("supplier_name", "N/A")
        contract_number = blob_metadata.get("contract_number", "N/A")
        amount = blob_metadata.get("amount", "N/A")

        # Load prompt
        if prompt_file:
            prompt_json = load_prompts_from_blob(prompt_file)
        else:
            prompt_json = load_prompts()
            
        # Build metadata section for prompt
        metadata_text = "\n".join([
            f"Date: {date if date else ''}",
            f"Document Type: {document_type if document_type else ''}",
            f"Persona: {persona if persona else ''}", 
            f"supplier_name: {supplier_name if supplier_name else ''}",
            f"contract_number: {contract_number if contract_number else ''}",
            f"amount: {amount if amount else ''}"
        ])

        # full_user_prompt = (
        #     f"{prompt_json['user_prompt']}\n\n"
        #     f"- Date: {date}\n"
        #     f"- Document Type: {document_type}\n"
        #     f"- Persona: {persona}\n\n"
        #     f"Text:\n{text_result}"
        # )
        
        # Construct full user prompt
        full_user_prompt = (
            f"{prompt_json['user_prompt']}\n\n"
            f"Metadata for verification:\n{metadata_text}\n\n"
            f"Document Text:\n\"\"\"\n{text_result}\n\"\"\""
        )

        logging.info(f"callAoai.py: Full user prompt: {full_user_prompt}")
        logging.info(f"callAoai.py: System prompt: {prompt_json['system_prompt']}")
        # response_content = run_prompt(prompt_json['system_prompt'], full_user_prompt)
        response_content = run_prompt(instance_id, prompt_json['system_prompt'], full_user_prompt)

        if response_content.startswith('```json') and response_content.endswith('```'):
            response_content = response_content.strip('`').replace('json', '', 1).strip()

        return response_content

    except Exception as e:
        logging.error(f"Error processing Sub Orchestration (callAoai): {instance_id}: {e}")
        return None
