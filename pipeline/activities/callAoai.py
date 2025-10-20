import azure.durable_functions as df
import logging
from pipelineUtils.prompts import load_prompts, load_prompts_from_blob  
from pipelineUtils.azure_openai import run_prompt
from openai import OpenAI
from configuration import Configuration

config = Configuration()

OPENAI_API_KEY = config.get_value("AZURE_OPENAI_API_KEY")

# Initialize Azure OpenAI client using the new v1 endpoint
# aoai_client = OpenAI(
#     api_key=os.getenv("AZURE_OPENAI_API_KEY"),
#     base_url="https://YOUR-RESOURCE-NAME.openai.azure.com/openai/v1/"
# )

name = "callAoai"
bp = df.Blueprint()

@bp.function_name(name)
@bp.activity_trigger(input_name="inputData")
def run(inputData: dict):
    """
    Calls the Azure OpenAI service with the provided text result or base64 PDF.
    """
    try:
        text_result = inputData.get('text_result')
        instance_id = inputData.get('instance_id')
        blob_metadata = inputData.get("blob_metadata", {})  
        prompt_file = inputData.get("prompt_file")  # optional
        input_type = inputData.get("input_type", "docintel")  # docintel or pdf_base64

        # Load prompt
        if prompt_file:
            prompt_json = load_prompts_from_blob(prompt_file)
        else:
            prompt_json = load_prompts()

        system_prompt = prompt_json['system_prompt']

        # Determine user prompt based on input type

        if input_type == "pdf_base64":
            try:
                # Initialize Azure OpenAI client
                aoai_client = OpenAI(
                    api_key=os.getenv("AZURE_OPENAI_API_KEY"),  # Stored in Function App settings
                    base_url="https://openai-wb.openai.azure.com/openai/v1/"
                )

                logging.info(f"Processing base64 PDF for instance: {instance_id}")

                # Make the SDK call using chat completions
                response = aoai_client.chat.completions.create(
                    model="gpt-4o-mini_WB",  # Deployment name from Azure AI Foundry
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "You are a document analysis assistant. The input below is a base64-encoded PDF file. "
                                "Extract and summarize the content meaningfully.\n\n" + prompt_json['system_prompt']
                            )
                        },
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "file",  # ✅ Correct type for Azure OpenAI
                                    "file": {
                                        "data": text_result,  # Base64 string from inputData
                                        "mime_type": "application/pdf"
                                    }
                                },
                                {
                                    "type": "text",  # ✅ Correct type for text input
                                    "text": prompt_json['user_prompt']
                                }
                            ]
                        }
                    ]
                )

                # Extract the model's response text
                response_content = response.choices[0].message.content

                # Clean up JSON if wrapped in markdown
                if response_content and response_content.startswith('```json') and response_content.endswith('```'):
                    response_content = response_content.strip('`').replace('json', '', 1).strip()

                return response_content

            except Exception as e:
                logging.error(f"Error processing PDF base64 input for instance {instance_id}: {e}")
                return None
    
        else:
            # Build metadata section for normal DocIntel text
            metadata_text = "\n".join([
                f"Date: {blob_metadata.get('date', '')}",
                f"Document Type: {blob_metadata.get('document_type', '')}",
                f"supplier_name: {blob_metadata.get('supplier_name', '')}",
                f"contract_number: {blob_metadata.get('contract_number', '')}",
                f"amount: {blob_metadata.get('amount', '')}"
            ])

            full_user_prompt = (
                f"{prompt_json['user_prompt']}\n\n"
                f"Metadata for verification:\n{metadata_text}\n\n"
                f"Document Text:\n\"\"\"\n{text_result}\n\"\"\""
            )

        logging.info(f"callAoai.py: Full user prompt: {full_user_prompt}")
        logging.info(f"callAoai.py: System prompt: {system_prompt}")

        response_content = run_prompt(instance_id, system_prompt, full_user_prompt)

        if response_content and response_content.startswith('```json') and response_content.endswith('```'):
            response_content = response_content.strip('`').replace('json', '', 1).strip()

        return response_content

    except Exception as e:
        logging.error(f"Error processing Sub Orchestration (callAoai): {instance_id}: {e}")
        return None
