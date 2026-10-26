"""Runtime configuration, read from Lambda environment variables.

Deliberately plain os.environ rather than pydantic-settings: it keeps the Lambda
deployment package pure-Python (no pydantic-core binary wheel), which means the
package can be built on Windows without cross-compiling manylinux wheels.
"""
import os


class Settings:
    def __init__(self) -> None:
        # Content providers
        self._youtube_api_key = os.environ.get("YOUTUBE_API_KEY", "")
        self.youtube_api_key_param = os.environ.get("YOUTUBE_API_KEY_PARAM", "")
        self.spotify_client_id = os.environ.get("SPOTIFY_CLIENT_ID", "")
        self.spotify_client_secret = os.environ.get("SPOTIFY_CLIENT_SECRET", "")

        # LLM
        self.llm_provider = os.environ.get("LLM_PROVIDER", "bedrock")
        self.bedrock_model_id = os.environ.get(
            "BEDROCK_MODEL_ID", "us.meta.llama3-3-70b-instruct-v1:0"
        )
        self.bedrock_region = os.environ.get("BEDROCK_REGION", "us-east-1")
        self.gemini_api_key = os.environ.get("GEMINI_API_KEY", "")
        self.gemini_model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

        # AWS wiring
        self.jobs_bucket = os.environ.get("JOBS_BUCKET", "")
        self.state_machine_arn = os.environ.get("STATE_MACHINE_ARN", "")
        self.deep_dive_state_machine_arn = os.environ.get("DEEP_DIVE_STATE_MACHINE_ARN", "")

    @property
    def youtube_api_key(self) -> str:
        """Resolve the YouTube key, preferring an SSM SecureString parameter.

        Resolved once per Lambda execution environment and cached, so this costs a
        single GetParameter call per cold start. Keeping the key in Parameter Store
        rather than a Lambda env var means it can be rotated without redeploying
        and is never visible in the Lambda console.
        """
        if self._youtube_api_key:
            return self._youtube_api_key

        if self.youtube_api_key_param:
            import boto3
            ssm = boto3.client("ssm")
            resp = ssm.get_parameter(
                Name=self.youtube_api_key_param, WithDecryption=True
            )
            self._youtube_api_key = resp["Parameter"]["Value"]

        return self._youtube_api_key


settings = Settings()
