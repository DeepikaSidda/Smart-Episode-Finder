"""LLM access and prompts.

Ported verbatim from activities/analyzer.py so ranking/summarisation behaviour is
unchanged; only the Temporal decorators and logging calls are removed.
"""
import json
import logging

from .config import settings

log = logging.getLogger(__name__)

EXTRACT_PROMPT = """You are an interest parser. Given a raw user interests string,
extract specific keywords and broader topic categories.
Keywords: specific terms the user mentioned (e.g. "kubernetes", "RAG", "temporal").
Topics: broader categories (e.g. "cloud infrastructure", "AI/ML", "devops").

Return ONLY valid JSON in this exact format, no other text:
{"keywords": ["keyword1", "keyword2"], "topics": ["topic1", "topic2"]}"""

RANK_PROMPT = """You are a podcast episode recommendation engine that scores long-form YouTube videos for a specific listener.

You receive rich metadata per episode: title, description, tags, chapters, view/like/comment counts, duration, and when available, transcript excerpts. Use ALL signals:
- Transcripts are the STRONGEST signal — they reveal exactly what was discussed
- Chapters reveal depth and breadth of topics
- Tags reveal exact topics and guest names
- High like-to-view ratio indicates engaging content

Scoring guide:
- 85-100: Episode directly covers a user keyword — confirmed by transcript, chapters, tags, or title
- 70-84: Strong topical overlap, likely valuable discussion
- 50-69: Adjacent topic a curious listener would enjoy
- 30-49: Weak connection
- 0-29: Completely unrelated

Rules:
- ONLY use videos from the provided list — never invent episodes
- Copy the EXACT title, URL, duration, and views from the input
- Every video MUST appear in output — rank ALL of them
- Sort by score descending
- In "why", mention what makes this episode valuable

Return ONLY valid JSON in this exact format, no other text:
{"recommendations": [{"title": "exact title", "url": "exact url", "score": 85, "why": "reason", "duration": "duration", "views": 12345}]}"""

SUMMARY_PROMPT = """You are a podcast channel analyst specializing in YouTube long-form content.
Given a channel name, its episode catalog, and when available, transcript excerpts, produce:
1. A brief summary of the podcast's focus and format (2-3 sentences)
2. 3-5 key insights (recurring themes, discussion depth, standout content)
3. The content tone: educational, entertainment, news, tutorial, or mixed

Return ONLY valid JSON in this exact format, no other text:
{"summary": "channel summary here", "key_insights": ["insight1", "insight2", "insight3"], "tone": "educational"}"""

DEEP_DIVE_PROMPT = """You are a podcast episode analyst. Given a full transcript of a podcast episode, produce a detailed deep dive analysis.

Be specific and detailed — reference actual content, examples, tools, and concepts mentioned in the episode. Don't be generic.

Return ONLY valid JSON in this exact format, no other text:
{
  "tldr": "3-4 sentence detailed summary covering the main narrative arc of the episode",
  "key_topics": ["topic1", "topic2", "topic3", "topic4", "topic5"],
  "key_takeaways": ["detailed takeaway 1", "detailed takeaway 2", "detailed takeaway 3", "detailed takeaway 4", "detailed takeaway 5", "detailed takeaway 6", "detailed takeaway 7"],
  "notable_quotes": ["exact quote 1", "exact quote 2", "exact quote 3"],
  "timestamps_summary": ["0:00 - Introduction and overview", "10:00 - First major topic", "20:00 - Second major topic"],
  "tools_and_resources": ["tool or resource mentioned 1", "tool or resource mentioned 2"],
  "who_should_watch": "Detailed description of the ideal viewer and what they will gain"
}"""


def _call_bedrock(system_prompt: str, user_prompt: str) -> str:
    """Invoke Bedrock with Meta's Llama prompt format."""
    import boto3

    client = boto3.client("bedrock-runtime", region_name=settings.bedrock_region)

    full_prompt = f"""<|begin_of_text|><|start_header_id|>system<|end_header_id|>

{system_prompt}<|eot_id|><|start_header_id|>user<|end_header_id|>

{user_prompt}<|eot_id|><|start_header_id|>assistant<|end_header_id|>

"""

    body = json.dumps({
        "prompt": full_prompt,
        "max_gen_len": 4096,
        "temperature": 0.2,
    })

    response = client.invoke_model(
        modelId=settings.bedrock_model_id,
        body=body,
        contentType="application/json",
        accept="application/json",
    )

    result = json.loads(response["body"].read())
    return result.get("generation", "")


def _call_gemini(system_prompt: str, user_prompt: str) -> str:
    """Invoke Google Gemini.

    google-genai is intentionally NOT bundled in the Lambda package (it is a large
    dependency and LLM_PROVIDER is bedrock). The code path is preserved so the
    provider can be re-enabled by adding the dependency to the build.
    """
    try:
        from google import genai
        from google.genai import types
    except ImportError as e:  # pragma: no cover
        raise RuntimeError(
            "LLM_PROVIDER=gemini requires the google-genai package, which is not "
            "included in this Lambda package. Add it to serverless/requirements.txt "
            "and redeploy, or set LLM_PROVIDER=bedrock."
        ) from e

    client = genai.Client(
        api_key=settings.gemini_api_key,
        http_options=types.HttpOptions(timeout=45_000),
    )

    response = client.models.generate_content(
        model=settings.gemini_model,
        contents=user_prompt,
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=0.2,
        ),
    )
    return response.text


def call_llm(system_prompt: str, user_prompt: str) -> str:
    """Route to the configured LLM provider."""
    if settings.llm_provider == "bedrock":
        return _call_bedrock(system_prompt, user_prompt)
    return _call_gemini(system_prompt, user_prompt)


def extract_json(text: str) -> dict:
    """Pull a JSON object out of an LLM response, tolerating markdown fences."""
    text = (text or "").strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines).strip()
    start = text.find("{")
    end = text.rfind("}") + 1
    if start >= 0 and end > start:
        return json.loads(text[start:end])
    return json.loads(text)
