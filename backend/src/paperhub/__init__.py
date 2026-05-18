"""PaperHub backend package."""

import logging


class _SuppressLiteLlmAwsPreload(logging.Filter):
    """Drop LiteLLM's boot-time warnings about missing Bedrock/SageMaker shapes.

    LiteLLM eagerly tries to pre-load AWS service shapes for streaming. We don't
    use AWS, so botocore is intentionally absent — the warnings are noise.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        return not (
            "could not pre-load bedrock-runtime" in msg
            or "could not pre-load sagemaker-runtime" in msg
        )


logging.getLogger("LiteLLM").addFilter(_SuppressLiteLlmAwsPreload())


def hello() -> str:
    return "Hello from paperhub!"
