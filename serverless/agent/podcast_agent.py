"""The Smart Episode Finder agent.

Built with the Strands Agents SDK on Amazon Bedrock. The model decides which tools
to call; nothing here is a fixed pipeline.

The `with_ears` flag exists so the same agent can be run with and without the new
Amazon Transcribe tool, which is how the before/after contrast is demonstrated.

Model choice, verified against this account:
  * Amazon Nova 2 Lite  - supports tool use WITH streaming. Used to drive the agent.
  * Llama 3.3 70B       - tool use only works with streaming disabled.
  * Anthropic Claude    - unavailable here (Bedrock Marketplace subscription blocked).
"""
import logging
import os

from strands import Agent
from strands.models import BedrockModel

from .tools import (
    LAST_TRANSCRIPT,
    TOOL_CALLS,
    listen_to_episode,
    reset_guards,
    search_podcast_episodes,
)

log = logging.getLogger(__name__)

AGENT_MODEL_ID = os.environ.get("AGENT_MODEL_ID", "us.amazon.nova-2-lite-v1:0")
AWS_REGION = os.environ.get("BEDROCK_REGION", "us-east-1")

_BASE_PROMPT = """You are Smart Episode Finder, an agent that helps people decide \
which podcast episodes and long-form videos are worth their time."""

_METADATA_ONLY_PROMPT = """
You have ONE tool:

- `search_podcast_episodes` queries the YouTube Data API. It returns METADATA ONLY —
  titles, descriptions, tags, view counts. It tells you what an episode is *called*,
  never what was *said* inside it.

Rules:
1. You have no way to access the spoken content of any episode. If asked what was
   said, discussed, claimed, or who appeared, you must say plainly that you cannot
   determine it, because you can only read episode metadata. Never guess an episode's
   content from its title and never invent quotes.
2. If a tool returns an `error` or an `instruction`, obey it and do not retry.

Be concise and honest about what you cannot do."""

_WITH_EARS_PROMPT = """
Your two tools read from COMPLETELY DIFFERENT SOURCES. This matters:

- `search_podcast_episodes` queries the YouTube Data API. It returns METADATA ONLY —
  titles, descriptions, tags, view counts. It tells you what an episode is *called*,
  never what was *said* inside it.
- `listen_to_episode` fetches the real episode audio from the podcast's RSS feed and
  transcribes it with Amazon Transcribe. It returns actual spoken words.

CRITICAL: `listen_to_episode` is INDEPENDENT of `search_podcast_episodes`. It does
not need search to run first, and it reaches a different catalogue. Many podcasts are
not on YouTube at all but are fully listenable via RSS. An episode missing from
search results may still be available to listen to.

Rules:
1. If the question is about what was SAID, DISCUSSED, CLAIMED, MENTIONED, or WHO
   APPEARED, call `listen_to_episode` FIRST. Do not call search first, and never
   answer a content question from a title — that is guessing.
2. NEVER conclude an episode does not exist because search did not return it. To
   answer a content question, call `listen_to_episode` with the show name and a hint
   from the episode title.
3. Only use `search_podcast_episodes` when the question is about which episodes exist,
   how long they are, or how popular they are.
4. When you have a transcript, quote it directly and cite the timestamps. Be specific
   about names and terms that appear in the transcript.
5. Call `listen_to_episode` AT MOST ONCE per episode. Each call is a paid
   transcription job. The transcript you get back is all the audio available to you —
   never call again asking for more minutes. If something is not in it, simply say it
   was not discussed in the portion transcribed.
6. If a tool returns an `error` or an `instruction`, obey the instruction and do not
   retry the tool.

Be concise and concrete."""


def system_prompt(with_ears: bool) -> str:
    """Build the prompt. The metadata-only agent is never told the listen tool
    exists, so the 'before' case reflects a genuine capability gap rather than an
    agent declining to use a tool it can see."""
    return _BASE_PROMPT + (_WITH_EARS_PROMPT if with_ears else _METADATA_ONLY_PROMPT)


def build_agent(with_ears: bool = True) -> Agent:
    """Create the agent.

    Args:
        with_ears: When False the Amazon Transcribe tool is withheld, reproducing the
            agent's original metadata-only behaviour.
    """
    tools = [search_podcast_episodes]
    if with_ears:
        tools.append(listen_to_episode)

    model = BedrockModel(
        model_id=AGENT_MODEL_ID,
        region_name=AWS_REGION,
        streaming=True,
    )

    log.info(
        "agent model=%s tools=%s", AGENT_MODEL_ID, [t.tool_name for t in tools]
    )
    return Agent(
        model=model,
        tools=tools,
        system_prompt=system_prompt(with_ears),
        callback_handler=None,
    )


def ask(question: str, with_ears: bool = True) -> dict:
    """Run one question through the agent and report which tools it used."""
    reset_guards()
    agent = build_agent(with_ears=with_ears)
    result = agent(question)
    return {
        "question": question,
        "with_ears": with_ears,
        "answer": str(result).strip(),
        "tools_used": list(TOOL_CALLS),
        "tools_available": (
            ["search_podcast_episodes", "listen_to_episode"]
            if with_ears
            else ["search_podcast_episodes"]
        ),
        # Raw Amazon Transcribe output, so a caller can show the evidence behind
        # the answer rather than just the answer.
        "transcript": dict(LAST_TRANSCRIPT) if LAST_TRANSCRIPT else None,
        "model": AGENT_MODEL_ID,
    }
