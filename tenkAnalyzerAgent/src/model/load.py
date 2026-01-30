import os
from strands.models import BedrockModel

# Uses global inference profile for Claude Sonnet 4.5
# https://docs.aws.amazon.com/bedrock/latest/userguide/inference-profiles-support.html
MODEL_ID = "global.anthropic.claude-sonnet-4-5-20250929-v1:0"

def load_model() -> BedrockModel:
    """
    Get Bedrock model client with optional guardrail configuration.
    Uses IAM authentication via the execution role.
    Guardrails provide AI safety controls for:
    - Content filtering (harmful content, prompt attacks)
    - PII protection (anonymization of sensitive data)
    - Topic restrictions (keep discussions on-topic)
    - Contextual grounding (reduce hallucinations)
    """
    # Get guardrail config from environment (set by CDK)
    guardrail_id = os.getenv("GUARDRAIL_ID")
    guardrail_version = os.getenv("GUARDRAIL_VERSION", "DRAFT")
    
    # Build model config with optional guardrails
    model_config = {
        "model_id": MODEL_ID,
    }
    
    # Add guardrail config if available
    if guardrail_id:
        model_config.update({
            "guardrail_id": guardrail_id,
            "guardrail_version": guardrail_version,
            "guardrail_trace": "enabled",  # Enable for debugging
        })
    
    return BedrockModel(**model_config)
